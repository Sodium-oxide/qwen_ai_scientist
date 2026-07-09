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
    try:
        from ._project import load_project, save_project
        from ._utils import clamp_int, new_id, normalize_key
    except ImportError:
        from _project import load_project, save_project
        from _utils import clamp_int, new_id, normalize_key
    """Run one bounded Boxue scheduling round.

    This is the missing coordinator loop on top of the existing task DAG and
    teammate mailbox. It creates a Boxue plan, starts currently unblocked
    specialists, watches the task board/inbox for a bounded time window, starts
    newly unblocked downstream specialists, records lightweight reviews for
    completed deliverables, and creates revision tasks for clearly stalled or
    failed items.
    """
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

    plan = {
        "delegation_plan_id": plan_id,
        "project_id": project_id,
        "objective": objective,
        "createdAt": time.time(),
        "policy": {
            "parallel_work": "branch scouts retrieve and judge evidence independently",
            "shared_state": "lead/synthesis gate performs PaperGraph imports serially after reviewing artifacts",
            "reason": "long single-agent ZhiZhi/TanXi/MingLi runs are brittle and produce oversized outputs",
        },
        "artifact_dir": str(artifact_dir),
        "providers": providers,
        "branch_tasks": branch_tasks,
        "synthesis_task_id": synthesis_task_id,
        "tanxi_task_id": tanxi_task_id,
        "mingli_task_id": mingli_task_id,
        "spawned_teammates": spawned,
        "next_step": (
            "Let scouts complete branch artifacts, then have the synthesis gate choose import candidates. "
            "The lead should import selected cached search results into the main project and continue TanXi/MingLi."
        ),
    }
    project.setdefault("delegation_plans", []).append(plan)
    project.setdefault("pipeline_tasks", [])
    project["pipeline_tasks"] = unique_preserve_order(
        list(project.get("pipeline_tasks", []))
        + branch_task_ids
        + [task_id for task_id in (synthesis_task_id, tanxi_task_id, mingli_task_id) if task_id]
    )
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "delegation_tasks_created", project_id=project_id, plan_id=plan_id, branches=len(branch_tasks))
    return json.dumps(plan, ensure_ascii=False, indent=2)

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
    imported_records: list[dict[str, Any]] = []
    for result in import_candidates:
        try:
            imported = json.loads(import_literature_search_result(project_id, search_id, int(result.get("result_index") or 0), use_llm=use_llm))
            imported_records.append(imported)
            record = imported.get("record") or imported.get("existing_record") or {}
            paper_id = record.get("paper_id")
            if paper_id:
                try:
                    extract_paper_keynote(project_id, paper_id=str(paper_id), use_llm=use_llm)
                except Exception as exc:
                    observations.append(f"keynote extraction failed for {paper_id}: {exc}")
        except Exception as exc:
            observations.append(f"import failed for result {result.get('result_index')}: {exc}")
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

