from __future__ import annotations

import argparse
import json
import time
from typing import Any

try:
    from .compact import compact_in_place, compact_messages
    from .cron_scheduler import agent_lock, consume_cron_queue, render_scheduled_prompt, start_cron_services
    from .hook import trigger_hook
    from .final_validation import extract_task_ids, validate_before_final
    from .llm import get_client
    from .log import log_event
    from .memory import extract_memories
    from .mcp_plugin import assemble_tool_pool
    from .recovery import RecoveryState, create_response_with_recovery
    from .skill import build_system
    from .agent_teams import consume_lead_inbox
    from .task_system import collect_background_notifications, should_run_background, start_background_task, strip_control_args
    from .tools import TOOL_HANDLERS as BUILTIN_TOOL_HANDLERS, TOOLS as BUILTIN_TOOLS
except ImportError:
    from compact import compact_in_place, compact_messages
    from cron_scheduler import agent_lock, consume_cron_queue, render_scheduled_prompt, start_cron_services
    from hook import trigger_hook
    from final_validation import extract_task_ids, validate_before_final
    from llm import get_client
    from log import log_event
    from memory import extract_memories
    from mcp_plugin import assemble_tool_pool
    from recovery import RecoveryState, create_response_with_recovery
    from skill import build_system
    from agent_teams import consume_lead_inbox
    from task_system import collect_background_notifications, should_run_background, start_background_task, strip_control_args
    from tools import TOOL_HANDLERS as BUILTIN_TOOL_HANDLERS, TOOLS as BUILTIN_TOOLS


def block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    if hasattr(block, "dict"):
        return block.dict(exclude_none=True)
    raise TypeError(f"Unsupported response block: {type(block)!r}")


def block_attr(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def response_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if block_attr(block, "type") == "text":
            parts.append(block_attr(block, "text", ""))
    return "\n".join(part for part in parts if part)


def tool_result(tool_use_id: str, output: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": output,
    }
    if is_error:
        result["is_error"] = True
    return result


def run_tool(
    block: Any,
    messages: list[dict[str, Any]],
    handlers: dict[str, Any],
) -> dict[str, Any]:
    name = normalize_tool_name(block_attr(block, "name"))
    tool_input = block_attr(block, "input", {}) or {}
    tool_use_id = block_attr(block, "id")

    duplicate_count = repeated_tool_call_count(messages, name, strip_control_args(tool_input))
    if name in {"verify_citation_uniqueness"} and duplicate_count >= 3:
        output = (
            "Duplicate idempotent tool call suppressed: this exact verify_citation_uniqueness input "
            f"has already been requested {duplicate_count} times in the current run. "
            "Stop repeating this check; use the cached uniqueness result or continue to import/search with real retrieved papers."
        )
        log_event("WARN", "duplicate_tool_call_suppressed", name=name, count=duplicate_count)
        return tool_result(tool_use_id, output, is_error=True)

    blocked = trigger_hook("PreToolUse", block)
    if blocked is not None:
        return tool_result(tool_use_id, blocked, is_error=True)

    try:
        if name == "compact":
            focus = str(tool_input.get("focus", ""))
            compact_in_place(messages, focus=focus, force_l0=False)
            output = "Context compacted."
        else:
            handler = handlers[name]
            if should_run_background(block):
                output = start_background_task(block, handler, strip_control_args(tool_input))
            else:
                output = handler(**strip_control_args(tool_input))
    except Exception as exc:
        output = f"ERROR: {exc}"
        trigger_hook("PostToolUse", block, output)
        return tool_result(tool_use_id, output, is_error=True)

    trigger_hook("PostToolUse", block, output)
    return tool_result(tool_use_id, output)


def repeated_tool_call_count(messages: list[dict[str, Any]], name: str, tool_input: dict[str, Any]) -> int:
    signature = tool_call_signature(name, tool_input)
    count = 0
    for message in messages[-120:]:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            prior_name = normalize_tool_name(block.get("name"))
            prior_input = strip_control_args(block.get("input") or {})
            if tool_call_signature(prior_name, prior_input) == signature:
                count += 1
    return count


def tool_call_signature(name: str, tool_input: dict[str, Any]) -> str:
    try:
        payload = json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        payload = str(tool_input)
    return f"{name}:{payload}"


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
        "todowrite": "todo_write",
        "todo_write": "todo_write",
        "task": "task",
        "spawnsubagent": "task",
        "spawn_subagent": "task",
        "loadskill": "load_skill",
        "load_skill": "load_skill",
        "createtask": "create_task",
        "create_task": "create_task",
        "listtasks": "list_tasks",
        "list_tasks": "list_tasks",
        "gettask": "get_task",
        "get_task": "get_task",
        "claimtask": "claim_task",
        "claim_task": "claim_task",
        "completetask": "complete_task",
        "complete_task": "complete_task",
        "spawnteammate": "spawn_teammate",
        "spawn_teammate": "spawn_teammate",
        "sendmessage": "send_message",
        "send_message": "send_message",
        "messageactiongateway": "send_message",
        "message_action_gateway": "send_message",
        "checkinbox": "check_inbox",
        "check_inbox": "check_inbox",
        "requestshutdown": "request_shutdown",
        "request_shutdown": "request_shutdown",
        "requestplan": "request_plan",
        "request_plan": "request_plan",
        "reviewplan": "review_plan",
        "review_plan": "review_plan",
        "createworktree": "create_worktree",
        "create_worktree": "create_worktree",
        "removeworktree": "remove_worktree",
        "remove_worktree": "remove_worktree",
        "keepworktree": "keep_worktree",
        "keep_worktree": "keep_worktree",
        "connectmcp": "connect_mcp",
        "connect_mcp": "connect_mcp",
        "schedulecron": "schedule_cron",
        "schedule_cron": "schedule_cron",
        "listcrons": "list_crons",
        "list_crons": "list_crons",
        "cancelcron": "cancel_cron",
        "cancel_cron": "cancel_cron",
        "createresearchproject": "create_research_project",
        "create_research_project": "create_research_project",
        "listresearchprojects": "list_research_projects",
        "list_research_projects": "list_research_projects",
        "getresearchproject": "get_research_project",
        "get_research_project": "get_research_project",
        "listscienceagents": "list_science_agents",
        "list_science_agents": "list_science_agents",
        "getscienceagentprompt": "get_science_agent_prompt",
        "get_science_agent_prompt": "get_science_agent_prompt",
        "listliteratureproviders": "list_literature_providers",
        "list_literature_providers": "list_literature_providers",
        "exploredomainsubspaces": "explore_domain_subspaces",
        "explore_domain_subspaces": "explore_domain_subspaces",
        "searchliterature": "search_literature",
        "search_literature": "search_literature",
        "searchliteraturestratified": "search_literature_stratified",
        "search_literature_stratified": "search_literature_stratified",
        "searchpapers": "search_papers",
        "search_papers": "search_papers",
        "searchpapersstratified": "search_papers_stratified",
        "search_papers_stratified": "search_papers_stratified",
        "extractstructuredinfo": "extract_structured_info",
        "extract_structured_info": "extract_structured_info",
        "selectliteratureresult": "select_literature_result",
        "select_literature_result": "select_literature_result",
        "expandliteraturegraph": "expand_literature_graph",
        "expand_literature_graph": "expand_literature_graph",
        "buildliteraturerelationgraph": "build_literature_relation_graph",
        "build_literature_relation_graph": "build_literature_relation_graph",
        "createsciencepipelinetasks": "create_science_pipeline_tasks",
        "create_science_pipeline_tasks": "create_science_pipeline_tasks",
        "createsciencedelegationtasks": "create_science_delegation_tasks",
        "create_science_delegation_tasks": "create_science_delegation_tasks",
        "createboxuedelegationtasks": "create_boxue_delegation_tasks",
        "create_boxue_delegation_tasks": "create_boxue_delegation_tasks",
        "runboxueresearchround": "run_boxue_research_round",
        "run_boxue_research_round": "run_boxue_research_round",
        "createautogengroupchat": "create_autogen_groupchat",
        "create_autogen_groupchat": "create_autogen_groupchat",
        "runautogenresearchflow": "run_autogen_research_flow",
        "run_autogen_research_flow": "run_autogen_research_flow",
        "listautogengroupchats": "list_autogen_groupchats",
        "list_autogen_groupchats": "list_autogen_groupchats",
        "getautogenrun": "get_autogen_run",
        "get_autogen_run": "get_autogen_run",
        "createsciencecrew": "create_autogen_groupchat",
        "create_science_crew": "create_autogen_groupchat",
        "runsciencecrewflow": "run_autogen_research_flow",
        "run_science_crew_flow": "run_autogen_research_flow",
        "listsciencecrews": "list_autogen_groupchats",
        "list_science_crews": "list_autogen_groupchats",
        "getsciencecrewrun": "get_autogen_run",
        "get_science_crew_run": "get_autogen_run",
        "buildknowledgemap": "build_knowledge_map",
        "build_knowledge_map": "build_knowledge_map",
        "addliteratureevidence": "add_literature_evidence",
        "add_literature_evidence": "add_literature_evidence",
        "importliteraturetext": "import_literature_text",
        "import_literature_text": "import_literature_text",
        "importliteraturefile": "import_literature_file",
        "import_literature_file": "import_literature_file",
        "importliteraturesearchresult": "import_literature_search_result",
        "import_literature_search_result": "import_literature_search_result",
        "extractpaperkeynote": "extract_paper_keynote",
        "extract_paper_keynote": "extract_paper_keynote",
        "importpapergraphrecord": "import_papergraph_record",
        "import_papergraph_record": "import_papergraph_record",
        "listpapergraphrecords": "list_papergraph_records",
        "list_papergraph_records": "list_papergraph_records",
        "verifycitationuniqueness": "verify_citation_uniqueness",
        "verify_citation_uniqueness": "verify_citation_uniqueness",
        "assessnovelty": "assess_novelty",
        "assess_novelty": "assess_novelty",
        "verifyuniqueness": "verify_uniqueness",
        "verify_uniqueness": "verify_uniqueness",
        "runzhizhiliteratureanalysis": "run_zhizhi_literature_analysis",
        "run_zhizhi_literature_analysis": "run_zhizhi_literature_analysis",
        "parseliteraturetext": "parse_literature_text",
        "parse_literature_text": "parse_literature_text",
        "buildcoveragematrix": "build_coverage_matrix",
        "build_coverage_matrix": "build_coverage_matrix",
        "detectknowledgegaps": "detect_knowledge_gaps",
        "detect_knowledge_gaps": "detect_knowledge_gaps",
        "runtanxigapexploration": "run_tanxi_gap_exploration",
        "run_tanxi_gap_exploration": "run_tanxi_gap_exploration",
        "checksemanticplausibility": "check_semantic_plausibility",
        "check_semantic_plausibility": "check_semantic_plausibility",
        "generateidea": "generate_idea",
        "generate_idea": "generate_idea",
        "designexperiment": "design_experiment",
        "design_experiment": "design_experiment",
        "finalizeidea": "finalize_idea",
        "finalize_idea": "finalize_idea",
        "createhypothesis": "create_hypothesis",
        "create_hypothesis": "create_hypothesis",
        "asksocraticquestions": "ask_socratic_questions",
        "ask_socratic_questions": "ask_socratic_questions",
        "askcriticalquestions": "ask_critical_questions",
        "ask_critical_questions": "ask_critical_questions",
        "findcounterexamples": "find_counterexamples",
        "find_counterexamples": "find_counterexamples",
        "stresstestassumptions": "stress_test_assumptions",
        "stress_test_assumptions": "stress_test_assumptions",
        "moderateround": "moderate_round",
        "moderate_round": "moderate_round",
        "summarizepositions": "summarize_positions",
        "summarize_positions": "summarize_positions",
        "extractemergentmethod": "extract_emergent_method",
        "extract_emergent_method": "extract_emergent_method",
        "runsocratichypothesisdebate": "run_socratic_hypothesis_debate",
        "run_socratic_hypothesis_debate": "run_socratic_hypothesis_debate",
        "runmechanismcheck": "run_mechanism_check",
        "run_mechanism_check": "run_mechanism_check",
        "checkinternalconsistency": "check_internal_consistency",
        "check_internal_consistency": "check_internal_consistency",
        "checkdataconsistency": "check_data_consistency",
        "check_data_consistency": "check_data_consistency",
        "regimeshifttest": "regime_shift_test",
        "regime_shift_test": "regime_shift_test",
        "detectselectivecitation": "detect_selective_citation",
        "detect_selective_citation": "detect_selective_citation",
        "causalchainaudit": "causal_chain_audit",
        "causal_chain_audit": "causal_chain_audit",
        "runyanzhenmechanismverification": "run_yanzhen_mechanism_verification",
        "run_yanzhen_mechanism_verification": "run_yanzhen_mechanism_verification",
        "exportresearchplan": "export_research_plan",
        "export_research_plan": "export_research_plan",
    }
    key = raw.replace("-", "_").replace(" ", "_").lower()
    compact_key = key.replace("_", "")
    return aliases.get(key) or aliases.get(compact_key) or key


def create_response(
    client: Any,
    user_input: str,
    messages: list[dict[str, Any]],
    recovery_state: RecoveryState,
    tools: list[dict[str, Any]],
) -> Any:
    return create_response_with_recovery(
        client,
        system=build_system(user_input),
        messages=messages,
        tools=tools,
        state=recovery_state,
        focus=user_input,
    )


def run_agent(user_input: str) -> str:
    with agent_lock:
        start_cron_services(agent_callback=run_agent)
        return run_agent_locked(user_input)


def run_agent_locked(user_input: str) -> str:
    client = get_client()
    recovery_state = RecoveryState()
    log_event("USER", "prompt", chars=len(user_input))
    injected = trigger_hook("UserPromptSubmit", user_input)
    prompt = injected if injected is not None else user_input
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    tracked_task_ids: set[str] = set()
    validation_attempts = 0

    while True:
        current_tools, current_handlers = assemble_tool_pool(BUILTIN_TOOLS, BUILTIN_TOOL_HANDLERS)
        fired_crons = consume_cron_queue()
        if fired_crons:
            messages.append({"role": "user", "content": render_scheduled_prompt(fired_crons)})
        team_messages = consume_lead_inbox()
        if team_messages:
            messages.append(
                {
                    "role": "user",
                    "content": "<team_inbox>\n" + "\n\n".join(team_messages) + "\n</team_inbox>",
                }
            )
        notifications = collect_background_notifications()
        if notifications:
            messages.append({"role": "user", "content": "\n\n".join(notifications)})
        messages[:] = compact_messages(messages)
        response = create_response(client, user_input, messages, recovery_state, current_tools)

        tool_blocks = [
            block for block in response.content if block_attr(block, "type") == "tool_use"
        ]
        if not tool_blocks:
            final_text = response_text(response.content)
            validation_issue = validate_before_final(user_input, tracked_task_ids)
            if validation_issue:
                validation_attempts += 1
                if validation_attempts <= 6:
                    messages.append({"role": "assistant", "content": [block_to_dict(block) for block in response.content]})
                    messages.append({"role": "user", "content": validation_issue})
                    messages[:] = compact_messages(messages)
                    continue
                final_text = (
                    final_text.strip()
                    + "\n\n"
                    + "Validation is still failing after multiple attempts:\n"
                    + validation_issue
                ).strip()
            trigger_hook("Stop", final_text)
            extract_memories(messages, final_text)
            log_event("AGENT", "final", text=final_text)
            return final_text

        messages.append(
            {
                "role": "assistant",
                "content": [block_to_dict(block) for block in response.content],
            }
        )
        tool_results = [run_tool(block, messages, current_handlers) for block in tool_blocks]
        for block, result in zip(tool_blocks, tool_results):
            if normalize_tool_name(block_attr(block, "name")) == "create_task":
                tracked_task_ids.update(extract_task_ids(str(result.get("content", ""))))
        messages.append({"role": "user", "content": tool_results})
        messages[:] = compact_messages(messages)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the v8 autonomous multi-agent loop.")
    parser.add_argument(
        "--serve-cron",
        action="store_true",
        help="Keep the process alive after the prompt so durable cron jobs can fire.",
    )
    parser.add_argument("prompt", nargs="*", help="Task prompt. If omitted, read one line.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    user_input = " ".join(args.prompt).strip()
    if not user_input:
        user_input = input("User> ").strip()
    if not user_input:
        raise SystemExit("Empty prompt.")
    run_agent(user_input)
    if args.serve_cron:
        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
