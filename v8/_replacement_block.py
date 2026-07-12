def boxue_research_query(project: dict[str, Any], goal: str) -> str:
    try:
        from ._utils import normalize_space, trim_text
    except ImportError:
        from _utils import normalize_space, trim_text
    domain = normalize_space(str(project.get("domain", "")))
    objective = normalize_space(str(project.get("objective", "")))
    goal_text = normalize_space(goal)
    text = " ".join(part for part in [domain, objective, goal_text] if part)
    if not text:
        text = "AI for Science literature review knowledge gaps hypothesis generation"
    return trim_text(text, 500)


def summarize_json_output(output: str) -> dict[str, Any]:
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
    text = str(output or "")
    summary: dict[str, Any] = {"chars": len(text)}
    try:
        payload = json.loads(text)
    except Exception:
        summary["preview"] = trim_text(text, 1200)
        return summary
    if isinstance(payload, dict):
        summary["keys"] = sorted(str(key) for key in payload.keys())[:20]
        for key in (
            "agent",
            "search_id",
            "project_id",
            "total_results",
            "imported_count",
            "gap_count",
            "hypothesis_count",
        ):
            if key in payload:
                summary[key] = payload.get(key)
        if "knowledge_gaps" in payload and isinstance(payload.get("knowledge_gaps"), list):
            summary["knowledge_gaps"] = len(payload.get("knowledge_gaps", []))
        if "ranked_gaps" in payload and isinstance(payload.get("ranked_gaps"), list):
            summary["ranked_gaps"] = len(payload.get("ranked_gaps", []))
        if "hypotheses" in payload and isinstance(payload.get("hypotheses"), list):
            summary["hypotheses"] = len(payload.get("hypotheses", []))
        if "persisted_hypotheses" in payload and isinstance(payload.get("persisted_hypotheses"), list):
            summary["persisted_hypotheses"] = len(payload.get("persisted_hypotheses", []))
    elif isinstance(payload, list):
        summary["items"] = len(payload)
    return summary


def boxue_force_complete_task(task_id: str) -> str:
    try:
        from .task_system import complete_task
    except ImportError:
        from task_system import complete_task
    return complete_task(task_id)


def boxue_task_state(task_id: str) -> dict[str, Any]:
    try:
        from .task_system import load_task
    except ImportError:
        from task_system import load_task
    try:
        task = load_task(task_id)
    except Exception as exc:
        return {"task_id": task_id, "status": "missing", "error": str(exc)}
    return {"task_id": task.id, "status": task.status, "owner": task.owner, "blockedBy": list(task.blockedBy)}


def boxue_task_dependencies_completed_by_id(task_id: str) -> bool:
    try:
        from .task_system import incomplete_dependencies, load_task
    except ImportError:
        from task_system import incomplete_dependencies, load_task
    try:
        task = load_task(task_id)
    except Exception:
        return False
    return not incomplete_dependencies(task)


def boxue_task_snapshot(task_ids: list[str]) -> dict[str, Any]:
    try:
        from .task_system import load_task
    except ImportError:
        from task_system import load_task

    rows: list[dict[str, Any]] = []
    counts = Counter()
    for task_id in task_ids:
        try:
            task = load_task(task_id)
        except Exception as exc:
            rows.append({"task_id": task_id, "status": "missing", "error": str(exc)})
            counts["missing"] += 1
            continue
        counts[task.status] += 1
        rows.append(
            {
                "task_id": task.id,
                "subject": task.subject,
                "status": task.status,
                "owner": task.owner,
                "blockedBy": list(task.blockedBy),
                "worktree": task.worktree,
                "updatedAt": task.updatedAt,
            }
        )
    return {
        "total": len(task_ids),
        "counts": dict(counts),
        "tasks": rows,
    }


def boxue_round_is_finished(task_ids: list[str]) -> bool:
    snapshot = boxue_task_snapshot(task_ids)
    return snapshot.get("total", 0) > 0 and snapshot.get("counts", {}).get("completed", 0) == snapshot.get("total", 0)


def boxue_finalize_round(snapshot: dict[str, Any], revisions: list[dict[str, Any]]) -> dict[str, Any]:
    total = int(snapshot.get("total") or 0)
    counts = snapshot.get("counts", {}) if isinstance(snapshot.get("counts"), dict) else {}
    completed = int(counts.get("completed") or 0)
    if total and completed == total and not revisions:
        status = "finalized"
        decision = "All Boxue specialist tasks completed; round can proceed to final synthesis."
    elif revisions:
        status = "revision_required"
        decision = "One or more tasks produced failure/stall signals; revision tasks were created before finalization."
    else:
        status = "in_progress"
        decision = "Round dispatched available specialists and is waiting for downstream task completion."
    return {
        "status": status,
        "completed_tasks": completed,
        "total_tasks": total,
        "pending_tasks": int(counts.get("pending") or 0),
        "in_progress_tasks": int(counts.get("in_progress") or 0),
        "revision_tasks_created": len(revisions),
        "decision": decision,
    }


def boxue_find_active_plan(project: dict[str, Any], phases: list[str] | None = None) -> dict[str, Any] | None:
    try:
        from ._utils import normalize_key, normalize_space
    except ImportError:
        from _utils import normalize_key, normalize_space
    requested_phases = {normalize_key(phase) for phase in (phases or []) if normalize_space(phase)}
    for plan in reversed(list(project.get("boxue_delegation_plans", []))):
        tasks = list(plan.get("tasks", []))
        if not tasks:
            continue
        if requested_phases:
            plan_phases = {normalize_key(str(item.get("phase", ""))) for item in tasks}
            if not requested_phases.issubset(plan_phases):
                continue
        task_ids = [str(item.get("task_id")) for item in tasks if item.get("task_id")]
        snapshot = boxue_task_snapshot(task_ids)
        counts = snapshot.get("counts", {}) if isinstance(snapshot.get("counts"), dict) else {}
        if int(counts.get("completed") or 0) < int(snapshot.get("total") or 0):
            return plan
    return None


def boxue_load_or_create_plan(
    *,
    project: dict[str, Any],
    project_id: str,
    goal: str,
    phases: list[str] | None,
    plan_id: str,
    max_steps: int,
    max_parallel_agents: int,
) -> dict[str, Any]:
    requested_plan_id = str(plan_id or "").strip()
    if requested_plan_id:
        for plan in project.get("boxue_delegation_plans", []):
            if str(plan.get("boxue_delegation_plan_id")) == requested_plan_id:
                payload = dict(plan)
                payload["reused_existing_plan"] = True
                return payload
        raise ValueError(f"Boxue delegation plan not found: {requested_plan_id}")

    active = boxue_find_active_plan(project, phases=phases)
    if active:
        payload = dict(active)
        payload["reused_existing_plan"] = True
        return payload

    payload = json.loads(
        create_boxue_delegation_tasks(
            project_id=project_id,
            goal=goal,
            phases=phases,
            max_steps=max_steps,
            max_parallel_agents=max_parallel_agents,
        )
    )
    payload["reused_existing_plan"] = False
    return payload


def boxue_consume_inbox(limit: int = 20) -> list[str]:
    """Stubbed: the teammate/agent_teams system was removed, so inbox is always empty."""
    return []


def boxue_review_completed_tasks(
    *,
    plan_tasks: list[dict[str, Any]],
    already_reviewed: set[str],
) -> list[dict[str, Any]]:
    try:
        from .task_system import load_task
    except ImportError:
        from task_system import load_task

    reviews: list[dict[str, Any]] = []
    by_id = {str(item.get("task_id")): item for item in plan_tasks if item.get("task_id")}
    for task_id, spec in by_id.items():
        if task_id in already_reviewed:
            continue
        try:
            task = load_task(task_id)
        except Exception:
            continue
        if task.status != "completed":
            continue
        already_reviewed.add(task_id)
        reviews.append(
            {
                "task_id": task_id,
                "agent": spec.get("agent"),
                "phase": spec.get("phase"),
                "verdict": "accepted_by_completion_gate",
                "rationale": (
                    "The specialist called complete_task and passed the task system completion checks. "
                    "Deeper scientific review is delegated to downstream Reviewer/Boxue final tasks."
                ),
                "acceptance_criteria": spec.get("acceptance_criteria", []),
                "reviewedAt": time.time(),
            }
        )
        log_event("SCIENCE", "boxue_task_reviewed", task_id=task_id, verdict="accepted_by_completion_gate")
    return reviews


def boxue_create_revision_tasks_for_failures(
    *,
    plan_id: str,
    plan_tasks: list[dict[str, Any]],
    inbox_events: list[str],
    already_revised: set[str],
    revision_after_seconds: int,
) -> list[dict[str, Any]]:
    try:
        from ._utils import extract_task_id
    except ImportError:
        from _utils import extract_task_id
    try:
        from .task_system import create_task, load_task
    except ImportError:
        from task_system import create_task, load_task

    revisions: list[dict[str, Any]] = []
    by_id = {str(item.get("task_id")): item for item in plan_tasks if item.get("task_id")}
    failure_text = "\n".join(inbox_events[-20:]).lower()
    now = time.time()
    for task_id, spec in by_id.items():
        if task_id in already_revised:
            continue
        try:
            task = load_task(task_id)
        except Exception:
            continue
        if task.status == "completed":
            continue
        explicit_failure = task_id.lower() in failure_text and any(
            marker in failure_text for marker in ("error", "failed", "blocked", "cannot", "unable")
        )
        stalled = task.status == "in_progress" and now - float(getattr(task, "updatedAt", now)) >= revision_after_seconds
        if not explicit_failure and not stalled:
            continue
        reason = "explicit_failure_signal" if explicit_failure else "stalled_in_progress"
        description = (
            f"Boxue revision task for plan {plan_id}.\n"
            f"Original task: {task_id}\n"
            f"Original subject: {task.subject}\n"
            f"Assigned agent: {spec.get('agent')}\n"
            f"Failure reason: {reason}\n\n"
            "Review the original task, preserve any useful partial output, repair the failure, "
            "and produce a compact JSON revision deliverable. Do not invent evidence."
        )
        rendered = create_task(
            subject=f"Boxue revision/{spec.get('agent')}: {spec.get('title')}",
            description=description,
            blockedBy=list(getattr(task, "blockedBy", [])),
        )
        revision_id = extract_task_id(rendered)
        already_revised.add(task_id)
        revisions.append(
            {
                "original_task_id": task_id,
                "revision_task_id": revision_id,
                "agent": spec.get("agent"),
                "reason": reason,
            }
        )
        log_event("SCIENCE", "boxue_revision_task_created", original=task_id, revision=revision_id, reason=reason)
    return revisions


def boxue_completed_agents_from_autogen_run(autogen_run: dict[str, Any]) -> set[str]:
    try:
        from ._utils import normalize_key
    except ImportError:
        from _utils import normalize_key
    completed: set[str] = set()
    for turn in autogen_run.get("turns", []):
        if not isinstance(turn, dict):
            continue
        status = normalize_key(str(turn.get("status") or ""))
        if status in {"error", "failed", "fail"}:
            continue
        name = normalize_key(str(turn.get("round") or ""))
        speaker = normalize_key(str(turn.get("speaker") or ""))
        if "literature" in name or "zhizhi" in speaker:
            completed.add("zhizhi")
        elif "gap" in name or "tanxi" in speaker:
            completed.add("tanxi")
        elif "mingli" in speaker or "hypothesis" in name or "methodology" in name:
            completed.add("mingli")
        elif "yanzhen" in speaker or "cawm" in name:
            completed.add("yanzhen")
        elif "bianlun" in speaker or "synthesis" in name:
            completed.update({"duzhi", "bianlun"})
        elif "duzhi" in speaker or "interrogation" in name:
            completed.add("duzhi")
    state = autogen_run.get("state", {}) if isinstance(autogen_run.get("state"), dict) else {}
    decision = normalize_key(str(state.get("final_decision") or ""))
    if decision and decision != "error":
        completed.add("boxue")
    return completed


def boxue_mark_autogen_tasks_completed(
    *,
    plan_tasks: list[dict[str, Any]],
    autogen_run: dict[str, Any],
) -> list[dict[str, Any]]:
    """Best-effort audit sync from AutoGen turns back to legacy Boxue DAG tasks."""
    try:
        from ._utils import normalize_key, trim_text
    except ImportError:
        from _utils import normalize_key, trim_text
    completed_agents = boxue_completed_agents_from_autogen_run(autogen_run)
    updates: list[dict[str, Any]] = []
    if not completed_agents:
        return updates
    progressed = True
    while progressed:
        progressed = False
        for item in plan_tasks:
            task_id = str(item.get("task_id") or "")
            agent = normalize_key(str(item.get("agent") or ""))
            if not task_id or agent not in completed_agents:
                continue
            state = boxue_task_state(task_id)
            if state.get("status") == "completed":
                continue
            if not boxue_task_dependencies_completed_by_id(task_id):
                continue
            try:
                completion = boxue_force_complete_task(task_id)
                updates.append(
                    {
                        "task_id": task_id,
                        "agent": agent,
                        "status": "completed",
                        "completion": trim_text(completion, 500),
                    }
                )
                progressed = True
            except Exception as exc:
                updates.append({"task_id": task_id, "agent": agent, "status": "sync_failed", "error": str(exc)})
    return updates


def boxue_finalize_autogen_round(
    pipeline_executions: list[dict[str, Any]],
    snapshot: dict[str, Any],
    revisions: list[dict[str, Any]],
) -> dict[str, Any]:
    execution = pipeline_executions[0] if pipeline_executions else {}
    state = execution.get("state", {}) if isinstance(execution.get("state"), dict) else {}
    status = str(execution.get("status") or "")
    flow_decision = str(state.get("final_decision") or "")
    if status == "failed" or flow_decision == "error":
        final_status = "error"
        decision = "AutoGen GroupChat pipeline failed; inspect the AutoGen run record and failed turn before retrying."
    elif revisions:
        final_status = "revision_required"
        decision = "AutoGen GroupChat pipeline ran, but legacy Boxue audit tasks produced revision signals."
    elif flow_decision in {"revision_required", "revise", "human_review"}:
        final_status = "revision_required"
        decision = "AutoGen GroupChat completed the research loop and requested hypothesis revision or human review."
    else:
        final_status = "autogen_groupchat_completed"
        decision = "AutoGen GroupChat completed the Boxue research loop without CrewAI, worktrees, or background teammates."
    counts = snapshot.get("counts", {}) if isinstance(snapshot.get("counts"), dict) else {}
    return {
        "status": final_status,
        "autogen_decision": flow_decision,
        "autogen_run_id": execution.get("run_id"),
        "groupchat_id": execution.get("groupchat_id"),
        "completed_tasks": int(counts.get("completed") or 0),
        "total_tasks": int(snapshot.get("total") or 0),
        "pending_tasks": int(counts.get("pending") or 0),
        "in_progress_tasks": int(counts.get("in_progress") or 0),
        "revision_tasks_created": len(revisions),
        "decision": decision,
    }


def boxue_run_autogen_groupchat_pipeline(
    *,
    project_id: str,
    plan_id: str,
    plan_tasks: list[dict[str, Any]],
    goal: str,
) -> list[dict[str, Any]]:
    """Run the AutoGen GroupChat pipeline behind the legacy Boxue pipeline mode."""
    try:
        from ._project import load_project
    except ImportError:
        from _project import load_project
    started = time.time()
    try:
        try:
            from .autogen_collab import run_autogen_research_flow
        except ImportError:
            from autogen_collab import run_autogen_research_flow

        output = run_autogen_research_flow(
            project_id=project_id,
            goal=boxue_research_query(load_project(project_id), goal),
            max_results=20,
            import_top_k=15,
            use_llm=True,
            live_search=False,
            run_debate=True,
            max_round=12,
            speaker_selection_method="round_robin",
            human_input_mode="TERMINATE",
            use_native_autogen=False,
        )
        payload = json.loads(output)
        completed_task_updates = boxue_mark_autogen_tasks_completed(plan_tasks=plan_tasks, autogen_run=payload)
        log_event(
            "SCIENCE",
            "boxue_autogen_groupchat_done",
            project_id=project_id,
            plan_id=plan_id,
            run_id=payload.get("run_id"),
            decision=(payload.get("state") or {}).get("final_decision"),
        )
        return [
            {
                "runner": "autogen_groupchat",
                "plan_id": plan_id,
                "status": "completed" if str((payload.get("state") or {}).get("final_decision")) != "error" else "failed",
                "elapsed_ms": int((time.time() - started) * 1000),
                "groupchat_id": payload.get("groupchat_id"),
                "run_id": payload.get("run_id"),
                "state": payload.get("state", {}),
                "turns": [
                    {
                        "round": turn.get("round"),
                        "speaker": turn.get("speaker"),
                        "status": turn.get("status"),
                        "error": turn.get("error"),
                    }
                    for turn in payload.get("turns", [])
                    if isinstance(turn, dict)
                ],
                "completed_task_updates": completed_task_updates,
                "output_summary": summarize_json_output(output),
            }
        ]
    except Exception as exc:
        log_event("WARN", "boxue_autogen_groupchat_failed", project_id=project_id, plan_id=plan_id, error=exc)
        return [
            {
                "runner": "autogen_groupchat",
                "plan_id": plan_id,
                "status": "failed",
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": str(exc),
            }
        ]


def boxue_round_next_step(final_decision: dict[str, Any]) -> str:
    status = str(final_decision.get("status") or "")
    if status == "finalized":
        return "Use Boxue final synthesis output to decide whether to start a new research iteration."
    if status == "autogen_groupchat_completed":
        return "Inspect the AutoGen GroupChat run record, then continue to experiment planning or start a focused revision round."
    if status == "error":
        return "Inspect the AutoGen failed turn or Boxue round record before retrying."
    if status == "revision_required":
        return "Run run_boxue_research_round again with the same plan_id after revision tasks complete, or inspect the created revision tasks."
    return "Let spawned teammates continue, then run run_boxue_research_round again with the same plan_id to monitor and dispatch newly unblocked specialists."


def run_boxue_research_round(
    project_id: str,
    goal: str = "",
    phases: list[str] | None = None,
    plan_id: str = "",
    execution_mode: str = "async",
    max_steps: int = 20,
    max_parallel_agents: int = 3,
    max_runtime_seconds: int = 45,
    poll_interval_seconds: float = 2.0,
    revision_after_seconds: int = 600,
) -> str:
    """Run one bounded Boxue scheduling round.

    This is the coordinator loop on top of the existing task DAG.
    It creates a Boxue plan, starts currently unblocked specialists, watches
    the task board/inbox for a bounded time window, starts newly unblocked
    downstream specialists, records lightweight reviews for completed
    deliverables, and creates revision tasks for clearly stalled or failed items.
    """
    try:
        from ._project import load_project, save_project
        from ._utils import clamp_int, new_id, normalize_key
    except ImportError:
        from _project import load_project, save_project
        from _utils import clamp_int, new_id, normalize_key

    project = load_project(project_id)
    runtime_limit = clamp_int(max_runtime_seconds, 0, 900)
    poll_interval = max(0.5, min(float(poll_interval_seconds or 2.0), 30.0))
    parallel_limit = clamp_int(max_parallel_agents, 1, 12)
    revision_timeout = clamp_int(revision_after_seconds, 30, 86_400)

    plan_payload = boxue_load_or_create_plan(
        project=project,
        project_id=project_id,
        goal=goal,
        phases=phases,
        plan_id=plan_id,
        max_steps=max_steps,
        max_parallel_agents=parallel_limit,
    )
    plan_id = str(plan_payload.get("boxue_delegation_plan_id", ""))
    reused_plan = bool(plan_payload.get("reused_existing_plan"))
    plan_tasks = list(plan_payload.get("tasks", []))
    task_ids = [str(item.get("task_id")) for item in plan_tasks if item.get("task_id")]

    round_id = new_id("boxue_round")
    started_at = time.time()
    spawned: list[dict[str, Any]] = []
    pipeline_executions: list[dict[str, Any]] = []
    inbox_events: list[str] = []
    reviews: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    seen_reviewed: set[str] = set()
    seen_revisions: set[str] = set()

    log_event(
        "SCIENCE",
        "boxue_round_start",
        project_id=project_id,
        round_id=round_id,
        plan_id=plan_id,
        tasks=len(task_ids),
    )

    mode = normalize_key(execution_mode or "async")
    autogen_mode = mode in {"pipeline", "sync", "synchronous", "closed_loop", "closedloop", "autogen", "groupchat", "group_chat", "crew", "crew_flow", "flow"}
    if autogen_mode:
        pipeline_executions = boxue_run_autogen_groupchat_pipeline(
            project_id=project_id,
            plan_id=plan_id,
            plan_tasks=plan_tasks,
            goal=goal or str(project.get("objective", "")),
        )
        runtime_limit = 0

    def remaining_time() -> float:
        return runtime_limit - (time.time() - started_at)

    while True:
        inbox_events.extend(boxue_consume_inbox(limit=12))

        reviews.extend(
            boxue_review_completed_tasks(
                plan_tasks=plan_tasks,
                already_reviewed=seen_reviewed,
            )
        )
        revisions.extend(
            boxue_create_revision_tasks_for_failures(
                plan_id=plan_id,
                plan_tasks=plan_tasks,
                inbox_events=inbox_events,
                already_revised=seen_revisions,
                revision_after_seconds=revision_timeout,
            )
        )

        if boxue_round_is_finished(task_ids):
            break
        if runtime_limit <= 0 or remaining_time() <= 0:
            break
        time.sleep(min(poll_interval, max(0.5, remaining_time())))

    snapshot = boxue_task_snapshot(task_ids)
    if autogen_mode:
        final_decision = boxue_finalize_autogen_round(pipeline_executions, snapshot, revisions)
    else:
        final_decision = boxue_finalize_round(snapshot, revisions)
    project = load_project(project_id)
    round_record = {
        "round_id": round_id,
        "plan_id": plan_id,
        "reused_existing_plan": reused_plan,
        "goal": goal or project.get("objective", ""),
        "createdAt": started_at,
        "completedAt": time.time(),
        "runtime_seconds": round(time.time() - started_at, 3),
        "execution_mode": mode,
        "spawned_teammates": spawned,
        "pipeline_executions": pipeline_executions,
        "inbox_events": inbox_events[-30:],
        "reviews": reviews,
        "revisions": revisions,
        "task_snapshot": snapshot,
        "final_decision": final_decision,
        "next_step": boxue_round_next_step(final_decision),
    }
    project.setdefault("boxue_research_rounds", []).append(round_record)
    project["updatedAt"] = time.time()
    save_project(project)
    log_event(
        "SCIENCE",
        "boxue_round_end",
        project_id=project_id,
        round_id=round_id,
        decision=final_decision.get("status"),
        spawned=len(spawned),
        revisions=len(revisions),
    )
    return json.dumps(round_record, ensure_ascii=False, indent=2)
