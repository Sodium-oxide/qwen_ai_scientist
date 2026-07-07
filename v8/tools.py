from __future__ import annotations

import glob as glob_module
import subprocess
from pathlib import Path
from typing import Callable

try:
    from .config import BASH_TIMEOUT_SECONDS, MAX_OUTPUT_CHARS, SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K, TOOL_RESULTS_DIR, WORKDIR
except ImportError:
    from config import BASH_TIMEOUT_SECONDS, MAX_OUTPUT_CHARS, SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K, TOOL_RESULTS_DIR, WORKDIR


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated to {limit} characters]"


def safe_cwd(cwd: str | None = None) -> Path:
    if not cwd:
        return WORKDIR
    raw = Path(cwd).expanduser()
    candidate = raw if raw.is_absolute() else WORKDIR / raw
    resolved = candidate.resolve()
    if not resolved.is_relative_to(WORKDIR):
        raise ValueError(f"cwd escapes workspace: {cwd}")
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    return resolved


def safe_path(path: str, cwd: str | None = None) -> Path:
    root = safe_cwd(cwd)
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes current workspace: {path}")
    return resolved


def path_escapes_workspace(path: str) -> bool:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else WORKDIR / raw
    return not candidate.resolve().is_relative_to(WORKDIR)


def relative(path: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(WORKDIR):
        return str(resolved.relative_to(WORKDIR)).replace("\\", "/")
    return str(resolved)


def bash(command: str, cwd: str | None = None) -> str:
    root = safe_cwd(cwd)
    completed = subprocess.run(
        command,
        cwd=root,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=BASH_TIMEOUT_SECONDS,
    )
    output = []
    if completed.stdout:
        output.append(completed.stdout)
    if completed.stderr:
        output.append(completed.stderr)
    if not output:
        output.append("(no output)")
    output.append(f"\n[exit_code={completed.returncode}]")
    return truncate("".join(output))


def read_file(path: str, limit: int | None = None, cwd: str | None = None) -> str:
    target = safe_path(path, cwd)
    if is_tool_result_artifact(target):
        return read_tool_result_artifact(target, limit)
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()

    if limit is not None and limit >= 0 and len(lines) > limit:
        visible = lines[:limit]
        visible.append(f"\n...[truncated after {limit} lines]\n")
        lines = visible

    return "".join(f"{index + 1:>4} | {line}" for index, line in enumerate(lines))


def is_tool_result_artifact(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = TOOL_RESULTS_DIR.resolve()
    except Exception:
        return False
    return resolved.is_relative_to(root) and resolved.suffix.lower() == ".txt"


def read_tool_result_artifact(path: Path, limit: int | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = relative(path)
    if limit is not None and 0 <= limit <= 80:
        lines = text.splitlines(keepends=True)
        visible = lines[:limit]
        if len(lines) > limit:
            visible.append(f"\n...[truncated after {limit} lines; tool result artifact has {len(text)} chars]\n")
        body = "".join(f"{index + 1:>4} | {line}" for index, line in enumerate(visible))
        return bounded_tool_result_preview(
            rel=rel,
            chars=len(text),
            mode=f"first {limit} lines",
            body=body,
        )
    head = text[:1200]
    tail = text[-1200:] if len(text) > 1200 else ""
    return bounded_tool_result_preview(
        rel=rel,
        chars=len(text),
        mode="head/tail",
        body=f"--- head ---\n{head}\n--- tail ---\n{tail}",
    )


def bounded_tool_result_preview(rel: str, chars: int, mode: str, body: str) -> str:
    header = (
        "[tool result artifact preview]\n"
        f"path: {rel}\n"
        f"chars: {chars}\n"
        f"mode: {mode}\n"
        "reason: Refusing full read of v8/tool_results artifacts to prevent recursive large-output loops.\n"
        "Use the original producing tool/search id when possible; this preview is intentionally bounded.\n\n"
    )
    rendered = header + body
    max_preview_chars = 6000
    if len(rendered) <= max_preview_chars:
        return rendered
    return rendered[:max_preview_chars] + (
        f"\n...[tool result artifact preview truncated to {max_preview_chars} chars]\n"
    )


def write_file(path: str, content: str, cwd: str | None = None, actor: str = "lead") -> str:
    target = safe_path(path, cwd)
    enforce_assigned_worktree_write_permission(target, actor)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {relative(target)}"


def edit_file(path: str, old_text: str, new_text: str, cwd: str | None = None, actor: str = "lead") -> str:
    target = safe_path(path, cwd)
    enforce_assigned_worktree_write_permission(target, actor)
    content = target.read_text(encoding="utf-8", errors="replace")
    if old_text not in content:
        raise ValueError("old_text was not found.")
    updated = content.replace(old_text, new_text, 1)
    target.write_text(updated, encoding="utf-8")
    return f"Replaced one occurrence in {relative(target)}"


def enforce_assigned_worktree_write_permission(target: Path, actor: str) -> None:
    actor_name = normalize_actor(actor)
    try:
        from .task_system import load_tasks
        from .worktree_isolation import resolve_worktree_cwd
    except ImportError:
        from task_system import load_tasks
        from worktree_isolation import resolve_worktree_cwd

    for task in load_tasks():
        if task.status != "in_progress" or not task.owner or not task.worktree:
            continue
        owner = normalize_actor(task.owner)
        if actor_name == owner:
            continue
        try:
            worktree_root = resolve_worktree_cwd(task.worktree)
        except Exception:
            continue
        if target.resolve().is_relative_to(worktree_root.resolve()):
            raise PermissionError(
                "Assigned worktree write blocked: "
                f"task {task.id} is owned by {task.owner}; actor {actor or 'lead'} "
                "must ask the owner teammate to modify this worktree."
            )


def normalize_actor(value: str) -> str:
    raw = str(value or "lead").strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(char for char in raw if char.isalnum() or char == "_") or "lead"


def glob(pattern: str, limit: int = 200, cwd: str | None = None) -> str:
    matches: list[str] = []
    root = safe_cwd(cwd)
    search_pattern = str(root / pattern)
    for match in glob_module.glob(search_pattern, recursive=True):
        path = Path(match).resolve()
        if path.is_relative_to(root):
            matches.append(relative(path))

    matches = sorted(set(matches))[:limit]
    if not matches:
        return "(no matches)"
    return "\n".join(matches)


def spawn_subagent(description: str) -> str:
    try:
        from .subagent import spawn_subagent as run_subagent
    except ImportError:
        from subagent import spawn_subagent as run_subagent

    return run_subagent(description)


def task(description: str) -> str:
    return spawn_subagent(description)


def todo_write(items: list[dict[str, object]] | list[str]) -> str:
    try:
        from .todo_state import todo_write as write_todos
    except ImportError:
        from todo_state import todo_write as write_todos
    return write_todos(items)


def load_skill(name: str) -> str:
    try:
        from .skill import load_skill as skill_load
    except ImportError:
        from skill import load_skill as skill_load
    return skill_load(name)


def compact(focus: str = "") -> str:
    if focus:
        return f"Compaction requested. Focus: {focus}"
    return "Compaction requested."


def create_task(subject: str, description: str, blockedBy: list[str] | None = None) -> str:
    try:
        from .task_system import create_task as task_create
    except ImportError:
        from task_system import create_task as task_create
    return task_create(subject, description, blockedBy)


def list_tasks(include_completed: bool = True) -> str:
    try:
        from .task_system import list_tasks as task_list
    except ImportError:
        from task_system import list_tasks as task_list
    return task_list(include_completed)


def get_task(task_id: str) -> str:
    try:
        from .task_system import get_task as task_get
    except ImportError:
        from task_system import get_task as task_get
    return task_get(task_id)


def claim_task(task_id: str, owner: str = "main") -> str:
    try:
        from .task_system import claim_task as task_claim
    except ImportError:
        from task_system import claim_task as task_claim
    return task_claim(task_id, owner)


def complete_task(task_id: str) -> str:
    try:
        from .task_system import complete_task as task_complete
    except ImportError:
        from task_system import complete_task as task_complete
    return task_complete(task_id)


def spawn_teammate(name: str, task: str = "") -> str:
    try:
        from .agent_teams import spawn_teammate as team_spawn
    except ImportError:
        from agent_teams import spawn_teammate as team_spawn
    return team_spawn(name, task)


def send_message(to: str, content: str, type: str = "message") -> str:
    try:
        from .agent_teams import LEAD, send_message as team_send
    except ImportError:
        from agent_teams import LEAD, send_message as team_send
    return team_send(LEAD, to, content, type=type)


def check_inbox(agent: str = "lead") -> str:
    try:
        from .agent_teams import check_inbox as team_check
    except ImportError:
        from agent_teams import check_inbox as team_check
    return team_check(agent)


def request_shutdown(teammate: str, reason: str = "") -> str:
    try:
        from .agent_teams import request_shutdown as team_shutdown
    except ImportError:
        from agent_teams import request_shutdown as team_shutdown
    return team_shutdown(teammate, reason)


def request_plan(teammate: str, prompt: str) -> str:
    try:
        from .agent_teams import request_plan as team_plan
    except ImportError:
        from agent_teams import request_plan as team_plan
    return team_plan(teammate, prompt)


def review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
    try:
        from .agent_teams import review_plan as team_review
    except ImportError:
        from agent_teams import review_plan as team_review
    return team_review(request_id, approve, feedback)


def create_worktree(name: str, task_id: str = "") -> str:
    try:
        from .worktree_isolation import create_worktree as wt_create
    except ImportError:
        from worktree_isolation import create_worktree as wt_create
    result = wt_create(name, task_id)
    if task_id:
        try:
            from .task_system import bind_task_worktree
        except ImportError:
            from task_system import bind_task_worktree
        result = f"{result}\n{bind_task_worktree(task_id, name)}"
    return result


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    try:
        from .worktree_isolation import remove_worktree as wt_remove
    except ImportError:
        from worktree_isolation import remove_worktree as wt_remove
    return wt_remove(name, discard_changes)


def keep_worktree(name: str, reason: str = "") -> str:
    try:
        from .worktree_isolation import keep_worktree as wt_keep
    except ImportError:
        from worktree_isolation import keep_worktree as wt_keep
    return wt_keep(name, reason)


def connect_mcp(name: str) -> str:
    try:
        from .mcp_plugin import connect_mcp as mcp_connect
    except ImportError:
        from mcp_plugin import connect_mcp as mcp_connect
    return mcp_connect(name)


def schedule_cron(
    cron: str,
    prompt: str,
    recurring: bool = True,
    durable: bool = True,
) -> str:
    try:
        from .cron_scheduler import schedule_cron as cron_schedule
    except ImportError:
        from cron_scheduler import schedule_cron as cron_schedule
    return cron_schedule(cron, prompt, recurring, durable)


def list_crons() -> str:
    try:
        from .cron_scheduler import list_crons as cron_list
    except ImportError:
        from cron_scheduler import list_crons as cron_list
    return cron_list()


def cancel_cron(job_id: str) -> str:
    try:
        from .cron_scheduler import cancel_cron as cron_cancel
    except ImportError:
        from cron_scheduler import cancel_cron as cron_cancel
    return cron_cancel(job_id)


def create_research_project(title: str, domain: str, objective: str, strategic_need: str = "") -> str:
    try:
        from .science_core import create_research_project as science_create
    except ImportError:
        from science_core import create_research_project as science_create
    return science_create(title, domain, objective, strategic_need)


def list_research_projects() -> str:
    try:
        from .science_core import list_research_projects as science_list
    except ImportError:
        from science_core import list_research_projects as science_list
    return science_list()


def get_research_project(project_id: str) -> str:
    try:
        from .science_core import get_research_project as science_get
    except ImportError:
        from science_core import get_research_project as science_get
    return science_get(project_id)


def list_science_agents() -> str:
    try:
        from .science_core import list_science_agents as science_agents
    except ImportError:
        from science_core import list_science_agents as science_agents
    return science_agents()


def get_science_agent_prompt(agent: str) -> str:
    try:
        from .science_core import get_science_agent_prompt as science_prompt
    except ImportError:
        from science_core import get_science_agent_prompt as science_prompt
    return science_prompt(agent)


def list_literature_providers() -> str:
    try:
        from .science_core import list_literature_providers as science_providers
    except ImportError:
        from science_core import list_literature_providers as science_providers
    return science_providers()


def explore_domain_subspaces(
    domain: str,
    max_subspaces: int = 12,
    probe_depth: int = 5,
    use_llm: bool = True,
    providers: list[str] | None = None,
    user_hints: list[str] | None = None,
) -> str:
    try:
        from .science_core import explore_domain_subspaces as science_explore_subspaces
    except ImportError:
        from science_core import explore_domain_subspaces as science_explore_subspaces
    return science_explore_subspaces(domain, max_subspaces, probe_depth, use_llm, providers, user_hints)


def search_literature(query: str, providers: list[str] | None = None, max_results: int = 10) -> str:
    try:
        from .science_core import search_literature as science_search
    except ImportError:
        from science_core import search_literature as science_search
    return science_search(query, providers, max_results)


def search_literature_stratified(
    query: str,
    providers: list[str] | None = None,
    max_results: int = 15,
    domain: str = "",
    focus_branches: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    try:
        from .science_core import search_literature_stratified as science_search_stratified
    except ImportError:
        from science_core import search_literature_stratified as science_search_stratified
    return science_search_stratified(query, providers, max_results, domain, focus_branches, use_llm)


def search_papers(
    query: str,
    databases: list[str] | None = None,
    max_results: int = 15,
    years: str = "",
) -> str:
    try:
        from .science_core import search_papers as science_search_papers
    except ImportError:
        from science_core import search_papers as science_search_papers
    return science_search_papers(query, databases, max_results, years)


def search_papers_stratified(
    query: str,
    databases: list[str] | None = None,
    max_results: int = 15,
    years: str = "",
    domain: str = "",
    focus_branches: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    try:
        from .science_core import search_papers_stratified as science_search_papers_stratified
    except ImportError:
        from science_core import search_papers_stratified as science_search_papers_stratified
    return science_search_papers_stratified(query, databases, max_results, years, domain, focus_branches, use_llm)


def extract_structured_info(
    paper_content: str,
    fields: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    try:
        from .science_core import extract_structured_info as science_extract_structured
    except ImportError:
        from science_core import extract_structured_info as science_extract_structured
    return science_extract_structured(paper_content, fields, use_llm)


def select_literature_result(search_id: str, query: str = "", top_k: int = 5, use_llm: bool = False) -> str:
    try:
        from .science_core import select_literature_result as science_select_result
    except ImportError:
        from science_core import select_literature_result as science_select_result
    return science_select_result(search_id, query, top_k, use_llm)


def expand_literature_graph(
    search_id: str,
    result_index: int = 0,
    query: str = "",
    direction: str = "both",
    max_results: int = 40,
    use_llm: bool = False,
    depth: int = 1,
    second_layer_top_k: int = 3,
    allow_fallback: bool = True,
) -> str:
    try:
        from .science_core import expand_literature_graph as science_expand_graph
    except ImportError:
        from science_core import expand_literature_graph as science_expand_graph
    return science_expand_graph(
        search_id,
        result_index,
        query,
        direction,
        max_results,
        use_llm,
        depth,
        second_layer_top_k,
        allow_fallback,
    )


def build_literature_relation_graph(
    search_id: str,
    query: str = "",
    max_nodes: int = 80,
    min_quality: float = 0.0,
    max_clusters: int = 8,
) -> str:
    try:
        from .science_core import build_literature_relation_graph as science_relation_graph
    except ImportError:
        from science_core import build_literature_relation_graph as science_relation_graph
    return science_relation_graph(search_id, query, max_nodes, min_quality, max_clusters)


def create_science_pipeline_tasks(project_id: str) -> str:
    try:
        from .science_core import create_science_pipeline_tasks as science_pipeline
    except ImportError:
        from science_core import create_science_pipeline_tasks as science_pipeline
    return science_pipeline(project_id)


def create_science_delegation_tasks(
    project_id: str,
    objective: str = "",
    subspace_map_id: str = "",
    selected_subfields: list[str] | None = None,
    focus_branches: list[str] | None = None,
    max_branch_tasks: int = 6,
    spawn_teammates: bool = False,
) -> str:
    try:
        from .science_core import create_science_delegation_tasks as science_delegation
    except ImportError:
        from science_core import create_science_delegation_tasks as science_delegation
    return science_delegation(
        project_id,
        objective,
        subspace_map_id,
        selected_subfields,
        focus_branches,
        max_branch_tasks,
        spawn_teammates,
    )


def create_boxue_delegation_tasks(
    project_id: str,
    goal: str = "",
    phases: list[str] | None = None,
    max_steps: int = 20,
    spawn_teammates: bool = False,
    max_parallel_agents: int = 3,
) -> str:
    try:
        from .science_core import create_boxue_delegation_tasks as boxue_delegation
    except ImportError:
        from science_core import create_boxue_delegation_tasks as boxue_delegation
    return boxue_delegation(project_id, goal, phases, max_steps, spawn_teammates, max_parallel_agents)


def run_boxue_research_round(
    project_id: str,
    goal: str = "",
    phases: list[str] | None = None,
    spawn_teammates: bool = True,
    plan_id: str = "",
    execution_mode: str = "async",
    max_steps: int = 20,
    max_parallel_agents: int = 3,
    max_runtime_seconds: int = 45,
    poll_interval_seconds: float = 2.0,
    revision_after_seconds: int = 600,
) -> str:
    try:
        from .science_core import run_boxue_research_round as boxue_round
    except ImportError:
        from science_core import run_boxue_research_round as boxue_round
    return boxue_round(
        project_id=project_id,
        goal=goal,
        phases=phases,
        spawn_teammates=spawn_teammates,
        plan_id=plan_id,
        execution_mode=execution_mode,
        max_steps=max_steps,
        max_parallel_agents=max_parallel_agents,
        max_runtime_seconds=max_runtime_seconds,
        poll_interval_seconds=poll_interval_seconds,
        revision_after_seconds=revision_after_seconds,
    )


def build_knowledge_map(project_id: str, dimension: str = "method-scenario-benchmark") -> str:
    try:
        from .science_core import build_knowledge_map as science_knowledge_map
    except ImportError:
        from science_core import build_knowledge_map as science_knowledge_map
    return science_knowledge_map(project_id, dimension)


def add_literature_evidence(
    project_id: str,
    title: str,
    citation: str,
    method: str,
    scenario: str,
    benchmark: str,
    contribution: str,
    limitation: str,
    url: str = "",
) -> str:
    try:
        from .science_core import add_literature_evidence as science_add_evidence
    except ImportError:
        from science_core import add_literature_evidence as science_add_evidence
    return science_add_evidence(project_id, title, citation, method, scenario, benchmark, contribution, limitation, url)


def import_literature_text(
    project_id: str,
    text: str,
    title: str = "",
    citation: str = "",
    provider: str = "manual",
    source_type: str = "abstract",
    url: str = "",
    doi: str = "",
    arxiv_id: str = "",
    semantic_scholar_id: str = "",
    authors: list[str] | None = None,
    year: str = "",
    venue: str = "",
    use_llm: bool = False,
) -> str:
    try:
        from .science_core import import_literature_text as science_import_text
    except ImportError:
        from science_core import import_literature_text as science_import_text
    return science_import_text(
        project_id=project_id,
        text=text,
        title=title,
        citation=citation,
        provider=provider,
        source_type=source_type,
        url=url,
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        authors=authors,
        year=year,
        venue=venue,
        use_llm=use_llm,
    )


def import_literature_file(
    project_id: str,
    path: str,
    title: str = "",
    citation: str = "",
    provider: str = "manual_file",
    source_type: str = "file",
    use_llm: bool = False,
) -> str:
    try:
        from .science_core import import_literature_file as science_import_file
    except ImportError:
        from science_core import import_literature_file as science_import_file
    return science_import_file(project_id, path, title, citation, provider, source_type, use_llm)


def import_literature_search_result(
    project_id: str,
    search_id: str,
    result_index: int = 0,
    use_llm: bool = False,
) -> str:
    try:
        from .science_core import import_literature_search_result as science_import_search_result
    except ImportError:
        from science_core import import_literature_search_result as science_import_search_result
    return science_import_search_result(project_id, search_id, result_index, use_llm)


def extract_paper_keynote(
    project_id: str,
    paper_id: str = "",
    search_id: str = "",
    result_index: int = 0,
    text: str = "",
    use_llm: bool = True,
) -> str:
    try:
        from .science_core import extract_paper_keynote as science_keynote
    except ImportError:
        from science_core import extract_paper_keynote as science_keynote
    return science_keynote(project_id, paper_id, search_id, result_index, text, use_llm)


def import_papergraph_record(
    project_id: str,
    title: str,
    citation: str,
    authors: list[str] | None = None,
    year: str = "",
    venue: str = "",
    provider: str = "manual",
    source_type: str = "metadata",
    doi: str = "",
    arxiv_id: str = "",
    semantic_scholar_id: str = "",
    url: str = "",
    abstract: str = "",
    full_text_excerpt: str = "",
    conclusion: str = "",
    strengths: list[str] | None = None,
    improvements: list[str] | None = None,
    method: str = "",
    scenario: str = "",
    benchmark: str = "",
    contribution: str = "",
    limitation: str = "",
) -> str:
    try:
        from .science_core import import_papergraph_record as science_import_record
    except ImportError:
        from science_core import import_papergraph_record as science_import_record
    return science_import_record(
        project_id=project_id,
        title=title,
        citation=citation,
        authors=authors,
        year=year,
        venue=venue,
        provider=provider,
        source_type=source_type,
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        url=url,
        abstract=abstract,
        full_text_excerpt=full_text_excerpt,
        conclusion=conclusion,
        strengths=strengths,
        improvements=improvements,
        method=method,
        scenario=scenario,
        benchmark=benchmark,
        contribution=contribution,
        limitation=limitation,
    )


def list_papergraph_records(project_id: str) -> str:
    try:
        from .science_core import list_papergraph_records as science_list_records
    except ImportError:
        from science_core import list_papergraph_records as science_list_records
    return science_list_records(project_id)


def verify_citation_uniqueness(
    project_id: str,
    title: str = "",
    citation: str = "",
    doi: str = "",
    arxiv_id: str = "",
    semantic_scholar_id: str = "",
    url: str = "",
) -> str:
    try:
        from .science_core import verify_citation_uniqueness as science_verify_unique
    except ImportError:
        from science_core import verify_citation_uniqueness as science_verify_unique
    return science_verify_unique(project_id, title, citation, doi, arxiv_id, semantic_scholar_id, url)


def assess_novelty(
    project_id: str,
    gap: dict[str, object] | str,
    dimensions: list[str] | None = None,
) -> str:
    try:
        from .science_core import assess_novelty as science_assess
    except ImportError:
        from science_core import assess_novelty as science_assess
    return science_assess(project_id, gap, dimensions)


def verify_uniqueness(
    project_id: str,
    idea: str,
    precision: str = "high",
    live_search: bool = False,
    providers: list[str] | None = None,
) -> str:
    try:
        from .science_core import verify_uniqueness as science_verify_idea
    except ImportError:
        from science_core import verify_uniqueness as science_verify_idea
    return science_verify_idea(project_id, idea, precision, live_search, providers)


def run_zhizhi_literature_analysis(
    project_id: str,
    domain: str,
    query: str,
    max_results: int = 10,
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
        from .science_core import run_zhizhi_literature_analysis as science_zhizhi
    except ImportError:
        from science_core import run_zhizhi_literature_analysis as science_zhizhi
    return science_zhizhi(
        project_id,
        domain,
        query,
        max_results,
        years,
        providers,
        import_top_k,
        graph_depth,
        use_llm,
        focus_branches,
        live_coverage_check,
        subspace_map_id,
        selected_subfields,
        interactive_mode,
    )


def parse_literature_text(text: str, use_llm: bool = False) -> str:
    try:
        from .science_core import parse_literature_text as science_parse_text
    except ImportError:
        from science_core import parse_literature_text as science_parse_text
    return science_parse_text(text, use_llm)


def build_coverage_matrix(project_id: str) -> str:
    try:
        from .science_core import build_coverage_matrix as science_matrix
    except ImportError:
        from science_core import build_coverage_matrix as science_matrix
    return science_matrix(project_id)


def detect_knowledge_gaps(project_id: str, max_gaps: int = 10) -> str:
    try:
        from .science_core import detect_knowledge_gaps as science_gaps
    except ImportError:
        from science_core import detect_knowledge_gaps as science_gaps
    return science_gaps(project_id, max_gaps)


def run_tanxi_gap_exploration(
    project_id: str,
    target_domain: str = "",
    strategic_domains: list[str] | None = None,
    max_gaps: int = 10,
) -> str:
    try:
        from .science_core import run_tanxi_gap_exploration as science_tanxi
    except ImportError:
        from science_core import run_tanxi_gap_exploration as science_tanxi
    return science_tanxi(project_id, target_domain, strategic_domains, max_gaps)


def evolve_domain_subspaces(
    project_id: str,
    subspace_map_id: str = "",
    max_actions: int = 10,
) -> str:
    try:
        from .science_core import evolve_domain_subspaces as science_evolve_subspaces
    except ImportError:
        from science_core import evolve_domain_subspaces as science_evolve_subspaces
    return science_evolve_subspaces(project_id, subspace_map_id, max_actions)


def build_temporal_knowledge_graph(project_id: str) -> str:
    try:
        from .science_core import build_temporal_knowledge_graph as science_temporal_kg
    except ImportError:
        from science_core import build_temporal_knowledge_graph as science_temporal_kg
    return science_temporal_kg(project_id)


def detect_structural_knowledge_gaps(project_id: str, max_gaps: int = 10) -> str:
    try:
        from .science_core import detect_structural_knowledge_gaps as science_structural_gaps
    except ImportError:
        from science_core import detect_structural_knowledge_gaps as science_structural_gaps
    return science_structural_gaps(project_id, max_gaps)


def find_structural_analogy_transfers(
    project_id: str,
    target_scenario: str = "",
    threshold: float = 0.55,
    max_results: int = 10,
) -> str:
    try:
        from .science_core import find_structural_analogy_transfers as science_analogies
    except ImportError:
        from science_core import find_structural_analogy_transfers as science_analogies
    return science_analogies(project_id, target_scenario, threshold, max_results)


def run_mingli_hypothesis_evolution(
    project_id: str,
    gap_ids: list[str] | None = None,
    population_size: int = 24,
    generations: int = 4,
    top_k: int = 5,
    use_llm: bool = False,
) -> str:
    try:
        from .science_core import run_mingli_hypothesis_evolution as science_mingli
    except ImportError:
        from science_core import run_mingli_hypothesis_evolution as science_mingli
    return science_mingli(project_id, gap_ids, population_size, generations, top_k, use_llm)


def create_hypothesis(
    project_id: str,
    gap_id: str,
    statement: str,
    mechanism: str,
    expected_value: str,
    test_plan: str,
) -> str:
    try:
        from .science_core import create_hypothesis as science_hypothesis
    except ImportError:
        from science_core import create_hypothesis as science_hypothesis
    return science_hypothesis(project_id, gap_id, statement, mechanism, expected_value, test_plan)


def run_mechanism_check(
    project_id: str,
    hypothesis_id: str,
    shifted_conditions: list[str] | None = None,
) -> str:
    try:
        from .science_core import run_mechanism_check as science_check
    except ImportError:
        from science_core import run_mechanism_check as science_check
    return science_check(project_id, hypothesis_id, shifted_conditions)


def export_research_plan(project_id: str) -> str:
    try:
        from .science_core import export_research_plan as science_export
    except ImportError:
        from science_core import export_research_plan as science_export
    return science_export(project_id)


BASIC_TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Run slow commands asynchronously and notify later.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional workspace-relative working directory.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file from the workspace. Files under v8/tool_results "
            "are returned as bounded previews only; prefer the original tool/search id "
            "instead of recursively reading tool result artifacts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "limit": {
                    "type": "integer",
                    "description": "Optional maximum number of lines to read.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional workspace-relative working directory.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a UTF-8 text file in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "content": {"type": "string", "description": "New file content."},
                "cwd": {
                    "type": "string",
                    "description": "Optional workspace-relative working directory.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace the first exact occurrence of text in a workspace file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "old_text": {"type": "string", "description": "Text to replace."},
                "new_text": {"type": "string", "description": "Replacement text."},
                "cwd": {
                    "type": "string",
                    "description": "Optional workspace-relative working directory.",
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "glob",
        "description": "Find workspace files using a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, for example '**/*.py'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of paths to return.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional workspace-relative working directory.",
                },
            },
            "required": ["pattern"],
        },
    },
]

TODO_TOOL = {
    "name": "todo_write",
    "description": "Replace the current session todo list with lightweight planning items.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "Todo items with content, status, priority, and optional id.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Todo text."},
                        "status": {
                            "type": "string",
                            "description": "pending, in_progress, or completed.",
                        },
                        "priority": {
                            "type": "string",
                            "description": "low, medium, or high.",
                        },
                        "id": {"type": "string", "description": "Optional stable id."},
                    },
                    "required": ["content"],
                },
            }
        },
        "required": ["items"],
    },
}

TASK_TOOL = {
    "name": "task",
    "description": (
        "Delegate a one-shot subtask to an isolated sub-agent. "
        "Use this for investigation or analysis that can return a final summary."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "The subtask goal and expected deliverable.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Run the sub-agent asynchronously and notify later.",
            }
        },
        "required": ["description"],
    },
}

LOAD_SKILL_TOOL = {
    "name": "load_skill",
    "description": "Load the full instructions for a skill listed in the system prompt catalog.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name to load."},
        },
        "required": ["name"],
    },
}

COMPACT_TOOL = {
    "name": "compact",
    "description": (
        "Request context compaction when the conversation history is getting too large. "
        "Use focus to preserve the most important topic."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "focus": {
                "type": "string",
                "description": "Optional area that the summary should preserve.",
            }
        },
        "required": [],
    },
}

TASK_TOOLS = [
    {
        "name": "create_task",
        "description": "Create a persistent DAG task with optional dependencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Short task title."},
                "description": {"type": "string", "description": "Detailed task context."},
                "blockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task ids that must be completed first.",
                },
            },
            "required": ["subject", "description"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List persistent tasks and their DAG state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_completed": {
                    "type": "boolean",
                    "description": "Whether completed tasks should be included.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_task",
        "description": "Read one persistent task by id.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task id."}},
            "required": ["task_id"],
        },
    },
    {
        "name": "claim_task",
        "description": "Claim a pending task if all dependencies are completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id."},
                "owner": {"type": "string", "description": "Agent or worker name."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a task completed and report newly unblocked downstream tasks.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task id."}},
            "required": ["task_id"],
        },
    },
]

TEAM_TOOLS = [
    {
        "name": "spawn_teammate",
        "description": "Start a named teammate agent in a background idle loop.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Stable teammate name."},
                "task": {
                    "type": "string",
                    "description": "Optional initial task to place in the teammate inbox.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "send_message",
        "description": "Send a mailbox message from Lead to another agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Target teammate or lead."},
                "content": {"type": "string", "description": "Message body."},
                "type": {
                    "type": "string",
                    "description": "Message type, usually message/result.",
                },
            },
            "required": ["to", "content"],
        },
    },
    {
        "name": "check_inbox",
        "description": "Read and clear an agent mailbox, routing Lead protocol responses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent name. Defaults to lead.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "request_shutdown",
        "description": "Ask a teammate to stop gracefully through the protocol state machine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "teammate": {"type": "string", "description": "Target teammate name."},
                "reason": {"type": "string", "description": "Optional shutdown reason."},
            },
            "required": ["teammate"],
        },
    },
    {
        "name": "request_plan",
        "description": "Ask a teammate to submit a plan for Lead approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "teammate": {"type": "string", "description": "Target teammate name."},
                "prompt": {"type": "string", "description": "Planning request."},
            },
            "required": ["teammate", "prompt"],
        },
    },
    {
        "name": "review_plan",
        "description": "Approve or reject a teammate plan approval request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "Protocol request id."},
                "approve": {"type": "boolean", "description": "Whether to approve the plan."},
                "feedback": {"type": "string", "description": "Optional feedback."},
            },
            "required": ["request_id", "approve"],
        },
    },
]

WORKTREE_TOOLS = [
    {
        "name": "create_worktree",
        "description": "Create an isolated git worktree. If git worktree fails, use the configured lightweight fallback snapshot instead of copying the whole workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Safe worktree name."},
                "task_id": {"type": "string", "description": "Optional task id to bind."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_worktree",
        "description": "Remove an isolated worktree after safety checks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Worktree name."},
                "discard_changes": {
                    "type": "boolean",
                    "description": "Allow deleting uncommitted or non-empty worktrees.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "keep_worktree",
        "description": "Keep a worktree and record an audit event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Worktree name."},
                "reason": {"type": "string", "description": "Why the worktree is kept."},
            },
            "required": ["name"],
        },
    },
]

MCP_TOOLS = [
    {
        "name": "connect_mcp",
        "description": "Connect a mock MCP server and expose its tools dynamically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Server name, for example docs, memory, or any custom echo server.",
                }
            },
            "required": ["name"],
        },
    }
]

CRON_TOOLS = [
    {
        "name": "schedule_cron",
        "description": "Schedule a prompt to be delivered by a five-field cron expression.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cron": {
                    "type": "string",
                    "description": "Five-field cron expression: minute hour day month weekday.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Prompt to inject when the schedule fires.",
                },
                "recurring": {
                    "type": "boolean",
                    "description": "Whether the job repeats. Defaults to true.",
                },
                "durable": {
                    "type": "boolean",
                    "description": "Persist to disk across process restarts. Defaults to true.",
                },
            },
            "required": ["cron", "prompt"],
        },
    },
    {
        "name": "list_crons",
        "description": "List scheduled cron jobs.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "cancel_cron",
        "description": "Cancel a scheduled cron job by id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Cron job id."},
            },
            "required": ["job_id"],
        },
    },
]

SCIENCE_TOOLS = [
    {
        "name": "create_research_project",
        "description": "Create a persistent AI-for-Science research project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Project title."},
                "domain": {"type": "string", "description": "Scientific domain."},
                "objective": {"type": "string", "description": "Research objective."},
                "strategic_need": {"type": "string", "description": "Optional strategic or application need."},
            },
            "required": ["title", "domain", "objective"],
        },
    },
    {
        "name": "list_research_projects",
        "description": "List persistent AI-for-Science research projects.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_research_project",
        "description": "Read one AI-for-Science research project by id.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "Science project id."}},
            "required": ["project_id"],
        },
    },
    {
        "name": "list_science_agents",
        "description": "List the Qwen-Zhikan science specialist agent roles.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_science_agent_prompt",
        "description": "Get a concise role prompt for a Qwen-Zhikan science agent.",
        "input_schema": {
            "type": "object",
            "properties": {"agent": {"type": "string", "description": "Agent name such as boxue, zhizhi, tanxi."}},
            "required": ["agent"],
        },
    },
    {
        "name": "list_literature_providers",
        "description": "List PaperGraph v1 literature provider connectors and placeholder status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "explore_domain_subspaces",
        "description": "Domain Subspace Explorer (DSE): before literature review, decompose a broad scientific domain into substantive subspaces, lightly probe each subspace with live literature search, estimate density/hotness, and produce a query_plan plus user-selection interaction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Broad research domain to map before ZhiZhi retrieval."},
                "max_subspaces": {"type": "integer", "description": "Maximum substantive subspaces to generate, default 12."},
                "probe_depth": {"type": "integer", "description": "Seed-probe result count per subspace, default 5."},
                "use_llm": {"type": "boolean", "description": "Use Qwen/LLM to generate domain subspaces when available."},
                "providers": {"type": "array", "items": {"type": "string"}, "description": "Probe providers, e.g. semantic_scholar, openalex, crossref, arxiv, dblp, openreview, biorxiv, medrxiv, chemrxiv."},
                "user_hints": {"type": "array", "items": {"type": "string"}, "description": "Optional user-supplied subspace hints or priorities."},
            },
            "required": ["domain"],
        },
    },
    {
        "name": "search_literature",
        "description": "Search live literature providers. Results are ranked by text relevance, recency, field-normalized citation impact, journal quartile/venue quality, and quality gates. If total_results is 0, stop and report retrieval failure; do not invent papers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Literature search query."},
                "providers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional providers: semantic_scholar, openalex, crossref, arxiv, dblp, openreview, biorxiv, medrxiv, chemrxiv, google_scholar, springer_nature.",
                },
                "max_results": {"type": "integer", "description": "Maximum provider result blocks."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_literature_stratified",
        "description": "Run a five-level cascade literature search: L0 high-impact review, L1 milestone/high-citation papers, L2 recent top-venue papers, L3 latest preprints/submissions, and L4 regular journal backfill. Results are deduplicated and each has stratified_layer plus _why_selected.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Literature search query."},
                "providers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional providers: semantic_scholar, openalex, crossref, arxiv, dblp, openreview, biorxiv, medrxiv, chemrxiv. Other providers may be placeholders.",
                },
                "max_results": {"type": "integer", "description": "Total stratified result budget, default 15."},
                "domain": {"type": "string", "description": "Optional broad research domain. When provided, the search expands into known sub-branches and applies a pre-import domain relevance gate."},
                "focus_branches": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional user-confirmed or manually supplied sub-branches to prioritize before generic LLM/heuristic expansion.",
                },
                "use_llm": {"type": "boolean", "description": "Use Qwen to generate domain-agnostic sub-branch queries for any scientific field."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_papers",
        "description": "ZhiZhi action alias for targeted literature retrieval. Wraps search_literature while accepting academic database names such as Semantic Scholar, OpenAlex, Crossref, arXiv, DBLP, OpenReview, and Web of Science.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword combination or research topic."},
                "databases": {"type": "array", "items": {"type": "string"}, "description": "Database names, e.g. Semantic Scholar, OpenAlex, Crossref, arXiv, DBLP, OpenReview, Web of Science."},
                "max_results": {"type": "integer", "description": "Maximum results to retrieve."},
                "years": {"type": "string", "description": "Time window, e.g. last 5 years."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_papers_stratified",
        "description": "ZhiZhi action for systematic literature mapping. Uses the five-level cascade: one field-map review, milestone high-citation papers, recent top-venue papers, latest preprints/submissions, then regular journal backfill.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword combination or research topic."},
                "databases": {"type": "array", "items": {"type": "string"}, "description": "Database names, e.g. Semantic Scholar, OpenAlex, Crossref, arXiv, DBLP, OpenReview, Web of Science."},
                "max_results": {"type": "integer", "description": "Total stratified result budget."},
                "years": {"type": "string", "description": "Time window hint, e.g. last 5 years."},
                "domain": {"type": "string", "description": "Broad domain for subfield query expansion and coverage self-check, e.g. Autonomous Grid Control."},
                "focus_branches": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional user-selected priority branches, e.g. demand response, building energy management, MARL.",
                },
                "use_llm": {"type": "boolean", "description": "Use Qwen to generate field-wide subqueries instead of generic fallback templates."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "extract_structured_info",
        "description": "ZhiZhi action for extracting method, scenario, benchmark, contribution/conclusion, limitation, and evidence claim types from paper text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_content": {"type": "string", "description": "Abstract, conclusion, or full paper text."},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "Optional fields to extract."},
                "use_llm": {"type": "boolean", "description": "Use Qwen JSON extraction before heuristic fallback."},
            },
            "required": ["paper_content"],
        },
    },
    {
        "name": "select_literature_result",
        "description": "Select the best cached literature result by relevance_score, using text relevance plus recency and citation-impact components. Use this after search_literature before importing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_id": {"type": "string", "description": "search_id returned by search_literature."},
                "query": {"type": "string", "description": "Optional query override for re-ranking cached results."},
                "top_k": {"type": "integer", "description": "How many ranked candidates to show."},
                "use_llm": {"type": "boolean", "description": "Use a Qwen judge to select among top candidates after rule ranking."},
            },
            "required": ["search_id"],
        },
    },
    {
        "name": "expand_literature_graph",
        "description": "DeepSurvey-style citation graph expansion from one cached seed paper through Semantic Scholar references/citations, then rank with PaperGraph quality gates. Tries arXiv IDs with and without version suffix. If graph edges are empty, optionally falls back to Semantic Scholar keyword expansion and marks fallback_used.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_id": {"type": "string", "description": "Seed search_id returned by search_literature or graph expansion."},
                "result_index": {"type": "integer", "description": "Seed result_index to expand from."},
                "query": {"type": "string", "description": "Optional topic query for ranking expanded papers."},
                "direction": {"type": "string", "description": "references | citations | both."},
                "max_results": {"type": "integer", "description": "Maximum expanded candidates to keep."},
                "use_llm": {"type": "boolean", "description": "Use Qwen judge on the top expanded candidates after rule ranking."},
                "depth": {"type": "integer", "description": "Citation graph depth: 1 for direct neighbors, 2 to expand a few high-quality first-layer papers."},
                "second_layer_top_k": {"type": "integer", "description": "When depth=2, expand only this many strongest first-layer papers to control graph growth."},
                "allow_fallback": {"type": "boolean", "description": "When true, use keyword fallback if citation edges are empty or the seed is not indexed. Set false for strict citation-graph verification."},
            },
            "required": ["search_id"],
        },
    },
    {
        "name": "build_literature_relation_graph",
        "description": "Build a mechanism lineage graph from cached search or graph-expansion results. Produces nodes, citation/relevance edges, mechanism clusters, PageRank centrality, and representative papers for claim-citation verification.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_id": {"type": "string", "description": "search_id or graph_search_id returned by search_literature/expand_literature_graph."},
                "query": {"type": "string", "description": "Optional topic query used for mechanism term extraction."},
                "max_nodes": {"type": "integer", "description": "Maximum papers to include in the graph."},
                "min_quality": {"type": "number", "description": "Optional publication_quality_score floor; use 0.55+ to exclude weak/noisy papers."},
                "max_clusters": {"type": "integer", "description": "Maximum mechanism clusters after merging singleton clusters; default 8."},
            },
            "required": ["search_id"],
        },
    },
    {
        "name": "create_science_pipeline_tasks",
        "description": "Create persistent DAG tasks for the full AI Scientist research pipeline.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "Science project id."}},
            "required": ["project_id"],
        },
    },
    {
        "name": "create_science_delegation_tasks",
        "description": "Create a subagent-friendly science DAG for long workflows: parallel branch scout tasks produce retrieval artifacts, then a synthesis gate serializes PaperGraph imports before TanXi/MingLi continue. Use this instead of one giant run_zhizhi_literature_analysis when broad retrieval would be brittle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "objective": {"type": "string", "description": "Optional delegation objective; defaults to the project objective."},
                "subspace_map_id": {"type": "string", "description": "Optional DSE subspace_map_id returned by explore_domain_subspaces."},
                "selected_subfields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Selected subspace names/ids from the DSE map.",
                },
                "focus_branches": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Manual branch labels/queries when no subspace_map_id is available.",
                },
                "max_branch_tasks": {"type": "integer", "description": "Maximum parallel branch scout tasks to create, default 6."},
                "spawn_teammates": {
                    "type": "boolean",
                    "description": "If true, also spawn one teammate per branch scout. Default false; creating tasks first is safer.",
                },
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "create_boxue_delegation_tasks",
        "description": "Implement Boxue's Chief Research Scheduler prompt as a persistent multi-agent DAG: assign role-bound tasks to ZhiZhi, TanXi, MingLi, DuZhi, BianLun, YanZhen, GeWu, CodeEngineer, MingBian, PaperWriter, Reviewer, and Boxue final synthesis, each with dependencies, deliverables, acceptance criteria, and risks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "goal": {"type": "string", "description": "Optional round goal; defaults to the project objective."},
                "phases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional subset of phases, e.g. Gap Discovery, Hypothesis Generation, Socratic Debate.",
                },
                "max_steps": {"type": "integer", "description": "Maximum Boxue delegation steps, default 20, capped at 25."},
                "spawn_teammates": {
                    "type": "boolean",
                    "description": "If true, spawn teammates for currently unblocked specialist tasks. Default false.",
                },
                "max_parallel_agents": {"type": "integer", "description": "Maximum teammates to spawn for unblocked tasks, default 3."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "run_boxue_research_round",
        "description": "Run Boxue's automatic scheduling loop for one bounded research round: create the Boxue DAG, spawn currently unblocked specialist teammates, monitor lead inbox and the task board, review completed deliverables, create revision tasks for failed or stalled items, and return Boxue synthesis/finalization status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "goal": {"type": "string", "description": "Round goal; defaults to the project objective."},
                "phases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional subset of phases, e.g. Gap Discovery, Hypothesis Generation, Socratic Debate.",
                },
                "spawn_teammates": {
                    "type": "boolean",
                    "description": "If true, start currently unblocked specialist teammates. Default true.",
                },
                "plan_id": {
                    "type": "string",
                    "description": "Optional existing boxue_delegation_plan_id to continue; if omitted, an active unfinished plan is reused or a new one is created.",
                },
                "execution_mode": {
                    "type": "string",
                    "description": "async starts/monitors teammate tasks; pipeline executes ZhiZhi -> TanXi -> MingLi directly in dependency order for a one-call closed loop.",
                },
                "max_steps": {"type": "integer", "description": "Maximum Boxue DAG steps to create, default 20, capped at 25."},
                "max_parallel_agents": {"type": "integer", "description": "Maximum concurrently spawned specialists, default 3."},
                "max_runtime_seconds": {
                    "type": "integer",
                    "description": "How long this coordinator call should monitor before returning, default 45 seconds.",
                },
                "poll_interval_seconds": {"type": "number", "description": "Task-board/inbox polling interval, default 2 seconds."},
                "revision_after_seconds": {
                    "type": "integer",
                    "description": "Create a revision task when an in-progress task is stale for this many seconds, default 600.",
                },
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "build_knowledge_map",
        "description": "ZhiZhi action for building a benchmark-aware method-scenario-benchmark knowledge map from project PaperGraph evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "dimension": {"type": "string", "description": "Usually method-scenario-benchmark."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "add_literature_evidence",
        "description": "Add one structured PaperGraph evidence record to a science project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "title": {"type": "string", "description": "Paper title."},
                "citation": {"type": "string", "description": "Citation or paper identifier."},
                "method": {"type": "string", "description": "Core method category."},
                "scenario": {"type": "string", "description": "Application scenario."},
                "benchmark": {"type": "string", "description": "Dataset or benchmark."},
                "contribution": {"type": "string", "description": "Core contribution."},
                "limitation": {"type": "string", "description": "Documented limitation."},
                "url": {"type": "string", "description": "Optional URL."},
            },
            "required": ["project_id", "title", "citation", "method", "scenario", "benchmark", "contribution", "limitation"],
        },
    },
    {
        "name": "import_literature_text",
        "description": "Import a paper from pasted abstract/full text into PaperGraph and auto-extract evidence fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "title": {"type": "string", "description": "Paper title."},
                "citation": {"type": "string", "description": "Citation or paper identifier."},
                "text": {"type": "string", "description": "Abstract, conclusion, or full text."},
                "provider": {"type": "string", "description": "Source provider."},
                "source_type": {"type": "string", "description": "abstract | conclusion | full_text | metadata."},
                "url": {"type": "string", "description": "Optional URL."},
                "doi": {"type": "string", "description": "Optional DOI."},
                "arxiv_id": {"type": "string", "description": "Optional arXiv id."},
                "semantic_scholar_id": {"type": "string", "description": "Optional Semantic Scholar id."},
                "authors": {"type": "array", "items": {"type": "string"}, "description": "Optional authors."},
                "year": {"type": "string", "description": "Optional publication year."},
                "venue": {"type": "string", "description": "Optional venue."},
                "use_llm": {"type": "boolean", "description": "Use Qwen/LLM JSON extraction before heuristic fallback."},
            },
            "required": ["project_id", "text"],
        },
    },
    {
        "name": "import_literature_file",
        "description": "Import a workspace text/PDF file into PaperGraph. PDF extraction uses optional pypdf and mines limitations/future-work/open-problem sections into gap_signals for TanXi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "path": {"type": "string", "description": "Workspace-relative literature text/PDF path."},
                "title": {"type": "string", "description": "Optional title."},
                "citation": {"type": "string", "description": "Optional citation."},
                "provider": {"type": "string", "description": "Optional provider."},
                "source_type": {"type": "string", "description": "Optional source type."},
                "use_llm": {"type": "boolean", "description": "Use Qwen/LLM JSON extraction after text/PDF extraction."},
            },
            "required": ["project_id", "path"],
        },
    },
    {
        "name": "import_literature_search_result",
        "description": "Import one real paper from a cached search_literature result by search_id and result_index. Fails if the search has no retrieved papers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "search_id": {"type": "string", "description": "search_id returned by search_literature."},
                "result_index": {"type": "integer", "description": "Zero-based result_index returned by search_literature."},
                "use_llm": {"type": "boolean", "description": "Use Qwen/LLM extraction on the result abstract before importing."},
            },
            "required": ["project_id", "search_id"],
        },
    },
    {
        "name": "extract_paper_keynote",
        "description": "Extract and store a DeepSurvey-style structured keynote from an imported paper, cached search result, or supplied text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "paper_id": {"type": "string", "description": "Optional PaperGraph paper_id."},
                "search_id": {"type": "string", "description": "Optional cached search_id."},
                "result_index": {"type": "integer", "description": "Search result index when search_id is provided."},
                "text": {"type": "string", "description": "Optional raw paper text or abstract."},
                "use_llm": {"type": "boolean", "description": "Use Qwen to extract a full keynote before heuristic fallback."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "import_papergraph_record",
        "description": "Import a fully structured PaperGraph record with credibility scoring and citation uniqueness check.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "title": {"type": "string", "description": "Paper title."},
                "citation": {"type": "string", "description": "Citation or paper identifier."},
                "authors": {"type": "array", "items": {"type": "string"}},
                "year": {"type": "string"},
                "venue": {"type": "string"},
                "provider": {"type": "string"},
                "source_type": {"type": "string"},
                "doi": {"type": "string"},
                "arxiv_id": {"type": "string"},
                "semantic_scholar_id": {"type": "string"},
                "url": {"type": "string"},
                "abstract": {"type": "string"},
                "full_text_excerpt": {"type": "string"},
                "conclusion": {"type": "string"},
                "strengths": {"type": "array", "items": {"type": "string"}},
                "improvements": {"type": "array", "items": {"type": "string"}},
                "method": {"type": "string"},
                "scenario": {"type": "string"},
                "benchmark": {"type": "string"},
                "contribution": {"type": "string"},
                "limitation": {"type": "string"},
            },
            "required": ["project_id", "title", "citation"],
        },
    },
    {
        "name": "list_papergraph_records",
        "description": "List imported PaperGraph records for a science project.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "Science project id."}},
            "required": ["project_id"],
        },
    },
    {
        "name": "verify_citation_uniqueness",
        "description": "Check whether a citation/DOI/arXiv/title key is already present in a project's PaperGraph.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "title": {"type": "string"},
                "citation": {"type": "string"},
                "doi": {"type": "string"},
                "arxiv_id": {"type": "string"},
                "semantic_scholar_id": {"type": "string"},
                "url": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "assess_novelty",
        "description": "ZhiZhi action for assessing a knowledge gap across academic novelty, application value, and implementation feasibility.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "gap": {"description": "Gap object or gap description string."},
                "dimensions": {"type": "array", "items": {"type": "string"}, "description": "Assessment dimensions."},
            },
            "required": ["project_id", "gap"],
        },
    },
    {
        "name": "verify_uniqueness",
        "description": "ZhiZhi action for checking whether a proposed research idea overlaps with imported project literature, optionally with live literature search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "idea": {"type": "string", "description": "Research idea or gap to verify."},
                "precision": {"type": "string", "description": "high | medium."},
                "live_search": {"type": "boolean", "description": "If true, also run live literature search."},
                "providers": {"type": "array", "items": {"type": "string"}, "description": "Optional providers for live search."},
            },
            "required": ["project_id", "idea"],
        },
    },
    {
        "name": "run_zhizhi_literature_analysis",
        "description": "Run Agent 1 ZhiZhi end-to-end: retrieve papers, select seed, import PaperGraph evidence, extract keynotes, expand citation graph, build benchmark-aware knowledge map, detect gaps, assess novelty/value/feasibility, verify overlap, and return TAO JSON.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "domain": {"type": "string", "description": "Research domain."},
                "query": {"type": "string", "description": "Literature query."},
                "max_results": {"type": "integer", "description": "Maximum retrieval results."},
                "years": {"type": "string", "description": "Time window."},
                "providers": {"type": "array", "items": {"type": "string"}, "description": "Providers/databases."},
                "import_top_k": {
                    "type": "integer",
                    "description": "How many papers to import; default 15. ZhiZhi uses layer-minimum import across L0 review, L1 milestone, L2 top latest, L3 preprint, and L4 regular before score backfill.",
                },
                "graph_depth": {"type": "integer", "description": "Citation graph depth, 1 or 2."},
                "use_llm": {"type": "boolean", "description": "Use Qwen for extraction/judging when available."},
                "focus_branches": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional user-confirmed branches for supplemental retrieval. Use this after user_interaction reports missing branches.",
                },
                "live_coverage_check": {"type": "boolean", "description": "Run small live Semantic Scholar/arXiv probes for missing sub-branches to detect false-negative retrieval gaps."},
                "subspace_map_id": {"type": "string", "description": "Optional DSE subspace_map_id returned by explore_domain_subspaces."},
                "selected_subfields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "User-selected subspace names/ids from the DSE map. Custom user-entered subspaces are allowed and become custom retrieval branches.",
                },
                "interactive_mode": {"type": "boolean", "description": "If true and no subspace_map_id is provided, stop after DSE and return user_interaction before importing papers."},
            },
            "required": ["project_id", "domain", "query"],
        },
    },
    {
        "name": "parse_literature_text",
        "description": "Parse pasted literature text into abstract, conclusion, strengths, improvements, method, scenario, benchmark, contribution, and limitation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Literature text to parse."},
                "use_llm": {"type": "boolean", "description": "Use Qwen/LLM JSON extraction before heuristic fallback."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "build_coverage_matrix",
        "description": "Build method-scenario coverage matrix from project evidence.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "Science project id."}},
            "required": ["project_id"],
        },
    },
    {
        "name": "detect_knowledge_gaps",
        "description": "Detect candidate knowledge gaps from a project's PaperGraph coverage matrix plus PDF/full-text gap_signals, then enrich/rank them with TanXi-style density holes, migration pairs, suspended problems, and strategic alignment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "max_gaps": {"type": "integer", "description": "Maximum gaps to return."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "run_tanxi_gap_exploration",
        "description": "Run Agent 2 TanXi gap discovery on an existing PaperGraph/knowledge map: coverage density scanning, cross-disciplinary unconnected pairs, suspended problem detection, strategic-need alignment, and ranked gap prioritization.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "target_domain": {"type": "string", "description": "Target research domain for density and strategic interpretation."},
                "strategic_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional strategic domains, e.g. carbon neutrality, health, energy, food security, AI for Science.",
                },
                "max_gaps": {"type": "integer", "description": "Maximum ranked gaps to return."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "evolve_domain_subspaces",
        "description": "Dynamic Subspace Evolution: update subspace metrics, detect fission/fusion/decline/emergent signals, and produce proposed subspace adjustments before MingLi hypothesis generation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "subspace_map_id": {"type": "string", "description": "Optional DSE subspace_map_id returned by explore_domain_subspaces."},
                "max_actions": {"type": "integer", "description": "Maximum proposed evolution actions to return."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "build_temporal_knowledge_graph",
        "description": "Build a temporal knowledge graph from PaperGraph triples: method, scenario, benchmark, year, citations, lifecycle status, and hotspot predictions.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "Science project id."}},
            "required": ["project_id"],
        },
    },
    {
        "name": "detect_structural_knowledge_gaps",
        "description": "Detect structural gaps in the PaperGraph concept topology: isolated/low-degree nodes, bottlenecks, and missing community bridges.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "max_gaps": {"type": "integer", "description": "Maximum structural gaps to return."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "find_structural_analogy_transfers",
        "description": "Find cross-domain transfer opportunities by encoding scenarios as problem-structure vectors and matching structurally similar but semantically distant scenarios.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "target_scenario": {"type": "string", "description": "Optional scenario to search analogs for; empty scans all scenarios."},
                "threshold": {"type": "number", "description": "Minimum structural similarity, default 0.55."},
                "max_results": {"type": "integer", "description": "Maximum analogy transfers to return."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "run_mingli_hypothesis_evolution",
        "description": "Agent 4 MingLi v1: generate seed hypotheses from validated gaps, run tournament selection plus mutation/crossover, score novelty/plausibility/grounding/testability/impact, and persist top hypotheses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "gap_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional specific gap ids; omit to use top-ranked gaps."},
                "population_size": {"type": "integer", "description": "Initial hypothesis population size."},
                "generations": {"type": "integer", "description": "Tournament evolution generations."},
                "top_k": {"type": "integer", "description": "Number of final hypotheses to persist."},
                "use_llm": {"type": "boolean", "description": "Reserved for future LLM seed generation; v1 uses auditable templates."},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "create_hypothesis",
        "description": "Create a research hypothesis linked to a detected knowledge gap.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "gap_id": {"type": "string", "description": "Knowledge gap id."},
                "statement": {"type": "string", "description": "Hypothesis statement."},
                "mechanism": {"type": "string", "description": "Claimed causal mechanism."},
                "expected_value": {"type": "string", "description": "Expected scientific or application value."},
                "test_plan": {"type": "string", "description": "Initial validation plan."},
            },
            "required": ["project_id", "gap_id", "statement", "mechanism", "expected_value", "test_plan"],
        },
    },
    {
        "name": "run_mechanism_check",
        "description": "Run a lightweight CAWM-style mechanism fidelity check for a hypothesis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Science project id."},
                "hypothesis_id": {"type": "string", "description": "Hypothesis id."},
                "shifted_conditions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional regime-shift conditions.",
                },
            },
            "required": ["project_id", "hypothesis_id"],
        },
    },
    {
        "name": "export_research_plan",
        "description": "Export the current project as a standard science hypothesis and research plan.",
        "input_schema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "Science project id."}},
            "required": ["project_id"],
        },
    },
]

TOOLS = (
    BASIC_TOOLS
    + [TODO_TOOL, TASK_TOOL, LOAD_SKILL_TOOL, COMPACT_TOOL]
    + TASK_TOOLS
    + TEAM_TOOLS
    + WORKTREE_TOOLS
    + MCP_TOOLS
    + CRON_TOOLS
    + SCIENCE_TOOLS
)

TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "bash": bash,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "glob": glob,
    "todo_write": todo_write,
    "task": task,
    "spawn_subagent": spawn_subagent,
    "load_skill": load_skill,
    "compact": compact,
    "create_task": create_task,
    "list_tasks": list_tasks,
    "get_task": get_task,
    "claim_task": claim_task,
    "complete_task": complete_task,
    "spawn_teammate": spawn_teammate,
    "send_message": send_message,
    "check_inbox": check_inbox,
    "request_shutdown": request_shutdown,
    "request_plan": request_plan,
    "review_plan": review_plan,
    "create_worktree": create_worktree,
    "remove_worktree": remove_worktree,
    "keep_worktree": keep_worktree,
    "connect_mcp": connect_mcp,
    "schedule_cron": schedule_cron,
    "list_crons": list_crons,
    "cancel_cron": cancel_cron,
    "create_research_project": create_research_project,
    "list_research_projects": list_research_projects,
    "get_research_project": get_research_project,
    "list_science_agents": list_science_agents,
    "get_science_agent_prompt": get_science_agent_prompt,
    "list_literature_providers": list_literature_providers,
    "explore_domain_subspaces": explore_domain_subspaces,
    "search_literature": search_literature,
    "search_literature_stratified": search_literature_stratified,
    "search_papers": search_papers,
    "search_papers_stratified": search_papers_stratified,
    "extract_structured_info": extract_structured_info,
    "select_literature_result": select_literature_result,
    "expand_literature_graph": expand_literature_graph,
    "build_literature_relation_graph": build_literature_relation_graph,
    "create_science_pipeline_tasks": create_science_pipeline_tasks,
    "create_science_delegation_tasks": create_science_delegation_tasks,
    "create_boxue_delegation_tasks": create_boxue_delegation_tasks,
    "run_boxue_research_round": run_boxue_research_round,
    "build_knowledge_map": build_knowledge_map,
    "add_literature_evidence": add_literature_evidence,
    "import_literature_text": import_literature_text,
    "import_literature_file": import_literature_file,
    "import_literature_search_result": import_literature_search_result,
    "extract_paper_keynote": extract_paper_keynote,
    "import_papergraph_record": import_papergraph_record,
    "list_papergraph_records": list_papergraph_records,
    "verify_citation_uniqueness": verify_citation_uniqueness,
    "assess_novelty": assess_novelty,
    "verify_uniqueness": verify_uniqueness,
    "run_zhizhi_literature_analysis": run_zhizhi_literature_analysis,
    "parse_literature_text": parse_literature_text,
    "build_coverage_matrix": build_coverage_matrix,
    "detect_knowledge_gaps": detect_knowledge_gaps,
    "run_tanxi_gap_exploration": run_tanxi_gap_exploration,
    "evolve_domain_subspaces": evolve_domain_subspaces,
    "build_temporal_knowledge_graph": build_temporal_knowledge_graph,
    "detect_structural_knowledge_gaps": detect_structural_knowledge_gaps,
    "find_structural_analogy_transfers": find_structural_analogy_transfers,
    "run_mingli_hypothesis_evolution": run_mingli_hypothesis_evolution,
    "create_hypothesis": create_hypothesis,
    "run_mechanism_check": run_mechanism_check,
    "export_research_plan": export_research_plan,
}
