from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


_load_dotenv()

PACKAGE_DIR = Path(__file__).resolve().parent
WORKDIR = Path(os.environ.get("AGENT_WORKDIR", Path.cwd())).resolve()
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", PACKAGE_DIR / "skills")).resolve()
LOG_PATH = Path(os.environ.get("AGENT_LOG_PATH", PACKAGE_DIR / "agent.log")).resolve()

MODEL_ID = os.environ.get(
    "MODEL_ID",
    os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", os.environ.get("ANTHROPIC_MAX_TOKENS", "8000")))
SUB_MAX_TOKENS = int(os.environ.get("SUB_MAX_TOKENS", "4000"))
SUB_MAX_TURNS = int(os.environ.get("SUB_MAX_TURNS", "30"))

BASH_TIMEOUT_SECONDS = int(os.environ.get("BASH_TIMEOUT_SECONDS", "120"))
MAX_OUTPUT_CHARS = int(os.environ.get("MAX_OUTPUT_CHARS", "50000"))
LARGE_OUTPUT_CHARS = int(os.environ.get("LARGE_OUTPUT_CHARS", "20000"))

ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
AUTO_APPROVE = os.environ.get("AGENT_AUTO_APPROVE", "").lower() in {"1", "true", "yes"}
DISABLE_CONTEXT_INJECTION = bool(os.environ.get("AGENT_DISABLE_CONTEXT_INJECTION"))
