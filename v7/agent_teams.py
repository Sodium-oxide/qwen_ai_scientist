from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from .compact import compact_messages
    from .config import TEAM_INBOX_DIR, TEAMMATE_IDLE_POLL_SECONDS, TEAMMATE_MAX_IDLE_SECONDS
    from .llm import get_client
    from .log import log_event
    from .recovery import RecoveryState, create_response_with_recovery
    from .skill import build_system
    from .tools import BASIC_TOOLS, COMPACT_TOOL, TOOL_HANDLERS
except ImportError:
    from compact import compact_messages
    from config import TEAM_INBOX_DIR, TEAMMATE_IDLE_POLL_SECONDS, TEAMMATE_MAX_IDLE_SECONDS
    from llm import get_client
    from log import log_event
    from recovery import RecoveryState, create_response_with_recovery
    from skill import build_system
    from tools import BASIC_TOOLS, COMPACT_TOOL, TOOL_HANDLERS


LEAD = "lead"
PROTOCOL_RESPONSE = {
    "shutdown": "shutdown_response",
    "plan_approval": "plan_approval_response",
}

bus_lock = threading.Lock()
protocol_lock = threading.Lock()
teammate_lock = threading.Lock()

pending_requests: dict[str, "ProtocolState"] = {}
teammate_threads: dict[str, threading.Thread] = {}
teammate_sessions: dict[str, str] = {}


@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float


def spawn_teammate(name: str, task: str = "") -> str:
    safe_name = sanitize_agent(name)
    with teammate_lock:
        existing = teammate_threads.get(safe_name)
        if existing and existing.is_alive():
            return f"Teammate {safe_name} is already running."
        clear_inbox(safe_name)
        session_id = new_session_id(safe_name)
        thread = threading.Thread(
            target=teammate_loop,
            args=(safe_name, session_id),
            daemon=True,
        )
        teammate_threads[safe_name] = thread
        teammate_sessions[safe_name] = session_id
        thread.start()
    log_event("TEAM", "spawned", name=safe_name, session_id=session_id, task=task)
    if task:
        send_message(LEAD, safe_name, task, type="message")
    return f"Spawned teammate {safe_name} ({session_id})."


def send_message(sender: str, to: str, content: str, type: str = "message", metadata: dict[str, Any] | None = None) -> str:
    sender_name = sanitize_agent(sender)
    target_name = sanitize_agent(to)
    metadata = dict(metadata or {})
    if sender_name == LEAD and target_name != LEAD:
        session_id = active_session(target_name)
        if not session_id:
            log_event("WARN", "send_to_offline_teammate", to=target_name, type=type)
            return f"Teammate {target_name} is not running; message was not sent."
        metadata.setdefault("target_session_id", session_id)
    msg = {
        "from": sender_name,
        "to": target_name,
        "content": content,
        "type": type,
        "ts": time.time(),
        "metadata": metadata,
    }
    append_inbox(msg["to"], msg)
    log_event("TEAM", "send", sender=msg["from"], to=msg["to"], type=type)
    return f"Sent {type} message to {msg['to']}."


def check_inbox(agent: str = LEAD) -> str:
    messages = consume_inbox(agent, route_protocol=(sanitize_agent(agent) == LEAD))
    if not messages:
        return "(inbox empty)"
    return render_messages(messages)


def request_shutdown(teammate: str, reason: str = "") -> str:
    teammate = sanitize_agent(teammate)
    req = create_protocol("shutdown", LEAD, teammate, reason or "Lead requested shutdown.")
    result = send_message(
        LEAD,
        teammate,
        req.payload,
        type="shutdown_request",
        metadata={"request_id": req.request_id},
    )
    if "not running" in result:
        with protocol_lock:
            req.status = "rejected"
            pending_requests[req.request_id] = req
        return result
    return f"Shutdown requested for {teammate}: {req.request_id}"


def request_plan(teammate: str, prompt: str) -> str:
    teammate = sanitize_agent(teammate)
    result = send_message(LEAD, teammate, prompt, type="message", metadata={"request_plan": True})
    if "not running" in result:
        return result
    return f"Plan requested from {teammate}."


def review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
    with protocol_lock:
        req = pending_requests.get(request_id)
    if not req:
        raise ValueError(f"Unknown protocol request: {request_id}")
    if req.type != "plan_approval":
        raise ValueError(f"Request {request_id} is not a plan approval request.")
    result = send_message(
        LEAD,
        req.sender,
        feedback or ("Approved." if approve else "Rejected."),
        type="plan_approval_response",
        metadata={"request_id": request_id, "approve": bool(approve)},
    )
    if "not running" in result:
        return result
    with protocol_lock:
        req.status = "approved" if approve else "rejected"
        pending_requests[request_id] = req
    log_event("TEAM", "protocol_resolved", request_id=request_id, status=req.status)
    return f"Plan {'approved' if approve else 'rejected'} for {req.sender}."


def consume_lead_inbox() -> list[str]:
    messages = consume_inbox(LEAD, route_protocol=True)
    return [render_message(message) for message in messages]


def consume_inbox(agent: str, *, route_protocol: bool = False) -> list[dict[str, Any]]:
    messages = read_inbox(agent)
    if route_protocol:
        for msg in messages:
            msg_type = str(msg.get("type", ""))
            if msg_type.endswith("_response"):
                request_id = str(msg.get("metadata", {}).get("request_id", ""))
                approve = bool(msg.get("metadata", {}).get("approve", msg_type != "plan_approval_response"))
                match_response(msg_type, request_id, approve)
            elif msg_type == "plan_approval_request":
                request_id = str(msg.get("metadata", {}).get("request_id", ""))
                with protocol_lock:
                    pending_requests.setdefault(
                        request_id,
                        ProtocolState(
                            request_id=request_id,
                            type="plan_approval",
                            sender=str(msg.get("from", "")),
                            target=LEAD,
                            status="pending",
                            payload=str(msg.get("content", "")),
                            created_at=float(msg.get("ts", time.time())),
                        ),
                    )
    return messages


def teammate_loop(name: str, session_id: str, initial_task: str = "") -> None:
    client = get_client()
    recovery_state = RecoveryState()
    messages: list[dict[str, Any]] = []
    if initial_task:
        messages.append({"role": "user", "content": initial_task})
    log_event("TEAMMATE", "loop_start", name=name, session_id=session_id)
    idle_since = time.time()

    try:
        while True:
            inbox = read_inbox(name)
            stop = False
            for msg in inbox:
                if handle_inbox_message(name, session_id, msg, messages):
                    stop = True
            if stop:
                log_event("TEAMMATE", "loop_stop", name=name, session_id=session_id)
                return

            if not messages:
                if time.time() - idle_since > TEAMMATE_MAX_IDLE_SECONDS:
                    log_event("TEAMMATE", "idle_timeout", name=name, session_id=session_id)
                    return
                time.sleep(TEAMMATE_IDLE_POLL_SECONDS)
                continue

            idle_since = time.time()
            messages[:] = compact_messages(messages)
            response = create_response_with_recovery(
                client,
                system=build_system(f"teammate {name}", subagent=True),
                messages=messages,
                tools=teammate_tools(),
                state=recovery_state,
                focus=name,
            )

            text = response_text(response.content)
            tool_blocks = [block for block in response.content if block_attr(block, "type") == "tool_use"]
            if text:
                send_message(name, LEAD, text, type="result", metadata={"from_session_id": session_id})
            if not tool_blocks:
                messages.clear()
                continue

            messages.append({"role": "assistant", "content": [block_to_dict(block) for block in response.content]})
            messages.append({"role": "user", "content": [run_teammate_tool(block) for block in tool_blocks]})
    finally:
        with teammate_lock:
            current = teammate_sessions.get(name)
            if current == session_id:
                teammate_threads.pop(name, None)
                teammate_sessions.pop(name, None)


def handle_inbox_message(name: str, session_id: str, msg: dict[str, Any], messages: list[dict[str, Any]]) -> bool:
    msg_type = str(msg.get("type", "message"))
    content = str(msg.get("content", ""))
    metadata = msg.get("metadata", {}) if isinstance(msg.get("metadata"), dict) else {}
    target_session_id = str(metadata.get("target_session_id", ""))
    if target_session_id and target_session_id != session_id:
        log_event(
            "WARN",
            "ignored_stale_team_message",
            name=name,
            session_id=session_id,
            target_session_id=target_session_id,
            type=msg_type,
        )
        return False

    if msg_type == "shutdown_request":
        request_id = str(metadata.get("request_id", ""))
        send_message(
            name,
            str(msg.get("from", LEAD)),
            "Shutdown acknowledged.",
            type="shutdown_response",
            metadata={"request_id": request_id, "approve": True, "from_session_id": session_id},
        )
        return True

    if msg_type == "plan_approval_response":
        approved = bool(metadata.get("approve"))
        marker = "[Plan approved]" if approved else "[Plan rejected]"
        messages.append({"role": "user", "content": f"{marker}\n{content}"})
        return False

    if metadata.get("request_plan"):
        request_id = new_request_id()
        create_protocol("plan_approval", name, LEAD, content, request_id=request_id)
        send_message(
            name,
            LEAD,
            f"Plan from {name}:\n{content}",
            type="plan_approval_request",
            metadata={"request_id": request_id, "from_session_id": session_id},
        )
        return False

    messages.append({"role": "user", "content": f"Message from {msg.get('from', 'unknown')}:\n{content}"})
    return False


def create_protocol(type: str, sender: str, target: str, payload: str, request_id: str | None = None) -> ProtocolState:
    req = ProtocolState(
        request_id=request_id or new_request_id(),
        type=type,
        sender=sender,
        target=target,
        status="pending",
        payload=payload,
        created_at=time.time(),
    )
    with protocol_lock:
        pending_requests[req.request_id] = req
    return req


def match_response(response_type: str, request_id: str, approve: bool) -> ProtocolState | None:
    if not request_id:
        return None
    with protocol_lock:
        req = pending_requests.get(request_id)
        if not req or req.status != "pending":
            return None
        expected = PROTOCOL_RESPONSE.get(req.type)
        if expected != response_type:
            log_event("WARN", "protocol_type_mismatch", request_id=request_id, expected=expected, got=response_type)
            return None
        req.status = "approved" if approve else "rejected"
        pending_requests[request_id] = req
    log_event("TEAM", "protocol_resolved", request_id=request_id, status=req.status)
    return req


def teammate_tools() -> list[dict[str, Any]]:
    return [*BASIC_TOOLS, COMPACT_TOOL]


def run_teammate_tool(block: Any) -> dict[str, Any]:
    name = normalize_tool_name(block_attr(block, "name"))
    tool_input = block_attr(block, "input", {}) or {}
    tool_use_id = block_attr(block, "id")
    try:
        if name == "compact":
            output = "Context compaction noted."
        else:
            output = TOOL_HANDLERS[name](**strip_control_args(tool_input))
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": output}
    except Exception as exc:
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": f"ERROR: {exc}", "is_error": True}


def append_inbox(agent: str, message: dict[str, Any]) -> None:
    TEAM_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    path = inbox_path(agent)
    line = json.dumps(message, ensure_ascii=False)
    with bus_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_inbox(agent: str) -> list[dict[str, Any]]:
    path = inbox_path(agent)
    with bus_lock:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        path.unlink(missing_ok=True)
    messages: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError as exc:
            log_event("WARN", "inbox_decode_failed", agent=agent, error=exc)
    return messages


def clear_inbox(agent: str) -> int:
    path = inbox_path(agent)
    with bus_lock:
        if not path.exists():
            return 0
        count = len(path.read_text(encoding="utf-8").splitlines())
        path.unlink(missing_ok=True)
    log_event("TEAM", "clear_inbox", agent=sanitize_agent(agent), messages=count)
    return count


def active_session(agent: str) -> str | None:
    safe_agent = sanitize_agent(agent)
    with teammate_lock:
        thread = teammate_threads.get(safe_agent)
        if not thread or not thread.is_alive():
            teammate_threads.pop(safe_agent, None)
            teammate_sessions.pop(safe_agent, None)
            return None
        return teammate_sessions.get(safe_agent)


def inbox_path(agent: str) -> Path:
    TEAM_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    return TEAM_INBOX_DIR / f"{sanitize_agent(agent)}.jsonl"


def render_messages(messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(render_message(message) for message in messages)


def render_message(message: dict[str, Any]) -> str:
    return (
        f"[{message.get('type', 'message')}] "
        f"from={message.get('from')} to={message.get('to')} "
        f"metadata={message.get('metadata', {})}\n"
        f"{message.get('content', '')}"
    )


def sanitize_agent(name: str) -> str:
    value = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    value = "".join(char for char in value if char.isalnum() or char == "_")
    return value or "agent"


def new_request_id() -> str:
    return f"req_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"


def new_session_id(name: str) -> str:
    return f"{sanitize_agent(name)}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"


def strip_control_args(tool_input: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in tool_input.items() if key != "run_in_background"}


def normalize_tool_name(name: Any) -> str:
    raw = str(name)
    aliases = {
        "bash": "bash",
        "read": "read_file",
        "readfile": "read_file",
        "read_file": "read_file",
        "write": "write_file",
        "writefile": "write_file",
        "write_file": "write_file",
        "edit": "edit_file",
        "editfile": "edit_file",
        "edit_file": "edit_file",
        "glob": "glob",
        "compact": "compact",
    }
    key = raw.replace("-", "_").replace(" ", "_").lower()
    compact_key = key.replace("_", "")
    return aliases.get(key) or aliases.get(compact_key) or key


def block_attr(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    if hasattr(block, "dict"):
        return block.dict(exclude_none=True)
    raise TypeError(f"Unsupported response block: {type(block)!r}")


def response_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if block_attr(block, "type") == "text":
            parts.append(block_attr(block, "text", ""))
    return "\n".join(part for part in parts if part)
