"""Module 7 isolated experiment executor.

The preferred backend is Docker because generated research code is untrusted.
For developer machines without Docker, a local subprocess backend is available
with timeout enforcement and (on POSIX) address-space limits. The local backend
is intentionally reported as ``degraded_isolation`` so callers cannot mistake
it for a security boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _read_text(path: Path, limit: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    size = path.stat().st_size
    with path.open("rb") as handle:
        payload = handle.read(max(0, limit))
    text = payload.decode("utf-8", errors="replace")
    truncated = size > len(payload)
    if truncated:
        text += f"\n...[preview truncated; complete log: {path}]"
    return text, truncated


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"Expected result file was not produced: {path.name}"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"Invalid result JSON {path.name}: {exc}"
    if not isinstance(value, dict):
        return None, f"Result JSON root must be an object: {path.name}"
    return value, None


@dataclass(frozen=True)
class SandboxLimits:
    """Resource and isolation policy for one experiment run."""

    backend: str = "auto"  # auto | docker | local
    python_executable: str = sys.executable
    docker_image: str = "qwen-ai-scientist/power-experiment:latest"
    cpu_limit: float = 2.0
    memory_limit_mb: int = 4096
    timeout_seconds: int = 1800
    pids_limit: int = 256
    network_enabled: bool = False
    shm_size_mb: int = 512
    max_log_preview_bytes: int = 200_000
    extra_environment: Mapping[str, str] = field(default_factory=dict)

    def normalized(self) -> "SandboxLimits":
        backend = str(self.backend).strip().lower()
        if backend not in {"auto", "docker", "local"}:
            raise ValueError("backend must be one of: auto, docker, local")
        if self.cpu_limit <= 0:
            raise ValueError("cpu_limit must be positive")
        if self.memory_limit_mb < 128:
            raise ValueError("memory_limit_mb must be at least 128")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        return SandboxLimits(
            backend=backend,
            python_executable=self.python_executable,
            docker_image=self.docker_image,
            cpu_limit=float(self.cpu_limit),
            memory_limit_mb=int(self.memory_limit_mb),
            timeout_seconds=int(self.timeout_seconds),
            pids_limit=max(16, int(self.pids_limit)),
            network_enabled=bool(self.network_enabled),
            shm_size_mb=max(64, int(self.shm_size_mb)),
            max_log_preview_bytes=max(1_000, int(self.max_log_preview_bytes)),
            extra_environment=dict(self.extra_environment),
        )


@dataclass
class SandboxRunResult:
    """Serializable outcome of a sandbox execution."""

    run_id: str
    backend: str
    isolation_level: str
    status: str
    returncode: int
    timed_out: bool
    elapsed_seconds: float
    stdout: str
    stderr: str
    stdout_log: str
    stderr_log: str
    execution_report: str
    result_path: str | None = None
    result_data: dict[str, Any] | None = None
    validation_error: str | None = None
    resource_limits: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return (
            self.status == "success"
            and self.returncode == 0
            and not self.timed_out
            and self.validation_error is None
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SandboxRunner:
    """Run a generated Python project with durable logs and resource limits."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        results_root: str | Path = "results/sandbox_runs",
        limits: SandboxLimits | None = None,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        results = Path(results_root)
        if not results.is_absolute():
            results = self.project_root / results
        self.results_root = results.resolve()
        self.limits = (limits or SandboxLimits()).normalized()
        self.results_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def docker_available(image: str | None = None) -> bool:
        if shutil.which("docker") is None:
            return False
        try:
            info = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if info.returncode != 0:
                return False
            if image:
                inspect = subprocess.run(
                    ["docker", "image", "inspect", image],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                return inspect.returncode == 0
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def run_project(
        self,
        project_dir: str | Path,
        *,
        entry_point: str = "experiment.py",
        expected_result: str = "result.json",
        run_id: str | None = None,
    ) -> SandboxRunResult:
        """Execute a project and validate its standardized result JSON."""

        workspace = Path(project_dir).resolve()
        if not workspace.is_dir():
            raise FileNotFoundError(f"Experiment project directory not found: {workspace}")
        entry = self._safe_relative_path(workspace, entry_point, must_exist=True)
        result_file = self._safe_relative_path(workspace, expected_result, must_exist=False)
        run_id = self._safe_run_id(run_id)
        run_dir = self._unique_run_dir(run_id)
        run_id = run_dir.name
        run_dir.mkdir(parents=True, exist_ok=False)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        report_path = run_dir / "execution_report.json"

        # Preserve the exact code and input contract used for this attempt.
        shutil.copy2(entry, run_dir / entry.name)
        plan_path = workspace / "plan.json"
        if plan_path.exists():
            shutil.copy2(plan_path, run_dir / "plan.json")

        backend = self._select_backend()
        if backend == "docker":
            command, container_name = self._docker_command(workspace, entry.relative_to(workspace))
            isolation = "docker"
        else:
            command = [self.limits.python_executable, str(entry)]
            container_name = None
            isolation = "degraded_isolation"

        started = time.monotonic()
        timed_out = False
        returncode = -1
        launch_error: str | None = None

        env = self._sanitized_environment()
        try:
            with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
                process = subprocess.Popen(
                    command,
                    cwd=str(workspace),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    stdin=subprocess.DEVNULL,
                    env=env if backend == "local" else None,
                    shell=False,
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                    ),
                    preexec_fn=self._local_preexec() if backend == "local" else None,
                )
                try:
                    returncode = process.wait(timeout=self.limits.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._terminate_process_tree(process, container_name)
                    returncode = process.wait(timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            launch_error = f"Sandbox launch failed: {type(exc).__name__}: {exc}"
            stderr_path.write_text(launch_error + "\n", encoding="utf-8")

        elapsed = round(time.monotonic() - started, 6)
        stdout, _ = _read_text(stdout_path, self.limits.max_log_preview_bytes)
        stderr, _ = _read_text(stderr_path, self.limits.max_log_preview_bytes)

        result_data: dict[str, Any] | None = None
        validation_error: str | None = launch_error
        if not validation_error and returncode == 0 and not timed_out:
            result_data, validation_error = _load_json(result_file)

        if timed_out:
            status = "timeout"
            validation_error = (
                f"Experiment exceeded {self.limits.timeout_seconds} seconds"
            )
        elif launch_error:
            status = "launch_failed"
        elif returncode != 0:
            status = "failed"
        elif validation_error:
            status = "invalid_result"
        else:
            status = "success"

        outcome = SandboxRunResult(
            run_id=run_id,
            backend=backend,
            isolation_level=isolation,
            status=status,
            returncode=returncode,
            timed_out=timed_out,
            elapsed_seconds=elapsed,
            stdout=stdout,
            stderr=stderr,
            stdout_log=str(stdout_path),
            stderr_log=str(stderr_path),
            execution_report=str(report_path),
            result_path=str(result_file) if result_file.exists() else None,
            result_data=result_data,
            validation_error=validation_error,
            resource_limits={
                "cpu_limit": self.limits.cpu_limit,
                "memory_limit_mb": self.limits.memory_limit_mb,
                "timeout_seconds": self.limits.timeout_seconds,
                "pids_limit": self.limits.pids_limit,
                "network_enabled": self.limits.network_enabled,
            },
        )
        report_path.write_text(
            json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        return outcome

    def run_code(
        self,
        code: str,
        *,
        expected_result: str = "result.json",
        run_id: str | None = None,
    ) -> SandboxRunResult:
        """Convenience API for a single-file experiment."""

        workspace_id = self._safe_run_id(f"workspace_{run_id or uuid.uuid4().hex[:10]}")
        workspace = self.results_root / "workspaces" / workspace_id
        workspace.mkdir(parents=True, exist_ok=False)
        (workspace / "experiment.py").write_text(code, encoding="utf-8")
        return self.run_project(
            workspace,
            entry_point="experiment.py",
            expected_result=expected_result,
            run_id=run_id,
        )

    def _select_backend(self) -> str:
        backend = self.limits.backend
        available = self.docker_available(self.limits.docker_image)
        if backend == "docker" and not available:
            raise RuntimeError(
                "Docker backend requested, but Docker daemon or image is unavailable: "
                f"{self.limits.docker_image}"
            )
        if backend == "auto":
            return "docker" if available else "local"
        return backend

    def _docker_command(self, workspace: Path, entry_point: Path) -> tuple[list[str], str]:
        container_name = f"qwen-exp-{uuid.uuid4().hex[:12]}"
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--cpus",
            str(self.limits.cpu_limit),
            "--memory",
            f"{self.limits.memory_limit_mb}m",
            "--pids-limit",
            str(self.limits.pids_limit),
            "--shm-size",
            f"{self.limits.shm_size_mb}m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--network",
            "bridge" if self.limits.network_enabled else "none",
            "--mount",
            f"type=bind,source={workspace},target=/workspace",
            "--workdir",
            "/workspace",
            "--env",
            "MPLBACKEND=Agg",
            "--env",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "--env",
            "PYTHONHASHSEED=0",
            "--env",
            "PYTHONIOENCODING=utf-8",
            "--env",
            "HOME=/sandbox_home",
            "--env",
            "USERPROFILE=/sandbox_home",
            "--env",
            "ANDES_NCPUS=1",
            "--env",
            "OMP_NUM_THREADS=1",
            "--env",
            "OPENBLAS_NUM_THREADS=1",
            "--env",
            "MKL_NUM_THREADS=1",
            "--env",
            "NUMEXPR_NUM_THREADS=1",
        ]
        for key, value in self.limits.extra_environment.items():
            command.extend(["--env", f"{key}={value}"])
        command.extend(
            [self.limits.docker_image, "python", entry_point.as_posix()]
        )
        return command, container_name

    def _sanitized_environment(self) -> dict[str, str]:
        # Generated experiments never receive Qwen credentials.
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "CUDA_VISIBLE_DEVICES",
            "LD_LIBRARY_PATH",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed}
        writable_tmp = self.results_root / "_tmp"
        mpl_config = self.results_root / "_matplotlib"
        writable_home = self.results_root / "_home"
        writable_tmp.mkdir(parents=True, exist_ok=True)
        mpl_config.mkdir(parents=True, exist_ok=True)
        writable_home.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "MPLBACKEND": "Agg",
                "MPLCONFIGDIR": str(mpl_config),
                "PYTHONHASHSEED": "0",
                "PYTHONIOENCODING": "utf-8",
                "TEMP": str(writable_tmp),
                "TMP": str(writable_tmp),
                "HOME": str(writable_home),
                "USERPROFILE": str(writable_home),
            }
        )
        env.update({str(k): str(v) for k, v in self.limits.extra_environment.items()})
        return env

    def _local_preexec(self) -> Any:
        if os.name != "posix":
            return None

        memory_bytes = self.limits.memory_limit_mb * 1024 * 1024
        cpu_seconds = max(1, self.limits.timeout_seconds + 5)

        def apply_limits() -> None:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            os.setsid()

        return apply_limits

    @staticmethod
    def _terminate_process_tree(
        process: subprocess.Popen[Any], container_name: str | None
    ) -> None:
        if container_name:
            subprocess.run(
                ["docker", "kill", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
            return
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()

    @staticmethod
    def _safe_relative_path(
        root: Path, relative: str, *, must_exist: bool
    ) -> Path:
        candidate_rel = Path(str(relative))
        if candidate_rel.is_absolute() or ".." in candidate_rel.parts:
            raise ValueError(f"Path must stay inside the experiment workspace: {relative}")
        candidate = (root / candidate_rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Path escapes the experiment workspace: {relative}") from exc
        if must_exist and not candidate.is_file():
            raise FileNotFoundError(f"Experiment entry point not found: {candidate}")
        return candidate

    @staticmethod
    def _safe_run_id(value: str | None) -> str:
        raw = str(value or f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}")
        clean = _SAFE_ID.sub("_", raw).strip("._-")[:96]
        return clean or f"run_{uuid.uuid4().hex[:8]}"

    def _unique_run_dir(self, run_id: str) -> Path:
        candidate = self.results_root / run_id
        if not candidate.exists():
            return candidate
        for suffix in range(1, 10_000):
            candidate = self.results_root / f"{run_id}_{suffix}"
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Cannot allocate a unique sandbox run directory for {run_id}")


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a generated experiment project")
    parser.add_argument("project_dir")
    parser.add_argument("--entry-point", default="experiment.py")
    parser.add_argument("--result", default="result.json")
    parser.add_argument("--backend", choices=("auto", "docker", "local"), default="auto")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--docker-image", default="qwen-ai-scientist/power-experiment:latest")
    return parser


def main() -> int:
    args = _build_cli().parse_args()
    limits = SandboxLimits(
        backend=args.backend,
        timeout_seconds=args.timeout,
        memory_limit_mb=args.memory_mb,
        cpu_limit=args.cpus,
        docker_image=args.docker_image,
    )
    runner = SandboxRunner(limits=limits)
    result = runner.run_project(
        args.project_dir,
        entry_point=args.entry_point,
        expected_result=args.result,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
