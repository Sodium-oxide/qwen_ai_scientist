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
TOOL_RESULTS_DIR = Path(
    os.environ.get("TOOL_RESULTS_DIR", PACKAGE_DIR / "tool_results")
).resolve()
TRANSCRIPTS_DIR = Path(
    os.environ.get("TRANSCRIPTS_DIR", PACKAGE_DIR / "transcripts")
).resolve()

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

L3_TOOL_RESULT_BUDGET = int(os.environ.get("L3_TOOL_RESULT_BUDGET", "16000"))
L3_SNIPPET_CHARS = int(os.environ.get("L3_SNIPPET_CHARS", "1500"))
L1_MAX_MESSAGES = int(os.environ.get("L1_MAX_MESSAGES", "60"))
L1_COOLDOWN_MESSAGES = int(os.environ.get("L1_COOLDOWN_MESSAGES", "8"))
L1_COMPACT_TRIGGER_MESSAGES = int(
    os.environ.get("L1_COMPACT_TRIGGER_MESSAGES", str(L1_MAX_MESSAGES + L1_COOLDOWN_MESSAGES))
)
L1_KEEP_HEAD = int(os.environ.get("L1_KEEP_HEAD", "15"))
L1_KEEP_TAIL = int(os.environ.get("L1_KEEP_TAIL", "44"))
L2_KEEP_TOOL_RESULTS = int(os.environ.get("L2_KEEP_TOOL_RESULTS", "8"))
L0_SERIALIZED_LIMIT = int(os.environ.get("L0_SERIALIZED_LIMIT", "80000"))
L0_SUMMARY_TOKENS = int(os.environ.get("L0_SUMMARY_TOKENS", "1200"))
EMERGENCY_KEEP_MESSAGES = int(os.environ.get("EMERGENCY_KEEP_MESSAGES", "5"))

ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
AUTO_APPROVE = os.environ.get("AGENT_AUTO_APPROVE", "").lower() in {"1", "true", "yes"}
DISABLE_CONTEXT_INJECTION = bool(os.environ.get("AGENT_DISABLE_CONTEXT_INJECTION"))
LOG_COLOR = os.environ.get("AGENT_LOG_COLOR", "1").lower() not in {"0", "false", "no"}
