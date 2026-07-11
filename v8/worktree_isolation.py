from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

try:
    from .config import (
        WORKDIR,
        WORKTREES_DIR,
        WORKTREE_EVENTS,
        WORKTREE_FALLBACK_MODE,
        WORKTREE_SPARSE_CHECKOUT,
        WORKTREE_SNAPSHOT_INCLUDE_PREFIXES,
        WORKTREE_SNAPSHOT_MAX_FILE_BYTES,
    )
    from .log import log_event
except ImportError:
    from config import (
        WORKDIR,
        WORKTREES_DIR,
        WORKTREE_EVENTS,
        WORKTREE_FALLBACK_MODE,
        WORKTREE_SPARSE_CHECKOUT,
        WORKTREE_SNAPSHOT_INCLUDE_PREFIXES,
        WORKTREE_SNAPSHOT_MAX_FILE_BYTES,
    )
    from log import log_event


WORKTREE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def create_worktree(name: str, task_id: str = "") -> str:
    safe_name = validate_worktree_name(name)
    target = worktree_path(safe_name)
    if target.exists():
        log_worktree_event("exists", safe_name, task_id)
        return f"Worktree {safe_name} already exists at {relative(target)}."

    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    branch = f"agent-{safe_name}"
    if is_git_repo(WORKDIR):
        prune_stale_git_worktrees()
        completed = subprocess.run(
            ["git", "worktree", "add", "-B", branch, str(target), "HEAD"],
            cwd=WORKDIR,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        if completed.returncode == 0:
            mode = "git"
            if WORKTREE_SPARSE_CHECKOUT:
                configure_sparse_git_worktree(target, task_id=task_id)
                copied, skipped = overlay_filtered_snapshot(target, task_id=task_id, mode="git-sparse-overlay")
                log_event("WORKTREE", "sparse_overlay", name=safe_name, copied=copied, skipped=skipped)
                mode = "git-sparse"
        else:
            mode = create_fallback_worktree(target, task_id=task_id)
            log_event(
                "WARN",
                "worktree_git_fallback",
                name=safe_name,
                mode=mode,
                error=(completed.stderr or completed.stdout or "unknown error").strip(),
            )
    else:
        mode = create_fallback_worktree(target, task_id=task_id)

    log_event("WORKTREE", "created", name=safe_name, task_id=task_id, mode=mode, path=target)
    log_worktree_event("created", safe_name, task_id, mode=mode, path=str(target))
    return f"Created {mode} worktree {safe_name} at {relative(target)}."


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    safe_name = validate_worktree_name(name)
    target = worktree_path(safe_name)
    if not target.exists():
        return f"Worktree {safe_name} does not exist."
    if not target.resolve().is_relative_to(WORKTREES_DIR):
        raise ValueError(f"Refusing to remove outside worktree root: {target}")

    if is_git_worktree(target):
        dirty = git_status(target)
        if dirty and not discard_changes:
            return (
                f"Worktree {safe_name} has uncommitted changes. "
                "Pass discard_changes=true to remove it."
            )
        completed = subprocess.run(
            ["git", "worktree", "remove", "--force" if discard_changes else str(target), str(target)]
            if discard_changes
            else ["git", "worktree", "remove", str(target)],
            cwd=WORKDIR,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "git worktree remove failed: "
                + (completed.stderr or completed.stdout or "unknown error").strip()
            )
    else:
        if any(target.iterdir()) and not discard_changes:
            return (
                f"Worktree {safe_name} is not empty. "
                "Pass discard_changes=true to remove it."
            )
        remove_directory_tree(target)

    log_event("WORKTREE", "removed", name=safe_name, discard_changes=discard_changes)
    log_worktree_event("removed", safe_name, discard_changes=discard_changes)
    return f"Removed worktree {safe_name}."


def keep_worktree(name: str, reason: str = "") -> str:
    safe_name = validate_worktree_name(name)
    target = worktree_path(safe_name)
    if not target.exists():
        return f"Worktree {safe_name} does not exist."
    log_event("WORKTREE", "kept", name=safe_name, reason=reason)
    log_worktree_event("kept", safe_name, reason=reason)
    return f"Kept worktree {safe_name} at {relative(target)}."


def resolve_worktree_cwd(name: str) -> Path:
    safe_name = validate_worktree_name(name)
    target = worktree_path(safe_name)
    if not target.exists():
        raise ValueError(f"Worktree does not exist: {safe_name}")
    return target.resolve()


def worktree_path(name: str) -> Path:
    safe_name = validate_worktree_name(name)
    return (WORKTREES_DIR / safe_name).resolve()


def validate_worktree_name(name: str) -> str:
    value = str(name).strip()
    if not WORKTREE_NAME_RE.fullmatch(value) or value in {".", ".."} or ".." in value.split("."):
        raise ValueError("Worktree name must match [A-Za-z0-9._-]{1,64} and not traverse paths.")
    return value


def sanitize_worktree_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name).strip())
    value = value.strip("._-") or "worktree"
    return value[:64]


def is_git_repo(path: Path) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def is_git_worktree(path: Path) -> bool:
    return (path / ".git").exists()


def git_status(path: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=path,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("git status failed: " + (completed.stderr or completed.stdout).strip())
    return completed.stdout.strip()


def prune_stale_git_worktrees() -> None:
    completed = subprocess.run(
        ["git", "worktree", "prune"],
        cwd=WORKDIR,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if completed.returncode != 0:
        log_event("WARN", "worktree_prune_failed", error=(completed.stderr or completed.stdout).strip())


def log_worktree_event(event: str, name: str, task_id: str = "", **data: object) -> None:
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "event": event,
        "name": name,
        "task_id": task_id,
        **data,
    }
    with WORKTREE_EVENTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def remove_directory_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            child.rmdir()
    path.rmdir()


def create_fallback_worktree(target: Path, task_id: str = "") -> str:
    mode = (WORKTREE_FALLBACK_MODE or "filtered").strip().lower()
    if mode in {"minimal", "empty", "none"}:
        create_minimal_worktree(target, reason="configured minimal fallback")
        return "minimal"
    if mode in {"copy", "full", "full_copy"}:
        create_full_snapshot_worktree(target)
        return "snapshot-full"
    try:
        copied, skipped = create_filtered_snapshot_worktree(target, task_id=task_id)
        log_event("WORKTREE", "filtered_snapshot", path=target, copied=copied, skipped=skipped)
        return "snapshot-filtered"
    except Exception as exc:
        log_event("WARN", "filtered_snapshot_failed", path=target, error=exc)
        create_minimal_worktree(target, reason=f"filtered snapshot failed: {exc}")
        return "minimal"


def create_minimal_worktree(target: Path, reason: str = "") -> None:
    target.mkdir(parents=True, exist_ok=False)
    readme = (
        "Minimal agent workspace\n"
        "=======================\n\n"
        "The normal git worktree creation failed, and full workspace copy is disabled to avoid excessive disk use.\n"
        f"Source workspace: {WORKDIR}\n"
        f"Reason: {reason or 'fallback'}\n\n"
        "If this task needs source files, fix git worktree creation or set WORKTREE_FALLBACK_MODE=filtered/copy explicitly.\n"
    )
    (target / "WORKTREE_FALLBACK_README.md").write_text(readme, encoding="utf-8")


def create_filtered_snapshot_worktree(target: Path, task_id: str = "") -> tuple[int, int]:
    target.mkdir(parents=True, exist_ok=False)
    copied, skipped = copy_filtered_snapshot_files(target, task_id=task_id)
    write_snapshot_manifest(
        target,
        copied=copied,
        skipped=skipped,
        mode="filtered",
        include_prefixes=snapshot_include_prefixes(task_id),
    )
    return copied, skipped


def overlay_filtered_snapshot(target: Path, task_id: str = "", mode: str = "overlay") -> tuple[int, int]:
    copied, skipped = copy_filtered_snapshot_files(target, task_id=task_id)
    write_snapshot_manifest(
        target,
        copied=copied,
        skipped=skipped,
        mode=mode,
        include_prefixes=snapshot_include_prefixes(task_id),
    )
    return copied, skipped


def copy_filtered_snapshot_files(target: Path, task_id: str = "") -> tuple[int, int]:
    rel_paths = candidate_snapshot_paths()
    include_prefixes = snapshot_include_prefixes(task_id)
    copied = 0
    skipped = 0
    for rel in rel_paths:
        normalized = normalize_rel(rel)
        if not normalized or not should_include_snapshot_path(normalized, include_prefixes=include_prefixes):
            skipped += 1
            continue
        source = (WORKDIR / normalized).resolve()
        dest = (target / normalized).resolve()
        try:
            if not source.is_file() or not source.is_relative_to(WORKDIR.resolve()) or not dest.is_relative_to(target.resolve()):
                skipped += 1
                continue
            if source.stat().st_size > WORKTREE_SNAPSHOT_MAX_FILE_BYTES:
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            copied += 1
        except OSError:
            skipped += 1
    return copied, skipped


def configure_sparse_git_worktree(target: Path, task_id: str = "") -> None:
    include_prefixes = snapshot_include_prefixes(task_id)
    patterns = sparse_checkout_patterns(include_prefixes)
    if not patterns:
        return
    init = subprocess.run(
        ["git", "sparse-checkout", "init", "--no-cone"],
        cwd=target,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if init.returncode != 0:
        log_event("WARN", "sparse_checkout_init_failed", path=target, error=(init.stderr or init.stdout).strip())
        return
    set_cmd = ["git", "sparse-checkout", "set", "--no-cone", *patterns]
    configured = subprocess.run(
        set_cmd,
        cwd=target,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if configured.returncode != 0:
        log_event("WARN", "sparse_checkout_set_failed", path=target, error=(configured.stderr or configured.stdout).strip())


def sparse_checkout_patterns(include_prefixes: tuple[str, ...]) -> list[str]:
    patterns: list[str] = []
    for prefix in include_prefixes:
        normalized = normalize_rel(prefix)
        if not normalized:
            continue
        source = WORKDIR / normalized
        if source.exists() and source.is_dir():
            patterns.append(f"{normalized}/**")
        else:
            patterns.append(normalized)
    return patterns


def candidate_snapshot_paths() -> list[str]:
    if is_git_repo(WORKDIR):
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=WORKDIR,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        if completed.returncode == 0:
            return [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        log_event("WARN", "snapshot_git_ls_files_failed", error=(completed.stderr or completed.stdout).strip())
    return filesystem_snapshot_paths()


def filesystem_snapshot_paths() -> list[str]:
    paths: list[str] = []
    root = WORKDIR.resolve()
    for path in root.rglob("*"):
        try:
            rel = normalize_rel(str(path.relative_to(root)))
        except ValueError:
            continue
        if path.is_dir():
            continue
        paths.append(rel)
    return paths


SNAPSHOT_IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "tool_results",
    "transcripts",
}

SNAPSHOT_IGNORED_PREFIXES = {
    ".agents",
    ".codex",
    "v1",
    "v2",
    "v3",
    "v4",
    "v5",
    "v6",
    "v7",
    "v1.md",
    "v2.md",
    "v3.md",
    "v4.md",
    "v5.md",
    "v6.md",
    "v7.md",
    "v8/.memory",
    "v8/.science",
    "v8/.scheduled_tasks.json",
    "v8/.tasks",
    "v8/.team",
    "v8/.worktrees",
    "v8/agent.log",
    "v8/tool_results",
    "v8/transcripts",
}


def snapshot_include_prefixes(task_id: str = "") -> tuple[str, ...]:
    prefixes = {normalize_rel(item) for item in WORKTREE_SNAPSHOT_INCLUDE_PREFIXES}
    prefixes.update(task_referenced_snapshot_prefixes(task_id))
    return tuple(sorted(prefix for prefix in prefixes if prefix))


def task_referenced_snapshot_prefixes(task_id: str = "") -> set[str]:
    task_id = str(task_id or "").strip()
    if not task_id:
        return set()
    try:
        from .task_system import load_task
    except ImportError:
        from task_system import load_task

    try:
        task = load_task(task_id)
    except Exception as exc:
        log_event("WARN", "snapshot_task_context_unavailable", task_id=task_id, error=exc)
        return set()

    text = f"{getattr(task, 'subject', '')}\n{getattr(task, 'description', '')}"
    prefixes: set[str] = set()
    for rel in extract_referenced_paths(text):
        top = normalize_rel(Path(rel).parts[0] if Path(rel).parts else rel)
        if top and top not in SNAPSHOT_IGNORED_PREFIXES:
            prefixes.add(top)
    return prefixes


def extract_referenced_paths(text: str) -> set[str]:
    paths: set[str] = set()
    for raw in re.findall(r"(?<![\w:.-])([A-Za-z0-9_.+-]+[\\/][A-Za-z0-9_./\\+-]+)", str(text)):
        cleaned = normalize_rel(raw.strip("`'\".,;:()[]{}"))
        if cleaned:
            paths.add(cleaned)
    for raw in re.findall(r"(?<![\w.-])([A-Za-z0-9_.+-]+\.(?:py|md|json|yaml|yml|toml|txt|js|ts|tsx|jsx|html|css))", str(text)):
        cleaned = normalize_rel(raw.strip("`'\".,;:()[]{}"))
        if cleaned:
            paths.add(cleaned)
    return paths


def path_matches_prefix(rel_path: str, prefix: str) -> bool:
    normalized = normalize_rel(rel_path)
    normalized_prefix = normalize_rel(prefix)
    return normalized == normalized_prefix or normalized.startswith(normalized_prefix.rstrip("/") + "/")


def should_include_snapshot_path(rel_path: str, include_prefixes: tuple[str, ...] | None = None) -> bool:
    normalized = normalize_rel(rel_path)
    if not normalized:
        return False
    prefixes = include_prefixes if include_prefixes is not None else snapshot_include_prefixes()
    if prefixes and not any(path_matches_prefix(normalized, prefix) for prefix in prefixes):
        return False
    parts = set(Path(normalized).parts)
    if parts & SNAPSHOT_IGNORED_PARTS:
        return False
    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in SNAPSHOT_IGNORED_PREFIXES):
        return False
    suffix = Path(normalized).suffix.lower()
    if suffix in {".pyc", ".pyo", ".log", ".tmp", ".zip", ".tar", ".gz", ".7z", ".sqlite", ".db"}:
        return False
    return True


def write_snapshot_manifest(
    target: Path,
    *,
    copied: int,
    skipped: int,
    mode: str,
    include_prefixes: tuple[str, ...] = (),
) -> None:
    manifest = {
        "mode": mode,
        "source": str(WORKDIR),
        "createdAt": time.time(),
        "copied_files": copied,
        "skipped_files": skipped,
        "max_file_bytes": WORKTREE_SNAPSHOT_MAX_FILE_BYTES,
        "include_prefixes": list(include_prefixes),
        "ignored_prefixes": sorted(SNAPSHOT_IGNORED_PREFIXES),
        "note": "Filtered fallback snapshot. Runtime state and large/generated artifacts are intentionally omitted.",
    }
    (target / ".worktree_snapshot.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_rel(path: str) -> str:
    normalized = str(path).strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def create_full_snapshot_worktree(target: Path) -> None:
    target_parent = target.parent.resolve()
    target_parent.mkdir(parents=True, exist_ok=True)
    ignored_roots = {
        ".git",
        ".agents",
        ".codex",
        ".memory",
        ".science",
        ".tasks",
        ".team",
        ".worktrees",
        ".scheduled_tasks.json",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "tool_results",
        "transcripts",
    }

    def ignore(directory: str, names: list[str]) -> set[str]:
        directory_path = Path(directory).resolve()
        ignored: set[str] = set()
        for name in names:
            child = (directory_path / name).resolve()
            if name in ignored_roots:
                ignored.add(name)
                continue
            if child == target or child == target_parent or child.is_relative_to(target_parent):
                ignored.add(name)
        return ignored

    shutil.copytree(WORKDIR, target, ignore=ignore)


def relative(path: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(WORKDIR):
        return str(resolved.relative_to(WORKDIR)).replace("\\", "/")
    return str(resolved)
