from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
import ast
import json
import re
import time

try:
    from .config import (
        SCIENCE_DIR,
        SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K,
        SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K,
    )
    from .log import log_event
except ImportError:
    from config import (
        SCIENCE_DIR,
        SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K,
        SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K,
    )
    from log import log_event



def create_science_pipeline_tasks(project_id: str) -> str:
    try:
        from ._models import PHASES
        from ._project import load_project, save_project
        from ._utils import extract_task_id
    except ImportError:
        from _models import PHASES
        from _project import load_project, save_project
        from _utils import extract_task_id
    project = load_project(project_id)
    try:
        from .task_system import create_task
    except ImportError:
        from task_system import create_task

    task_ids: list[str] = []
    previous: list[str] = []
    for index, phase in enumerate(PHASES):
        agents = agents_for_phase(phase)
        description = (
            f"Science project: {project['title']}\n"
            f"Domain: {project['domain']}\n"
            f"Objective: {project['objective']}\n"
            f"Phase: {phase}\n"
            f"Responsible science agents: {', '.join(agents)}\n"
            "Deliverable must be structured JSON and include evidence, acceptance criteria, and risks."
        )
        rendered = create_task(
            subject=f"Science phase {index + 1}: {phase}",
            description=description,
            blockedBy=previous,
        )
        task_id = extract_task_id(rendered)
        if task_id:
            task_ids.append(task_id)
            previous = [task_id]
    project["pipeline_tasks"] = task_ids
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "pipeline_tasks_created", project_id=project_id, count=len(task_ids))
    return json.dumps({"project_id": project_id, "task_ids": task_ids}, ensure_ascii=False, indent=2)

def create_boxue_delegation_tasks(
    project_id: str,
    goal: str = "",
    phases: list[str] | None = None,
    max_steps: int = 20,
    max_parallel_agents: int = 3,
) -> str:
    try:
        from ._project import load_project, save_project
        from ._utils import clamp_int, extract_task_id, new_id, normalize_key, normalize_space, unique_preserve_order
    except ImportError:
        from _project import load_project, save_project
        from _utils import clamp_int, extract_task_id, new_id, normalize_key, normalize_space, unique_preserve_order
    """Create Boxue-style role-bound delegation tasks.

    This follows the Boxue prompt contract: Boxue decomposes, assigns,
    establishes acceptance criteria, and creates synthesis/review gates rather
    than doing specialist science work itself.
    """
    project = load_project(project_id)
    try:
        from .task_system import create_task
    except ImportError:
        from task_system import create_task

    selected_phases = {normalize_key(phase) for phase in (phases or []) if normalize_space(phase)}
    max_items = clamp_int(max_steps, 1, 25)
    plan_id = new_id("boxue")
    task_specs = [
        spec
        for spec in boxue_default_task_specs()
        if not selected_phases or normalize_key(spec["phase"]) in selected_phases
    ][:max_items]
    if not task_specs:
        raise ValueError("No Boxue task specs selected; check phases or max_steps.")

    task_ids_by_key: dict[str, str] = {}
    created_tasks: list[dict[str, Any]] = []
    for spec in task_specs:
        blocked_by = [task_ids_by_key[key] for key in spec.get("blocked_by", []) if key in task_ids_by_key]
        description = boxue_delegation_task_description(project, spec, goal=goal, plan_id=plan_id)
        rendered = create_task(
            subject=f"Boxue/{spec['agent']}: {spec['title']}",
            description=description,
            blockedBy=blocked_by,
        )
        task_id = extract_task_id(rendered)
        if not task_id:
            continue
        task_ids_by_key[spec["key"]] = task_id
        created_tasks.append(
            {
                "task_id": task_id,
                "agent": spec["agent"],
                "phase": spec["phase"],
                "title": spec["title"],
                "blockedBy": blocked_by,
                "priority": spec.get("priority", "medium"),
                "acceptance_criteria": spec.get("acceptance", []),
            }
        )

    spawned: list[dict[str, str]] = []

    plan = {
        "boxue_delegation_plan_id": plan_id,
        "project_id": project_id,
        "goal": goal or project.get("objective", ""),
        "createdAt": time.time(),
        "prompt_alignment": boxue_prompt_alignment_summary(),
        "coordination_policy": {
            "boxue_role": "decompose, assign, review, synthesize, adjust, finalize",
            "specialist_role": "execute only the assigned scientific subtask",
            "shared_state": "state-changing PaperGraph/project updates should be gated by lead/synthesis tasks",
            "step_limit": max_items,
        },
        "tasks": created_tasks,
        "spawned_teammates": spawned,
        "next_step": (
            "Let unblocked specialist tasks run first. Boxue should review outputs at dependency gates, "
            "then adjust or unlock downstream phases."
        ),
    }
    project.setdefault("boxue_delegation_plans", []).append(plan)
    project.setdefault("pipeline_tasks", [])
    project["pipeline_tasks"] = unique_preserve_order(
        list(project.get("pipeline_tasks", [])) + [task["task_id"] for task in created_tasks if task.get("task_id")]
    )
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "boxue_delegation_tasks_created", project_id=project_id, plan_id=plan_id, tasks=len(created_tasks))
    return json.dumps(plan, ensure_ascii=False, indent=2)

def boxue_default_task_specs() -> list[dict[str, Any]]:
    try:
        from ._models import Hypothesis
    except ImportError:
        from _models import Hypothesis
    return [
        {
            "key": "zhizhi_evidence",
            "agent": "zhizhi",
            "phase": "Gap Discovery",
            "title": "Build grounded PaperGraph evidence substrate",
            "priority": "high",
            "blocked_by": [],
            "task": "Retrieve and structure representative literature without inventing sources.",
            "deliverable": "Compact JSON evidence report plus search/import recommendations.",
            "acceptance": [
                "At least one verifiable source or an explicit retrieval-failure report",
                "Method/scenario/benchmark/contribution/limitation fields are populated or flagged unknown",
                "Unsupported claims are marked for human review",
            ],
            "risks": ["retrieval failure", "low-quality venues", "unsupported method labels"],
        },
        {
            "key": "tanxi_gaps",
            "agent": "tanxi",
            "phase": "Gap Discovery",
            "title": "Rank high-value knowledge gaps",
            "priority": "high",
            "blocked_by": ["zhizhi_evidence"],
            "task": "Use PaperGraph evidence to detect density holes, suspended problems, contradictions, and migration gaps.",
            "deliverable": "Ranked gap list with supporting references and pseudo-gap risk checks.",
            "acceptance": [
                "Every reported gap has supporting references or is flagged as ungrounded",
                "No more than 10 ranked gaps",
                "Each gap includes novelty/value/feasibility rationale",
            ],
            "risks": ["pseudo-gaps", "uncovered subfields mistaken as real gaps"],
        },
        {
            "key": "mingli_hypotheses",
            "agent": "mingli",
            "phase": "Hypothesis Generation",
            "title": "Generate and evolve gap-grounded hypotheses",
            "priority": "high",
            "blocked_by": ["tanxi_gaps"],
            "task": "Generate falsifiable hypotheses from validated gaps and run tournament-style selection.",
            "deliverable": "Top hypotheses with mechanisms, expected value, lineage, and test plans.",
            "acceptance": [
                "Each hypothesis links to a gap_id",
                "Each hypothesis contains mechanism and falsification condition",
                "Overlap/novelty risk is reported",
            ],
            "risks": ["creative rephrasing without structural novelty", "weak mechanism grounding"],
        },
        {
            "key": "duzhi_critique",
            "agent": "duzhi",
            "phase": "Socratic Debate",
            "title": "Run Socratic critique",
            "priority": "medium",
            "blocked_by": ["mingli_hypotheses"],
            "task": "Challenge hypotheses through assumptions, causal links, counterexamples, alternatives, and falsification standards.",
            "deliverable": "Socratic critique JSON for each finalist hypothesis.",
            "acceptance": [
                "At least one counterexample per hypothesis",
                "At least one alternative explanation per hypothesis",
                "Actionable revision or rejection recommendation",
            ],
            "risks": ["vague critique", "ungrounded objections"],
        },
        {
            "key": "bianlun_synthesis",
            "agent": "bianlun",
            "phase": "Socratic Debate",
            "title": "Moderate structured debate and synthesize refined hypothesis",
            "priority": "medium",
            "blocked_by": ["duzhi_critique"],
            "task": "Integrate proposer and critic positions, identify disagreements, and produce refined hypotheses.",
            "deliverable": "Debate record with convergence points, unresolved issues, and emergent methods.",
            "acceptance": [
                "Arguments are separated into factual vs conceptual disagreements",
                "Refined hypothesis lists improvements from critique",
                "Remaining risks are explicit",
            ],
            "risks": ["false consensus", "debate loops without synthesis"],
        },
        {
            "key": "yanzhen_mechanism",
            "agent": "yanzhen",
            "phase": "Mechanism Verification",
            "title": "Audit mechanism fidelity",
            "priority": "high",
            "blocked_by": ["bianlun_synthesis"],
            "task": "Run internal consistency, data consistency, and regime-shift checks for refined hypotheses.",
            "deliverable": "Mechanism fidelity report with CAWM risk level.",
            "acceptance": [
                "All three verification layers are addressed",
                "Regime-shift conditions are explicit",
                "High-risk mechanisms are routed to revision or human review",
            ],
            "risks": ["correct answer wrong mechanism", "selective citation"],
        },
        {
            "key": "gewu_experiment",
            "agent": "gewu",
            "phase": "Experimental Design",
            "title": "Design falsifiable validation protocol",
            "priority": "high",
            "blocked_by": ["yanzhen_mechanism"],
            "task": "Translate verified hypotheses into executable experiments with baselines and metrics.",
            "deliverable": "Experiment protocol with datasets, baselines, metrics, controls, and falsification criteria.",
            "acceptance": [
                "At least one standard and one strong baseline",
                "Metrics have success thresholds",
                "Falsification criteria are stated before execution",
            ],
            "risks": ["insufficient baseline", "unreproducible protocol"],
        },
        {
            "key": "codeengineer_impl",
            "agent": "codeengineer",
            "phase": "Implementation",
            "title": "Implement reproducible experiment",
            "priority": "medium",
            "blocked_by": ["gewu_experiment"],
            "task": "Implement the experiment or a minimal reproducible benchmark according to GeWu protocol.",
            "deliverable": "Runnable code, dependency notes, execution log, and results artifact.",
            "acceptance": [
                "Code runs or failure is diagnosed with logs",
                "Random seeds and dependencies are documented",
                "Outputs are saved in reproducible artifacts",
            ],
            "risks": ["execution failure", "hidden dependency drift"],
        },
        {
            "key": "mingbian_analysis",
            "agent": "mingbian",
            "phase": "Review & Iteration",
            "title": "Analyze experiment outcomes",
            "priority": "medium",
            "blocked_by": ["codeengineer_impl"],
            "task": "Analyze results, compare baselines, and recommend iteration or claim revisions.",
            "deliverable": "Analysis report with effect sizes, uncertainty, verdict, and iteration plan.",
            "acceptance": [
                "Distinguishes supported/refuted/inconclusive",
                "Reports effect size or practical significance",
                "Failed experiments are documented as negative knowledge",
            ],
            "risks": ["overclaiming", "ignoring inconclusive results"],
        },
        {
            "key": "paperwriter_draft",
            "agent": "paperwriter",
            "phase": "Manuscript Writing",
            "title": "Draft evidence-grounded research plan/manuscript",
            "priority": "medium",
            "blocked_by": ["mingbian_analysis"],
            "task": "Transform validated claims, experiments, and limitations into a publication-style draft.",
            "deliverable": "Structured manuscript or research-plan draft with verified references.",
            "acceptance": [
                "Claims are backed by results or citations",
                "Limitations and failed paths are included",
                "References are traceable to PaperGraph or retrieval artifacts",
            ],
            "risks": ["citation hallucination", "claims exceeding evidence"],
        },
        {
            "key": "reviewer_gate",
            "agent": "reviewer",
            "phase": "Review & Iteration",
            "title": "Run automated peer review gate",
            "priority": "medium",
            "blocked_by": ["paperwriter_draft"],
            "task": "Review draft for originality, quality, clarity, significance, ethics, and reproducibility.",
            "deliverable": "Peer-review report with scores, weaknesses, questions, and decision.",
            "acceptance": [
                "Scores include specific justifications",
                "Citation and claim/result alignment are checked",
                "Revision actions are concrete",
            ],
            "risks": ["rubber-stamp review", "missed reproducibility flaws"],
        },
        {
            "key": "boxue_final",
            "agent": "boxue",
            "phase": "Review & Iteration",
            "title": "Synthesize final decision and next round",
            "priority": "high",
            "blocked_by": ["reviewer_gate"],
            "task": "Aggregate specialist outputs and decide finalize vs revise.",
            "deliverable": "Boxue final decision JSON with completed tasks, unresolved risks, and next iteration plan.",
            "acceptance": [
                "Decision references specialist outputs",
                "Knowledge-gap lifecycle status is updated",
                "Next actions are either finalize or explicit revision tasks",
            ],
            "risks": ["coordinator makes unsupported specialist judgments"],
        },
    ]

def boxue_delegation_task_description(
    project: dict[str, Any],
    spec: dict[str, Any],
    *,
    goal: str,
    plan_id: str,
) -> str:
    try:
        from ._models import SCIENCE_AGENTS
    except ImportError:
        from _models import SCIENCE_AGENTS
    acceptance = "\n".join(f"- {item}" for item in spec.get("acceptance", []))
    risks = "\n".join(f"- {item}" for item in spec.get("risks", []))
    tools = ", ".join(SCIENCE_AGENTS.get(str(spec.get("agent", "")), {}).get("tools", []))
    return (
        f"Boxue delegation plan: {plan_id}\n"
        f"Project: {project.get('title', '')} ({project.get('project_id', '')})\n"
        f"Domain: {project.get('domain', '')}\n"
        f"Project objective: {project.get('objective', '')}\n"
        f"Round goal: {goal or project.get('objective', '')}\n"
        f"Assigned agent: {spec.get('agent')}\n"
        f"Phase: {spec.get('phase')}\n"
        f"Priority: {spec.get('priority', 'medium')}\n"
        f"Allowed/expected tools: {tools or 'role-specific reasoning and available project tools'}\n\n"
        f"Task:\n{spec.get('task')}\n\n"
        f"Deliverable:\n{spec.get('deliverable')}\n\n"
        f"Acceptance criteria:\n{acceptance}\n\n"
        f"Known risks to handle:\n{risks}\n\n"
        "Role boundary: complete only this specialist responsibility. Do not take over Boxue coordination, "
        "do not invent evidence, and mark unsupported claims for review.\n"
        "Output should be compact structured JSON suitable for downstream agents.\n"
    )

def boxue_prompt_alignment_summary() -> dict[str, Any]:
    return {
        "comparison": {
            "prompt_assign_task": "implemented as persistent create_task DAG entries with assigned agent, phase, dependency, deliverable, priority, and acceptance criteria",
            "prompt_review_output": "implemented as reviewer_gate plus boxue_final dependency gates; Boxue can add revision tasks via the task system",
            "prompt_synthesize": "implemented as bianlun_synthesis, paperwriter_draft, and boxue_final synthesis tasks",
            "prompt_adjust_plan": "implemented operationally by creating additional tasks or new delegation plans after reviewing outputs",
            "prompt_finalize": "implemented as boxue_final decision task, not as specialist execution",
        },
        "stronger_than_old_pipeline_tasks": [
            "role-specific tasks instead of generic phase tasks",
            "explicit acceptance criteria",
            "risk constraints embedded in every task",
            "dependencies mirror the research lifecycle",
            "optional teammate spawning for unblocked specialist work",
        ],
        "remaining_manual_gate": "Boxue still needs to review outputs and decide whether to create revision tasks; this preserves human/lead control over shared project state.",
    }

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

def create_science_delegation_tasks(
    project_id: str,
    objective: str = "",
    subspace_map_id: str = "",
    selected_subfields: list[str] | None = None,
    focus_branches: list[str] | None = None,
    max_branch_tasks: int = 6,
) -> str:
    """Create a subagent-friendly DAG for long science workflows."""
    project = load_project(project_id)
    try:
        from .task_system import create_task
    except ImportError:
        from task_system import create_task
    plan_id = new_id("sdeleg")
    artifact_dir = SCIENCE_DIR / "delegation" / plan_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    branches = science_delegation_branch_plan(
        project,
        subspace_map_id=subspace_map_id,
        selected_subfields=selected_subfields,
        focus_branches=focus_branches,
        max_branch_tasks=max_branch_tasks,
    )
    if not branches:
        raise ValueError("No delegation branches could be built; provide focus_branches or a subspace_map_id.")
    providers = default_literature_providers(domain=str(project.get("domain", "")), query=str(project.get("objective", "")))
    branch_task_ids: list[str] = []
    branch_tasks: list[dict[str, Any]] = []
    for index, branch in enumerate(branches, 1):
        artifact_path = science_delegation_artifact_relpath(plan_id, index, str(branch.get("branch") or branch.get("name") or "branch"))
        description = science_branch_scout_description(project, objective=objective, branch=branch, artifact_path=artifact_path, providers=providers)
        rendered = create_task(subject=f"Science scout {index}: {branch.get('name') or branch.get('branch')}", description=description, blockedBy=[])
        task_id = extract_task_id(rendered)
        if task_id:
            branch_task_ids.append(task_id)
            branch_tasks.append({"task_id": task_id, "branch": branch.get("branch"), "name": branch.get("name"), "query": branch.get("query"), "artifact_path": artifact_path})
    synthesis_description = science_synthesis_gate_description(project, objective=objective, plan_id=plan_id, branch_tasks=branch_tasks)
    synthesis_rendered = create_task(subject=f"Science synthesis gate: {project.get('title', project_id)}", description=synthesis_description, blockedBy=branch_task_ids)
    synthesis_task_id = extract_task_id(synthesis_rendered)
    tanxi_rendered = create_task(
        subject=f"TanXi gap ranking after delegation: {project.get('title', project_id)}",
        description=(f"Science delegation plan: {plan_id}\nProject: {project.get('title', '')} ({project_id})\nDomain: {project.get('domain', '')}\nWait until the synthesis gate confirms lead-side PaperGraph imports are complete. Then run build_knowledge_map, run_tanxi_gap_exploration, and produce a compact ranked-gap report."),
        blockedBy=[synthesis_task_id] if synthesis_task_id else branch_task_ids,
    )
    tanxi_task_id = extract_task_id(tanxi_rendered)
    mingli_rendered = create_task(
        subject=f"MingLi hypothesis evolution after delegation: {project.get('title', project_id)}",
        description=(f"Science delegation plan: {plan_id}\nProject: {project.get('title', '')} ({project_id})\nAfter TanXi completes, run run_mingli_hypothesis_evolution on the validated top gaps."),
        blockedBy=[tanxi_task_id] if tanxi_task_id else [],
    )
    mingli_task_id = extract_task_id(mingli_rendered)
    plan = {
        "delegation_plan_id": plan_id, "project_id": project_id, "objective": objective, "createdAt": time.time(),
        "policy": {"parallel_work": "branch scouts retrieve and judge evidence independently", "shared_state": "lead/synthesis gate performs PaperGraph imports serially after reviewing artifacts"},
        "artifact_dir": str(artifact_dir), "providers": providers, "branch_tasks": branch_tasks,
        "synthesis_task_id": synthesis_task_id, "tanxi_task_id": tanxi_task_id, "mingli_task_id": mingli_task_id,
        "next_step": "Let scouts complete branch artifacts, then have the synthesis gate choose import candidates.",
    }
    project.setdefault("delegation_plans", []).append(plan)
    project.setdefault("pipeline_tasks", [])
    project["pipeline_tasks"] = unique_preserve_order(
        list(project.get("pipeline_tasks", [])) + branch_task_ids + [tid for tid in (synthesis_task_id, tanxi_task_id, mingli_task_id) if tid]
    )
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "delegation_tasks_created", project_id=project_id, plan_id=plan_id, branches=len(branch_tasks))
    return json.dumps(plan, ensure_ascii=False, indent=2)

def science_delegation_branch_plan(
    project: dict[str, Any],
    *,
    subspace_map_id: str = "",
    selected_subfields: list[str] | None = None,
    focus_branches: list[str] | None = None,
    max_branch_tasks: int = 6,
) -> list[dict[str, Any]]:
    try:
        from ._literature_scoring import slug_label
        from ._project import load_subspace_map, query_plan_from_subspace_map
        from ._utils import clamp_int, normalize_space, string_list
    except ImportError:
        from _literature_scoring import slug_label
        from _project import load_subspace_map, query_plan_from_subspace_map
        from _utils import clamp_int, normalize_space, string_list
    limit = clamp_int(max_branch_tasks, 1, 20)
    if subspace_map_id:
        subspace_map = load_subspace_map(subspace_map_id)
        return query_plan_from_subspace_map(subspace_map, selected_subfields=selected_subfields or focus_branches)[:limit]
    branches: list[dict[str, Any]] = []
    for raw in focus_branches or []:
        label = normalize_space(str(raw))
        if not label:
            continue
        branches.append(
            {
                "branch": slug_label(label),
                "name": label,
                "query": label,
                "quota": 2,
                "estimated_density": "unknown",
                "strategic_importance": 7,
                "search_strategy": "user_focus_branch",
                "custom": True,
            }
        )
    if branches:
        return branches[:limit]
    knowledge_map = project.get("knowledge_map") if isinstance(project.get("knowledge_map"), dict) else {}
    scenarios = string_list(knowledge_map.get("main_scenarios"))[:limit]
    if scenarios:
        return [
            {
                "branch": slug_label(scenario),
                "name": scenario,
                "query": normalize_space(f"{project.get('domain', '')} {scenario}"),
                "quota": 2,
                "estimated_density": "project_known",
                "strategic_importance": 6,
                "search_strategy": "project_scenario",
            }
            for scenario in scenarios
            if scenario
        ][:limit]
    domain = normalize_space(str(project.get("domain") or project.get("title") or "science project"))
    objective = normalize_space(str(project.get("objective") or "knowledge gap discovery"))
    return [
        {
            "branch": slug_label(domain),
            "name": domain,
            "query": normalize_space(f"{domain} {objective}"),
            "quota": 3,
            "estimated_density": "unknown",
            "strategic_importance": 7,
            "search_strategy": "fallback_domain",
        }
    ]

def science_delegation_artifact_relpath(plan_id: str, index: int, branch: str) -> str:
    try:
        from ._literature_scoring import slug_label
    except ImportError:
        from _literature_scoring import slug_label
    safe_branch = slug_label(branch) or f"branch_{index}"
    return str(Path("claude-code") / "v8" / ".science" / "delegation" / plan_id / f"{index:02d}_{safe_branch}.json")

def science_branch_scout_description(
    project: dict[str, Any],
    *,
    objective: str,
    branch: dict[str, Any],
    artifact_path: str,
    providers: list[str],
) -> str:
    try:
        from ._gap_detection import build_knowledge_map, detect_knowledge_gaps
        from ._literature_graph import expand_literature_graph
        from ._literature_import import import_literature_search_result, import_papergraph_record
        from ._literature_search import search_literature_stratified, select_literature_result
    except ImportError:
        from _gap_detection import build_knowledge_map, detect_knowledge_gaps
        from _literature_graph import expand_literature_graph
        from _literature_import import import_literature_search_result, import_papergraph_record
        from _literature_search import search_literature_stratified, select_literature_result
    branch_name = str(branch.get("name") or branch.get("branch") or "")
    branch_query = str(branch.get("query") or branch_name)
    return (
        f"Role: ZhiZhi branch scout for a delegated AI-for-science workflow.\n"
        f"Project: {project.get('title', '')} ({project.get('project_id', '')})\n"
        f"Domain: {project.get('domain', '')}\n"
        f"Objective: {objective or project.get('objective', '')}\n"
        f"Branch: {branch_name}\n"
        f"Branch query: {branch_query}\n"
        f"Suggested providers: {', '.join(providers)}\n\n"
        "Important shared-state rule: do NOT call import_literature_search_result, import_papergraph_record, "
        "run_zhizhi_literature_analysis, build_knowledge_map, or detect_knowledge_gaps. Those mutate the shared science project. "
        "Your job is retrieval scouting only.\n\n"
        "Steps:\n"
        "1. Run search_literature_stratified with this branch query, modest max_results (8-15), the suggested providers, "
        "and domain from above.\n"
        "2. Inspect/select the top 3-5 candidates using select_literature_result or cached result summaries.\n"
        "3. Optionally run expand_literature_graph only for the best seed if it has a Semantic Scholar/DOI/arXiv id.\n"
        f"4. Write a compact JSON artifact to `{artifact_path}` with keys: branch, query, search_ids, recommended_imports "
        "(search_id/result_index/title/why), coverage_blind_spots, quality_risks, and scout_summary.\n"
        "5. Complete the task with a short summary and artifact path.\n"
    )

def science_synthesis_gate_description(
    project: dict[str, Any],
    *,
    objective: str,
    plan_id: str,
    branch_tasks: list[dict[str, Any]],
) -> str:
    try:
        from ._gap_detection import build_knowledge_map
        from ._literature_import import import_literature_search_result
    except ImportError:
        from _gap_detection import build_knowledge_map
        from _literature_import import import_literature_search_result
    artifact_paths = [str(item.get("artifact_path", "")) for item in branch_tasks if item.get("artifact_path")]
    return (
        "Role: lead-side synthesis gate for delegated science retrieval.\n"
        f"Delegation plan: {plan_id}\n"
        f"Project: {project.get('title', '')} ({project.get('project_id', '')})\n"
        f"Domain: {project.get('domain', '')}\n"
        f"Objective: {objective or project.get('objective', '')}\n\n"
        "Read the branch scout artifacts:\n"
        + "\n".join(f"- {path}" for path in artifact_paths)
        + "\n\n"
        "Synthesize a deduplicated import plan. The final shared-state mutation should be done serially by the lead in the main workspace: "
        "for each approved candidate, call import_literature_search_result(project_id, search_id, result_index), then build_knowledge_map. "
        "If you are running in an isolated worktree, do not assume project JSON changes landed in the main workspace.\n\n"
        "Deliverable JSON keys: approved_imports, rejected_candidates, missing_branches, recommended_lead_commands, risks. "
        "Keep the output compact enough that downstream TanXi does not inherit giant raw retrieval dumps.\n"
    )

def export_research_plan(project_id: str) -> str:
    try:
        from ._project import load_project
    except ImportError:
        from _project import load_project
    project = load_project(project_id)
    gaps = project.get("knowledge_gaps", [])
    hypotheses = project.get("hypotheses", [])
    reports = project.get("mechanism_reports", [])
    lines = [
        f"Project: {project.get('title', '')}",
        f"Domain: {project.get('domain', '')}",
        f"Objective: {project.get('objective', '')}",
        f"Strategic Need: {project.get('strategic_need', '')}",
        "",
        "Knowledge Gaps:",
    ]
    for gap in gaps:
        lines.append(f"- {gap.get('gap_id')}: [{gap.get('gap_type')}] {gap.get('description')}")
    lines.extend(["", "Hypotheses:"])
    for hypothesis in hypotheses:
        lines.append(f"- {hypothesis.get('hypothesis_id')}: {hypothesis.get('statement')}")
        lines.append(f"  Mechanism: {hypothesis.get('mechanism')}")
        lines.append(f"  Test Plan: {hypothesis.get('test_plan')}")
    lines.extend(["", "Mechanism Fidelity Reports:"])
    for report in reports:
        lines.append(f"- {report.get('report_id')}: {report.get('overall_verdict')}")
    lines.extend(["", "Pipeline Tasks:"])
    for task_id in project.get("pipeline_tasks", []):
        lines.append(f"- {task_id}")
    return "\n".join(lines).strip() + "\n"

def assess_novelty(
    project_id: str,
    gap: dict[str, Any] | str,
    dimensions: list[str] | None = None,
) -> str:
    try:
        from ._gap_detection import assess_gap_dict, parse_gap_input
        from ._project import load_project, save_project
    except ImportError:
        from _gap_detection import assess_gap_dict, parse_gap_input
        from _project import load_project, save_project
    project = load_project(project_id)
    gap_dict = parse_gap_input(gap)
    assessment = assess_gap_dict(project, gap_dict, dimensions=dimensions)
    project.setdefault("novelty_assessments", []).append(assessment)
    project["updatedAt"] = time.time()
    save_project(project)
    return json.dumps(assessment, ensure_ascii=False, indent=2)

def verify_uniqueness(
    project_id: str,
    idea: str,
    precision: str = "high",
    live_search: bool = False,
    providers: list[str] | None = None,
) -> str:
    try:
        from ._gap_detection import local_idea_overlap, summarize_uniqueness_live_search
        from ._literature_search import search_literature
        from ._project import default_literature_providers, load_project, save_project
    except ImportError:
        from _gap_detection import local_idea_overlap, summarize_uniqueness_live_search
        from _literature_search import search_literature
        from _project import default_literature_providers, load_project, save_project
    project = load_project(project_id)
    local_matches = local_idea_overlap(project, idea)
    live_result: dict[str, Any] = {}
    if live_search:
        try:
            live_result = json.loads(search_literature(idea, providers=providers or default_literature_providers(query=idea), max_results=5))
        except Exception as exc:
            live_result = {"status": "error", "error": str(exc)}
    threshold = 0.45 if precision == "high" else 0.6
    strongest = local_matches[0]["overlap_score"] if local_matches else 0.0
    verdict = "likely_unique" if strongest < threshold else "overlap_risk"
    result = {
        "idea": idea,
        "precision": precision,
        "verdict": verdict,
        "strongest_local_overlap": strongest,
        "local_matches": local_matches[:8],
        "live_search": summarize_uniqueness_live_search(live_result) if live_result else {"used": False},
        "next_step": "If verdict is overlap_risk, refine the idea or inspect matched papers before claiming novelty.",
    }
    project.setdefault("uniqueness_checks", []).append(result)
    project["updatedAt"] = time.time()
    save_project(project)
    return json.dumps(result, ensure_ascii=False, indent=2)

def run_zhizhi_literature_analysis(
    project_id: str,
    domain: str,
    query: str,
    max_results: int = 40,
    years: str = "last 5 years",
    providers: list[str] | None = None,
    import_top_k: int = SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K,
    graph_depth: int = 1,
    use_llm: bool = False,
    focus_branches: list[str] | None = None,
    live_coverage_check: bool = True,
    subspace_map_id: str = "",
    selected_subfields: list[str] | None = None,
    interactive_mode: bool = False,
) -> str:
    try:
        from ._gap_detection import build_knowledge_map, detect_knowledge_gaps, knowledge_map_unknown_summary, zhizhi_standard_output
        from ._literature_graph import build_literature_relation_graph, expand_literature_graph
        from ._literature_import import extract_paper_keynote, import_literature_search_result, select_zhizhi_import_results
        from ._literature_scoring import literature_domain_coverage_diagnostic
        from ._literature_search import build_branch_user_interaction, database_to_provider, search_papers_stratified, select_literature_result
        from ._project import default_literature_providers, explore_domain_subspaces, live_literature_provider_names, load_project, load_subspace_map, post_retrieval_subspace_coverage, query_plan_from_subspace_map, save_project
        from ._supplement import zhizhi_auto_supplement_blind_spots
        from ._utils import clamp_int, unique_preserve_order
    except ImportError:
        from _gap_detection import build_knowledge_map, detect_knowledge_gaps, knowledge_map_unknown_summary, zhizhi_standard_output
        from _literature_graph import build_literature_relation_graph, expand_literature_graph
        from _literature_import import extract_paper_keynote, import_literature_search_result, select_zhizhi_import_results
        from _literature_scoring import literature_domain_coverage_diagnostic
        from _literature_search import build_branch_user_interaction, database_to_provider, search_papers_stratified, select_literature_result
        from _project import default_literature_providers, explore_domain_subspaces, live_literature_provider_names, load_project, load_subspace_map, post_retrieval_subspace_coverage, query_plan_from_subspace_map, save_project
        from _supplement import zhizhi_auto_supplement_blind_spots
        from _utils import clamp_int, unique_preserve_order
    project = load_project(project_id)
    action: dict[str, Any] = {"agent": "zhizhi", "query": query, "domain": domain, "years": years}
    observations: list[str] = []
    import_limit = clamp_int(import_top_k, 1, SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K)
    search_budget = max(clamp_int(max_results, 1, 200), import_limit)
    selected_providers = [database_to_provider(item) for item in (providers or default_literature_providers(domain=domain, query=query))]
    selected_providers = unique_preserve_order([item for item in selected_providers if item in live_literature_provider_names()])
    if not selected_providers:
        selected_providers = ["semantic_scholar"]
    if not use_llm:
        observations.append(
            "use_llm=false: ontology fallback is enabled, but key papers should be rerun with use_llm=true for fewer unknown method/scenario fields."
        )
    selected_subfields = selected_subfields or []
    active_subspace_map: dict[str, Any] | None = None
    if subspace_map_id:
        subspace_map = load_subspace_map(subspace_map_id)
        active_subspace_map = subspace_map
        action["domain_subspace_explorer"] = {
            "subspace_map_id": subspace_map_id,
            "coverage_plan": subspace_map.get("coverage_plan", {}),
            "selected_subfields": selected_subfields,
        }
        subspace_queries = [
            item.get("query", "")
            for item in query_plan_from_subspace_map(subspace_map, selected_subfields=selected_subfields or focus_branches)
            if item.get("query")
        ]
        focus_branches = unique_preserve_order(list(focus_branches or []) + subspace_queries)
    elif interactive_mode:
        subspace_payload = json.loads(
            explore_domain_subspaces(
                domain=domain,
                max_subspaces=10,
                probe_depth=3,
                use_llm=use_llm,
                providers=selected_providers,
                user_hints=focus_branches,
            )
        )
        action["domain_subspace_explorer"] = {
            "subspace_map_id": subspace_payload.get("subspace_map_id"),
            "coverage_plan": subspace_payload.get("coverage_plan"),
            "user_interaction": subspace_payload.get("user_interaction"),
        }
        observations.append(
            "Interactive mode produced a Domain Subspace Map. Ask the user to select subspaces, then rerun with subspace_map_id and selected_subfields."
        )
        return json.dumps(
            zhizhi_standard_output(
                thought="ZhiZhi stopped before paper import because pre-retrieval subspace selection is required.",
                action=action,
                knowledge_map={},
                gaps=[],
                observations=observations,
            ),
            ensure_ascii=False,
            indent=2,
        )

    search_payload = json.loads(
        search_papers_stratified(
            query,
            databases=selected_providers,
            max_results=search_budget,
            years=years,
            domain=domain,
            focus_branches=focus_branches,
            use_llm=use_llm,
        )
    )
    action["search_papers_stratified"] = {
        "search_id": search_payload.get("search_id"),
        "total_results": search_payload.get("total_results", 0),
        "requested_max_results": max_results,
        "effective_search_budget": search_budget,
        "providers": search_payload.get("providers", []),
        "strategy": search_payload.get("strategy", ""),
        "query_plan": search_payload.get("query_plan", []),
        "focus_branches": focus_branches or [],
        "strata": search_payload.get("strata", []),
        "errors": [block for block in search_payload.get("provider_blocks", []) if block.get("status") != "ok"],
    }
    if int(search_payload.get("total_results") or 0) <= 0:
        observations.append("No retrieved papers; stopped before import to avoid invented evidence.")
        return json.dumps(
            zhizhi_standard_output(
                thought="Retrieval produced no usable papers, so ZhiZhi cannot build a grounded knowledge map yet.",
                action=action,
                knowledge_map={},
                gaps=[],
                observations=observations,
            ),
            ensure_ascii=False,
            indent=2,
        )

    search_id = str(search_payload.get("search_id"))
    log_event("SCIENCE", "search_phase_complete", search_id=search_id, total_results=search_payload.get("total_results", 0),
              providers=selected_providers, strata={s.get("layer"): s.get("count", 0) for s in (search_payload.get("strata") or [])})
    coverage_diagnostic = literature_domain_coverage_diagnostic(
        search_id,
        domain=domain,
        query=query,
        live_validate=live_coverage_check,
        use_llm=use_llm,
    )
    action["domain_coverage_diagnostic"] = coverage_diagnostic
    interaction = build_branch_user_interaction(coverage_diagnostic)
    if interaction.get("needed"):
        action["user_interaction"] = interaction
    for spot in coverage_diagnostic.get("blind_spots", []):
        observations.append(
            "Potential retrieval blind spot: "
            f"{spot.get('topic')} was not represented in retrieved/imported candidates; "
            f"suggested query: {spot.get('suggested_query')}; "
            f"live_probe_results={(spot.get('live_probe') or {}).get('total_results', 'not_run') if isinstance(spot.get('live_probe'), dict) else 'not_run'}"
        )

    # === IMPORT MAIN SEARCH PAPERS FIRST (before blind spot supplement burns SS quota) ===
    selected_payload = json.loads(select_literature_result(search_id, query=query, top_k=min(5, search_budget), use_llm=use_llm))
    selected = selected_payload.get("selected") or {}
    action["select_literature_result"] = selected

    import_candidates, import_plan = select_zhizhi_import_results(search_payload.get("results", []), import_limit)
    action["stratified_import_plan"] = import_plan
    for missing in import_plan.get("missing_layers", []):
        observations.append(
            "Layer import target not met: "
            f"{missing.get('layer')} selected={missing.get('selected')}/target={missing.get('target')} "
            f"from candidates={missing.get('candidates')}. This indicates retrieval/candidate scarcity rather than top-K truncation."
        )
    # === STRATIFIED LAYER-BY-LAYER IMPORT ===
    # Separate search results from import: search is already done, now import layer by layer
    imported_records: list[dict[str, Any]] = []
    layer_order = ["L0_review", "L1_milestone", "L2_top_latest", "L3_preprint", "L4_regular"]
    # Group candidates by stratified_layer
    by_layer: dict[str, list[dict[str, Any]]] = {layer: [] for layer in layer_order}
    for candidate in import_candidates:
        layer = str(candidate.get("stratified_layer") or "L4_regular")
        if layer in by_layer:
            by_layer[layer].append(candidate)
        else:
            by_layer["L4_regular"].append(candidate)

    log_event("SCIENCE", "import_phase_start", search_id=search_id, total_candidates=len(import_candidates),
              layers={layer: len(items) for layer, items in by_layer.items() if items})

    for layer_name in layer_order:
        layer_candidates = by_layer.get(layer_name, [])
        if not layer_candidates:
            continue
        log_event("SCIENCE", "import_layer_start", layer=layer_name, candidates=len(layer_candidates))
        layer_imported = 0
        layer_failed = 0
        for result in layer_candidates:
            result_index = int(result.get("result_index") or 0)
            result_title = str(result.get("title") or "untitled")[:120]
            log_event("SCIENCE", "import_paper_attempt", layer=layer_name, title=result_title, result_index=result_index)
            try:
                imported = json.loads(import_literature_search_result(project_id, search_id, result_index, use_llm=use_llm))
                imported_records.append(imported)
                layer_imported += 1
                record = imported.get("record") or imported.get("existing_record") or {}
                paper_id = record.get("paper_id")
                log_event("SCIENCE", "paper_imported", layer=layer_name, title=result_title, paper_id=paper_id, result_index=result_index)
                if paper_id:
                    try:
                        extract_paper_keynote(project_id, paper_id=str(paper_id), use_llm=use_llm)
                    except Exception as exc:
                        observations.append(f"keynote extraction failed for {paper_id}: {exc}")
            except Exception as exc:
                layer_failed += 1
                log_event("SCIENCE", "import_paper_failed", layer=layer_name, title=result_title, result_index=result_index, error=str(exc)[:200])
                observations.append(f"import failed for {layer_name} result {result_index} ({result_title}): {exc}")
        log_event("SCIENCE", "import_layer_complete", layer=layer_name, imported=layer_imported, failed=layer_failed, total=len(layer_candidates))

    log_event("SCIENCE", "import_phase_complete", search_id=search_id, total_imported=len(imported_records), total_candidates=len(import_candidates))
    action["imported_records"] = len(imported_records)

    # === BLIND SPOT SUPPLEMENT (runs after main import to avoid exhausting SS quota first) ===
    supplemental_imports: list[dict[str, Any]] = []
    supplemental_report = zhizhi_auto_supplement_blind_spots(
        project_id=project_id,
        coverage_diagnostic=coverage_diagnostic,
        providers=selected_providers,
        domain=domain,
        use_llm=use_llm,
        max_branches=3,
        per_branch_imports=2,
    )
    if supplemental_report.get("attempted"):
        action["auto_supplement_blind_spots"] = supplemental_report
        supplemental_imports = supplemental_report.get("imports", []) if isinstance(supplemental_report.get("imports"), list) else []
        observations.append(
            "Auto-supplemented confirmed retrieval blind spots before TanXi gap reasoning: "
            f"branches={supplemental_report.get('branches_attempted', 0)}, imports={len(supplemental_imports)}."
        )
    if active_subspace_map is not None:
        subspace_coverage = post_retrieval_subspace_coverage(active_subspace_map, selected_subfields or focus_branches, imported_records)
        action["post_retrieval_subspace_coverage"] = subspace_coverage
        if subspace_coverage.get("needs_second_alignment"):
            action["post_retrieval_user_interaction"] = subspace_coverage.get("user_interaction")
            for item in subspace_coverage.get("coverage", []):
                if item.get("status") != "sufficient":
                    observations.append(
                        "Selected subspace under-covered after import: "
                        f"{item.get('subspace')} actual={item.get('actual')}/target={item.get('target')}; "
                        f"suggested_query={item.get('suggested_query')}"
                    )

    graph_search_id = ""
    try:
        selected_index = int(selected.get("result_index") or 0)
        graph_payload = json.loads(
            expand_literature_graph(
                search_id,
                result_index=selected_index,
                query=query,
                direction="both",
                max_results=max_results * 2,
                use_llm=use_llm,
                depth=graph_depth,
            )
        )
        graph_search_id = str(graph_payload.get("graph_search_id") or "")
        action["expand_literature_graph"] = {
            "graph_search_id": graph_search_id,
            "total_results": graph_payload.get("total_results", 0),
            "fallback_used": graph_payload.get("fallback_used", False),
            "depth": graph_payload.get("depth", graph_depth),
        }
    except Exception as exc:
        observations.append(f"citation graph expansion failed: {exc}")

    if graph_search_id:
        try:
            relation_payload = json.loads(
                build_literature_relation_graph(graph_search_id, query=query, max_nodes=max_results * 2, min_quality=0.45, max_clusters=8)
            )
            action["build_literature_relation_graph"] = {
                "relation_graph_id": relation_payload.get("relation_graph_id"),
                "cluster_count": relation_payload.get("cluster_count"),
                "edge_summary": relation_payload.get("edge_summary"),
                "analysis_confidence": relation_payload.get("analysis_confidence"),
            }
        except Exception as exc:
            observations.append(f"relation graph failed: {exc}")

    knowledge_map = json.loads(build_knowledge_map(project_id))
    unknown_summary = knowledge_map_unknown_summary(knowledge_map)
    if unknown_summary["unknown_triples"] > 0:
        observations.append(
            f"Knowledge map still contains {unknown_summary['unknown_triples']} triples with unknown fields; rerun extraction with use_llm=true for key papers."
        )
    gaps = json.loads(detect_knowledge_gaps(project_id, max_gaps=8))
    assessed_gaps = []
    for gap in gaps:
        assessed = json.loads(assess_novelty(project_id, gap))
        uniqueness = json.loads(verify_uniqueness(project_id, assessed.get("description", ""), precision="high", live_search=False))
        assessed["uniqueness_verdict"] = uniqueness.get("verdict")
        assessed["strongest_overlap"] = uniqueness.get("strongest_local_overlap")
        assessed_gaps.append(assessed)

    output = zhizhi_standard_output(
        thought=(
            "ZhiZhi retrieved and filtered literature, imported grounded PaperGraph evidence, "
            "built a benchmark-aware knowledge map, expanded citation context when available, "
            "and generated gaps with novelty/value/feasibility checks."
        ),
        action=action,
        knowledge_map=knowledge_map,
        gaps=assessed_gaps,
        observations=observations,
    )
    project = load_project(project_id)
    project.setdefault("zhizhi_reports", []).append(output)
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "zhizhi_analysis_complete", project_id=project_id, gaps=len(assessed_gaps))
    return json.dumps(output, ensure_ascii=False, indent=2)

def agents_for_phase(phase: str) -> list[str]:
    try:
        from ._models import SCIENCE_AGENTS
    except ImportError:
        from _models import SCIENCE_AGENTS
    return [name for name, spec in SCIENCE_AGENTS.items() if spec.get("phase") in {phase, "all"}]

def supporting_references_for_method_or_scenario(project: dict[str, Any], method: str, scenario: str) -> list[str]:
    try:
        from ._utils import normalize_label
    except ImportError:
        from _utils import normalize_label
    refs: list[str] = []
    for evidence in project.get("evidence", []):
        if normalize_label(evidence.get("method", "")) == method or normalize_label(evidence.get("scenario", "")) == scenario:
            citation = str(evidence.get("citation", ""))
            if citation and citation not in refs:
                refs.append(citation)
    return refs[:5]

def project_records_for_mapping(project: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in project.get("papergraph", []):
        if isinstance(record, dict):
            records.append(record)
    for evidence in project.get("evidence", []):
        if isinstance(evidence, dict):
            records.append(evidence)
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("unique_key") or record.get("citation") or record.get("title") or id(record))
        deduped[key] = record
    return list(deduped.values())

def classify_record_evidence(record: dict[str, Any]) -> list[dict[str, str]]:
    text = "\n".join(
        str(record.get(key, ""))
        for key in ("abstract", "conclusion", "contribution", "limitation")
        if record.get(key)
    )
    return classify_evidence_claims(text, record)

def classify_evidence_claims(text: str, parsed: dict[str, Any] | None = None) -> list[dict[str, str]]:
    try:
        from ._utils import scalar, split_sentences, trim_text
    except ImportError:
        from _utils import scalar, split_sentences, trim_text
    parsed = parsed or {}
    claims: list[dict[str, str]] = []
    candidates = [
        ("methodological_description", parsed.get("method", "")),
        ("empirical_result", parsed.get("contribution", "")),
        ("author_opinion", parsed.get("limitation", "")),
        ("theoretical_claim", parsed.get("conclusion", "")),
    ]
    for claim_type, claim in candidates:
        rendered = scalar(claim)
        if rendered:
            claims.append({"claim_type": claim_type, "claim": trim_text(rendered, 300), "support": "structured_field"})
    for sentence in split_sentences(text)[:12]:
        lowered = sentence.lower()
        claim_type = ""
        if any(term in lowered for term in ("experiment", "result", "outperform", "accuracy", "measured", "observed")):
            claim_type = "empirical_result"
        elif any(term in lowered for term in ("theorem", "theory", "prove", "derive", "model predicts")):
            claim_type = "theoretical_claim"
        elif any(term in lowered for term in ("method", "algorithm", "framework", "approach", "we propose")):
            claim_type = "methodological_description"
        elif any(term in lowered for term in ("suggest", "may", "could", "indicate", "limitation", "future work")):
            claim_type = "author_opinion"
        if claim_type:
            claims.append({"claim_type": claim_type, "claim": trim_text(sentence, 300), "support": "source_sentence"})
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in claims:
        key = (item["claim_type"], item["claim"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:12]

