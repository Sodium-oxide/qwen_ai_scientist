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
MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", PACKAGE_DIR / ".memory")).resolve()
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
TASKS_DIR = Path(os.environ.get("TASKS_DIR", PACKAGE_DIR / ".tasks")).resolve()
TEAM_DIR = Path(os.environ.get("TEAM_DIR", PACKAGE_DIR / ".team")).resolve()
TEAM_INBOX_DIR = TEAM_DIR / "inboxes"
SCHEDULED_TASKS_PATH = Path(
    os.environ.get("SCHEDULED_TASKS_PATH", PACKAGE_DIR / ".scheduled_tasks.json")
).resolve()
SCIENCE_DIR = Path(os.environ.get("SCIENCE_DIR", PACKAGE_DIR / ".science")).resolve()
SEMANTIC_SCHOLAR_API_KEY = (
    os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    or os.environ.get("SEMANTIC_SCHOLAR_KEY")
    or os.environ.get("S2_API_KEY")
)
SCIENCE_LLM_EXTRACTOR = os.environ.get("SCIENCE_LLM_EXTRACTOR", "qwen").strip().lower()
SCIENCE_INSECURE_SSL = os.environ.get("SCIENCE_INSECURE_SSL", "").lower() in {"1", "true", "yes"}
SCIENCE_DOMAIN_EMBEDDINGS_ENABLED = os.environ.get(
    "SCIENCE_DOMAIN_EMBEDDINGS_ENABLED", "0"
).lower() in {"1", "true", "yes"}
SCIENCE_DOMAIN_EMBEDDING_MODEL_PATH = os.environ.get(
    "SCIENCE_DOMAIN_EMBEDDING_MODEL_PATH", ""
).strip()
SCIENCE_DOMAIN_EMBEDDING_REVIEW_THRESHOLD = float(
    os.environ.get("SCIENCE_DOMAIN_EMBEDDING_REVIEW_THRESHOLD", "0.18")
)
SCIENCE_DOMAIN_EMBEDDING_REJECT_THRESHOLD = float(
    os.environ.get("SCIENCE_DOMAIN_EMBEDDING_REJECT_THRESHOLD", "0.1")
)
SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS = float(
    os.environ.get("SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS", "1.5")
)
SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS = float(
    os.environ.get("SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS", "1.5")
)
SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT = int(
    os.environ.get("SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT", "2")
)
SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429 = os.environ.get(
    "SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429", "0"
).lower() not in {"0", "false", "no"}
SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS = float(
    os.environ.get("SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS", "3")
)
SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS = int(
    os.environ.get("SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS", "2")
)
SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS = float(
    os.environ.get("SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS", "86400")
)
SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT = int(
    os.environ.get("SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT", "10")
)
SCIENCE_COMMUNITY_AWARE_SEED_SELECTION = os.environ.get(
    "SCIENCE_COMMUNITY_AWARE_SEED_SELECTION", "1"
).lower() not in {"0", "false", "no"}
SCIENCE_MIN_CROSS_COMMUNITY_SEEDS = int(
    os.environ.get("SCIENCE_MIN_CROSS_COMMUNITY_SEEDS", "2")
)
SCIENCE_CROSS_COMMUNITY_EDGE_BONUS = float(
    os.environ.get("SCIENCE_CROSS_COMMUNITY_EDGE_BONUS", "0.25")
)
SCIENCE_BRIDGE_SEARCH_ENABLED = os.environ.get(
    "SCIENCE_BRIDGE_SEARCH_ENABLED", "1"
).lower() not in {"0", "false", "no"}
SCIENCE_BRIDGE_SEARCH_MAX_RESULTS = int(
    os.environ.get("SCIENCE_BRIDGE_SEARCH_MAX_RESULTS", "12")
)
SCIENCE_BRIDGE_SEARCH_QUERY_LIMIT = int(
    os.environ.get("SCIENCE_BRIDGE_SEARCH_QUERY_LIMIT", "2")
)
SCIENCE_SPARSE_GRAPH_THRESHOLD = float(
    os.environ.get("SCIENCE_SPARSE_GRAPH_THRESHOLD", "0.3")
)
SCIENCE_LOUVAIN_ENABLED = os.environ.get(
    "SCIENCE_LOUVAIN_ENABLED", "1"
).lower() not in {"0", "false", "no"}
SCIENCE_LOUVAIN_RESOLUTION = float(
    os.environ.get("SCIENCE_LOUVAIN_RESOLUTION", "1.0")
)
SCIENCE_LOUVAIN_BRIDGE_THRESHOLD = float(
    os.environ.get("SCIENCE_LOUVAIN_BRIDGE_THRESHOLD", "0.3")
)
SCIENCE_LOUVAIN_MAX_NODES = int(
    os.environ.get("SCIENCE_LOUVAIN_MAX_NODES", "500")
)
SCIENCE_LOUVAIN_INCLUDE_ARTIFICIAL_EDGES = os.environ.get(
    "SCIENCE_LOUVAIN_INCLUDE_ARTIFICIAL_EDGES", "0"
).lower() in {"1", "true", "yes"}
SCIENCE_LOUVAIN_MIN_COMMUNITY_RECORDS = int(
    os.environ.get("SCIENCE_LOUVAIN_MIN_COMMUNITY_RECORDS", "2")
)
SCIENCE_ARXIV_MIN_INTERVAL_SECONDS = float(
    os.environ.get("SCIENCE_ARXIV_MIN_INTERVAL_SECONDS", "3.5")
)
SCIENCE_ARXIV_CIRCUIT_SECONDS = float(
    os.environ.get("SCIENCE_ARXIV_CIRCUIT_SECONDS", "30")
)
SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER = int(
    os.environ.get("SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER", "4")
)
SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER = int(
    os.environ.get("SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER", "3")
)
SCIENCE_PREPRINT_ZERO_RESULT_TTL_SECONDS = float(
    os.environ.get("SCIENCE_PREPRINT_ZERO_RESULT_TTL_SECONDS", "900")
)
SCIENCE_SOCRATES_PREPRINT_SCAN_LIMIT = int(
    os.environ.get("SCIENCE_SOCRATES_PREPRINT_SCAN_LIMIT", "180")
)
SCIENCE_SOCRATES_PREPRINT_PROVIDER_RESULT_TARGET = int(
    os.environ.get("SCIENCE_SOCRATES_PREPRINT_PROVIDER_RESULT_TARGET", "3")
)
SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K = int(
    os.environ.get("SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K", "20")
)
SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K = int(
    os.environ.get("SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K", "50")
)
SCIENCE_ZHIZHI_IMPORT_LLM_LIMIT = int(
    os.environ.get("SCIENCE_ZHIZHI_IMPORT_LLM_LIMIT", "2")
)
SCIENCE_ZHIZHI_SERIAL_SUBSPACE_SEARCH = os.environ.get(
    "SCIENCE_ZHIZHI_SERIAL_SUBSPACE_SEARCH", "1"
).lower() not in {"0", "false", "no"}
SCIENCE_ZHIZHI_SUBSPACE_ROUNDS = int(
    os.environ.get("SCIENCE_ZHIZHI_SUBSPACE_ROUNDS", "8")
)
SCIENCE_ZHIZHI_BOUNDARY_EXTENSION_ROUNDS = int(
    os.environ.get("SCIENCE_ZHIZHI_BOUNDARY_EXTENSION_ROUNDS", "3")
)
SCIENCE_ZHIZHI_PER_SUBSPACE_RESULTS = int(
    os.environ.get("SCIENCE_ZHIZHI_PER_SUBSPACE_RESULTS", "12")
)
SCIENCE_ZHIZHI_PER_SUBSPACE_IMPORTS = int(
    os.environ.get("SCIENCE_ZHIZHI_PER_SUBSPACE_IMPORTS", "6")
)

QWEN_MODEL_ID = os.environ.get("QWEN_MODEL_ID", "qwen-plus")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
QWEN_API_BASE = os.environ.get("QWEN_API_BASE") or os.environ.get("DASHSCOPE_API_BASE")
DEFAULT_LLM_PROVIDER = "qwen" if QWEN_API_KEY else "anthropic"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip().lower()
MODEL_ID = os.environ.get(
    "MODEL_ID",
    QWEN_MODEL_ID if LLM_PROVIDER in {"qwen", "dashscope"} else os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", os.environ.get("ANTHROPIC_MAX_TOKENS", "8000")))
SUB_MAX_TOKENS = int(os.environ.get("SUB_MAX_TOKENS", "4000"))
SUB_MAX_TURNS = int(os.environ.get("SUB_MAX_TURNS", "30"))

BASH_TIMEOUT_SECONDS = int(os.environ.get("BASH_TIMEOUT_SECONDS", "120"))
MAX_OUTPUT_CHARS = int(os.environ.get("MAX_OUTPUT_CHARS", "200000"))
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
MEMORY_RETRIEVAL_LIMIT = int(os.environ.get("MEMORY_RETRIEVAL_LIMIT", "5"))
MEMORY_MERGE_THRESHOLD = int(os.environ.get("MEMORY_MERGE_THRESHOLD", "10"))
MEMORY_EXTRACT_TOKENS = int(os.environ.get("MEMORY_EXTRACT_TOKENS", "1200"))
MEMORY_MERGE_TOKENS = int(os.environ.get("MEMORY_MERGE_TOKENS", "2000"))
RECOVERY_MAX_TOKENS_ESCALATED = int(os.environ.get("RECOVERY_MAX_TOKENS_ESCALATED", "64000"))
RECOVERY_CONTINUATION_LIMIT = int(os.environ.get("RECOVERY_CONTINUATION_LIMIT", "3"))
RECOVERY_RETRY_LIMIT = int(os.environ.get("RECOVERY_RETRY_LIMIT", "5"))
RECOVERY_BASE_DELAY_MS = int(os.environ.get("RECOVERY_BASE_DELAY_MS", "500"))
RECOVERY_MAX_DELAY_MS = int(os.environ.get("RECOVERY_MAX_DELAY_MS", "32000"))
FALLBACK_MODEL_ID = os.environ.get("FALLBACK_MODEL_ID", MODEL_ID)
BACKGROUND_ENABLED = os.environ.get("BACKGROUND_ENABLED", "1").lower() not in {"0", "false", "no"}
BACKGROUND_MAX_OUTPUT_CHARS = int(os.environ.get("BACKGROUND_MAX_OUTPUT_CHARS", "20000"))
CRON_ENABLED = os.environ.get("CRON_ENABLED", "1").lower() not in {"0", "false", "no"}
CRON_POLL_SECONDS = float(os.environ.get("CRON_POLL_SECONDS", "1.0"))
CRON_QUEUE_POLL_SECONDS = float(os.environ.get("CRON_QUEUE_POLL_SECONDS", "0.2"))
CRON_MAX_JOBS = int(os.environ.get("CRON_MAX_JOBS", "50"))

ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
AUTO_APPROVE = os.environ.get("AGENT_AUTO_APPROVE", "").lower() in {"1", "true", "yes"}
DISABLE_CONTEXT_INJECTION = bool(os.environ.get("AGENT_DISABLE_CONTEXT_INJECTION"))
LOG_COLOR = os.environ.get("AGENT_LOG_COLOR", "1").lower() not in {"0", "false", "no"}
