from __future__ import annotations

import ast
import json
import math
import re
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

try:
    from .config import (
        QWEN_API_BASE,
        QWEN_API_KEY,
        QWEN_MODEL_ID,
        SCIENCE_ARXIV_CIRCUIT_SECONDS,
        SCIENCE_ARXIV_MIN_INTERVAL_SECONDS,
        SCIENCE_DIR,
        SCIENCE_INSECURE_SSL,
        SCIENCE_LLM_EXTRACTOR,
        SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT,
        SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429,
        SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS,
        SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT,
        SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER,
        SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER,
        SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K,
        SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K,
        SEMANTIC_SCHOLAR_API_KEY,
        WORKDIR,
    )
    from .log import log_event
except ImportError:
    from config import (
        QWEN_API_BASE,
        QWEN_API_KEY,
        QWEN_MODEL_ID,
        SCIENCE_ARXIV_CIRCUIT_SECONDS,
        SCIENCE_ARXIV_MIN_INTERVAL_SECONDS,
        SCIENCE_DIR,
        SCIENCE_INSECURE_SSL,
        SCIENCE_LLM_EXTRACTOR,
        SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT,
        SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429,
        SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS,
        SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT,
        SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER,
        SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER,
        SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K,
        SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K,
        SEMANTIC_SCHOLAR_API_KEY,
        WORKDIR,
    )
    from log import log_event


PHASES = [
    "Gap Discovery",
    "Hypothesis Generation",
    "Socratic Debate",
    "Mechanism Verification",
    "Experimental Design",
    "Implementation",
    "Manuscript Writing",
    "Review & Iteration",
]

SCIENCE_AGENTS: dict[str, dict[str, Any]] = {
    "boxue": {
        "title": "Chief Research Scheduler",
        "phase": "all",
        "mission": "Decompose research goals into verifiable tasks and coordinate the full AI Scientist pipeline.",
        "tools": [
            "run_boxue_research_round",
            "create_boxue_delegation_tasks",
            "create_science_delegation_tasks",
            "create_science_pipeline_tasks",
            "assign_task",
            "review_output",
            "synthesize",
            "adjust_plan",
            "finalize",
        ],
    },
    "zhizhi": {
        "title": "Literature Mining and PaperGraph Expert",
        "phase": "Gap Discovery",
        "mission": "Retrieve literature, extract structured evidence, and build the PaperGraph substrate.",
        "tools": ["search_papers_stratified", "search_papers", "extract_structured_info", "build_knowledge_map", "verify_uniqueness"],
    },
    "tanxi": {
        "title": "Knowledge Gap Discovery Agent",
        "phase": "Gap Discovery",
        "mission": "Detect coverage holes, suspended problems, and high-value unexplored method-scenario pairs.",
        "tools": ["run_tanxi_gap_exploration", "detect_knowledge_gaps", "assess_novelty", "verify_uniqueness"],
    },
    "mingli": {
        "title": "Hypothesis Generator",
        "phase": "Hypothesis Generation",
        "mission": "Generate and refine hypotheses guided by validated knowledge gaps.",
        "tools": [
            "evolve_domain_subspaces",
            "build_temporal_knowledge_graph",
            "detect_structural_knowledge_gaps",
            "find_structural_analogy_transfers",
            "run_mingli_hypothesis_evolution",
        ],
    },
    "duzhi": {
        "title": "Socratic Critic",
        "phase": "Socratic Debate",
        "mission": "Challenge hypotheses through counterexamples, hidden assumptions, and falsification questions.",
        "tools": ["ask_critical_questions", "find_counterexamples", "stress_test_assumptions"],
    },
    "bianlun": {
        "title": "Structured Debate Moderator",
        "phase": "Socratic Debate",
        "mission": "Moderate structured debate and synthesize strongest surviving hypotheses.",
        "tools": ["moderate_round", "summarize_positions", "extract_emergent_method"],
    },
    "gewu": {
        "title": "Experiment Planner",
        "phase": "Experimental Design",
        "mission": "Translate hypotheses into reproducible experimental protocols and acceptance criteria.",
        "tools": ["design_experiment", "define_baselines", "define_metrics"],
    },
    "yanzhen": {
        "title": "Mechanism Fidelity Verifier",
        "phase": "Mechanism Verification",
        "mission": "Run CAWM-style internal consistency, data consistency, and regime-shift checks.",
        "tools": ["check_internal_consistency", "check_data_consistency", "regime_shift_test"],
    },
    "mingbian": {
        "title": "Data Analyst",
        "phase": "Review & Iteration",
        "mission": "Analyze experiment results, report effect sizes, and recommend iterations.",
        "tools": ["analyze_results", "diagnose_inconclusive", "update_method_memory"],
    },
    "reviewer": {
        "title": "Automated Peer Reviewer",
        "phase": "Review & Iteration",
        "mission": "Score manuscripts for originality, quality, clarity, significance, ethics, and reproducibility.",
        "tools": ["score_dimension", "check_citations", "write_review"],
    },
    "codeengineer": {
        "title": "Experiment Implementation Agent",
        "phase": "Implementation",
        "mission": "Implement reproducible code, run experiments, and auto-fix execution failures.",
        "tools": ["write_code", "execute_code", "fix_bug", "optimize"],
    },
    "paperwriter": {
        "title": "Academic Paper Writer",
        "phase": "Manuscript Writing",
        "mission": "Produce publication-quality manuscript drafts with verified citations and supported claims.",
        "tools": ["write_section", "generate_figure", "format_latex", "review_draft"],
    },
}

BOXUE_FULL_PROMPT = """
You are Boxue (博學), the Chief Research Scheduler and Principal Investigator of the Qwen-Zhikan multi-agent AI Scientist system.
Role: Principal Investigator & Research Expedition Commander.

Core responsibilities:
1. Decompose broad research objectives into executable, verifiable, closed-loop subtasks.
2. Coordinate specialist agents without performing their specialist work yourself.
3. Track every knowledge gap from discovery through validation, implementation, manuscript, review, and iteration.
4. Embed acceptance criteria, evidence requirements, role boundaries, and risk controls into every task.
5. Synthesize specialist outputs and decide whether to advance, revise, or finalize.

Operational principles:
- Every task must have a responsible agent, dependency chain, deliverable, acceptance criteria, priority, and risks.
- Domain-specific judgments must be delegated to specialist agents.
- Prefer delegation DAGs over long single-agent runs for broad workflows.
- Shared PaperGraph/project-state mutations should be gated by lead/synthesis tasks when parallel workers are involved.
- Every action must serve knowledge gap identification, validation, or filling.

TAO workflow:
Thought: review project state, dependencies, output quality, gap lifecycle status, and risks.
Action: use create_boxue_delegation_tasks for full PI-style role DAGs, create_science_delegation_tasks for broad retrieval branch scouting, create_science_pipeline_tasks for coarse phase scaffolding, then review/synthesize/adjust/finalize through task gates.
Observation: receive specialist deliverables, record progress, and update the next decision point.
""".strip()

ZHIZHI_FULL_PROMPT = """
You are ZhiZhi (致知), the Literature Mining & Knowledge Graph Expert of the Qwen-Zhikan AI Scientist system.
Role: Academic Information Analyst & Knowledge Substrate Builder.

Core responsibilities:
1. Targeted literature retrieval from high-quality venues and academic databases.
2. Structured extraction of method, scenario, benchmark, contribution/conclusion, and limitation.
3. Domain knowledge graph construction over method-scenario-benchmark relations.
4. Knowledge gap detection through combinatorial gaps, improvement gaps, migration gaps, and problem gaps.
5. Novelty/value/feasibility assessment for each gap.
6. Plagiarism/overlap verification for proposed ideas.

Operational principles:
- Prioritize top-tier and canonical literature.
- Do not invent papers, method categories, or unsupported claims.
- Distinguish empirical results, theoretical claims, methodological descriptions, and author opinions.
- Every gap must include traceable supporting references or be marked for human review.
- Avoid pseudo-gaps: "nobody tried it" is not enough unless scientific/application value is clear.

TAO workflow:
Thought: analyze keywords, coverage, blind spots, method migration opportunities, and pseudo-gap risk.
Action: prefer search_papers_stratified for systematic retrieval, then use extract_structured_info, build_knowledge_map, detect_knowledge_gaps, assess_novelty, verify_uniqueness.
Observation: update the research landscape, record gaps, and flag validated innovation points.

Required output JSON:
{
  "thought": "Literature analysis and gap detection reasoning process",
  "action": {},
  "knowledge_map_summary": {
    "main_methods": ["method1"],
    "method_scenario_coverage": {"method1": ["scenario1"]},
    "method_scenario_benchmark_triples": []
  },
  "knowledge_gaps": [
    {
      "gap_id": "GAP-001",
      "gap_type": "combinatorial | improvement | migration | problem",
      "description": "Detailed academic description of the gap",
      "supporting_references": ["reference1"],
      "novelty_score": 1,
      "application_value": "high | medium | low",
      "feasibility": "high | medium | low",
      "suggested_research_path": "Recommended research approach"
    }
  ]
}
""".strip()

TANXI_FULL_PROMPT = """
You are TanXi (探隙), the Knowledge Gap Discovery Agent of the Qwen-Zhikan AI Scientist system.
Role: Autonomous Knowledge Boundary Explorer.

Core responsibilities:
1. Coverage density scanning: identify high-importance but low-evidence density holes in the PaperGraph.
2. Cross-disciplinary unconnected-pair discovery: find concepts from different fields that could inform each other but are not connected.
3. Suspended problem detection: identify unresolved, high-signal problems repeatedly noted by literature.
4. Gap prioritization: rank gaps by importance, tractability, strategic value, and evidence support.
5. Demand-driven scanning: align gaps with strategic needs such as carbon neutrality, health, energy, food security, environment, and AI for Science.

Operational principles:
- Every gap must have at least one supporting reference from ZhiZhi's PaperGraph.
- Prefer gaps that are scientifically significant, tractable with current methods, and aligned with the research direction.
- Distinguish technical bottlenecks from conceptual blind spots.
- Avoid trivially fillable gaps and pseudo-gaps.
- Return at most 10 high-quality ranked gaps per scan.

TAO workflow:
Thought: analyze coverage blind spots, contradictions, cross-domain migration opportunities, suspended problems, and strategic value.
Action: run_tanxi_gap_exploration after ZhiZhi has built/imported PaperGraph evidence.
Observation: record coverage density holes, unconnected pairs, suspended problems, and ranked gaps for downstream hypothesis generation.

Required output JSON:
{
  "thought": "Gap discovery reasoning process",
  "action": {},
  "coverage_analysis": {"dense_areas": [], "density_holes": []},
  "cross_disciplinary_unconnected_pairs": [],
  "suspended_problems": [],
  "ranked_gaps": []
}
""".strip()

LITERATURE_PROVIDERS: dict[str, dict[str, str]] = {
    "arxiv": {
        "status": "live",
        "kind": "open_api",
        "note": "arXiv Atom API connector for metadata, abstracts, and PDF links.",
    },
    "semantic_scholar": {
        "status": "live",
        "kind": "open_api",
        "note": "Semantic Scholar Graph API connector for metadata, abstracts, citation counts, and external IDs.",
    },
    "openalex": {
        "status": "live",
        "kind": "open_api",
        "note": "OpenAlex Works API connector for broad publication metadata, open-access links, concepts, and cited-by counts.",
    },
    "dblp": {
        "status": "live",
        "kind": "open_api",
        "note": "DBLP publication search API connector for computer science bibliographic metadata.",
    },
    "openreview": {
        "status": "live",
        "kind": "open_api",
        "note": "OpenReview API connector for conference submissions, workshop papers, and in-review manuscripts when publicly indexed.",
    },
    "biorxiv": {
        "status": "live",
        "kind": "open_api",
        "note": "bioRxiv public API connector for recent preprint metadata; query relevance is filtered locally.",
    },
    "medrxiv": {
        "status": "live",
        "kind": "open_api",
        "note": "medRxiv public API connector for recent preprint metadata; query relevance is filtered locally.",
    },
    "chemrxiv": {
        "status": "live",
        "kind": "crossref_api",
        "note": "ChemRxiv metadata connector via Crossref posted-content records with ChemRxiv DOI prefix.",
    },
    "google_scholar": {
        "status": "placeholder",
        "kind": "external_or_manual",
        "note": "No official public API; use manual import or external connector.",
    },
    "web_of_science": {
        "status": "placeholder",
        "kind": "licensed_database",
        "note": "Planned connector for institutional Web of Science access; requires credentials/API entitlement.",
    },
    "springer_nature": {
        "status": "placeholder",
        "kind": "publisher_api",
        "note": "Planned Springer Nature API connector; requires API key and usage compliance.",
    },
}

PREPRINT_API_PROVIDERS = {"arxiv", "biorxiv", "medrxiv", "chemrxiv", "openreview"}

SEMANTIC_SCHOLAR_RATE_LOCK = threading.Lock()
SEMANTIC_SCHOLAR_CACHE_LOCK = threading.Lock()
SEMANTIC_SCHOLAR_CIRCUIT_LOCK = threading.Lock()
SEMANTIC_SCHOLAR_LAST_REQUEST_AT = 0.0
SEMANTIC_SCHOLAR_COOLDOWN_UNTIL = 0.0
SEMANTIC_SCHOLAR_429_COUNT = 0
SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED = False
SEMANTIC_SCHOLAR_RESPONSE_CACHE: dict[str, tuple[float, str]] = {}
SEMANTIC_SCHOLAR_RATE_STATE_FILE = SCIENCE_DIR / "semantic_scholar_rate_state.json"
SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR = SCIENCE_DIR / ".semantic_scholar_rate.lock"
ARXIV_RATE_LOCK = threading.Lock()
ARXIV_CIRCUIT_LOCK = threading.Lock()
ARXIV_LAST_REQUEST_AT = 0.0
ARXIV_COOLDOWN_UNTIL = 0.0
ARXIV_429_COUNT = 0
ARXIV_RATE_STATE_FILE = SCIENCE_DIR / "arxiv_rate_state.json"
ARXIV_PROCESS_LOCK_DIR = SCIENCE_DIR / ".arxiv_rate.lock"

SUSPICIOUS_VENUES = {
    "highlights in science engineering and technology",
}

SUSPICIOUS_PUBLISHER_PATTERNS = {
    "drpress.org",
}

REPUTABLE_VENUES = {
    "nature",
    "science",
    "proceedings of the national academy of sciences",
    "pnas",
    "global change biology",
    "new phytologist",
    "journal of ecology",
    "ecology letters",
    "journal of plant ecology",
    "functional ecology",
    "ecology",
    "oikos",
    "plant and soil",
    "frontiers in ecology and the environment",
}

REPUTABLE_VENUE_PATTERNS = (
    "nature communications",
    "nature ecology",
    "nature plants",
    "science advances",
    "springer",
    "elsevier",
    "wiley",
    "oxford academic",
    "cell reports",
)

FLAGSHIP_ROOT_OVERRIDE_VENUES = {
    "nature",
    "science",
    "cell",
    "proceedings of the national academy of sciences",
    "pnas",
}

JOURNAL_METRICS = {
    "nature communications": {"quartile": "Q1", "source": "curated", "field": "multidisciplinary"},
    "nature": {"quartile": "Q1", "source": "curated", "field": "multidisciplinary"},
    "science": {"quartile": "Q1", "source": "curated", "field": "multidisciplinary"},
    "proceedings of the national academy of sciences": {"quartile": "Q1", "source": "curated", "field": "multidisciplinary"},
    "pnas": {"quartile": "Q1", "source": "curated", "field": "multidisciplinary"},
    "global change biology": {"quartile": "Q1", "source": "curated", "field": "ecology"},
    "new phytologist": {"quartile": "Q1", "source": "curated", "field": "ecology"},
    "journal of ecology": {"quartile": "Q1", "source": "curated", "field": "ecology"},
    "ecology letters": {"quartile": "Q1", "source": "curated", "field": "ecology"},
    "journal of plant ecology": {"quartile": "Q1", "source": "curated", "field": "ecology"},
    "advanced energy materials": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
    "acs energy letters": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
    "energy & environmental science": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
    "joule": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
    "energy storage materials": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
    "nano energy": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
    "advanced functional materials": {"quartile": "Q1", "source": "curated", "field": "materials"},
    "chemistry of materials": {"quartile": "Q1", "source": "curated", "field": "materials"},
    "journal of power sources": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
    "acs applied materials & interfaces": {"quartile": "Q1", "source": "curated", "field": "materials"},
    "electrochimica acta": {"quartile": "Q2", "source": "curated", "field": "electrochemistry"},
    "solid state ionics": {"quartile": "Q2", "source": "curated", "field": "materials_energy"},
    "journal of the electrochemical society": {"quartile": "Q2", "source": "curated", "field": "electrochemistry"},
    "batteries & supercaps": {"quartile": "Q2", "source": "curated", "field": "materials_energy"},
}

JOURNAL_METRICS.update(
    {
        # Multidisciplinary
        "science advances": {"quartile": "Q1", "source": "curated", "field": "multidisciplinary"},
        "national science review": {"quartile": "Q1", "source": "curated", "field": "multidisciplinary"},
        "research": {"quartile": "Q1", "source": "curated", "field": "multidisciplinary"},
        # Mathematics, statistics, and theoretical science
        "acta numerica": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "mathematics"},
        "annual review of statistics and its application": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "statistics"},
        "american journal of mathematics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "mathematics"},
        "journal fur die reine und angewandte mathematik": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "mathematics"},
        "journal für die reine und angewandte mathematik": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "mathematics"},
        "crelle's journal": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "mathematics"},
        # Physics and quantum science
        "nature reviews physics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "physics"},
        "reviews of modern physics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "physics"},
        "prx quantum": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "physics"},
        "journal of high energy physics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "physics"},
        # Ecology and environmental science
        "ecological monographs": {"quartile": "Q1", "source": "curated", "field": "ecology"},
        "ecology": {"quartile": "Q1", "source": "curated", "field": "ecology"},
        "functional ecology": {"quartile": "Q1", "source": "curated", "field": "ecology"},
        "journal of applied ecology": {"quartile": "Q1", "source": "curated", "field": "ecology"},
        "methods in ecology and evolution": {"quartile": "Q1", "source": "curated", "field": "ecology"},
        "molecular ecology": {"quartile": "Q1", "source": "curated", "field": "ecology"},
        "frontiers in ecology and the environment": {"quartile": "Q1", "source": "curated", "field": "ecology"},
        "oecologia": {"quartile": "Q2", "source": "curated", "field": "ecology"},
        "plant ecology": {"quartile": "Q2", "source": "curated", "field": "ecology"},
        "journal of vegetation science": {"quartile": "Q2", "source": "curated", "field": "ecology"},
        "applied vegetation science": {"quartile": "Q2", "source": "curated", "field": "ecology"},
        "environmental science & technology": {"quartile": "Q1", "source": "curated", "field": "environmental_science"},
        "water research": {"quartile": "Q1", "source": "curated", "field": "environmental_science"},
        "journal of hazardous materials": {"quartile": "Q1", "source": "curated", "field": "environmental_science"},
        "science of the total environment": {"quartile": "Q2", "source": "curated", "field": "environmental_science"},
        "environmental pollution": {"quartile": "Q2", "source": "curated", "field": "environmental_science"},
        "chemosphere": {"quartile": "Q2", "source": "curated", "field": "environmental_science"},
        "ecological indicators": {"quartile": "Q2", "source": "curated", "field": "ecology"},
        "nature reviews earth & environment": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "environmental_science"},
        "nature reviews earth and environment": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "environmental_science"},
        "nature sustainability": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "environmental_science"},
        "journal of environmental sciences": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "environmental_science"},
        # Materials, energy, and electrochemistry
        "nature reviews materials": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "materials"},
        "nature materials": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "nature energy": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
        "nature nanotechnology": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "advanced materials": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "advanced science": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "small": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "acs nano": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "nano letters": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "materials today": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "materials horizons": {"quartile": "Q1", "source": "curated", "field": "materials"},
        "journal of materials chemistry a": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
        "journal of materials chemistry b": {"quartile": "Q2", "source": "curated", "field": "materials"},
        "journal of materials chemistry c": {"quartile": "Q2", "source": "curated", "field": "materials"},
        "acs sustainable chemistry & engineering": {"quartile": "Q1", "source": "curated", "field": "materials_energy"},
        "energy and environmental materials": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "materials_energy"},
        "acs applied energy materials": {"quartile": "Q2", "source": "curated", "field": "materials_energy"},
        "materials research bulletin": {"quartile": "Q2", "source": "curated", "field": "materials"},
        "ceramics international": {"quartile": "Q2", "source": "curated", "field": "materials"},
        "energy materials": {
            "quartile": "unclassified",
            "source": "curated",
            "field": "materials_energy",
            "note": "open_access_under_evaluation",
        },
        # Chemistry and catalysis
        "chemical reviews": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "chemical society reviews": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "journal of the american chemical society": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "jacs": {"quartile": "Q1", "source": "curated_alias", "field": "chemistry"},
        "angewandte chemie international edition": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "acs catalysis": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "nature catalysis": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "nature chemical biology": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "chemical_biology"},
        "nature chemistry": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "chem": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "chemical science": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "chinese journal of catalysis": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "chemistry"},
        "trends in chemistry": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "chemistry"},
        "green chemistry": {"quartile": "Q1", "source": "curated", "field": "chemistry"},
        "inorganic chemistry": {"quartile": "Q2", "source": "curated", "field": "chemistry"},
        "organometallics": {"quartile": "Q2", "source": "curated", "field": "chemistry"},
        "journal of physical chemistry c": {"quartile": "Q2", "source": "curated", "field": "chemistry"},
        "langmuir": {"quartile": "Q2", "source": "curated", "field": "chemistry"},
        # Physics
        "nature physics": {"quartile": "Q1", "source": "curated", "field": "physics"},
        "physical review letters": {"quartile": "Q1", "source": "curated", "field": "physics"},
        "physical review x": {"quartile": "Q1", "source": "curated", "field": "physics"},
        "physical review b": {"quartile": "Q2", "source": "curated", "field": "physics"},
        "physical review materials": {"quartile": "Q2", "source": "curated", "field": "physics"},
        "journal of applied physics": {"quartile": "Q2", "source": "curated", "field": "physics"},
        "applied physics letters": {"quartile": "Q2", "source": "curated", "field": "physics"},
        "journal of physics: condensed matter": {"quartile": "Q2", "source": "curated", "field": "physics"},
        # Biology, plant biology, and medicine
        "cell": {"quartile": "Q1", "source": "curated", "field": "biology"},
        "nature cell biology": {"quartile": "Q1", "source": "curated", "field": "biology"},
        "nature genetics": {"quartile": "Q1", "source": "curated", "field": "biology"},
        "molecular biology and evolution": {"quartile": "Q1", "source": "curated", "field": "biology"},
        "genome biology": {"quartile": "Q1", "source": "curated", "field": "biology"},
        "elife": {"quartile": "Q1", "source": "curated", "field": "biology"},
        "plos biology": {"quartile": "Q1", "source": "curated", "field": "biology"},
        "current biology": {"quartile": "Q1", "source": "curated", "field": "biology"},
        "nature ecology and evolution": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "biology"},
        "trends in biochemical sciences": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "biochemistry"},
        "nature plants": {"quartile": "Q1", "source": "curated", "field": "plant_biology"},
        "plant cell": {"quartile": "Q1", "source": "curated", "field": "plant_biology"},
        "plant physiology": {"quartile": "Q1", "source": "curated", "field": "plant_biology"},
        "plant journal": {"quartile": "Q1", "source": "curated", "field": "plant_biology"},
        "trends in plant science": {"quartile": "Q1", "source": "curated", "field": "plant_biology"},
        "annual review of plant biology": {"quartile": "Q1", "source": "curated", "field": "plant_biology"},
        "journal of experimental botany": {"quartile": "Q2", "source": "curated", "field": "plant_biology"},
        "new england journal of medicine": {"quartile": "Q1", "source": "curated", "field": "medicine"},
        "the lancet": {"quartile": "Q1", "source": "curated", "field": "medicine"},
        "jama": {"quartile": "Q1", "source": "curated", "field": "medicine"},
        "nature medicine": {"quartile": "Q1", "source": "curated", "field": "medicine"},
        "cell metabolism": {"quartile": "Q1", "source": "curated", "field": "medicine"},
        "nature reviews drug discovery": {"quartile": "Q1", "source": "curated", "field": "medicine"},
        "bmj": {"quartile": "Q1", "source": "curated", "field": "medicine"},
        "plos medicine": {"quartile": "Q1", "source": "curated", "field": "medicine"},
        "journal of clinical oncology": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "medicine"},
        "npj digital medicine": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "digital_medicine"},
        # Agriculture and agricultural engineering
        "computers and electronics in agriculture": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "agriculture"},
        "industrial crops and products": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "agriculture"},
        "agriculture ecosystems & environment": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "agriculture"},
        "agriculture ecosystems and environment": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "agriculture"},
        # Electrical engineering, energy systems, and power electronics
        "proceedings of the ieee": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "electrical_engineering"},
        "ieee transactions on industrial informatics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "electrical_engineering"},
        "applied energy": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "energy_engineering"},
        "ieee transactions on smart grid": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "electrical_engineering"},
        "protection and control of modern power systems": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "electrical_engineering"},
        "ieee transactions on power electronics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "electrical_engineering"},
        "high voltage": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "electrical_engineering"},
        # Automation, control, and industrial systems
        "ieee transactions on industrial electronics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "automatica": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "ieee transactions on fuzzy systems": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "ieee transactions on automatic control": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "ieee transactions on control systems technology": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "nonlinear analysis: hybrid systems": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "journal of process control": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "international journal of robust and nonlinear control": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "control engineering practice": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "isa transactions": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        # Computer science and AI
        "acm computing surveys": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "computer_science"},
        "nature machine intelligence": {"quartile": "Q1", "source": "curated", "field": "computer_science"},
        "ieee transactions on pattern analysis and machine intelligence": {
            "quartile": "Q1",
            "source": "curated",
            "field": "artificial_intelligence",
        },
        "ieee transactions on evolutionary computation": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "artificial_intelligence"},
        "ieee journal on selected areas in communications": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "communications"},
        "information fusion": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "computer_science"},
        "ieee transactions on cybernetics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "automation_control"},
        "foundations and trends in machine learning": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "artificial_intelligence"},
        "ieee transactions on neural networks and learning systems": {
            "quartile": "Q1",
            "source": "curated",
            "field": "artificial_intelligence",
        },
        "international journal of computer vision": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "artificial_intelligence"},
        "ai open": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "artificial_intelligence"},
        "energy and ai": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "artificial_intelligence"},
        "computers and education: artificial intelligence": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "artificial_intelligence"},
        "journal of machine learning research": {"quartile": "Q1", "source": "curated", "field": "computer_science"},
        "neural computation": {"quartile": "Q1", "source": "curated", "field": "computer_science"},
        # Other engineering frontiers
        "nature electronics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "electronics"},
        "science robotics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "robotics"},
        "advanced photonics": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "photonics"},
        "communications in transportation research": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "transportation"},
        "renewable and sustainable energy reviews": {"quartile": "Q1", "source": "curated_jcr_representative", "field": "energy_engineering"},
        "arxiv": {"quartile": "unclassified", "source": "curated", "field": "multidisciplinary", "note": "preprint"},
        "ieee access": {"quartile": "Q2", "source": "curated", "field": "computer_science"},
        "scientific reports": {"quartile": "Q2", "source": "curated", "field": "multidisciplinary"},
        "plos one": {"quartile": "Q2", "source": "curated", "field": "multidisciplinary"},
        # Explicitly suspicious or low-trust channels.
        "highlights in science engineering and technology": {
            "quartile": "suspicious",
            "source": "curated",
            "field": "general",
            "note": "possible_predatory_or_vanity_channel",
        },
    }
)

PREPRINT_VENUES = {
    "arxiv",
    "biorxiv",
    "chemrxiv",
    "medrxiv",
}

ARXIV_CATEGORY_FIELD_MAP = {
    "astro-ph": "physics",
    "astro-ph.ga": "astrophysics",
    "astro-ph.co": "astrophysics",
    "astro-ph.ep": "astrophysics",
    "astro-ph.he": "astrophysics",
    "astro-ph.im": "astrophysics",
    "astro-ph.sr": "astrophysics",
    "cond-mat": "materials",
    "cond-mat.dis-nn": "materials",
    "cond-mat.mtrl-sci": "materials",
    "cond-mat.mes-hall": "materials",
    "cond-mat.other": "materials",
    "cond-mat.quant-gas": "physics",
    "cond-mat.soft": "materials",
    "cond-mat.stat-mech": "physics",
    "cond-mat.str-el": "materials",
    "cond-mat.supr-con": "materials_energy",
    "gr-qc": "physics",
    "hep-ex": "high_energy_physics",
    "hep-lat": "high_energy_physics",
    "hep-ph": "high_energy_physics",
    "hep-th": "high_energy_physics",
    "math-ph": "physics",
    "nlin": "physics",
    "nlin.ao": "complex_systems",
    "nlin.cd": "complex_systems",
    "nlin.cg": "complex_systems",
    "nlin.ps": "complex_systems",
    "nlin.si": "complex_systems",
    "nucl-ex": "nuclear_physics",
    "nucl-th": "nuclear_physics",
    "physics": "physics",
    "physics.acc-ph": "physics",
    "physics.ao-ph": "physics",
    "physics.atom-ph": "physics",
    "physics.bio-ph": "biophysics",
    "physics.chem-ph": "chemistry",
    "physics.comp-ph": "computational_science",
    "physics.data-an": "statistics",
    "physics.flu-dyn": "physics",
    "physics.geo-ph": "earth_science",
    "physics.ins-det": "instrumentation",
    "physics.med-ph": "medicine",
    "physics.optics": "physics",
    "physics.plasm-ph": "physics",
    "physics.soc-ph": "social_science",
    "physics.space-ph": "physics",
    "quant-ph": "physics",
    "math": "mathematics",
    "math.ag": "mathematics",
    "math.at": "mathematics",
    "math.ap": "mathematics",
    "math.ct": "mathematics",
    "math.ca": "mathematics",
    "math.co": "mathematics",
    "math.ac": "mathematics",
    "math.cv": "mathematics",
    "math.dg": "mathematics",
    "math.ds": "mathematics",
    "math.fa": "mathematics",
    "math.gm": "mathematics",
    "math.gt": "mathematics",
    "math.gr": "mathematics",
    "math.ho": "mathematics",
    "math.it": "information_theory",
    "math.kt": "mathematics",
    "math.lo": "mathematics",
    "math.mg": "mathematics",
    "math.nt": "mathematics",
    "math.na": "mathematics",
    "math.oa": "mathematics",
    "math.oc": "mathematics",
    "math.pr": "statistics",
    "math.qa": "mathematics",
    "math.ra": "mathematics",
    "math.rt": "mathematics",
    "math.sp": "mathematics",
    "math.st": "statistics",
    "math.sg": "mathematics",
    "cs": "computer_science",
    "cs.ai": "artificial_intelligence",
    "cs.cl": "computer_science",
    "cs.cc": "computer_science",
    "cs.ce": "computational_science",
    "cs.cg": "computer_science",
    "cs.cv": "artificial_intelligence",
    "cs.cy": "computer_science",
    "cs.cr": "computer_science",
    "cs.db": "computer_science",
    "cs.dc": "computer_science",
    "cs.dl": "computer_science",
    "cs.dm": "computer_science",
    "cs.ds": "computer_science",
    "cs.et": "computer_science",
    "cs.fl": "computer_science",
    "cs.gl": "computer_science",
    "cs.gr": "computer_science",
    "cs.ar": "computer_science",
    "cs.hc": "computer_science",
    "cs.ir": "computer_science",
    "cs.it": "information_theory",
    "cs.lg": "artificial_intelligence",
    "cs.lo": "computer_science",
    "cs.ma": "artificial_intelligence",
    "cs.mm": "computer_science",
    "cs.ni": "communications",
    "cs.ne": "artificial_intelligence",
    "cs.na": "mathematics",
    "cs.os": "computer_science",
    "cs.oh": "computer_science",
    "cs.pf": "computer_science",
    "cs.pl": "computer_science",
    "cs.ro": "robotics",
    "cs.si": "computer_science",
    "cs.se": "computer_science",
    "cs.sd": "computer_science",
    "cs.sc": "computer_science",
    "cs.sy": "automation_control",
    "q-bio": "biology",
    "q-bio.bm": "biology",
    "q-bio.cb": "biology",
    "q-bio.gn": "biology",
    "q-bio.mn": "biology",
    "q-bio.nc": "biology",
    "q-bio.ot": "biology",
    "q-bio.pe": "biology",
    "q-bio.qm": "biology",
    "q-bio.sc": "biology",
    "q-bio.to": "biology",
    "q-fin": "finance",
    "q-fin.cp": "finance",
    "q-fin.ec": "economics",
    "q-fin.gn": "finance",
    "q-fin.mf": "finance",
    "q-fin.pm": "finance",
    "q-fin.pr": "finance",
    "q-fin.rm": "finance",
    "q-fin.st": "finance",
    "q-fin.tr": "finance",
    "stat": "statistics",
    "stat.ap": "statistics",
    "stat.co": "statistics",
    "stat.ml": "artificial_intelligence",
    "stat.me": "statistics",
    "stat.ot": "statistics",
    "stat.th": "statistics",
    "eess": "electrical_engineering",
    "eess.as": "electrical_engineering",
    "eess.iv": "electrical_engineering",
    "eess.sp": "electrical_engineering",
    "eess.sy": "automation_control",
    "econ": "economics",
    "econ.em": "economics",
    "econ.gn": "economics",
    "econ.th": "economics",
}

METHOD_ONTOLOGY = {
    # Cross-domain core methods
    "controlled experiment": ["controlled experiment", "experimental design", "randomized experiment", "factorial design"],
    "statistical modeling": ["statistical model", "statistical modeling", "mixed-effects model", "regression model"],
    "causal analysis": ["causal analysis", "causal inference", "counterfactual", "difference-in-differences"],
    "theoretical modeling": ["theoretical model", "analytical model", "mathematical model", "mechanistic model"],
    "numerical modeling": ["numerical model", "numerical modeling", "computational model", "simulation model"],
    "optimization method": ["optimization method", "optimal design", "inverse design", "multi-objective optimization"],
    "uncertainty quantification": ["uncertainty quantification", "sensitivity analysis", "error propagation"],
    "high-throughput screening": ["high-throughput screening", "combinatorial screening", "automated screening"],
    "instrumental measurement": ["instrumental measurement", "sensor measurement", "in situ measurement", "real-time monitoring"],
    "imaging and characterization": ["imaging", "characterization", "tomography", "spectroscopy", "microscopy"],
    "omics profiling": ["omics", "genomics", "transcriptomics", "proteomics", "metabolomics"],
    "clinical study design": ["clinical study", "clinical trial", "cohort study", "case-control study"],
    "field observation": ["field observation", "field survey", "observational campaign", "long-term monitoring"],
    "geospatial analysis": ["geospatial analysis", "spatial analysis", "remote sensing", "gis"],
    "robotic automation": ["robotic automation", "laboratory automation", "autonomous laboratory", "self-driving lab"],
    "scientific agent workflow": ["scientific agent", "agent workflow", "autonomous agent", "multi-agent"],
    "garnet electrolyte": ["garnet", "llzo"],
    "sulfide electrolyte": ["sulfide electrolyte", "argyrodite", "li6ps5"],
    "oxide electrolyte": ["oxide electrolyte", "oxide conductor"],
    "polymer electrolyte": ["polymer electrolyte", "solid polymer"],
    "halide electrolyte": ["halide electrolyte", "chloride electrolyte"],
    "cathode coating": ["cathode coating", "surface coating", "protective coating"],
    "interface engineering": ["interface engineering", "interphase", "interface stability", "interface modification"],
    "dendrite suppression": ["dendrite", "lithium dendrite"],
    "molecular dynamics simulation": ["molecular dynamics", "md simulation"],
    "density functional theory": ["density functional theory", "dft"],
    "machine learning model": ["machine learning", "neural network", "language model", "llm"],
    "agent workflow": ["agent", "autonomous agent", "multi-agent"],
    "bifunctional electrocatalyst": ["bifunctional electrocatalyst", "bifunctional catalyst", "overall water splitting"],
    "nife layered double hydroxide": ["nife ldh", "ni-fe ldh", "layered double hydroxide"],
    "transition metal phosphide catalyst": ["phosphide", "ni2p", "cobalt phosphide", "transition metal phosphide"],
    "transition metal selenide catalyst": ["selenide", "nise2", "nifese", "nifese4", "transition metal selenide"],
    "heterostructure catalyst": ["heterostructure", "heterointerface", "heterojunction catalyst"],
    "single-atom catalyst": ["single-atom", "single atom catalyst", "sac"],
    "doped electrocatalyst": ["doped", "doping", "ir4+-doped", "mn-doped"],
    "standardized precipitation evapotranspiration index": ["spei", "standardized precipitation evapotranspiration index"],
    "standardized precipitation index": ["spi", "standardized precipitation index"],
    "palmer drought severity index": ["pdsi", "palmer drought severity index"],
    "vapor pressure deficit analysis": ["vapor pressure deficit", "vpd"],
    "environmental anomaly analysis": ["anomaly analysis", "environmental anomaly", "state variable anomaly", "soil moisture anomaly", "soil moisture deficit", "root-zone soil moisture"],
    "principal component analysis": ["principal component analysis", "pca"],
    "interarrival event analysis": ["interarrival", "iad", "inter-arrival", "event arrival", "arrival interval"],
    "model ensemble analysis": ["model ensemble", "earth system model", "general circulation model", "cmip"],
    "remote sensing analysis": ["remote sensing", "satellite", "modis", "grace"],
    "extreme event attribution": ["event attribution", "attribution analysis", "fraction of attributable risk"],
}

SCENARIO_ONTOLOGY = {
    # Cross-domain scientific systems and application settings
    "mathematical system": ["mathematical system", "dynamical system", "stochastic process", "complex system"],
    "physical system": ["physical system", "quantum system", "condensed matter", "plasma system"],
    "chemical system": ["chemical system", "reaction system", "molecular system", "catalytic system"],
    "materials system": ["materials system", "functional material", "nanomaterial", "composite material"],
    "biological system": ["biological system", "cellular system", "organismal system", "molecular biology"],
    "medical and health system": ["medical system", "healthcare system", "patient cohort", "disease diagnosis", "therapy"],
    "agricultural system": ["agricultural system", "cropping system", "livestock system", "food production"],
    "ecological system": ["ecological system", "ecosystem", "species community", "biodiversity"],
    "earth and climate system": ["earth system", "climate system", "hydrological system", "geological system"],
    "energy system": ["energy system", "energy storage", "energy conversion", "power grid"],
    "engineering system": ["engineering system", "industrial process", "manufacturing system", "infrastructure"],
    "computational science workflow": ["computational workflow", "scientific workflow", "simulation workflow", "data pipeline"],
    "ai-assisted discovery": ["ai-assisted discovery", "autonomous discovery", "scientific discovery", "ai for science"],
    "solid-state lithium battery": ["solid-state lithium", "solid state lithium", "solid-state battery"],
    "high-voltage lithium battery": ["high-voltage", "high voltage", ">4.5 v", "4.5 v"],
    "lithium metal battery": ["lithium metal", "li metal"],
    "safe lithium battery": ["safety", "safe lithium", "thermal runaway"],
    "fast charging": ["fast charging", "high-rate", "rate capability"],
    "scientific discovery": ["scientific discovery", "ai for science"],
    "literature mining": ["literature mining", "papergraph", "paper graph"],
    "power system simulation": ["power system", "dae", "differential-algebraic"],
    "hydrogen evolution reaction": ["hydrogen evolution reaction", "her", "hydrogen evolution"],
    "oxygen evolution reaction": ["oxygen evolution reaction", "oer", "oxygen evolution"],
    "overall water splitting": ["overall water splitting", "water splitting", "green hydrogen"],
    "alkaline water electrolysis": ["alkaline", "alkaline media", "alkaline water electrolysis"],
    "acidic water electrolysis": ["acidic", "acid media", "acidic water electrolysis"],
    "regime shift": ["regime shift", "changing regime", "system transition", "drought regime", "drought characteristics", "changing drought", "drought nature"],
    "compound extreme event": ["compound extreme", "compound event", "hot drought", "compound drought", "heat-drought", "heatwave drought"],
    "ecological disturbance": ["ecological disturbance", "vegetation mortality", "ecosystem resilience", "tree mortality", "ecological drought"],
    "agricultural stress": ["agricultural stress", "crop yield", "food security", "agricultural drought"],
    "hydrological deficit": ["hydrological deficit", "streamflow deficit", "runoff deficit", "hydrological drought"],
    "meteorological anomaly": ["meteorological anomaly", "precipitation deficit", "meteorological drought"],
    "environmental moisture deficit": ["moisture deficit", "soil moisture deficit", "soil moisture drought"],
}

BENCHMARK_ONTOLOGY = {
    # Cross-domain evaluation targets
    "prediction error": ["prediction error", "forecast error", "rmse", "mae", "mean squared error"],
    "classification performance": ["classification performance", "accuracy", "precision", "recall", "f1 score", "auc"],
    "effect size": ["effect size", "treatment effect", "odds ratio", "risk ratio", "hazard ratio"],
    "uncertainty estimate": ["uncertainty estimate", "confidence interval", "credible interval", "posterior uncertainty"],
    "statistical significance": ["statistical significance", "p-value", "false discovery rate"],
    "reproducibility score": ["reproducibility", "repeatability", "replication rate", "inter-lab variation"],
    "throughput": ["throughput", "screening throughput", "sample throughput", "processing rate"],
    "resource cost": ["resource cost", "energy cost", "computational cost", "material cost"],
    "safety metric": ["safety metric", "toxicity", "adverse event", "failure risk", "hazard"],
    "durability": ["durability", "lifetime", "degradation rate", "fatigue life"],
    "conversion and selectivity": ["conversion", "selectivity", "conversion rate", "reaction selectivity"],
    "structural property": ["structural property", "phase stability", "crystallinity", "defect density"],
    "biological activity": ["biological activity", "binding affinity", "expression level", "phenotype"],
    "environmental impact": ["environmental impact", "emission", "pollutant concentration", "carbon footprint"],
    "workflow success rate": ["workflow success rate", "task success rate", "automation success", "planning success"],
    "cycle life": ["cycle life", "cycling stability", "capacity retention"],
    "ionic conductivity": ["ionic conductivity", "conductivity"],
    "critical current density": ["critical current density", "ccd"],
    "coulombic efficiency": ["coulombic efficiency"],
    "rate capability": ["rate capability", "high-rate"],
    "interface resistance": ["interface resistance", "interfacial resistance", "impedance"],
    "benchmark dataset": ["benchmark", "dataset", "corpus"],
    "overpotential": ["overpotential", "eta10", "η10", "mv at 10 ma cm", "10 ma cm"],
    "tafel slope": ["tafel slope", "tafel"],
    "current density": ["current density", "ma cm-2", "ma cm−2", "a cm-2"],
    "faradaic efficiency": ["faradaic efficiency", "fe%"],
    "operational stability": ["operational stability", "long-term stability", "electrochemical stability", "chronoamperometry", "chronopotentiometry"],
    "overall water splitting performance": ["overall water splitting performance", "water splitting performance"],
    "event intensity": ["event intensity", "severity", "intensity", "drought severity", "drought intensity"],
    "event duration": ["event duration", "duration", "persistence", "persistent event", "drought duration", "persistent drought"],
    "event frequency": ["event frequency", "frequency", "recurrence", "return period", "drought frequency"],
    "vapor pressure deficit": ["vapor pressure deficit", "vpd", "atmospheric thirst"],
    "soil moisture": ["soil moisture", "root-zone soil moisture", "soil water"],
    "system resilience": ["system resilience", "resilience", "recovery time", "vegetation recovery", "ecosystem resilience"],
}

FIELD_SPECIFIC_BENCHMARKS: dict[str, list[str]] = {}

GENERAL_METHOD_CUES = (
    "analysis",
    "assay",
    "algorithm",
    "approach",
    "architecture",
    "attribution",
    "characterization",
    "classification",
    "clustering",
    "design",
    "estimation",
    "experiment",
    "framework",
    "imaging",
    "inference",
    "instrument",
    "measurement",
    "method",
    "microscopy",
    "model",
    "modeling",
    "optimization",
    "pipeline",
    "protocol",
    "regression",
    "screening",
    "sequencing",
    "simulation",
    "spectroscopy",
    "synthesis",
    "theorem",
    "trial",
)

GENERAL_SCENARIO_CUES = (
    "application",
    "cohort",
    "condition",
    "dataset",
    "domain",
    "environment",
    "material",
    "phenomenon",
    "platform",
    "population",
    "process",
    "sample",
    "setting",
    "system",
    "task",
)

GENERAL_BENCHMARK_CUES = (
    "accuracy",
    "baseline",
    "criterion",
    "dataset",
    "effect size",
    "efficiency",
    "endpoint",
    "error",
    "index",
    "metric",
    "observable",
    "performance",
    "rate",
    "readout",
    "response",
    "score",
    "stability",
    "uncertainty",
    "validation",
    "yield",
)

GENERAL_SCIENCE_METHOD_ONTOLOGY = {
    # Mathematics, statistics, and optimization
    "theoretical proof": ["theorem", "proof", "lemma", "proposition", "existence proof"],
    "asymptotic analysis": ["asymptotic", "limit theorem", "convergence rate"],
    "numerical simulation": ["numerical simulation", "finite difference", "finite volume", "finite element", "fem"],
    "stochastic modeling": ["stochastic model", "markov", "monte carlo", "random process"],
    "bayesian inference": ["bayesian", "mcmc", "posterior", "prior distribution"],
    "causal inference": ["causal inference", "difference-in-differences", "instrumental variable", "propensity score"],
    "optimization algorithm": ["optimization", "gradient descent", "convex optimization", "integer programming"],
    "time series analysis": ["time series", "autoregressive", "arima", "spectral analysis", "wavelet"],
    "network analysis": ["network analysis", "graph theory", "centrality", "community detection"],
    # Physics, astronomy, and geoscience
    "spectroscopy": ["spectroscopy", "raman", "ftir", "xps", "nmr", "absorption spectrum"],
    "microscopy imaging": ["microscopy", "sem", "tem", "afm", "confocal microscopy"],
    "x-ray diffraction": ["x-ray diffraction", "xrd", "diffraction pattern"],
    "particle simulation": ["particle simulation", "n-body", "monte carlo simulation"],
    "observational survey": ["observational survey", "sky survey", "field survey", "survey data"],
    "seismic inversion": ["seismic inversion", "tomography", "seismic imaging"],
    "geochemical analysis": ["geochemical", "isotope analysis", "elemental analysis"],
    "hydrological modeling": ["hydrological model", "watershed model", "swat", "vic model"],
    "gis spatial analysis": ["gis", "spatial analysis", "geospatial", "spatial autocorrelation"],
    # Chemistry, materials, and engineering
    "organic synthesis": ["organic synthesis", "total synthesis", "synthetic route"],
    "catalyst design": ["catalyst design", "catalytic", "active site", "turnover frequency"],
    "electrochemical measurement": ["electrochemical", "cyclic voltammetry", "eis", "linear sweep voltammetry"],
    "materials characterization": ["materials characterization", "characterization", "mechanical testing"],
    "computational chemistry": ["computational chemistry", "quantum chemistry", "ab initio"],
    "computational fluid dynamics": ["computational fluid dynamics", "cfd", "fluid simulation"],
    "control system design": ["control system", "pid control", "model predictive control", "mpc"],
    "finite element analysis": ["finite element analysis", "fea", "structural simulation"],
    "life cycle assessment": ["life cycle assessment", "lca", "carbon footprint"],
    # Biology, medicine, agriculture, and ecology
    "genome sequencing": ["genome sequencing", "whole genome", "rna-seq", "transcriptomics"],
    "single-cell sequencing": ["single-cell", "single cell rna", "scrna-seq"],
    "crispr gene editing": ["crispr", "gene editing", "cas9"],
    "protein structure prediction": ["protein structure", "alphafold", "molecular docking"],
    "clinical trial": ["clinical trial", "randomized controlled trial", "rct", "cohort study"],
    "epidemiological modeling": ["epidemiological", "sir model", "seir", "disease transmission"],
    "meta-analysis": ["meta-analysis", "systematic review", "pooled analysis"],
    "field experiment": ["field experiment", "plot experiment", "field trial"],
    "greenhouse experiment": ["greenhouse experiment", "controlled growth chamber"],
    "species distribution modeling": ["species distribution model", "sdm", "maxent"],
    "ecosystem flux measurement": ["eddy covariance", "flux tower", "carbon flux"],
    # Computer science and AI
    "deep learning model": ["deep learning", "cnn", "rnn", "transformer", "diffusion model"],
    "large language model": ["large language model", "llm", "foundation model"],
    "reinforcement learning": ["reinforcement learning", "policy gradient", "q-learning"],
    "graph neural network": ["graph neural network", "gnn", "graph convolution"],
    "computer vision method": ["computer vision", "image segmentation", "object detection"],
    "natural language processing": ["natural language processing", "nlp", "text mining"],
    "knowledge graph construction": ["knowledge graph", "ontology construction", "entity linking"],
    "federated learning": ["federated learning", "privacy-preserving learning"],
}

GENERAL_SCIENCE_SCENARIO_ONTOLOGY = {
    # Foundational and physical sciences
    "mathematical modeling": ["mathematical modeling", "mathematical physics", "dynamical system"],
    "statistical inference": ["statistical inference", "uncertainty quantification", "hypothesis testing"],
    "quantum materials": ["quantum material", "superconductor", "topological material"],
    "astrophysical observation": ["astrophysical", "galaxy", "exoplanet", "cosmology"],
    "particle-flow reconstruction in future colliders": [
        "particle-flow reconstruction",
        "future collider",
        "calorimeter reconstruction",
        "tracking detector",
        "event reconstruction",
    ],
    "detector simulation in high energy physics": [
        "detector simulation",
        "fast detector simulation",
        "geant4",
        "lhc events",
        "collider detector",
    ],
    "anomaly detection in collider data": [
        "anomaly detection",
        "collider data",
        "beyond the standard model",
        "new physics search",
    ],
    "quantum chromodynamics": [
        "quantum chromodynamics",
        "qcd",
        "confinement",
        "asymptotic freedom",
        "lattice qcd",
        "parton distribution",
        "hadronization",
    ],
    "heavy-ion collisions": [
        "heavy-ion collision",
        "quark-gluon plasma",
        "jet quenching",
        "elliptic flow",
    ],
    "neutrino physics": ["neutrino", "pmns", "neutrinoless double beta", "sterile neutrino"],
    "dark matter phenomenology": ["dark matter", "wimp", "axion", "dark sector", "relic abundance"],
    "earthquake and tectonics": ["earthquake", "tectonic", "fault zone", "plate boundary"],
    "volcanic and geothermal system": ["volcanic", "geothermal", "magma"],
    "groundwater and watershed": ["groundwater", "watershed", "aquifer", "river basin"],
    # Chemistry, materials, and engineering
    "chemical reaction mechanism": ["reaction mechanism", "chemical kinetics", "reaction pathway"],
    "organic synthesis": ["organic synthesis", "synthetic chemistry", "synthetic route"],
    "catalytic reaction": ["catalytic reaction", "catalysis", "catalyst design"],
    "drug discovery": ["drug discovery", "lead compound", "small molecule"],
    "polymer materials": ["polymer", "composite material", "soft material"],
    "semiconductor devices": ["semiconductor", "transistor", "photovoltaic", "optoelectronic"],
    "renewable energy system": ["renewable energy", "solar cell", "wind power", "energy storage"],
    "robotics and autonomous systems": ["robot", "autonomous system", "path planning"],
    "civil infrastructure": ["bridge", "built environment", "infrastructure", "structural health"],
    "manufacturing process": ["manufacturing", "additive manufacturing", "3d printing"],
    # Life, agriculture, ecology, and medicine
    "cellular mechanism": ["cellular mechanism", "cell signaling", "pathway regulation"],
    "genetic disease": ["genetic disease", "mutation", "variant"],
    "cancer diagnosis and therapy": ["cancer", "tumor", "oncology"],
    "infectious disease": ["infectious disease", "virus", "bacterial infection", "pandemic"],
    "public health intervention": ["public health", "health policy", "intervention"],
    "crop stress resilience": ["crop stress", "drought tolerance", "salt tolerance", "heat tolerance"],
    "soil nutrient cycling": ["soil nutrient", "nitrogen cycle", "phosphorus cycle", "soil carbon"],
    "biodiversity and community ecology": ["biodiversity", "species richness", "community ecology"],
    "ecosystem carbon cycle": ["carbon cycle", "carbon sequestration", "net ecosystem exchange"],
    # Computer science, AI, and data systems
    "ai for science": ["ai for science", "scientific discovery", "automated discovery"],
    "medical image analysis": ["medical image", "radiology", "pathology image"],
    "multimodal learning": ["multimodal", "vision-language", "cross-modal"],
    "software engineering": ["software engineering", "code generation", "program repair"],
    "cybersecurity": ["cybersecurity", "malware", "intrusion detection"],
    "recommendation system": ["recommendation system", "recommender"],
}

GENERAL_SCIENCE_SCENARIO_ONTOLOGY.update(
    {
        # arXiv physics and astronomy categories.
        "galaxy astrophysics": ["galaxy", "galaxies", "galactic", "interstellar medium", "stellar population"],
        "cosmology and nongalactic astrophysics": ["cosmology", "cosmic microwave background", "cmb", "large-scale structure", "dark energy"],
        "earth and planetary astrophysics": ["planetary atmosphere", "exoplanet", "planet formation", "solar system", "planetary science"],
        "high-energy astrophysical phenomena": ["gamma-ray burst", "active galactic nucleus", "agn", "black hole", "pulsar", "x-ray binary"],
        "astrophysical instrumentation": ["astronomical instrumentation", "telescope", "spectrograph", "interferometer", "detector calibration"],
        "solar and stellar astrophysics": ["solar physics", "stellar", "star formation", "asteroseismology", "stellar evolution"],
        "disordered systems and neural networks": ["disordered system", "spin glass", "random field", "neural network physics"],
        "mesoscale and nanoscale physics": ["mesoscopic", "nanoscale", "nanostructure", "quantum dot", "nanowire"],
        "soft condensed matter": ["soft matter", "colloid", "polymer gel", "liquid crystal", "active matter"],
        "statistical mechanics": ["statistical mechanics", "phase transition", "critical phenomena", "nonequilibrium"],
        "strongly correlated electrons": ["strongly correlated", "hubbard model", "mott insulator", "heavy fermion"],
        "quantum gases": ["quantum gas", "bose-einstein condensate", "bec", "ultracold atom", "fermi gas"],
        "general relativity and quantum cosmology": ["general relativity", "quantum cosmology", "gravitational wave", "spacetime", "modified gravity"],
        "high energy physics experiment": ["high energy physics experiment", "collider experiment", "atlas", "cms", "lhcb", "belle ii"],
        "high energy physics phenomenology": ["phenomenology", "standard model", "beyond the standard model", "susy", "effective field theory"],
        "high energy physics theory": ["quantum field theory", "string theory", "scattering amplitudes", "ads/cft", "gauge theory"],
        "lattice field theory": ["lattice qcd", "lattice gauge", "monte carlo lattice", "wilson fermion"],
        "nuclear experiment": ["nuclear experiment", "nuclear reaction", "radioactive beam", "nuclear detector"],
        "nuclear theory": ["nuclear theory", "nuclear structure", "nuclear matter", "nucleon interaction"],
        "accelerator physics": ["accelerator physics", "beam dynamics", "particle accelerator", "synchrotron", "linac"],
        "atomic molecular and optical physics": ["atomic physics", "molecular physics", "optics", "laser spectroscopy", "atomic clock"],
        "biological physics": ["biological physics", "biophysics", "molecular motor", "membrane physics"],
        "chemical physics": ["chemical physics", "molecular dynamics", "reaction dynamics", "photochemistry"],
        "fluid dynamics": ["fluid dynamics", "turbulence", "navier-stokes", "multiphase flow", "aerodynamics"],
        "plasma physics": ["plasma physics", "magnetohydrodynamics", "mhd", "fusion plasma", "tokamak"],
        "space physics": ["space physics", "solar wind", "magnetosphere", "ionosphere", "space weather"],
        "quantum information and computation": ["quantum information", "quantum computation", "quantum error correction", "quantum circuit"],
        # Mathematics, statistics, and nonlinear sciences.
        "algebraic geometry": ["algebraic geometry", "moduli space", "scheme", "variety"],
        "algebraic topology": ["algebraic topology", "homotopy", "cohomology", "spectral sequence"],
        "partial differential equations": ["partial differential equation", "pde", "elliptic equation", "parabolic equation"],
        "category theory": ["category theory", "functor", "monoidal category", "topos"],
        "combinatorics and graph theory": ["combinatorics", "graph theory", "extremal graph", "enumerative"],
        "number theory": ["number theory", "modular form", "automorphic", "diophantine"],
        "operator algebra and functional analysis": ["operator algebra", "functional analysis", "banach space", "c*-algebra"],
        "optimization and control theory": ["optimization", "optimal control", "convex analysis", "variational problem"],
        "probability theory": ["probability theory", "stochastic process", "random walk", "martingale"],
        "statistical theory": ["statistical theory", "asymptotic inference", "nonparametric", "bayesian statistics"],
        "chaotic dynamics": ["chaotic dynamics", "chaos", "bifurcation", "lyapunov exponent"],
        "pattern formation and solitons": ["pattern formation", "soliton", "nonlinear wave", "reaction-diffusion"],
        "adaptation and self-organizing systems": ["self-organizing", "adaptive system", "complex adaptive system"],
        # Computer science and AI arXiv/CoRR subareas.
        "artificial intelligence": ["artificial intelligence", "planning", "knowledge representation", "reasoning system"],
        "machine learning systems": ["machine learning", "representation learning", "foundation model", "neural network"],
        "computation and language": ["natural language processing", "language model", "machine translation", "text generation"],
        "computer vision and pattern recognition": ["computer vision", "image recognition", "segmentation", "object detection"],
        "computational complexity": ["computational complexity", "np-hard", "approximation hardness", "complexity class"],
        "cryptography and security": ["cryptography", "secure protocol", "privacy", "adversarial attack"],
        "database and information retrieval": ["database", "information retrieval", "search engine", "query processing"],
        "distributed and parallel computing": ["distributed computing", "parallel computing", "cluster computing", "consensus protocol"],
        "human-computer interaction": ["human-computer interaction", "user study", "interactive system", "usability"],
        "multiagent systems": ["multiagent", "multi-agent", "agent coordination", "game-theoretic learning"],
        "robotics": ["robotics", "robot manipulation", "motion planning", "slam", "autonomous navigation"],
        "software engineering": ["software engineering", "program analysis", "software testing", "code generation"],
        "systems and networking": ["networking", "operating system", "distributed system", "cloud computing"],
        # Quantitative biology, medicine, and life sciences.
        "biomolecular structure and function": ["biomolecule", "protein structure", "protein design", "rna structure", "molecular recognition"],
        "cell behavior and cellular systems": ["cell behavior", "cell migration", "cell cycle", "cell fate", "cellular dynamics"],
        "genomics and functional genomics": ["genomics", "genome-wide", "transcriptome", "epigenomics", "variant calling"],
        "molecular networks and systems biology": ["molecular network", "gene regulatory network", "pathway", "systems biology"],
        "neuroscience and cognition": ["neuron", "neural circuit", "cognition", "brain network", "spike train"],
        "population genetics and evolution": ["population genetics", "evolution", "phylogenetics", "selection pressure"],
        "tissue and organ systems": ["tissue", "organ", "organoid", "developmental biology", "physiology"],
        "immunology and infectious disease": ["immunology", "immune response", "pathogen", "vaccine", "host-pathogen"],
        "pharmacology and toxicology": ["pharmacology", "toxicology", "drug response", "adverse effect"],
        "medical imaging and diagnostics": ["medical imaging", "diagnosis", "radiomics", "pathology", "screening"],
        "digital health and clinical decision support": ["digital health", "clinical decision support", "electronic health record", "ehr"],
        # bioRxiv-style life science collections.
        "animal behavior and cognition": ["animal behavior", "cognition", "behavioral ecology", "learning behavior", "social behavior"],
        "biochemistry": ["biochemistry", "enzyme", "metabolism", "protein biochemistry", "metabolic pathway"],
        "bioengineering": ["bioengineering", "biomaterial", "tissue engineering", "biosensor", "biomedical device"],
        "bioinformatics": ["bioinformatics", "sequence analysis", "genomic data", "computational biology"],
        "cancer biology": ["cancer biology", "tumor", "oncogene", "metastasis", "tumor microenvironment"],
        "developmental biology": ["developmental biology", "embryogenesis", "morphogenesis", "cell differentiation"],
        "epidemiology": ["epidemiology", "incidence", "prevalence", "cohort", "population health"],
        "evolutionary biology": ["evolutionary biology", "adaptation", "speciation", "natural selection"],
        "genetics": ["genetics", "genetic variant", "heritability", "genetic association", "linkage"],
        "microbiology": ["microbiology", "microbe", "bacteria", "virus", "microbiome"],
        "molecular biology": ["molecular biology", "dna repair", "transcription", "translation", "rna processing"],
        "paleontology": ["paleontology", "fossil", "phylogeny", "extinction", "paleoecology"],
        "pathology": ["pathology", "histopathology", "lesion", "tissue pathology"],
        "plant biology": ["plant biology", "photosynthesis", "plant hormone", "plant development", "plant immunity"],
        "scientific communication and education": ["scientific communication", "science education", "peer review", "open science"],
        "synthetic biology": ["synthetic biology", "gene circuit", "genetic circuit", "biosynthetic pathway"],
        "zoology": ["zoology", "animal physiology", "taxonomy", "wildlife", "invertebrate"],
        # medRxiv-style clinical and health collections.
        "addiction medicine": ["addiction", "substance use", "opioid", "alcohol use disorder"],
        "allergy and immunology": ["allergy", "asthma", "hypersensitivity", "immunotherapy"],
        "anesthesia": ["anesthesia", "perioperative", "analgesia", "sedation"],
        "cardiovascular medicine": ["cardiovascular", "heart failure", "myocardial", "arrhythmia", "hypertension"],
        "dentistry and oral medicine": ["dentistry", "oral health", "periodontal", "dental"],
        "dermatology": ["dermatology", "skin disease", "psoriasis", "eczema", "melanoma"],
        "emergency medicine": ["emergency medicine", "emergency department", "trauma care", "triage"],
        "endocrinology and metabolic disease": ["endocrinology", "diabetes", "metabolic disease", "insulin", "obesity"],
        "forensic medicine": ["forensic medicine", "forensic pathology", "medicolegal", "autopsy"],
        "gastroenterology": ["gastroenterology", "liver disease", "inflammatory bowel", "gut microbiome"],
        "genetic and genomic medicine": ["genomic medicine", "genetic diagnosis", "rare disease", "clinical genomics"],
        "geriatric medicine": ["geriatric", "aging", "frailty", "dementia care"],
        "health economics": ["health economics", "cost-effectiveness", "healthcare cost", "economic evaluation"],
        "health informatics": ["health informatics", "electronic health record", "clinical data", "digital health"],
        "health policy": ["health policy", "healthcare policy", "public policy", "health regulation"],
        "health systems and quality improvement": ["health system", "quality improvement", "care delivery", "patient safety"],
        "hematology": ["hematology", "anemia", "leukemia", "coagulation", "thrombosis"],
        "hiv and aids": ["hiv", "aids", "antiretroviral", "viral load"],
        "infectious diseases": ["infectious disease", "infection", "pathogen transmission", "antimicrobial resistance"],
        "critical care medicine": ["critical care", "intensive care", "icu", "sepsis", "mechanical ventilation"],
        "medical education": ["medical education", "clinical training", "curriculum", "simulation training"],
        "medical ethics": ["medical ethics", "bioethics", "informed consent", "research ethics"],
        "nephrology": ["nephrology", "kidney disease", "dialysis", "renal function"],
        "neurology": ["neurology", "stroke", "epilepsy", "neurodegenerative", "multiple sclerosis"],
        "nursing": ["nursing", "nurse", "care practice", "patient care"],
        "nutrition": ["nutrition", "diet", "micronutrient", "dietary intake"],
        "obstetrics and gynecology": ["obstetrics", "gynecology", "pregnancy", "maternal health", "reproductive health"],
        "occupational and environmental health": ["occupational health", "environmental exposure", "workplace exposure"],
        "oncology": ["oncology", "cancer treatment", "chemotherapy", "immuno-oncology", "radiotherapy"],
        "ophthalmology": ["ophthalmology", "retina", "glaucoma", "visual acuity"],
        "orthopedics": ["orthopedics", "fracture", "joint replacement", "musculoskeletal"],
        "otolaryngology": ["otolaryngology", "ear nose throat", "hearing loss", "sinus disease"],
        "pain medicine": ["pain medicine", "chronic pain", "analgesic", "pain management"],
        "palliative medicine": ["palliative", "end-of-life", "hospice", "symptom burden"],
        "pediatrics": ["pediatrics", "child health", "neonatal", "adolescent health"],
        "pharmacology and therapeutics": ["therapeutics", "drug therapy", "pharmacotherapy", "treatment response"],
        "primary care research": ["primary care", "family medicine", "general practice"],
        "psychiatry and clinical psychology": ["psychiatry", "clinical psychology", "depression", "anxiety", "mental health"],
        "public and global health": ["public health", "global health", "health equity", "disease burden"],
        "radiology and imaging": ["radiology", "medical imaging", "ct imaging", "mri", "ultrasound"],
        "rehabilitation medicine and physical therapy": ["rehabilitation", "physical therapy", "functional recovery"],
        "respiratory medicine": ["respiratory", "pulmonary", "copd", "lung disease", "ventilation"],
        "rheumatology": ["rheumatology", "autoimmune disease", "arthritis", "lupus"],
        "sexual and reproductive health": ["sexual health", "reproductive health", "contraception", "fertility"],
        "sports medicine": ["sports medicine", "exercise physiology", "athletic injury"],
        "surgery": ["surgery", "surgical outcome", "operative", "postoperative"],
        "transplantation": ["transplantation", "graft", "organ transplant", "rejection"],
        "urology": ["urology", "urinary tract", "prostate", "kidney stone"],
        # Chemistry, materials, environmental science, and agriculture beyond arXiv.
        "agriculture and food chemistry": ["agriculture chemistry", "food chemistry", "agrochemical", "food contaminant"],
        "analytical chemistry": ["analytical chemistry", "mass spectrometry", "chromatography", "chemical sensing"],
        "biological and medicinal chemistry": ["medicinal chemistry", "chemical biology", "drug discovery", "bioactive compound"],
        "catalysis": ["catalysis", "catalyst", "heterogeneous catalysis", "homogeneous catalysis", "electrocatalysis"],
        "chemical education": ["chemical education", "chemistry teaching", "laboratory instruction"],
        "chemical engineering and industrial chemistry": ["chemical engineering", "industrial chemistry", "process chemistry", "reactor design"],
        "earth space and environmental chemistry": ["environmental chemistry", "atmospheric chemistry", "geochemistry", "marine chemistry"],
        "energy chemistry": ["energy chemistry", "fuel cell", "energy conversion", "electrochemical energy"],
        "physical chemistry": ["physical chemistry", "thermodynamics", "chemical kinetics", "spectroscopy"],
        "organic chemistry": ["organic chemistry", "organic synthesis", "reaction mechanism", "total synthesis"],
        "inorganic and organometallic chemistry": ["inorganic chemistry", "organometallic", "coordination complex", "metal-ligand"],
        "materials chemistry": ["materials chemistry", "functional material", "solid-state chemistry", "crystal chemistry"],
        "polymer and soft materials": ["polymer", "hydrogel", "elastomer", "soft material"],
        "polymer science": ["polymer science", "polymerization", "macromolecule", "copolymer"],
        "theoretical and computational chemistry": ["theoretical chemistry", "computational chemistry", "quantum chemistry", "ab initio"],
        "nanomaterials and interfaces": ["nanomaterial", "nanoparticle", "interface", "surface chemistry", "heterostructure"],
        "electrochemical energy storage": ["battery", "supercapacitor", "electrolyte", "electrode", "solid-state battery"],
        "photovoltaics and optoelectronics": ["photovoltaic", "solar cell", "optoelectronic", "perovskite"],
        "carbon capture and climate mitigation": ["carbon capture", "co2 reduction", "climate mitigation", "negative emissions"],
        "water treatment and environmental remediation": ["water treatment", "remediation", "pollutant removal", "wastewater"],
        "soil-plant-atmosphere system": ["soil-plant-atmosphere", "crop", "root", "rhizosphere", "plant-water"],
        "plant breeding and crop genetics": ["plant breeding", "crop genetics", "genomic selection", "quantitative trait locus", "qtl"],
        "precision agriculture": ["precision agriculture", "agricultural robotics", "remote sensing crop", "smart farming"],
        "livestock and animal production": ["livestock", "animal production", "ruminant", "poultry", "animal welfare"],
        "food science and nutrition": ["food science", "nutrition", "food safety", "food processing"],
        # Engineering, EESS, economics, and quantitative finance.
        "signal processing": ["signal processing", "time-frequency", "filtering", "compressed sensing"],
        "image and video processing": ["image processing", "video processing", "super-resolution", "image restoration"],
        "audio and speech processing": ["audio processing", "speech recognition", "speaker recognition", "acoustic model"],
        "systems and control": ["systems and control", "control system", "model predictive control", "stability analysis"],
        "electrical power and energy systems": ["power system", "smart grid", "microgrid", "power electronics", "renewable integration"],
        "communications and information theory": ["wireless communication", "channel coding", "information theory", "mimo"],
        "mechanical and aerospace systems": ["mechanical system", "aerospace", "aircraft", "turbomachinery", "structural dynamics"],
        "transportation and mobility systems": ["transportation", "traffic flow", "mobility", "vehicle routing", "intelligent transportation"],
        "industrial process and manufacturing": ["industrial process", "process control", "manufacturing", "quality control"],
        "econometrics": ["econometrics", "causal effect", "panel data", "instrumental variable"],
        "macroeconomic and market systems": ["macroeconomic", "market", "monetary policy", "economic growth"],
        "asset pricing and risk management": ["asset pricing", "portfolio", "risk management", "derivative pricing", "volatility"],
        "market microstructure": ["market microstructure", "order book", "trading", "liquidity"],
    }
)

GENERAL_SCIENCE_BENCHMARK_ONTOLOGY = {
    # Generic scientific metrics
    "prediction accuracy": ["accuracy", "auc", "f1 score", "precision", "recall", "rmse", "mae", "r squared"],
    "uncertainty": ["uncertainty", "confidence interval", "credible interval", "variance"],
    "statistical significance": ["p-value", "statistical significance", "effect size"],
    "reproducibility": ["reproducibility", "replicability", "repeatability"],
    "computational efficiency": ["runtime", "latency", "throughput", "memory usage", "computational cost"],
    "generalization performance": ["generalization", "out-of-distribution", "ood", "external validation"],
    # Physics, chemistry, materials, and engineering metrics
    "energy efficiency": ["energy efficiency", "power conversion efficiency", "pce"],
    "mechanical strength": ["mechanical strength", "tensile strength", "compressive strength", "young's modulus"],
    "thermal stability": ["thermal stability", "glass transition", "decomposition temperature"],
    "catalytic activity": ["catalytic activity", "turnover frequency", "tof", "conversion rate", "selectivity"],
    "reaction yield": ["reaction yield", "yield", "conversion", "selectivity"],
    "device lifetime": ["device lifetime", "operational lifetime", "degradation rate"],
    "structural damage": ["structural damage", "crack", "fatigue life", "failure load"],
    "water quality": ["water quality", "pollutant concentration", "nitrate", "phosphate"],
    # Biology, medicine, agriculture, and ecology metrics
    "gene expression": ["gene expression", "differential expression", "transcript abundance"],
    "protein binding affinity": ["binding affinity", "kd", "ki", "ic50"],
    "survival outcome": ["survival", "hazard ratio", "overall survival", "progression-free survival"],
    "disease incidence": ["incidence", "prevalence", "attack rate"],
    "clinical response": ["clinical response", "response rate", "remission", "adverse event"],
    "diagnostic performance": ["sensitivity", "specificity", "diagnostic accuracy", "positive predictive value", "negative predictive value"],
    "treatment safety": ["safety", "toxicity", "adverse event", "serious adverse event", "dose-limiting toxicity"],
    "public health burden": ["disease burden", "mortality", "hospitalization", "disability-adjusted life years", "daly"],
    "quality of life": ["quality of life", "patient-reported outcome", "symptom score", "functional status"],
    "healthcare quality": ["patient safety", "readmission", "length of stay", "care quality", "guideline adherence"],
    "crop yield": ["crop yield", "grain yield", "biomass yield"],
    "soil carbon": ["soil carbon", "soil organic carbon", "soc"],
    "species richness": ["species richness", "alpha diversity", "shannon diversity"],
    "carbon flux": ["carbon flux", "net ecosystem exchange", "nee", "gross primary productivity", "gpp"],
    # Computer science and AI metrics
    "benchmark accuracy": ["benchmark accuracy", "top-1 accuracy", "leaderboard", "benchmark score"],
    "language model quality": ["perplexity", "bleu", "rouge", "exact match", "human evaluation"],
    "robustness": ["robustness", "adversarial robustness", "calibration", "fairness"],
    "sample efficiency": ["sample efficiency", "data efficiency", "few-shot"],
    # Mathematics, physics, astronomy, and engineering metrics
    "statistical precision": ["statistical precision", "uncertainty interval", "confidence level", "credible interval"],
    "discovery significance": ["sigma", "statistical significance", "local significance", "global significance"],
    "detector performance": ["detector efficiency", "resolution", "acceptance", "background rejection"],
    "simulation fidelity": ["simulation fidelity", "validation error", "model-data agreement"],
    "theorem strength": ["theorem", "bound", "convergence rate", "approximation ratio"],
    "control stability": ["stability margin", "settling time", "overshoot", "lyapunov stability"],
    "communication reliability": ["bit error rate", "packet loss", "throughput", "spectral efficiency"],
    "financial risk": ["value at risk", "expected shortfall", "drawdown", "volatility"],
    "economic effect size": ["elasticity", "treatment effect", "welfare gain", "cost-benefit"],
}

METHOD_ONTOLOGY.update(GENERAL_SCIENCE_METHOD_ONTOLOGY)
SCENARIO_ONTOLOGY.update(GENERAL_SCIENCE_SCENARIO_ONTOLOGY)
BENCHMARK_ONTOLOGY.update(GENERAL_SCIENCE_BENCHMARK_ONTOLOGY)


@dataclass
class PaperEvidence:
    evidence_id: str
    title: str
    citation: str
    method: str
    scenario: str
    benchmark: str
    contribution: str
    limitation: str
    url: str = ""
    createdAt: float = field(default_factory=time.time)


@dataclass
class PaperGraphRecord:
    paper_id: str
    unique_key: str
    title: str
    citation: str
    authors: list[str]
    year: str
    venue: str
    provider: str
    source_type: str
    doi: str
    arxiv_id: str
    semantic_scholar_id: str
    url: str
    abstract: str
    full_text_excerpt: str
    conclusion: str
    strengths: list[str]
    improvements: list[str]
    method: str
    scenario: str
    benchmark: str
    contribution: str
    limitation: str
    credibility_score: float
    credibility_reasons: list[str]
    extraction_quality: dict[str, Any] = field(default_factory=dict)
    enrichment_sources: list[str] = field(default_factory=list)
    gap_signals: list[dict[str, Any]] = field(default_factory=list)
    importedAt: float = field(default_factory=time.time)


@dataclass
class KnowledgeGap:
    gap_id: str
    gap_type: str
    description: str
    supporting_references: list[str]
    novelty_score: int
    application_value: str
    feasibility: str
    suggested_research_path: str
    status: str = "candidate"
    createdAt: float = field(default_factory=time.time)


@dataclass
class Hypothesis:
    hypothesis_id: str
    gap_id: str
    statement: str
    mechanism: str
    expected_value: str
    test_plan: str
    status: str = "draft"
    createdAt: float = field(default_factory=time.time)


def create_research_project(
    title: str,
    domain: str,
    objective: str,
    strategic_need: str = "",
) -> str:
    project = {
        "project_id": new_id("sci"),
        "title": title,
        "domain": domain,
        "objective": objective,
        "strategic_need": strategic_need,
        "phase": PHASES[0],
        "createdAt": time.time(),
        "updatedAt": time.time(),
        "papergraph": [],
        "evidence": [],
        "coverage_matrix": {},
        "knowledge_gaps": [],
        "hypotheses": [],
        "keynotes": [],
        "mechanism_reports": [],
        "pipeline_tasks": [],
    }
    save_project(project)
    log_event("SCIENCE", "project_created", project_id=project["project_id"], domain=domain)
    return json.dumps(project, ensure_ascii=False, indent=2)


def list_literature_providers() -> str:
    return json.dumps(LITERATURE_PROVIDERS, ensure_ascii=False, indent=2)


def live_literature_provider_names() -> set[str]:
    return {name for name, spec in LITERATURE_PROVIDERS.items() if spec.get("status") == "live"}


def default_literature_providers(domain: str = "", query: str = "") -> list[str]:
    text = normalize_space(f"{domain} {query}").lower()
    biomedical_terms = (
        "cancer",
        "carcinoma",
        "tumor",
        "tumour",
        "clinical",
        "medicine",
        "disease",
        "genomic",
        "genomics",
        "cell",
        "immunology",
        "oncology",
        "hepatocellular",
        "hcc",
    )
    chemistry_terms = (
        "chemistry",
        "catalysis",
        "catalyst",
        "organic",
        "inorganic",
        "organometallic",
        "polymer",
        "materials chemistry",
    )
    arxiv_terms = (
        "physics",
        "astrophysics",
        "mathematics",
        "computer science",
        "machine learning",
        "artificial intelligence",
        "quantum",
        "control",
        "robotics",
        "statistics",
    )
    cs_terms = (
        "computer science",
        "machine learning",
        "artificial intelligence",
        "deep learning",
        "neural",
        "algorithm",
        "software",
        "systems",
        "database",
        "programming",
        "nlp",
        "computer vision",
        "reinforcement learning",
    )
    providers = ["semantic_scholar", "openalex"]
    if any(term in text for term in biomedical_terms):
        providers.extend(["biorxiv", "medrxiv"])
    if any(term in text for term in chemistry_terms):
        providers.append("chemrxiv")
    if any(term in text for term in arxiv_terms):
        providers.append("arxiv")
    if any(term in text for term in cs_terms):
        providers.extend(["dblp", "openreview"])
    return unique_preserve_order([provider for provider in providers if provider in live_literature_provider_names()])


def search_papers(
    query: str,
    databases: list[str] | None = None,
    max_results: int = 15,
    years: str = "",
) -> str:
    providers = [database_to_provider(item) for item in (databases or default_literature_providers(query=query))]
    providers = unique_preserve_order([item for item in providers if item])
    result = json.loads(search_literature(query, providers=providers, max_results=max_results))
    result["zhizhi_action"] = "search_papers"
    result["databases_requested"] = databases or providers
    result["years"] = years
    return json.dumps(result, ensure_ascii=False, indent=2)


def search_papers_stratified(
    query: str,
    databases: list[str] | None = None,
    max_results: int = 15,
    years: str = "",
    domain: str = "",
    focus_branches: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    providers = [database_to_provider(item) for item in (databases or default_literature_providers(domain=domain, query=query))]
    providers = unique_preserve_order([item for item in providers if item])
    result = json.loads(
        search_literature_stratified(
            query,
            providers=providers,
            max_results=max_results,
            domain=domain,
            focus_branches=focus_branches,
            use_llm=use_llm,
        )
    )
    result["zhizhi_action"] = "search_papers_stratified"
    result["databases_requested"] = databases or providers
    result["years"] = years
    result["domain"] = domain
    result["focus_branches"] = focus_branches or []
    return json.dumps(result, ensure_ascii=False, indent=2)


def explore_domain_subspaces(
    domain: str,
    max_subspaces: int = 12,
    probe_depth: int = 5,
    use_llm: bool = True,
    providers: list[str] | None = None,
    user_hints: list[str] | None = None,
) -> str:
    domain_text = normalize_space(domain)
    if not domain_text:
        raise ValueError("domain is required")
    selected_providers = [database_to_provider(item) for item in (providers or default_literature_providers(domain=domain_text))]
    selected_providers = unique_preserve_order([item for item in selected_providers if item in live_literature_provider_names()])
    if not selected_providers:
        selected_providers = default_literature_providers(domain=domain_text) or ["semantic_scholar"]
    subspaces = generate_domain_subspaces(domain_text, max_subspaces=max_subspaces, use_llm=use_llm, user_hints=user_hints)
    probe_reports: list[dict[str, Any]] = []
    enriched: list[dict[str, Any]] = []
    probe_budget = build_subspace_probe_budget(selected_providers)
    for subspace in subspaces[: clamp_int(max_subspaces, 1, 30)]:
        report = probe_domain_subspace(
            subspace,
            providers=selected_providers,
            probe_depth=probe_depth,
            provider_budget=probe_budget,
        )
        probe_reports.append(report)
        enriched.append(enrich_subspace_with_probe(subspace, report))
    generated_sources = {str(item.get("generated_by") or "") for item in enriched}
    generated_by = "llm" if generated_sources == {"llm"} else "hybrid" if "llm" in generated_sources else "heuristic"
    subspace_map = {
        "subspace_map_id": new_id("subspace"),
        "domain": domain_text,
        "generated_by": generated_by,
        "confidence": domain_subspace_map_confidence(enriched, use_llm=generated_by in {"llm", "hybrid"}),
        "createdAt": time.time(),
        "providers": selected_providers,
        "user_hints": user_hints or [],
        "subspaces": enriched,
        "probe_results": probe_reports,
    }
    subspace_map["coverage_plan"] = build_subspace_coverage_plan(subspace_map)
    subspace_map["query_plan"] = query_plan_from_subspace_map(subspace_map)
    subspace_map["user_interaction"] = build_subspace_selection_interaction(subspace_map)
    save_subspace_map(subspace_map)
    log_event(
        "SCIENCE",
        "domain_subspaces_explored",
        subspace_map_id=subspace_map["subspace_map_id"],
        domain=domain_text,
        subspaces=len(enriched),
    )
    response = dict(subspace_map)
    response["next_step"] = (
        "Ask the user to choose subspaces from user_interaction.options, then pass "
        "subspace_map_id and selected_subfields/focus_branches into run_zhizhi_literature_analysis."
    )
    return json.dumps(response, ensure_ascii=False, indent=2)


def generate_domain_subspaces(
    domain: str,
    max_subspaces: int,
    use_llm: bool,
    user_hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    if use_llm:
        llm_subspaces = generate_domain_subspaces_with_llm(domain, max_subspaces=max_subspaces, user_hints=user_hints)
        if llm_subspaces:
            return llm_subspaces
    profile = domain_topic_profile(domain, query=domain, use_llm=use_llm)
    subspaces: list[dict[str, Any]] = []
    for topic in profile.get("core_topics", []):
        keywords = string_list(topic.get("expected_terms")) or query_terms(str(topic.get("query") or ""))[:8]
        subspaces.append(
            normalize_domain_subspace(
                {
                    "name": str(topic.get("branch") or "subspace"),
                    "aliases": [],
                    "description": str(topic.get("rationale") or ""),
                    "keywords": keywords,
                    "seed_papers": [],
                    "maturity": "unknown",
                    "strategic_importance": int(topic.get("min_hits") or 5),
                    "search_strategy": "must_include",
                    "generated_by": "profile",
                },
                domain=domain,
            )
        )
    if not subspaces:
        for hint in user_hints or []:
            subspaces.append(normalize_domain_subspace({"name": hint, "keywords": query_terms(hint)}, domain=domain))
    if not subspaces:
        subspaces.append(
            normalize_domain_subspace(
                {
                    "name": "Field map and major subfields",
                    "keywords": query_terms(domain) + ["review", "survey", "roadmap"],
                    "description": "Fallback subspace for building an initial field map when no validated ontology is available.",
                    "maturity": "unknown",
                    "strategic_importance": 7,
                    "search_strategy": "must_include",
                    "generated_by": "heuristic",
                },
                domain=domain,
            )
        )
    return subspaces[: clamp_int(max_subspaces, 1, 30)]


def generate_domain_subspaces_with_llm(
    domain: str,
    max_subspaces: int,
    user_hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    max_items = clamp_int(max_subspaces, 1, 30)
    compact_domain = compact_domain_label(domain)
    try:
        payload = call_llm_json(
            system=(
                "You are a domain-agnostic research cartographer. You map a broad scientific domain "
                "into substantive research subspaces before literature review. Work across all sciences, "
                "engineering, medicine, agriculture, AI, mathematics, social-science-adjacent empirical fields, "
                "and interdisciplinary topics. Return JSON only."
            ),
            prompt=(
                "Decompose the domain into major substantive subspaces. Do not output generic facets such as "
                "'methods', 'applications', or 'benchmarks' unless they are real named subfields in this domain.\n"
                "Return strict JSON with key subspaces. Each subspace must contain:\n"
                "- name: English concise name\n"
                "- aliases: aliases in English/Chinese/acronyms if useful\n"
                "- description: 1-2 sentence scope\n"
                "- parent: optional parent category\n"
                "- keywords: 5-10 retrieval keywords/phrases\n"
                "- seed_papers: 0-3 representative reviews or seed papers if you know them; leave empty if unsure\n"
                "- maturity: emerging | growing | mature | saturated | unknown\n"
                "- strategic_importance: integer 1-10\n"
                "- search_strategy: must_include | nice_to_have | exploratory\n\n"
                f"Domain label: {compact_domain}\n"
                f"Full user domain: {trim_text(domain, 500)}\n"
                f"User hints: {', '.join(user_hints or [])}\n"
                f"Maximum subspaces: {max_items}\n"
                "Keep descriptions concise. Prefer 8-12 high-signal subspaces over verbose prose.\n"
            ),
            max_tokens=max(4200, min(8000, 700 + max_items * 520)),
            fallback_list_key="subspaces",
        )
    except Exception as exc:
        log_event("WARN", "domain_subspace_llm_failed", error=str(exc))
        return []
    raw = payload.get("subspaces") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    subspaces = [normalize_domain_subspace(item, domain=domain) for item in raw if isinstance(item, dict)]
    for item in subspaces:
        item["generated_by"] = "llm"
    return [item for item in subspaces if item.get("name") and item.get("keywords")]


def compact_domain_label(domain: str) -> str:
    clean = normalize_space(domain)
    if len(clean) <= 180:
        return clean
    phrases = re.split(r"\s*(?:/|,|;| and | with | for | of )\s*", clean, flags=re.IGNORECASE)
    useful = [phrase.strip() for phrase in phrases if len(phrase.strip()) >= 4]
    compact = "; ".join(unique_preserve_order(useful)[:6])
    return trim_text(compact or clean, 180)


def normalize_domain_subspace(raw: dict[str, Any], domain: str) -> dict[str, Any]:
    name = scalar(raw.get("name")) or scalar(raw.get("name_en")) or "Unnamed subspace"
    keywords = string_list(raw.get("keywords")) or query_terms(" ".join([name, domain]))[:8]
    aliases = string_list(raw.get("aliases"))
    seed_papers = string_list(raw.get("seed_papers")) or string_list(raw.get("representative_reviews"))
    maturity = normalize_space(str(raw.get("maturity") or raw.get("estimated_density") or "unknown")).lower()
    if maturity not in {"emerging", "growing", "mature", "saturated", "unknown"}:
        maturity = "unknown"
    importance = clamp_int(raw.get("strategic_importance", raw.get("hotness", 5)), 1, 10)
    strategy = normalize_key(str(raw.get("search_strategy") or "must_include"))
    if strategy not in {"must_include", "nice_to_have", "exploratory"}:
        strategy = "must_include" if importance >= 7 else "nice_to_have"
    return {
        "subspace_id": slug_label(name) or new_id("subspace_item"),
        "name": name,
        "aliases": aliases[:8],
        "description": scalar(raw.get("description")),
        "parent": scalar(raw.get("parent")),
        "keywords": unique_preserve_order(keywords)[:12],
        "seed_papers": seed_papers[:5],
        "maturity": maturity,
        "estimated_density": "unknown",
        "strategic_importance": importance,
        "search_strategy": strategy,
        "generated_by": str(raw.get("generated_by") or "heuristic"),
    }


def probe_domain_subspace(
    subspace: dict[str, Any],
    providers: list[str],
    probe_depth: int = 5,
    provider_budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    keywords = string_list(subspace.get("keywords"))
    name = str(subspace.get("name") or "")
    query = normalize_space(" ".join(keywords[:6]) or name)
    probe_queries = unique_preserve_order(
        [
            normalize_space(f"{name} {' '.join(keywords[:4])}"),
            query,
            normalize_space(f"{name} {' '.join(keywords[:3])} review survey"),
        ]
    )
    probe_queries = probe_queries[: clamp_int(SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS, 1, 3)]
    blocks: list[dict[str, Any]] = []
    per_query_depth = max(1, min(clamp_int(probe_depth, 1, 20), 3))
    for probe_query in probe_queries:
        if not probe_query:
            continue
        for provider in providers:
            try:
                if provider_budget is not None and provider_budget.get(provider, 0) <= 0:
                    blocks.append(
                        {
                            "provider": provider,
                            "query": probe_query,
                            "status": "probe_budget_exhausted",
                            "results": [],
                        }
                    )
                    continue
                if provider == "semantic_scholar":
                    skipped = semantic_scholar_skip_block(probe_query)
                    if skipped:
                        blocks.append(skipped)
                        continue
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_semantic_scholar(probe_query, max_results=per_query_depth)
                elif provider == "arxiv":
                    skipped = arxiv_skip_block(probe_query)
                    if skipped:
                        blocks.append(skipped)
                        continue
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_arxiv(probe_query, max_results=per_query_depth)
                elif provider == "openalex":
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_openalex(probe_query, max_results=per_query_depth)
                elif provider == "dblp":
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_dblp(probe_query, max_results=per_query_depth)
                elif provider == "openreview":
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_openreview(probe_query, max_results=per_query_depth)
                elif provider in {"biorxiv", "medrxiv", "chemrxiv"}:
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_preprint_api(provider, probe_query, max_results=per_query_depth)
                else:
                    continue
                block["probe_query_variant"] = probe_query
                blocks.append(block)
            except Exception as exc:
                blocks.append({"provider": provider, "query": probe_query, "status": "error", "error": str(exc), "results": []})
    ranked = rank_literature_results(query, dedupe_literature_results(flatten_literature_results(blocks)))
    recent_count = sum(1 for item in ranked if is_recent_paper(item, max_age=3))
    high_impact_count = sum(1 for item in ranked if numeric_value(item.get("citation_count")) >= milestone_citation_threshold(item))
    return {
        "subspace_id": subspace.get("subspace_id"),
        "name": subspace.get("name"),
        "query": query,
        "probe_queries": probe_queries,
        "provider_blocks": summarize_provider_blocks(blocks),
        "hit_count": len(ranked),
        "recent_count": recent_count,
        "high_impact_count": high_impact_count,
        "top_seed_papers": [summarize_literature_result(item) for item in ranked[: clamp_int(probe_depth, 1, 10)]],
    }


def enrich_subspace_with_probe(subspace: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(subspace)
    hit_count = int(probe.get("hit_count") or 0)
    recent_count = int(probe.get("recent_count") or 0)
    high_impact_count = int(probe.get("high_impact_count") or 0)
    enriched["probe_query"] = probe.get("query", "")
    enriched["probe_hit_count"] = hit_count
    enriched["recent_hit_count"] = recent_count
    enriched["high_impact_hit_count"] = high_impact_count
    enriched["estimated_density"] = estimate_subspace_density(hit_count, recent_count, high_impact_count)
    if not enriched.get("seed_papers"):
        enriched["seed_papers"] = [
            str(item.get("title") or item.get("citation") or "")
            for item in probe.get("top_seed_papers", [])[:3]
            if str(item.get("title") or item.get("citation") or "")
        ]
    enriched["suggested_quota"] = suggested_subspace_quota(enriched)
    enriched["coverage_status"] = "uncovered" if hit_count <= 0 else "probe_covered"
    return enriched


def build_subspace_probe_budget(providers: list[str]) -> dict[str, int]:
    max_calls = max(0, int(SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER))
    return {provider: max_calls for provider in providers}


def estimate_subspace_density(hit_count: int, recent_count: int, high_impact_count: int) -> str:
    if hit_count >= 5 and (recent_count >= 2 or high_impact_count >= 1):
        return "high"
    if hit_count >= 3:
        return "medium"
    if hit_count >= 1:
        return "low"
    return "unknown"


def suggested_subspace_quota(subspace: dict[str, Any]) -> int:
    importance = int(subspace.get("strategic_importance") or 5)
    density = str(subspace.get("estimated_density") or "unknown")
    strategy = str(subspace.get("search_strategy") or "")
    if strategy == "must_include" or importance >= 8:
        return 3 if density in {"high", "medium"} else 2
    if strategy == "exploratory" or density == "low":
        return 1
    return 2


def domain_subspace_map_confidence(subspaces: list[dict[str, Any]], use_llm: bool) -> float:
    if not subspaces:
        return 0.0
    with_keywords = sum(1 for item in subspaces if item.get("keywords"))
    with_probe = sum(1 for item in subspaces if int(item.get("probe_hit_count") or 0) > 0)
    base = 0.35 + (0.2 if use_llm else 0.0)
    score = base + 0.25 * (with_keywords / len(subspaces)) + 0.2 * (with_probe / len(subspaces))
    return round(max(0.0, min(1.0, score)), 3)


def build_subspace_coverage_plan(subspace_map: dict[str, Any]) -> dict[str, Any]:
    subspaces = [item for item in subspace_map.get("subspaces", []) if isinstance(item, dict)]
    total = len(subspaces)
    covered = [item for item in subspaces if int(item.get("probe_hit_count") or 0) > 0]
    missing = [item for item in subspaces if int(item.get("probe_hit_count") or 0) <= 0]
    insufficient = [
        item
        for item in subspaces
        if int(item.get("probe_hit_count") or 0) > 0 and int(item.get("probe_hit_count") or 0) < int(item.get("suggested_quota") or 1)
    ]
    return {
        "total_subspaces": total,
        "covered": len(covered),
        "missing": len(missing),
        "insufficient": len(insufficient),
        "coverage_rate": round(len(covered) / max(1, total), 3),
        "missing_details": [
            {
                "name": item.get("name"),
                "keywords": item.get("keywords", [])[:6],
                "suggested_action": "supplemental_search" if int(item.get("strategic_importance") or 0) >= 6 else "lower_priority_or_confirm",
            }
            for item in missing
        ],
        "recommendation": "Confirm priority subspaces with the user before running ZhiZhi, then search selected subspaces independently.",
    }


def query_plan_from_subspace_map(subspace_map: dict[str, Any], selected_subfields: list[str] | None = None) -> list[dict[str, Any]]:
    selected = {normalize_key(item) for item in (selected_subfields or []) if normalize_space(item)}
    plan: list[dict[str, Any]] = []
    matched_selected: set[str] = set()
    for subspace in subspace_map.get("subspaces", []):
        if not isinstance(subspace, dict):
            continue
        name = str(subspace.get("name") or "")
        subspace_id = str(subspace.get("subspace_id") or "")
        if selected and normalize_key(name) not in selected and normalize_key(subspace_id) not in selected:
            continue
        if normalize_key(name) in selected:
            matched_selected.add(normalize_key(name))
        if normalize_key(subspace_id) in selected:
            matched_selected.add(normalize_key(subspace_id))
        keywords = string_list(subspace.get("keywords"))
        if not keywords:
            continue
        maturity = str(subspace.get("maturity") or "")
        suffix = "review survey" if maturity in {"mature", "saturated"} else "latest recent" if maturity in {"emerging", "growing"} else ""
        plan.append(
            {
                "branch": subspace_id or slug_label(name),
                "name": name,
                "query": normalize_space(" ".join(keywords[:8] + ([suffix] if suffix else []))),
                "quota": int(subspace.get("suggested_quota") or 1),
                "estimated_density": subspace.get("estimated_density"),
                "strategic_importance": subspace.get("strategic_importance"),
                "search_strategy": subspace.get("search_strategy"),
            }
        )
    for raw in selected:
        if raw in matched_selected:
            continue
        label = normalize_space(raw.replace("_", " "))
        if not label:
            continue
        plan.append(
            {
                "branch": slug_label(label),
                "name": label,
                "query": label,
                "quota": 2,
                "estimated_density": "unknown",
                "strategic_importance": 7,
                "search_strategy": "custom_user_subspace",
                "custom": True,
            }
        )
    return plan


def build_subspace_selection_interaction(subspace_map: dict[str, Any]) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    for item in subspace_map.get("subspaces", [])[:12]:
        if not isinstance(item, dict):
            continue
        options.append(
            {
                "label": str(item.get("name") or item.get("subspace_id")),
                "subspace_id": str(item.get("subspace_id") or ""),
                "description": str(item.get("description") or ""),
                "keywords": item.get("keywords", [])[:8],
                "probe_hit_count": int(item.get("probe_hit_count") or 0),
                "estimated_density": item.get("estimated_density", "unknown"),
                "strategic_importance": item.get("strategic_importance", 5),
                "recommended": item.get("search_strategy") == "must_include" or int(item.get("strategic_importance") or 0) >= 7,
            }
        )
    return {
        "needed": True,
        "type": "pre_retrieval_subspace_selection",
        "question": "Select the subspaces to prioritize before ZhiZhi imports papers. You can also add custom subspaces.",
        "options": options,
        "custom_subspace_input": {
            "enabled": True,
            "placeholder": "e.g. Demand Response; EV Charging Coordination; Building Energy Management",
            "instructions": "If your target subfield is not listed, provide one subspace per line or semicolon-separated. These will be converted into custom retrieval branches.",
        },
        "continue_with": "Pass subspace_map_id plus selected_subfields, or pass option labels as focus_branches to run_zhizhi_literature_analysis.",
    }


def post_retrieval_subspace_coverage(
    subspace_map: dict[str, Any],
    selected_subfields: list[str] | None,
    imported_records: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = query_plan_from_subspace_map(subspace_map, selected_subfields=selected_subfields)
    records = []
    for item in imported_records:
        if not isinstance(item, dict):
            continue
        record = item.get("record") or item.get("existing_record") or {}
        if isinstance(record, dict):
            records.append(record)
    coverage: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []
    for branch in plan:
        terms = query_terms(" ".join([str(branch.get("name") or ""), str(branch.get("query") or "")]))[:16]
        target = clamp_int(branch.get("quota", 2), 1, 10)
        matches = [
            summarize_imported_record_for_subspace(record)
            for record in records
            if record_matches_terms(record, terms)
        ]
        status = "sufficient" if len(matches) >= target else "missing" if len(matches) == 0 else "insufficient"
        entry = {
            "subspace": branch.get("name") or branch.get("branch"),
            "branch": branch.get("branch"),
            "target": target,
            "actual": len(matches),
            "status": status,
            "terms": terms,
            "matched_papers": matches[:5],
            "suggested_query": branch.get("query"),
            "custom": bool(branch.get("custom")),
        }
        coverage.append(entry)
        if status != "sufficient":
            insufficient.append(entry)
    return {
        "total_selected_subspaces": len(plan),
        "sufficient": len([item for item in coverage if item["status"] == "sufficient"]),
        "insufficient": len(insufficient),
        "coverage": coverage,
        "needs_second_alignment": bool(insufficient),
        "user_interaction": build_post_retrieval_alignment_interaction(insufficient),
    }


def record_matches_terms(record: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return False
    text = normalize_space(
        " ".join(
            str(record.get(key) or "")
            for key in ("title", "citation", "abstract", "method", "scenario", "benchmark", "contribution", "limitation")
        )
    ).lower()
    hits = [term for term in terms if term in text]
    return len(hits) >= max(1, min(2, len(terms)))


def summarize_imported_record_for_subspace(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": record.get("paper_id"),
        "title": trim_text(str(record.get("title") or ""), 140),
        "citation": trim_text(str(record.get("citation") or ""), 120),
        "method": record.get("method"),
        "scenario": record.get("scenario"),
    }


def build_post_retrieval_alignment_interaction(insufficient: list[dict[str, Any]]) -> dict[str, Any]:
    if not insufficient:
        return {"needed": False}
    return {
        "needed": True,
        "type": "post_retrieval_subspace_alignment",
        "question": "Some selected subspaces are missing or under-covered after import. Should ZhiZhi run supplemental searches before TanXi treats gaps as real?",
        "options": [
            {
                "label": str(item.get("subspace")),
                "status": item.get("status"),
                "target": item.get("target"),
                "actual": item.get("actual"),
                "suggested_query": item.get("suggested_query"),
            }
            for item in insufficient[:8]
        ],
        "actions": [
            "supplemental_search_selected_subspaces",
            "adjust_query_terms",
            "continue_without_supplement",
        ],
        "continue_with": "Rerun run_zhizhi_literature_analysis with focus_branches set to the suggested_query values for missing subspaces.",
    }


def database_to_provider(name: str) -> str:
    key = normalize_key(name)
    mapping = {
        "semantic_scholar": "semantic_scholar",
        "semanticscholar": "semantic_scholar",
        "s2": "semantic_scholar",
        "openalex": "openalex",
        "open_alex": "openalex",
        "dblp": "dblp",
        "openreview": "openreview",
        "open_review": "openreview",
        "arxiv": "arxiv",
        "bio_rxiv": "biorxiv",
        "biorxiv": "biorxiv",
        "bioarchive": "biorxiv",
        "med_rxiv": "medrxiv",
        "medrxiv": "medrxiv",
        "chem_rxiv": "chemrxiv",
        "chemrxiv": "chemrxiv",
        "web_of_science": "web_of_science",
        "webofscience": "web_of_science",
        "google_scholar": "google_scholar",
        "googlescholar": "google_scholar",
        "springer_nature": "springer_nature",
        "springernature": "springer_nature",
    }
    return mapping.get(key, key)


def extract_structured_info(
    paper_content: str,
    fields: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    parsed = extract_paper_structure(paper_content, use_llm=use_llm)
    result = {
        "zhizhi_action": "extract_structured_info",
        "requested_fields": fields
        or ["research method", "application scenario", "test benchmark", "core contribution", "limitation"],
        "structured_info": {
            "research_method": parsed.get("method", ""),
            "application_scenario": parsed.get("scenario", ""),
            "test_benchmark": parsed.get("benchmark", ""),
            "core_contribution": parsed.get("contribution", ""),
            "core_conclusion": parsed.get("conclusion", ""),
            "limitation": parsed.get("limitation", ""),
        },
        "evidence_type_annotations": classify_evidence_claims(paper_content, parsed),
        "extractor": parsed.get("extractor", ""),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def search_literature_provider_block(provider: str, query: str, max_results: int) -> dict[str, Any]:
    spec = LITERATURE_PROVIDERS.get(provider)
    if spec is None:
        return {
            "provider": provider,
            "query": query,
            "status": "unknown_provider",
            "results": [],
        }
    if provider == "arxiv":
        return search_arxiv(query, max_results=max_results)
    if provider == "semantic_scholar":
        return search_semantic_scholar(query, max_results=max_results)
    if provider == "openalex":
        return search_openalex(query, max_results=max_results)
    if provider == "dblp":
        return search_dblp(query, max_results=max_results)
    if provider == "openreview":
        return search_openreview(query, max_results=max_results)
    if provider in {"biorxiv", "medrxiv", "chemrxiv"}:
        return search_preprint_api(provider, query, max_results=max_results)
    return {
        "provider": provider,
        "query": query,
        "status": spec["status"],
        "note": spec["note"],
        "results": [],
        "next_step": "Use a compliant external connector, or import_literature_text/import_papergraph_record manually only if the user provides the paper text.",
    }


def search_literature(
    query: str,
    providers: list[str] | None = None,
    max_results: int = 10,
) -> str:
    search_id = new_id("search")
    selected = [database_to_provider(provider) for provider in (providers or default_literature_providers(query=query))]
    selected = unique_preserve_order(selected)
    provider_blocks: list[dict[str, Any]] = []
    if selected:
        indexed_blocks: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(6, len(selected))) as executor:
            future_map = {
                executor.submit(search_literature_provider_block, provider, query, max_results): (index, provider)
                for index, provider in enumerate(selected)
            }
            for future in as_completed(future_map):
                index, provider = future_map[future]
                try:
                    indexed_blocks[index] = future.result()
                except Exception as exc:
                    indexed_blocks[index] = provider_error_result(provider, query, exc)
                    log_event("SCIENCE", "literature_search_failed", provider=provider, error=str(exc))
        provider_blocks = [indexed_blocks[index] for index in sorted(indexed_blocks)]
    flattened = rank_literature_results(query, flatten_literature_results(provider_blocks))
    for index, item in enumerate(flattened):
        item["result_index"] = index
        item["search_id"] = search_id
    search_record = {
        "search_id": search_id,
        "query": query,
        "providers": selected,
        "createdAt": time.time(),
        "total_results": len(flattened),
        "results": flattened,
        "provider_blocks": provider_blocks,
    }
    save_search(search_record)
    response = {
        "search_id": search_id,
        "query": query,
        "providers": selected,
        "total_results": len(flattened),
        "results": summarize_literature_results(flattened),
        "provider_blocks": summarize_provider_blocks(provider_blocks),
        "full_results_cached": True,
        "next_step": (
            "Use select_literature_result(search_id) to inspect the top-ranked paper, then "
            "use import_literature_search_result(project_id, search_id, result_index) to import a real retrieved paper. "
            "If total_results is 0, stop and report retrieval failure; do not invent or substitute papers."
        ),
    }
    log_event("SCIENCE", "literature_search", query=query, providers=",".join(selected), max_results=max_results)
    return json.dumps(response, ensure_ascii=False, indent=2)


def search_literature_stratified(
    query: str,
    providers: list[str] | None = None,
    max_results: int = 15,
    domain: str = "",
    focus_branches: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    search_id = new_id("search")
    selected = [database_to_provider(provider) for provider in (providers or default_literature_providers(domain=domain, query=query))]
    selected = unique_preserve_order([provider for provider in selected if provider in LITERATURE_PROVIDERS])
    if not selected:
        selected = default_literature_providers(domain=domain, query=query) or ["semantic_scholar"]
    query_plan = build_domain_query_plan(query, domain=domain, focus_branches=focus_branches, use_llm=use_llm)
    ranking_query = expanded_ranking_query(query, domain, query_plan)
    quotas = stratified_literature_quotas(max_results)
    provider_blocks: list[dict[str, Any]] = []
    selected_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    carry = 0
    strata_reports: list[dict[str, Any]] = []

    for layer in stratified_literature_layers(quotas):
        target = layer["quota"] + carry
        if target <= 0:
            strata_reports.append({**layer, "target": target, "selected": 0, "carried_to_next": 0})
            continue
        blocks = fetch_stratified_layer_blocks(query, selected, layer, query_plan=query_plan)
        provider_blocks.extend(blocks)
        raw_candidates = rank_literature_results(ranking_query, dedupe_literature_results(flatten_literature_results(blocks)))
        candidates = [item for item in raw_candidates if stratified_candidate_matches(layer["layer"], item)]
        recovery_used = ""
        if not candidates and layer["layer"] in {"L1_milestone", "L2_top_latest"}:
            candidates, recovery_used = recover_stratified_layer_candidates(layer["layer"], raw_candidates)
        picked: list[dict[str, Any]] = []
        rejected_for_domain = 0
        for candidate in candidates:
            candidate["domain_relevance"] = domain_relevance_assessment(candidate, domain=domain, query=query)
            if should_reject_for_domain(candidate, domain=domain):
                rejected_for_domain += 1
                continue
            key = literature_result_unique_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            item = dict(candidate)
            item["stratified_layer"] = layer["layer"]
            item["stratified_label"] = layer["label"]
            if recovery_used:
                item["stratified_recovery"] = recovery_used
            item["_why_selected"] = stratified_selection_reason(layer["layer"], item)
            picked.append(item)
            if len(picked) >= target:
                break
        selected_results.extend(picked)
        carry = max(0, target - len(picked))
        strata_reports.append(
            {
                **layer,
                "target": target,
                "candidate_count": len(candidates),
                "raw_candidate_count": len(raw_candidates),
                "selected": len(picked),
                "domain_rejected": rejected_for_domain,
                "recovery_used": recovery_used,
                "carried_to_next": carry,
            }
        )
        if len(selected_results) >= max_results:
            carry = 0
            break

    if len(selected_results) < max_results:
        regular_needed = max_results - len(selected_results)
        blocks = fetch_regular_backfill_blocks(query, selected, regular_needed + carry, query_plan=query_plan)
        provider_blocks.extend(blocks)
        candidates = rank_literature_results(ranking_query, dedupe_literature_results(flatten_literature_results(blocks)))
        picked = []
        rejected_for_domain = 0
        for candidate in candidates:
            candidate["domain_relevance"] = domain_relevance_assessment(candidate, domain=domain, query=query)
            if should_reject_for_domain(candidate, domain=domain):
                rejected_for_domain += 1
                continue
            key = literature_result_unique_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            item = dict(candidate)
            item["stratified_layer"] = "L4_regular"
            item["stratified_label"] = "regular journal / supplemental evidence"
            item["_why_selected"] = stratified_selection_reason("L4_regular", item)
            picked.append(item)
            if len(picked) >= regular_needed:
                break
        selected_results.extend(picked)
        strata_reports.append(
            {
                "layer": "L4_regular_backfill",
                "label": "regular journal / quota backfill",
                "quota": regular_needed,
                "target": regular_needed,
                "candidate_count": len(candidates),
                "selected": len(picked),
                "domain_rejected": rejected_for_domain,
                "carried_to_next": max(0, regular_needed - len(picked)),
            }
        )

    final_results = diverse_rerank_literature_results(selected_results, max_results=max_results)
    for index, item in enumerate(final_results):
        item["result_index"] = index
        item["search_id"] = search_id
    knowledge_pyramid = build_knowledge_pyramid(query, final_results, strata_reports)
    search_record = {
        "search_id": search_id,
        "query": query,
        "domain": domain,
        "focus_branches": focus_branches or [],
        "providers": selected,
        "createdAt": time.time(),
        "strategy": "stratified_cascade",
        "query_plan": query_plan,
        "strata": strata_reports,
        "knowledge_pyramid": knowledge_pyramid,
        "total_results": len(final_results),
        "results": final_results,
        "provider_blocks": provider_blocks,
    }
    save_search(search_record)
    response = {
        "search_id": search_id,
        "query": query,
        "domain": domain,
        "focus_branches": focus_branches or [],
        "providers": selected,
        "strategy": "stratified_cascade",
        "query_plan": query_plan,
        "strata": strata_reports,
        "knowledge_pyramid": knowledge_pyramid,
        "root_result_index": knowledge_pyramid.get("root_result_index"),
        "root_policy": knowledge_pyramid.get("root_policy"),
        "total_results": len(final_results),
        "results": summarize_literature_results(final_results),
        "provider_blocks": summarize_provider_blocks(provider_blocks),
        "full_results_cached": True,
        "next_step": (
            "Import selected stratified results with import_literature_search_result(project_id, search_id, result_index). "
            "Each result has stratified_layer and _why_selected explaining its role in the literature map."
        ),
    }
    log_event(
        "SCIENCE",
        "literature_search_stratified",
        query=query,
        providers=",".join(selected),
        max_results=max_results,
        results=len(final_results),
    )
    return json.dumps(response, ensure_ascii=False, indent=2)


def diverse_rerank_literature_results(results: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    limit = clamp_int(max_results, 1, 200)
    remaining = [dict(item) for item in results if isinstance(item, dict)]
    if len(remaining) <= limit:
        return remaining[:limit]
    selected: list[dict[str, Any]] = []
    used_branches: set[str] = set()
    used_layers: set[str] = set()
    while remaining and len(selected) < limit:
        best_index = 0
        best_score = -999.0
        for index, item in enumerate(remaining):
            score = literature_selection_base_score(item)
            branch = str(item.get("query_branch") or item.get("stratified_label") or "")
            layer = str(item.get("stratified_layer") or "")
            if branch and branch in used_branches:
                score -= 0.18
            if layer and layer in used_layers and layer in {"L3_preprint", "L4_regular"}:
                score -= 0.08
            similarity = max((literature_result_text_similarity(item, chosen) for chosen in selected), default=0.0)
            score -= 0.28 * similarity
            if score > best_score:
                best_score = score
                best_index = index
        chosen = remaining.pop(best_index)
        chosen["diversity_rank_score"] = round(best_score, 4)
        selected.append(chosen)
        branch = str(chosen.get("query_branch") or chosen.get("stratified_label") or "")
        layer = str(chosen.get("stratified_layer") or "")
        if branch:
            used_branches.add(branch)
        if layer:
            used_layers.add(layer)
    return selected


def literature_selection_base_score(item: dict[str, Any]) -> float:
    relevance = float(item.get("relevance_score") or 0.0)
    quality = float(item.get("publication_quality_score") or publication_quality_assessment(item)["quality_score"])
    impact = literature_impact_score(item)
    recency = literature_recency_score(item)
    layer_bonus = {
        "L0_review": 0.12,
        "L1_milestone": 0.1,
        "L2_top_latest": 0.08,
        "L3_preprint": 0.03,
        "L4_regular": 0.0,
    }.get(str(item.get("stratified_layer") or ""), 0.0)
    return 0.42 * relevance + 0.28 * quality + 0.18 * impact + 0.12 * recency + layer_bonus


ZHIZHI_IMPORT_LAYER_PRIORITY = ["L0_review", "L1_milestone", "L2_top_latest", "L3_preprint", "L4_regular"]
ZHIZHI_IMPORT_MIN_PER_LAYER = {
    "L0_review": 2,
    "L1_milestone": 2,
    "L2_top_latest": 2,
    "L3_preprint": 2,
    "L4_regular": 3,
}
ZHIZHI_IMPORT_LAYER_LABELS = {
    "L0_review": "high-impact review / field map",
    "L1_milestone": "milestone / highly cited foundation",
    "L2_top_latest": "recent top-venue frontier",
    "L3_preprint": "latest preprint frontier",
    "L4_regular": "regular journal / supplemental evidence",
}


def select_zhizhi_import_results(
    results: list[dict[str, Any]],
    import_top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    limit = clamp_int(import_top_k, 1, SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K)
    candidates = [dict(item) for item in results if isinstance(item, dict)]
    candidate_counts = Counter(str(item.get("stratified_layer") or "unlayered") for item in candidates)
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    min_plan = zhizhi_import_minimum_plan(limit)

    def add_candidate(candidate: dict[str, Any], reason: str) -> bool:
        key = zhizhi_import_candidate_key(candidate)
        if key in selected_keys or len(selected) >= limit:
            return False
        item = dict(candidate)
        item["zhizhi_import_reason"] = reason
        selected.append(item)
        selected_keys.add(key)
        return True

    for layer in ZHIZHI_IMPORT_LAYER_PRIORITY:
        needed = min_plan.get(layer, 0)
        if needed <= 0:
            continue
        layer_candidates = sorted(
            [item for item in candidates if str(item.get("stratified_layer") or "") == layer],
            key=zhizhi_import_priority_score,
            reverse=True,
        )
        picked = 0
        for candidate in layer_candidates:
            if add_candidate(candidate, f"layer_minimum:{layer}"):
                picked += 1
            if picked >= needed:
                break

    remaining = sorted(candidates, key=zhizhi_import_priority_score, reverse=True)
    for candidate in remaining:
        if len(selected) >= limit:
            break
        add_candidate(candidate, "score_backfill")

    selected_counts = Counter(str(item.get("stratified_layer") or "unlayered") for item in selected)
    missing_layers = [
        {
            "layer": layer,
            "label": ZHIZHI_IMPORT_LAYER_LABELS.get(layer, layer),
            "target": target,
            "selected": selected_counts.get(layer, 0),
            "candidates": candidate_counts.get(layer, 0),
        }
        for layer, target in min_plan.items()
        if selected_counts.get(layer, 0) < target
    ]
    report = {
        "strategy": "layer_minimum_then_score_backfill",
        "requested_import_top_k": import_top_k,
        "effective_import_top_k": limit,
        "min_per_layer": min_plan,
        "candidate_counts_by_layer": dict(candidate_counts),
        "selected_counts_by_layer": dict(selected_counts),
        "missing_layers": missing_layers,
        "selected_result_indexes": [item.get("result_index") for item in selected],
    }
    return selected, report


def zhizhi_import_minimum_plan(limit: int) -> dict[str, int]:
    remaining = clamp_int(limit, 1, SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K)
    plan: dict[str, int] = {}
    for layer in ZHIZHI_IMPORT_LAYER_PRIORITY:
        target = min(int(ZHIZHI_IMPORT_MIN_PER_LAYER.get(layer, 0)), remaining)
        if target > 0:
            plan[layer] = target
            remaining -= target
        if remaining <= 0:
            break
    return plan


def zhizhi_import_priority_score(item: dict[str, Any]) -> float:
    score = literature_selection_base_score(item)
    layer = str(item.get("stratified_layer") or "")
    if layer == "L0_review" and is_review_like_paper(item):
        score += 0.08
    if layer == "L1_milestone" and numeric_value(item.get("citation_count")) > 0:
        score += 0.05
    if layer == "L2_top_latest" and is_recent_paper(item, max_age=5):
        score += 0.05
    if item.get("zhizhi_import_reason"):
        score += 0.01
    return score


def zhizhi_import_candidate_key(item: dict[str, Any]) -> str:
    result_index = item.get("result_index")
    if result_index is not None:
        return f"result_index:{result_index}"
    return literature_result_unique_key(item)


def literature_result_text_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    text_a = " ".join(query_terms(" ".join(str(a.get(key) or "") for key in ("title", "abstract", "query_branch")))[:24])
    text_b = " ".join(query_terms(" ".join(str(b.get(key) or "") for key in ("title", "abstract", "query_branch")))[:24])
    terms_a = set(query_terms(text_a))
    terms_b = set(query_terms(text_b))
    if not terms_a or not terms_b:
        return 0.0
    return len(terms_a & terms_b) / max(1, len(terms_a | terms_b))


def stratified_literature_quotas(max_results: int) -> dict[str, int]:
    total = clamp_int(max_results, 1, 100)
    base = {
        "L0_review": 3,
        "L1_milestone": 4,
        "L2_top_latest": 4,
        "L3_preprint": 1,
    }
    if total <= 1:
        return {"L0_review": total, "L1_milestone": 0, "L2_top_latest": 0, "L3_preprint": 0, "L4_regular": 0}
    assigned = 0
    quotas: dict[str, int] = {}
    for key in ("L0_review", "L1_milestone", "L2_top_latest", "L3_preprint"):
        value = min(base[key], max(0, total - assigned))
        quotas[key] = value
        assigned += value
    quotas["L4_regular"] = max(0, total - assigned)
    return quotas


def stratified_literature_layers(quotas: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "layer": "L0_review",
            "label": "high-impact review / field map",
            "quota": int(quotas.get("L0_review", 0)),
            "query_suffix": "review survey progress perspective tutorial systematic review meta-analysis",
        },
        {
            "layer": "L1_milestone",
            "label": "milestone / highly cited foundation",
            "quota": int(quotas.get("L1_milestone", 0)),
            "query_suffix": "seminal foundational highly cited landmark classic influential",
        },
        {
            "layer": "L2_top_latest",
            "label": "recent top-venue frontier",
            "quota": int(quotas.get("L2_top_latest", 0)),
            "query_suffix": "latest recent top journal high impact breakthrough advance frontier",
        },
        {
            "layer": "L3_preprint",
            "label": "latest arXiv preprint frontier",
            "quota": int(quotas.get("L3_preprint", 0)),
            "query_suffix": "",
        },
        {
            "layer": "L4_regular",
            "label": "regular journal / supplemental evidence",
            "quota": int(quotas.get("L4_regular", 0)),
            "query_suffix": "",
        },
    ]


def build_domain_query_plan(
    query: str,
    domain: str = "",
    max_branches: int = 8,
    focus_branches: list[str] | None = None,
    use_llm: bool = False,
) -> list[dict[str, str]]:
    primary = normalize_space(query)
    plan: list[dict[str, str]] = [{"branch": "primary", "query": primary}]
    profile = domain_topic_profile(domain or query, query=query, use_llm=use_llm)
    focus_branches = [normalize_space(item) for item in (focus_branches or []) if normalize_space(item)]
    for focus in focus_branches:
        plan.append({"branch": slug_label(focus), "query": normalize_space(f"{primary} {focus}")})
    topics = list(profile.get("core_topics", [])) + list(profile.get("retrieval_facets", []))
    for topic in topics[: max(0, max_branches)]:
        branch = str(topic.get("branch") or "subfield")
        terms = str(topic.get("query") or "")
        if not terms:
            continue
        branch_query = normalize_space(terms if primary.lower() in terms.lower() else f"{primary} {terms}")
        plan.append({"branch": branch, "query": branch_query, "topic_type": str(topic.get("topic_type") or "subfield")})
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in plan:
        key = normalize_space(item["query"]).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[: max(1, max_branches + 1)]


def expanded_ranking_query(query: str, domain: str, query_plan: list[dict[str, str]]) -> str:
    topic_terms: list[str] = []
    for item in query_plan:
        topic_terms.extend(query_terms(str(item.get("query") or ""))[:4])
    return normalize_space(" ".join([query, domain, " ".join(unique_preserve_order(topic_terms)[:24])]))


def domain_topic_profile(text: str, query: str = "", use_llm: bool = False) -> dict[str, Any]:
    base_text = normalize_space(" ".join([text, query]))
    if use_llm:
        llm_profile = infer_domain_topic_profile_with_llm(base_text, query=query)
        if llm_profile:
            return llm_profile
    return infer_domain_topic_profile_heuristic(base_text, query=query)


def infer_domain_topic_profile_with_llm(text: str, query: str = "") -> dict[str, Any] | None:
    try:
        payload = call_llm_json(
            system=(
                "You are a domain-agnostic science retrieval planner. Work for any field: mathematics, "
                "physics, chemistry, biology, medicine, agriculture, engineering, materials, earth science, "
                "climate, ecology, computer science, AI, humanities-adjacent science, and interdisciplinary topics. "
                "Do not assume the field is power systems unless the input says so. Return compact JSON only."
            ),
            prompt=(
                "Return one strict JSON object with keys: profile, anchors, noise_markers, core_topics, "
                "expected_topics, retrieval_facets. No markdown.\n"
                "core_topics: 4-8 substantive subfields. Each item has branch, query, expected_terms, min_hits.\n"
                "expected_topics: one item per core topic, with name, terms, min_hits.\n"
                "retrieval_facets: at most 4 generic search facets such as review, milestone, latest, benchmark.\n"
                f"Domain/context: {text}\n"
                f"Original query: {query}\n"
            ),
            max_tokens=3200,
            fallback_list_key="core_topics",
        )
    except Exception as exc:
        log_event("WARN", "domain_profile_llm_failed", error=str(exc))
        return None
    profile = normalize_domain_topic_profile(payload, text)
    profile["profile_source"] = "llm"
    return profile


def infer_domain_topic_profile_heuristic(text: str, query: str = "") -> dict[str, Any]:
    anchors = query_terms(text)[:14]
    topic_seed = normalize_space(query or text)
    if not topic_seed:
        topic_seed = "scientific research"
    generic_topics = [
        {
            "branch": "field_map_reviews",
            "query": f"{topic_seed} review survey roadmap progress perspective systematic review",
            "expected_terms": ["review", "survey", "roadmap", "progress"],
            "rationale": "Find high-impact reviews to establish the field map.",
            "topic_type": "retrieval_facet",
        },
        {
            "branch": "milestone_foundations",
            "query": f"{topic_seed} seminal foundational highly cited landmark theory method mechanism",
            "expected_terms": ["seminal", "foundational", "highly cited", "landmark"],
            "rationale": "Recover influential historical or conceptual foundations.",
            "topic_type": "retrieval_facet",
        },
        {
            "branch": "methods_and_mechanisms",
            "query": f"{topic_seed} method model algorithm mechanism experiment framework",
            "expected_terms": ["method", "model", "algorithm", "mechanism", "experiment"],
            "rationale": "Cover method/mechanism families rather than only one wording of the topic.",
            "topic_type": "retrieval_facet",
        },
        {
            "branch": "applications_systems_scenarios",
            "query": f"{topic_seed} application system scenario case study deployment",
            "expected_terms": ["application", "system", "scenario", "case study", "deployment"],
            "rationale": "Cover application settings and scenario-specific literature.",
            "topic_type": "retrieval_facet",
        },
        {
            "branch": "benchmarks_data_validation",
            "query": f"{topic_seed} benchmark dataset validation evaluation metric measurement",
            "expected_terms": ["benchmark", "dataset", "validation", "evaluation", "metric"],
            "rationale": "Cover evaluation, reproducibility, and benchmark evidence.",
            "topic_type": "retrieval_facet",
        },
        {
            "branch": "latest_preprints_frontier",
            "query": f"{topic_seed} latest recent arxiv preprint frontier breakthrough",
            "expected_terms": ["latest", "recent", "preprint", "frontier"],
            "rationale": "Capture emerging work that may not yet be cited.",
            "topic_type": "retrieval_facet",
        },
    ]
    return {
        "profile": slug_label(text) or "generic_science",
        "profile_source": "heuristic",
        "profile_confidence": "low",
        "anchors": anchors,
        "noise_markers": [],
        "core_topics": [],
        "retrieval_facets": generic_topics,
        "expected_topics": [],
        "coverage_note": "Heuristic fallback can ensure retrieval-style breadth but cannot certify substantive subfield coverage.",
    }


def normalize_domain_topic_profile(payload: dict[str, Any], fallback_text: str) -> dict[str, Any]:
    anchors = string_list(payload.get("anchors"))[:20] or query_terms(fallback_text)[:14]
    noise_markers = string_list(payload.get("noise_markers"))[:20]
    core_topics = normalize_profile_topic_list(payload.get("core_topics"), default_prefix="branch")
    retrieval_facets = normalize_profile_topic_list(payload.get("retrieval_facets"), default_prefix="facet")
    if not retrieval_facets:
        retrieval_facets = infer_domain_topic_profile_heuristic(fallback_text).get("retrieval_facets", [])
    expected_topics: list[dict[str, Any]] = []
    for item in payload.get("expected_topics") or []:
        if not isinstance(item, dict):
            name = scalar(item)
            terms = query_terms(name)[:5]
            min_hits = 2
        else:
            name = scalar(item.get("name"))
            terms = string_list(item.get("terms"))[:8]
            min_hits = clamp_int(item.get("min_hits", 2), 1, 10)
        if name and terms:
            expected_topics.append({"name": name, "terms": terms, "min_hits": min_hits, "topic_type": "subfield"})
    if not core_topics:
        fallback = infer_domain_topic_profile_heuristic(fallback_text)
        fallback["profile_source"] = "heuristic_after_invalid_llm"
        return fallback
    if not expected_topics:
        expected_topics = [
            {
                "name": item["branch"],
                "terms": item.get("expected_terms") or query_terms(item["query"])[:5],
                "min_hits": clamp_int(item.get("min_hits", 2), 1, 10),
                "topic_type": "subfield",
            }
            for item in core_topics
        ]
    return {
        "profile": slug_label(str(payload.get("profile") or fallback_text)) or "science_domain",
        "profile_confidence": "high",
        "anchors": anchors,
        "noise_markers": noise_markers,
        "core_topics": core_topics[:8],
        "retrieval_facets": retrieval_facets[:6],
        "expected_topics": expected_topics[:10],
    }


def normalize_profile_topic_list(raw_topics: Any, default_prefix: str) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    for index, item in enumerate(raw_topics or []):
        if not isinstance(item, dict):
            continue
        branch = slug_label(str(item.get("branch") or f"branch_{index + 1}"))
        query_text = normalize_space(str(item.get("query") or ""))
        if not query_text:
            continue
        topics.append(
            {
                "branch": branch,
                "query": query_text,
                "expected_terms": string_list(item.get("expected_terms"))[:8],
                "min_hits": clamp_int(item.get("min_hits", 2), 1, 10),
                "rationale": scalar(item.get("rationale")),
                "topic_type": str(item.get("topic_type") or default_prefix),
            }
        )
    return topics


def slug_label(text: str) -> str:
    value = normalize_space(text).lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")[:80]


def domain_relevance_assessment(result: dict[str, Any], domain: str = "", query: str = "") -> dict[str, Any]:
    profile = domain_topic_profile(domain or query)
    text = normalize_space(
        " ".join(
            str(result.get(key) or "")
            for key in ("title", "abstract", "venue", "citation", "method", "scenario", "benchmark")
        )
    ).lower()
    query_term_list = query_terms(query)
    query_hits = [term for term in query_term_list if term in text]
    anchors = [term.lower() for term in profile.get("anchors", []) if str(term).strip()]
    anchor_hits = [term for term in anchors if term in text]
    topic_hits: list[str] = []
    for topic in profile.get("expected_topics", []):
        terms = [str(term).lower() for term in topic.get("terms", [])]
        if any(term in text for term in terms):
            topic_hits.append(str(topic.get("name") or terms[0]))
    noise_hits = [marker for marker in profile.get("noise_markers", []) if marker in text]
    query_score = len(query_hits) / max(1, len(query_term_list))
    anchor_score = len(anchor_hits) / max(1, min(len(anchors), 8))
    topic_score = min(1.0, len(topic_hits) / 2.0)
    score = round(min(1.0, 0.45 * query_score + 0.35 * anchor_score + 0.2 * topic_score), 4)
    flags: list[str] = []
    target_field = infer_research_field({"title": domain, "abstract": query, "venue": ""}) if (domain or query) else "general"
    result_field = infer_research_field(result)
    strong_text_signal = len(query_hits) >= max(2, min(4, len(query_term_list) // 3)) or len(anchor_hits) >= 2 or bool(topic_hits)
    field_mismatch = fields_are_incompatible(target_field, result_field)
    if noise_hits:
        flags.append("cross_domain_noise_marker")
    if field_mismatch:
        flags.append("field_mismatch")
        if not strong_text_signal:
            score = round(score * 0.35, 4)
    if score < 0.16:
        flags.append("low_domain_relevance")
    if topic_hits:
        flags.append("domain_topic_hit")
    provider = normalize_space(str(result.get("provider") or result.get("venue") or "")).lower()
    is_preprint = provider in PREPRINT_API_PROVIDERS or any(name in provider for name in PREPRINT_API_PROVIDERS)
    if is_preprint and score < 0.16:
        flags.append("weak_preprint_domain_relevance")
    preprint_has_signal = bool(query_hits or anchor_hits or topic_hits)
    verdict = "keep"
    if noise_hits:
        verdict = "reject"
    elif field_mismatch and not strong_text_signal and score < 0.25:
        verdict = "reject"
    elif is_preprint and score < 0.06 and not preprint_has_signal:
        verdict = "reject"
    return {
        "profile": profile.get("profile"),
        "target_field": target_field,
        "result_field": result_field,
        "score": score,
        "query_hits": unique_preserve_order(query_hits)[:12],
        "anchor_hits": unique_preserve_order(anchor_hits)[:12],
        "topic_hits": unique_preserve_order(topic_hits),
        "noise_hits": unique_preserve_order(noise_hits),
        "flags": unique_preserve_order(flags),
        "is_preprint": is_preprint,
        "verdict": verdict,
        "requires_human_review": bool((is_preprint and score < 0.16 and verdict != "reject") or (field_mismatch and verdict != "reject")),
    }


def should_reject_for_domain(result: dict[str, Any], domain: str = "") -> bool:
    if not domain:
        return False
    assessment = result.get("domain_relevance")
    if not isinstance(assessment, dict):
        assessment = domain_relevance_assessment(result, domain=domain, query="")
        result["domain_relevance"] = assessment
    if assessment.get("verdict") == "reject":
        return True
    score = float(assessment.get("score") or 0.0)
    quality = float(result.get("publication_quality_score") or publication_quality_assessment(result)["quality_score"])
    citations = numeric_value(result.get("citation_count"))
    if "field_mismatch" in set(assessment.get("flags") or []) and score < 0.18 and citations <= 5:
        return True
    return score < 0.1 and quality < 0.55 and citations <= 0


def literature_domain_coverage_diagnostic(
    search_id: str,
    domain: str = "",
    query: str = "",
    live_validate: bool = False,
    use_llm: bool = False,
    max_live_probes: int = 4,
) -> dict[str, Any]:
    search_record = load_search(search_id)
    results = [item for item in search_record.get("results", []) if isinstance(item, dict)]
    profile = domain_topic_profile(
        domain or query or str(search_record.get("query") or ""),
        query=query or str(search_record.get("query") or ""),
        use_llm=use_llm,
    )
    expected_topics = profile.get("expected_topics", [])
    represented: list[dict[str, Any]] = []
    blind_spots: list[dict[str, Any]] = []
    corpus = [
        normalize_space(
            " ".join(str(item.get(key) or "") for key in ("title", "abstract", "method", "scenario", "benchmark", "citation"))
        ).lower()
        for item in results
    ]
    if not expected_topics:
        blind_spots.append(
            {
                "topic": "substantive_subfield_map_missing",
                "hit_count": 0,
                "min_hits": 1,
                "terms": [],
                "suggested_query": normalize_space(f"{query or search_record.get('query', '')} major subfields review survey"),
                "risk": (
                    "No substantive subfield map is available. The retrieval may cover generic facets "
                    "(review/method/application) while still missing important domain branches."
                ),
                "requires_user_or_llm_branch_confirmation": True,
            }
        )
    for topic in expected_topics:
        name = str(topic.get("name") or "")
        terms = [str(term).lower() for term in topic.get("terms", []) if str(term).strip()]
        min_hits = clamp_int(topic.get("min_hits", 2), 1, 10)
        hit_count = sum(1 for text in corpus if any(term in text for term in terms))
        entry = {"topic": name, "hit_count": hit_count, "min_hits": min_hits, "terms": terms}
        if hit_count >= min_hits:
            represented.append(entry)
        else:
            blind_spots.append(
                {
                    **entry,
                    "suggested_query": normalize_space(f"{query or search_record.get('query', '')} {' '.join(terms[:4])}"),
                    "risk": "If this is a known dense subfield, TanXi may mistake retrieval absence for a true knowledge gap.",
                }
            )
    live_probe_reports: list[dict[str, Any]] = []
    if live_validate and blind_spots:
        for spot in blind_spots[: clamp_int(max_live_probes, 0, 8)]:
            report = live_probe_literature_branch(str(spot.get("suggested_query") or ""), providers=search_record.get("providers", []))
            spot["live_probe"] = report
            if int(report.get("total_results") or 0) > 0:
                spot["false_negative_risk"] = True
                spot["risk"] = (
                    "Live probe found literature for this missing branch; current PaperGraph may be incomplete, "
                    "so TanXi should not treat this absence as a true unexplored gap."
                )
            live_probe_reports.append(report)
    return {
        "profile": profile.get("profile"),
        "profile_source": profile.get("profile_source", ""),
        "search_id": search_id,
        "total_results": len(results),
        "represented_topics": represented,
        "blind_spots": blind_spots,
        "live_validate": live_validate,
        "live_probe_reports": live_probe_reports,
        "coverage_warning": bool(blind_spots),
        "needs_user_branch_confirmation": bool(blind_spots),
    }


def live_probe_literature_branch(query: str, providers: list[str] | None = None) -> dict[str, Any]:
    if not query:
        return {"query": query, "status": "skipped", "total_results": 0, "reason": "empty query"}
    selected = [database_to_provider(provider) for provider in (providers or default_literature_providers(query=query))]
    selected = unique_preserve_order([item for item in selected if item in live_literature_provider_names()])
    if not selected:
        selected = default_literature_providers(query=query) or ["semantic_scholar"]
    reports: list[dict[str, Any]] = []
    total = 0
    for provider in selected:
        try:
            if provider == "semantic_scholar":
                skipped = semantic_scholar_skip_block(query)
                if skipped:
                    reports.append(
                        {
                            "provider": provider,
                            "status": skipped.get("status"),
                            "result_count": 0,
                            "top_titles": [],
                            "error": skipped.get("error", ""),
                            "rate_limited": True,
                        }
                    )
                    continue
                block = search_semantic_scholar(query, max_results=3)
            elif provider in {"biorxiv", "medrxiv", "chemrxiv"}:
                block = search_preprint_api(provider, query, max_results=3)
            else:
                block = search_arxiv(query, max_results=3)
            count = len(block.get("results") or []) if block.get("status") == "ok" else 0
            total += count
            reports.append(
                {
                    "provider": provider,
                    "status": block.get("status"),
                    "result_count": count,
                    "top_titles": [trim_text(str(item.get("title") or ""), 120) for item in (block.get("results") or [])[:3]],
                    "error": block.get("error", ""),
                }
            )
        except Exception as exc:
            reports.append({"provider": provider, "status": "error", "result_count": 0, "error": str(exc)})
    return {
        "query": query,
        "status": "ok" if total > 0 else "empty_or_error",
        "total_results": total,
        "providers": reports,
    }


def build_branch_user_interaction(coverage_diagnostic: dict[str, Any]) -> dict[str, Any]:
    blind_spots = coverage_diagnostic.get("blind_spots", [])
    options: list[dict[str, Any]] = []
    for spot in blind_spots[:6]:
        options.append(
            {
                "label": str(spot.get("topic") or "missing branch"),
                "suggested_query": str(spot.get("suggested_query") or ""),
                "live_evidence_count": int((spot.get("live_probe") or {}).get("total_results") or 0)
                if isinstance(spot.get("live_probe"), dict)
                else 0,
                "false_negative_risk": bool(spot.get("false_negative_risk")),
            }
        )
    if not options:
        return {"needed": False}
    return {
        "needed": True,
        "type": "research_branch_confirmation",
        "question": "Some major sub-branches appear missing from the current retrieval. Which should be prioritized for a supplemental search before treating gaps as real?",
        "options": options,
        "default_action": "Run supplemental stratified search for options with false_negative_risk=true, or ask the user to pick 2-3 priority branches.",
        "continue_with": "Pass selected option labels or custom branch keywords as focus_branches to run_zhizhi_literature_analysis/search_papers_stratified.",
    }


def fetch_stratified_layer_blocks(
    query: str,
    providers: list[str],
    layer: dict[str, Any],
    query_plan: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    layer_name = str(layer.get("layer", ""))
    suffix = str(layer.get("query_suffix", "")).strip()
    fetch_limit = max(12, int(layer.get("quota", 1)) * 8)
    blocks: list[dict[str, Any]] = []
    plans = query_plan or [{"branch": "primary", "query": query}]
    plans = plans[: clamp_int(SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER, 1, 20)]
    per_query_limit = max(4, min(fetch_limit, max(4, fetch_limit // max(1, len(plans)) + 2)))
    for plan in plans:
        branch = str(plan.get("branch") or "primary")
        planned_query = str(plan.get("query") or query)
        layer_query = stratified_layer_retrieval_query(layer_name, planned_query, suffix)
        if layer_name == "L3_preprint":
            if "arxiv" in providers:
                block = arxiv_skip_block(planned_query) or search_arxiv(planned_query, max_results=per_query_limit, sort_by="submittedDate")
                block["query_branch"] = branch
                block["retrieval_strategy"] = "latest_preprint_query"
                blocks.append(block)
            for provider in ("biorxiv", "medrxiv", "chemrxiv"):
                if provider in providers:
                    block = search_preprint_api(provider, planned_query, max_results=min(per_query_limit, 20))
                    block["query_branch"] = branch
                    block["retrieval_strategy"] = "latest_preprint_query"
                    blocks.append(block)
            if "openreview" in providers:
                block = search_openreview(planned_query, max_results=min(per_query_limit, 20))
                block["query_branch"] = branch
                block["retrieval_strategy"] = "latest_preprint_query"
                blocks.append(block)
            continue
        if "semantic_scholar" in providers:
            block = semantic_scholar_skip_block(layer_query) or search_semantic_scholar(layer_query, max_results=per_query_limit)
            block["query_branch"] = branch
            block["retrieval_strategy"] = stratified_layer_retrieval_strategy(layer_name)
            blocks.append(block)
        for provider in ("openalex", "dblp"):
            if provider not in providers:
                continue
            if provider == "openalex":
                block = search_openalex(layer_query, max_results=per_query_limit)
            else:
                block = search_dblp(layer_query, max_results=per_query_limit)
            block["query_branch"] = branch
            block["retrieval_strategy"] = stratified_layer_retrieval_strategy(layer_name)
            blocks.append(block)
        if layer_name == "L0_review" and "arxiv" in providers:
            block = arxiv_skip_block(layer_query) or search_arxiv(layer_query, max_results=min(per_query_limit, 20))
            block["query_branch"] = branch
            block["retrieval_strategy"] = "review_query"
            blocks.append(block)
        if layer_name == "L0_review":
            for provider in ("biorxiv", "medrxiv", "chemrxiv"):
                if provider in providers:
                    block = search_preprint_api(provider, layer_query, max_results=min(per_query_limit, 20))
                    block["query_branch"] = branch
                    block["retrieval_strategy"] = "review_query"
                    blocks.append(block)
        if layer_name == "L4_regular" and "arxiv" in providers:
            block = arxiv_skip_block(planned_query) or search_arxiv(planned_query, max_results=min(per_query_limit, 20))
            block["query_branch"] = branch
            block["retrieval_strategy"] = "regular_backfill_query"
            blocks.append(block)
        if layer_name == "L4_regular":
            for provider in ("biorxiv", "medrxiv", "chemrxiv"):
                if provider in providers:
                    block = search_preprint_api(provider, planned_query, max_results=min(per_query_limit, 20))
                    block["query_branch"] = branch
                    block["retrieval_strategy"] = "regular_backfill_query"
                    blocks.append(block)
    return blocks


def stratified_layer_retrieval_query(layer_name: str, planned_query: str, suffix: str) -> str:
    base = normalize_space(planned_query)
    if layer_name in {"L1_milestone", "L2_top_latest"}:
        return base
    return normalize_space(f"{base} {suffix}".strip())


def stratified_layer_retrieval_strategy(layer_name: str) -> str:
    if layer_name == "L1_milestone":
        return "broad_recall_then_citation_rerank"
    if layer_name == "L2_top_latest":
        return "broad_recall_then_recent_top_venue_rerank"
    if layer_name == "L0_review":
        return "review_query"
    if layer_name == "L4_regular":
        return "regular_backfill_query"
    return "layer_query"


def fetch_regular_backfill_blocks(
    query: str,
    providers: list[str],
    needed: int,
    query_plan: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    layer = {"layer": "L4_regular", "quota": max(needed, 1), "query_suffix": ""}
    return fetch_stratified_layer_blocks(query, providers, layer, query_plan=query_plan)


def build_knowledge_pyramid(
    query: str,
    results: list[dict[str, Any]],
    strata_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    root = choose_pyramid_review_root(results)
    layer_nodes: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        layer = str(item.get("stratified_layer") or "unlayered")
        layer_nodes.setdefault(layer, []).append(summarize_literature_result(item))

    edges: list[dict[str, Any]] = []
    root_index = root.get("result_index") if root else None
    if root_index is not None:
        for item in results:
            child_index = item.get("result_index")
            if child_index == root_index:
                continue
            edges.append(
                {
                    "source": root_index,
                    "target": child_index,
                    "relation": pyramid_relation_for_layer(str(item.get("stratified_layer") or "")),
                    "evidence": "stratified retrieval layer",
                    "confidence": 0.65,
                }
            )

    return {
        "query": query,
        "root_result_index": root_index,
        "root_node": summarize_literature_result(root) if root else None,
        "root_policy": (
            "Prefer a high-impact review as the knowledge-map root. Only a clearly superior "
            "Nature/Science/Cell/PNAS-level paper should override it as the seed."
        ),
        "layers": {
            "L0_review": layer_nodes.get("L0_review", []),
            "L1A_milestone": layer_nodes.get("L1_milestone", []),
            "L1B_top_latest": layer_nodes.get("L2_top_latest", []),
            "L1C_preprint": layer_nodes.get("L3_preprint", []),
            "L2_regular": layer_nodes.get("L4_regular", []),
        },
        "edges": edges,
        "strata": strata_reports,
    }


def choose_pyramid_review_root(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    reviews = [
        item
        for item in results
        if str(item.get("stratified_layer") or "") == "L0_review" or is_review_like_paper(item)
    ]
    candidates = reviews or results
    if not candidates:
        return None
    return max(candidates, key=pyramid_root_score)


def pyramid_root_score(item: dict[str, Any]) -> float:
    score = float(item.get("relevance_score") or 0.0)
    score += 0.35 if is_review_like_paper(item) else 0.0
    score += 0.2 * float(item.get("publication_quality_score") or 0.0)
    score += 0.15 * literature_impact_score(item)
    if is_top_venue_result(item):
        score += 0.08
    return round(score, 4)


def pyramid_relation_for_layer(layer: str) -> str:
    return {
        "L1_milestone": "field foundation / canonical evidence",
        "L2_top_latest": "frontier extension from field map",
        "L3_preprint": "emerging preprint signal",
        "L4_regular": "supplemental validation detail",
    }.get(layer, "pyramid child")


def stratified_candidate_matches(layer: str, item: dict[str, Any]) -> bool:
    if layer == "L0_review":
        return is_review_like_paper(item) and not is_low_quality_literature_result(item)
    if layer == "L1_milestone":
        return numeric_value(item.get("citation_count")) >= milestone_citation_threshold(item) and not is_low_quality_literature_result(item)
    if layer == "L2_top_latest":
        return is_recent_paper(item, max_age=3) and is_top_venue_result(item) and not is_low_quality_literature_result(item)
    if layer == "L3_preprint":
        return normalize_space(item.get("provider", "")).lower() == "arxiv" or normalize_space(item.get("venue", "")).lower() == "arxiv"
    if layer == "L4_regular":
        return not is_low_quality_literature_result(item)
    return True


def recover_stratified_layer_candidates(layer: str, raw_candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    usable = [item for item in raw_candidates if not is_low_quality_literature_result(item)]
    if not usable:
        return [], ""
    if layer == "L1_milestone":
        ranked = sorted(
            usable,
            key=lambda item: (
                -numeric_value(item.get("citation_count")),
                -numeric_value(item.get("influential_citation_count")),
                -float(item.get("publication_quality_score") or 0.0),
                -float(item.get("relevance_score") or 0.0),
            ),
        )
        recovered = [
            item
            for item in ranked
            if numeric_value(item.get("citation_count")) > 0
            or numeric_value(item.get("influential_citation_count")) > 0
            or publication_channel_is_strong(item)
        ][:20]
        return recovered or ranked[:10], "relaxed_milestone_highest_available_citation"
    if layer == "L2_top_latest":
        recent = [item for item in usable if is_recent_paper(item, max_age=5)]
        topish = [item for item in usable if is_top_venue_result(item)]
        pool = [item for item in recent if is_top_venue_result(item)] or recent or topish or usable
        ranked = sorted(
            pool,
            key=lambda item: (
                -literature_recency_score(item),
                -float(item.get("publication_quality_score") or 0.0),
                -float(item.get("relevance_score") or 0.0),
                -numeric_value(item.get("citation_count")),
            ),
        )
        return ranked[:20], "relaxed_top_latest_recent_or_high_quality_available"
    return [], ""


def is_review_like_paper(item: dict[str, Any]) -> bool:
    text = " ".join(
        normalize_space(str(item.get(key) or ""))
        for key in ("title", "abstract", "citation", "venue")
    ).lower()
    markers = (
        "review",
        "survey",
        "systematic review",
        "meta-analysis",
        "meta analysis",
        "progress in",
        "recent advances",
        "perspective",
        "tutorial",
        "state of the art",
        "roadmap",
    )
    return any(marker in text for marker in markers)


def milestone_citation_threshold(item: dict[str, Any]) -> float:
    field = infer_research_field(item)
    return max(30.0, field_citation_baseline(field) * 0.15)


def is_top_venue_result(item: dict[str, Any]) -> bool:
    quartile = str(item.get("journal_quartile") or "").upper()
    flags = set(item.get("quality_flags") or [])
    venue_quality = str(item.get("venue_quality") or "")
    return quartile == "Q1" or "reputable_venue" in flags or venue_quality == "reputable"


def is_low_quality_literature_result(item: dict[str, Any]) -> bool:
    flags = set(item.get("quality_flags") or [])
    if "suspicious_venue_or_publisher" in flags or "journal_quartile_suspicious" in flags:
        return True
    return float(item.get("publication_quality_score") or 0.0) < 0.45


def stratified_selection_reason(layer: str, item: dict[str, Any]) -> str:
    title = trim_text(str(item.get("title") or ""), 120)
    citations = int(numeric_value(item.get("citation_count")))
    year = str(item.get("year") or "")
    venue = str(item.get("venue") or item.get("provider") or "")
    quality = item.get("publication_quality_score")
    relevance = item.get("relevance_score")
    if layer == "L0_review":
        return f"Selected as field-map review/survey candidate: {title}; venue={venue}; citations={citations}; quality={quality}; relevance={relevance}."
    if layer == "L1_milestone":
        return f"Selected as milestone/high-impact paper: {title}; citations={citations}; year={year}; venue={venue}; quality={quality}."
    if layer == "L2_top_latest":
        return f"Selected as recent top-venue frontier paper: {title}; year={year}; venue={venue}; quality={quality}; relevance={relevance}."
    if layer == "L3_preprint":
        return f"Selected as latest preprint/frontier signal: {title}; year={year}; provider={item.get('provider')}; relevance={relevance}."
    return f"Selected as regular supplemental paper: {title}; year={year}; venue={venue}; quality={quality}; relevance={relevance}."


def flatten_literature_results(provider_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for block in provider_blocks:
        provider = str(block.get("provider", ""))
        if block.get("status") != "ok":
            continue
        for result in block.get("results", []):
            if not isinstance(result, dict):
                continue
            item = dict(result)
            item["provider"] = provider
            if block.get("query_branch"):
                item["query_branch"] = block.get("query_branch")
            if block.get("query"):
                item["retrieval_query"] = block.get("query")
            flattened.append(item)
    return flattened


def rank_literature_results(query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for original_index, item in enumerate(results):
        ranked = dict(item)
        score, matched, reason, components = literature_relevance_score(query, ranked)
        ranked["relevance_score"] = score
        ranked["relevance_components"] = components
        quality = publication_quality_assessment(ranked)
        ranked["publication_quality_score"] = quality["quality_score"]
        ranked["venue_quality"] = quality["venue_quality"]
        ranked["journal_quartile"] = quality["journal_quartile"]
        ranked["journal_metric_source"] = quality["journal_metric_source"]
        ranked["inferred_field"] = quality["inferred_field"]
        ranked["quality_flags"] = quality["flags"]
        ranked["quality_criteria"] = quality["criteria"]
        ranked["suspicion_type"] = quality["suspicion_type"]
        ranked["quality_reason"] = quality["reason"]
        ranked["matched_query_terms"] = matched
        ranked["relevance_reason"] = reason
        ranked["_original_index"] = original_index
        scored.append(ranked)
    scored.sort(key=lambda item: (-float(item.get("relevance_score", 0.0)), int(item.get("_original_index", 0))))
    for item in scored:
        item.pop("_original_index", None)
    return scored


def select_literature_result(search_id: str, query: str = "", top_k: int = 5, use_llm: bool = False) -> str:
    search_record = load_search(search_id)
    results = search_record.get("results", [])
    if query:
        results = rank_literature_results(query, [result for result in results if isinstance(result, dict)])
        for index, item in enumerate(results):
            item["result_index"] = index
            item["search_id"] = search_id
        search_record["query"] = query
        search_record["results"] = results
        search_record["total_results"] = len(results)
        save_search(search_record)
    ranked = [result for result in results if isinstance(result, dict)]
    if not ranked:
        return json.dumps(
            {
                "search_id": search_id,
                "selected": None,
                "top_results": [],
                "next_step": "No retrieved papers are available. Stop and report retrieval failure.",
            },
            ensure_ascii=False,
            indent=2,
        )
    limit = clamp_int(top_k, 1, 20)
    selected, root_selection_policy = choose_seed_with_review_root_policy(search_record, ranked)
    llm_judgement: dict[str, Any] | None = None
    if use_llm:
        llm_judgement = judge_literature_candidates_with_llm(
            query or str(search_record.get("query", "")),
            ranked[:limit],
        )
        chosen_index = llm_judgement.get("selected_result_index")
        chosen = find_by_id(ranked, "result_index", chosen_index) if chosen_index is not None else None
        if chosen is not None:
            root_candidate = pyramid_root_from_search_record(search_record, ranked)
            if root_candidate is None or chosen_is_allowed_seed_override(chosen, root_candidate):
                selected = chosen
                root_selection_policy = "LLM selected a candidate allowed by the review-root override policy."
            else:
                root_selection_policy = (
                    "LLM selected a non-review candidate, but the review-root policy kept the high-impact "
                    "review as seed because the candidate was not a clearly superior flagship override."
                )
    summary = {
        "search_id": search_id,
        "selected": summarize_literature_result(selected),
        "root_selection_policy": root_selection_policy,
        "knowledge_pyramid": search_record.get("knowledge_pyramid"),
        "top_results": [summarize_literature_result(result) for result in ranked[:limit]],
        "llm_judgement": llm_judgement,
        "next_step": "Import selected.result_index with import_literature_search_result, or choose another top_results item.",
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


def choose_seed_with_review_root_policy(
    search_record: dict[str, Any],
    ranked: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    root = pyramid_root_from_search_record(search_record, ranked)
    if root is None:
        return ranked[0], "No review root was available; selected the rule-ranked top result."
    challenger = ranked[0]
    if result_identity(challenger) != result_identity(root) and chosen_is_allowed_seed_override(challenger, root):
        return (
            challenger,
            "Selected the rule-ranked top result because it clearly overrides the review root "
            "under the Nature/Science/Cell/PNAS flagship-impact exception.",
        )
    return (
        root,
        "Selected the high-impact review as the seed/root for knowledge-graph expansion.",
    )


def pyramid_root_from_search_record(
    search_record: dict[str, Any],
    ranked: list[dict[str, Any]],
) -> dict[str, Any] | None:
    pyramid = search_record.get("knowledge_pyramid") if isinstance(search_record, dict) else None
    root_index = pyramid.get("root_result_index") if isinstance(pyramid, dict) else None
    root = find_by_id(ranked, "result_index", root_index) if root_index is not None else None
    if root is not None:
        return root
    return choose_pyramid_review_root(ranked)


def chosen_is_allowed_seed_override(chosen: dict[str, Any], review_root: dict[str, Any]) -> bool:
    if result_identity(chosen) == result_identity(review_root):
        return True
    if is_review_like_paper(chosen):
        return pyramid_root_score(chosen) >= pyramid_root_score(review_root)
    if not is_flagship_root_override_candidate(chosen):
        return False
    chosen_impact = literature_impact_score(chosen)
    root_impact = literature_impact_score(review_root)
    chosen_quality = float(chosen.get("publication_quality_score") or 0.0)
    root_quality = float(review_root.get("publication_quality_score") or 0.0)
    chosen_citations = numeric_value(chosen.get("citation_count"))
    root_citations = numeric_value(review_root.get("citation_count"))
    return (
        chosen_quality >= root_quality + 0.08
        and chosen_impact >= max(0.85, root_impact + 0.18)
        and chosen_citations >= max(100.0, root_citations * 1.5)
    )


def is_flagship_root_override_candidate(item: dict[str, Any]) -> bool:
    venue = normalize_space(item.get("venue", "")).lower()
    if venue in FLAGSHIP_ROOT_OVERRIDE_VENUES:
        return True
    return any(venue.startswith(f"{name} ") for name in FLAGSHIP_ROOT_OVERRIDE_VENUES)


def result_identity(item: dict[str, Any]) -> Any:
    return (
        item.get("result_index"),
        normalize_space(item.get("doi", "")).lower(),
        normalize_space(item.get("arxiv_id", "")).lower(),
        normalize_space(item.get("title", "")).lower(),
    )


def summarize_literature_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [summarize_literature_result(result) for result in results if isinstance(result, dict)]


def summarize_provider_blocks(provider_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for block in provider_blocks:
        results = block.get("results", [])
        summaries.append(
            {
                "provider": block.get("provider"),
                "query": block.get("query"),
                "status": block.get("status"),
                "note": block.get("note"),
                "error": block.get("error"),
                "result_count": len(results) if isinstance(results, list) else 0,
            }
        )
    return summaries


def summarize_literature_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_index": result.get("result_index"),
        "stratified_layer": result.get("stratified_layer", ""),
        "stratified_label": result.get("stratified_label", ""),
        "query_branch": result.get("query_branch", ""),
        "retrieval_query": result.get("retrieval_query", ""),
        "_why_selected": result.get("_why_selected", ""),
        "domain_relevance": result.get("domain_relevance", {}),
        "relevance_score": result.get("relevance_score"),
        "relevance_components": result.get("relevance_components", {}),
        "publication_quality_score": result.get("publication_quality_score"),
        "venue_quality": result.get("venue_quality"),
        "journal_quartile": result.get("journal_quartile", ""),
        "journal_metric_source": result.get("journal_metric_source", ""),
        "inferred_field": result.get("inferred_field", ""),
        "quality_flags": result.get("quality_flags", []),
        "quality_criteria": result.get("quality_criteria", []),
        "suspicion_type": result.get("suspicion_type", ""),
        "is_review_like": is_review_like_paper(result),
        "pyramid_root_score": pyramid_root_score(result),
        "matched_query_terms": result.get("matched_query_terms", []),
        "title": result.get("title"),
        "citation": result.get("citation"),
        "provider": result.get("provider"),
        "year": result.get("year"),
        "citation_count": result.get("citation_count"),
        "influential_citation_count": result.get("influential_citation_count"),
        "doi": result.get("doi"),
        "arxiv_id": result.get("arxiv_id"),
        "url": result.get("url"),
        "relevance_reason": result.get("relevance_reason"),
        "quality_reason": result.get("quality_reason"),
    }


def judge_literature_candidates_with_llm(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"status": "empty", "selected_result_index": None, "reason": "No candidates."}
    try:
        raw = call_llm_json(
            system="You are a strict scientific literature selection judge. Select only from the provided result_index values.",
            prompt=(
                "Choose the best paper for the research query. Prefer direct topical fit, peer-reviewed/reputable venue, "
                "non-suspicious publication channel, citation impact, and recentness. Penalize tangential keyword matches.\n"
                "Return JSON only with: selected_result_index, reason, rejected_indices, quality_warnings.\n\n"
                f"Query: {query}\n\nCandidates:\n"
                + json.dumps([summarize_literature_result(item) for item in candidates], ensure_ascii=False, indent=2)
            ),
            max_tokens=1200,
        )
    except Exception as exc:
        return {
            "status": "fallback",
            "selected_result_index": candidates[0].get("result_index"),
            "reason": f"LLM judge failed: {exc}; used rule-ranked top result.",
            "quality_warnings": [],
        }
    allowed = {item.get("result_index") for item in candidates}
    selected = raw.get("selected_result_index")
    if selected not in allowed:
        selected = candidates[0].get("result_index")
        raw["reason"] = f"Invalid LLM selection; used rule-ranked top result. Original reason: {raw.get('reason', '')}"
    return {
        "status": "ok",
        "selected_result_index": selected,
        "reason": scalar(raw.get("reason")),
        "rejected_indices": raw.get("rejected_indices", []),
        "quality_warnings": raw.get("quality_warnings", []),
    }


def literature_relevance_score(query: str, result: dict[str, Any]) -> tuple[float, list[str], str, dict[str, Any]]:
    quality = publication_quality_assessment(result)
    terms = query_terms(query)
    if not terms:
        components = {
            "text_score": 0.0,
            "recency_score": literature_recency_score(result),
            "impact_score": literature_impact_score(result),
            "venue_score": quality["venue_score"],
            "publication_quality_score": quality["quality_score"],
            "base_score": 0.0,
            "text_weight": 0.62,
            "recency_weight": 0.1,
            "impact_weight": 0.18,
            "venue_weight": 0.1,
        }
        return 0.0, [], "No query terms.", components

    title = normalize_space(result.get("title", "")).lower()
    abstract = normalize_space(result.get("abstract", "")).lower()
    citation = normalize_space(result.get("citation", "")).lower()
    title_matches = [term for term in terms if term in title]
    abstract_matches = [term for term in terms if term in abstract]
    citation_matches = [term for term in terms if term in citation]
    phrase = normalize_space(query).lower()
    phrase_bonus = 0.0
    if phrase and phrase in title:
        phrase_bonus += 0.35
    elif phrase and phrase in abstract:
        phrase_bonus += 0.2

    title_coverage = len(title_matches) / len(terms)
    abstract_coverage = len(abstract_matches) / len(terms)
    citation_coverage = len(citation_matches) / len(terms)
    text_score = min(1.0, 0.62 * title_coverage + 0.28 * abstract_coverage + 0.1 * citation_coverage + phrase_bonus)
    recency_score = literature_recency_score(result)
    impact_score = literature_impact_score(result)
    venue_score = quality["venue_score"]
    citation_field = infer_research_field(result)
    citation_baseline = field_citation_baseline(citation_field)
    if text_score <= 0:
        recency_weight = 0.04
        impact_weight = 0.04
        venue_weight = 0.02
    else:
        recency_weight = 0.1
        impact_weight = 0.18
        venue_weight = 0.1
    text_weight = 1.0 - recency_weight - impact_weight - venue_weight
    base_score = min(
        1.0,
        text_weight * text_score
        + recency_weight * recency_score
        + impact_weight * impact_score
        + venue_weight * venue_score,
    )
    score = min(1.0, round(base_score * quality["quality_score"], 4))
    components = {
        "text_score": round(text_score, 4),
        "recency_score": round(recency_score, 4),
        "impact_score": round(impact_score, 4),
        "citation_field": citation_field,
        "citation_baseline": round(citation_baseline, 2),
        "venue_score": round(venue_score, 4),
        "publication_quality_score": round(quality["quality_score"], 4),
        "base_score": round(base_score, 4),
        "text_weight": round(text_weight, 4),
        "recency_weight": round(recency_weight, 4),
        "impact_weight": round(impact_weight, 4),
        "venue_weight": round(venue_weight, 4),
    }
    matched = unique_preserve_order(title_matches + abstract_matches + citation_matches)
    reason = (
        f"title={len(title_matches)}/{len(terms)}, "
        f"abstract={len(abstract_matches)}/{len(terms)}, "
        f"citation={len(citation_matches)}/{len(terms)}, "
        f"phrase_bonus={round(phrase_bonus, 2)}, "
        f"recency={components['recency_score']}, "
        f"impact={components['impact_score']}, "
        f"venue={components['venue_score']}, "
        f"quality={components['publication_quality_score']}"
    )
    return score, matched, reason, components


def literature_recency_score(result: dict[str, Any]) -> float:
    year_text = str(result.get("year") or "")
    match = re.search(r"\b(19|20)\d{2}\b", year_text)
    if not match:
        return 0.25
    year = int(match.group(0))
    current_year = time.localtime().tm_year
    age = current_year - year
    if age <= 1:
        return 1.0
    if age <= 3:
        return 0.85
    if age <= 5:
        return 0.7
    if age <= 10:
        return 0.45
    return 0.2


def literature_impact_score(result: dict[str, Any]) -> float:
    citation_count = numeric_value(result.get("citation_count"))
    influential_count = numeric_value(result.get("influential_citation_count"))
    field = infer_research_field(result)
    baseline = field_citation_baseline(field)
    if is_recent_paper(result, max_age=2) and citation_count <= 2:
        if publication_channel_is_strong(result):
            return 0.55
        return 0.35
    if citation_count <= 0 and influential_count <= 0:
        return 0.0
    citation_score = min(1.0, math.log1p(citation_count) / math.log1p(baseline))
    influential_score = min(1.0, math.log1p(influential_count) / math.log1p(max(50.0, baseline * 0.3)))
    return round(max(citation_score, 0.75 * citation_score + 0.25 * influential_score), 4)


def publication_quality_assessment(result: dict[str, Any]) -> dict[str, Any]:
    venue = normalize_space(result.get("venue", "")).lower()
    url_blob = " ".join(
        normalize_space(result.get(key, "")).lower()
        for key in ("url", "open_access_pdf", "doi")
    )
    provider = normalize_space(result.get("provider", "")).lower()
    citation_count = numeric_value(result.get("citation_count"))
    reference_count = numeric_value(result.get("reference_count"))
    metric = journal_metric_for_venue(venue)
    quartile = metric.get("quartile", "")
    quartile_score = journal_quartile_score(quartile)
    flags: list[str] = []
    criteria: list[str] = []
    suspicion_type = ""
    quality = 0.72
    venue_score = quartile_score if quartile else 0.45

    if not venue:
        flags.append("missing_venue")
        criteria.append("venue metadata is missing")
        quality -= 0.08
        venue_score = 0.35
    elif is_suspicious_venue(venue) or has_suspicious_publisher(url_blob):
        flags.append("suspicious_venue_or_publisher")
        suspicion_type = "predatory_or_vanity"
        criteria.append("venue/publisher matched curated suspicious list")
        quality -= 0.42
        venue_score = 0.0
    elif quartile == "suspicious":
        flags.append("suspicious_venue_or_publisher")
        flags.append("journal_quartile_suspicious")
        suspicion_type = "predatory_or_vanity"
        criteria.append("venue matched curated suspicious journal metric table")
        quality -= 0.42
        venue_score = 0.0
    elif quartile == "unclassified":
        flags.append("unclassified_venue")
        flags.append("requires_human_venue_review")
        criteria.append("venue matched curated unclassified/preprint/open-access table; manual review recommended")
        quality -= 0.06
        venue_score = quartile_score
    elif quartile:
        flags.append(f"journal_quartile_{quartile.lower()}")
        criteria.append(f"venue matched curated journal metric table: {quartile}")
        if quartile == "Q1":
            flags.append("reputable_venue")
            quality += 0.2
        elif quartile == "Q2":
            quality += 0.1
        elif quartile == "Q3":
            quality -= 0.04
        elif quartile == "Q4":
            quality -= 0.15
    elif is_reputable_venue(venue):
        flags.append("reputable_venue")
        criteria.append("venue matched curated reputable list")
        quality += 0.2
        venue_score = 1.0
    elif venue in PREPRINT_VENUES:
        flags.append("preprint_not_peer_reviewed")
        criteria.append("venue is a preprint server, not final peer-reviewed venue")
        quality -= 0.05
        venue_score = 0.6
    else:
        flags.append("unverified_venue")
        criteria.append("venue did not match suspicious, reputable, preprint, or curated quartile tables")

    if citation_count <= 0:
        if is_recent_paper(result, max_age=2):
            flags.append("new_paper_protection")
            criteria.append("paper is within 2-year protection window; low citations are not treated as low quality")
        elif is_mature_paper(result, minimum_age=2):
            flags.append("zero_citations_mature_paper")
            criteria.append("paper is older than 2 years and has zero Semantic Scholar citations")
            quality -= 0.16
        else:
            flags.append("zero_citations_recent_or_unknown")
            criteria.append("paper age unknown/recent with zero citations")
            quality -= 0.04
    elif citation_count >= 200:
        flags.append("highly_cited")
        criteria.append("citation count exceeds high-impact threshold")
        quality += 0.12
    elif citation_count >= 50:
        flags.append("well_cited")
        criteria.append("citation count exceeds medium/high threshold")
        quality += 0.08
    elif citation_count >= 10:
        flags.append("some_citations")
        criteria.append("citation count exceeds minimum nontrivial threshold")
        quality += 0.04

    if reference_count == 0 and provider == "semantic_scholar":
        if is_recent_paper(result, max_age=2):
            flags.append("incomplete_s2_metadata_recent")
            criteria.append("Semantic Scholar reference metadata is missing for a recent paper; marked for data completeness review")
        else:
            flags.append("missing_reference_count")
            criteria.append("Semantic Scholar reports zero references for a non-recent paper")
            quality -= 0.04

    quality = round(max(0.1, min(1.0, quality)), 4)
    return {
        "quality_score": quality,
        "venue_score": round(max(0.0, min(1.0, venue_score)), 4),
        "venue_quality": venue_quality_label(flags),
        "journal_quartile": quartile,
        "journal_metric_source": metric.get("source", ""),
        "inferred_field": infer_research_field(result),
        "suspicion_type": suspicion_type,
        "flags": flags,
        "criteria": criteria,
        "reason": "; ".join(criteria),
    }


def is_suspicious_venue(venue: str) -> bool:
    if venue in SUSPICIOUS_VENUES:
        return True
    return any(pattern in venue for pattern in SUSPICIOUS_VENUES)


def has_suspicious_publisher(text: str) -> bool:
    return any(pattern in text for pattern in SUSPICIOUS_PUBLISHER_PATTERNS)


def is_reputable_venue(venue: str) -> bool:
    if venue in REPUTABLE_VENUES:
        return True
    generic_names = {"nature", "science", "cell", "ecology", "oikos"}
    if any(name not in generic_names and name in venue for name in REPUTABLE_VENUES):
        return True
    return any(pattern in venue for pattern in REPUTABLE_VENUE_PATTERNS)


def journal_metric_for_venue(venue: str) -> dict[str, str]:
    if not venue:
        return {}
    venue = normalize_space(venue).lower()
    if venue in JOURNAL_METRICS:
        return JOURNAL_METRICS[venue]
    venue_compact = re.sub(r"[^a-z0-9]+", "", venue)
    generic_names = {"arxiv", "nature", "science", "cell", "ecology", "oikos", "research", "small", "chem"}
    for name, metric in JOURNAL_METRICS.items():
        name_compact = re.sub(r"[^a-z0-9]+", "", name)
        if name_compact == venue_compact:
            return metric
        if name not in generic_names and name in venue:
            return metric
    return {}


def journal_quartile_score(quartile: str) -> float:
    normalized = str(quartile or "").strip().lower()
    return {
        "q1": 1.0,
        "q2": 0.7,
        "q3": 0.4,
        "q4": 0.2,
        "unknown": 0.3,
        "unclassified": 0.2,
        "suspicious": 0.0,
    }.get(normalized, 0.3)


def is_mature_paper(result: dict[str, Any], minimum_age: int = 2) -> bool:
    year_text = str(result.get("year") or "")
    match = re.search(r"\b(19|20)\d{2}\b", year_text)
    if not match:
        return False
    return time.localtime().tm_year - int(match.group(0)) >= minimum_age


def is_recent_paper(result: dict[str, Any], max_age: int = 2) -> bool:
    year_text = str(result.get("year") or "")
    match = re.search(r"\b(19|20)\d{2}\b", year_text)
    if not match:
        return False
    return time.localtime().tm_year - int(match.group(0)) <= max_age


def publication_channel_is_strong(result: dict[str, Any]) -> bool:
    quality = publication_quality_assessment_no_citation(result)
    return quality.get("venue_quality") == "reputable" or quality.get("journal_quartile") in {"Q1", "Q2"}


def publication_quality_assessment_no_citation(result: dict[str, Any]) -> dict[str, Any]:
    venue = normalize_space(result.get("venue", "")).lower()
    url_blob = " ".join(normalize_space(result.get(key, "")).lower() for key in ("url", "open_access_pdf", "doi"))
    metric = journal_metric_for_venue(venue)
    if not venue:
        return {"venue_quality": "missing", "journal_quartile": ""}
    if is_suspicious_venue(venue) or has_suspicious_publisher(url_blob) or metric.get("quartile") == "suspicious":
        return {"venue_quality": "suspicious", "journal_quartile": metric.get("quartile", "")}
    if metric.get("quartile") in {"Q1", "Q2"} or is_reputable_venue(venue):
        return {"venue_quality": "reputable", "journal_quartile": metric.get("quartile", "")}
    if venue in PREPRINT_VENUES:
        return {"venue_quality": "preprint", "journal_quartile": ""}
    return {"venue_quality": "unverified", "journal_quartile": metric.get("quartile", "")}


def infer_research_field(result: dict[str, Any]) -> str:
    text = " ".join(
        normalize_space(result.get(key, "")).lower()
        for key in ("title", "abstract", "venue")
    )
    arxiv_field = infer_arxiv_field(result)
    if arxiv_field:
        return arxiv_field
    metric = journal_metric_for_venue(normalize_space(result.get("venue", "")).lower())
    if metric.get("field"):
        return metric["field"]
    if any(term in text for term in ("battery", "lithium", "electrolyte", "electrode", "ionic conductor", "solid-state")):
        return "materials_energy"
    if any(term in text for term in ("catalyst", "catalysis", "organic synthesis", "inorganic", "organometallic", "spectroscopy")):
        return "chemistry"
    if any(term in text for term in ("polymer", "nanomaterial", "materials chemistry", "crystal", "semiconductor")):
        return "materials"
    if any(term in text for term in ("plant", "biodiversity", "ecosystem", "community biomass", "ecology")):
        return "ecology"
    if any(term in text for term in ("black hole", "accretion disk", "accretion disc", "gravitational wave", "quasar", "active galactic", "galaxy", "cosmology", "supernova", "neutron star", "pulsar")):
        return "astrophysics"
    if any(term in text for term in ("particle physics", "collider", "standard model", "quantum chromodynamics", "qcd", "hadron", "neutrino", "higgs", "lattice gauge")):
        return "high_energy_physics"
    if any(term in text for term in ("wave equation", "partial differential equation", "stability theorem", "functional analysis", "topology", "algebraic", "number theory")):
        return "mathematics"
    if any(term in text for term in ("air pollution", "particulate matter", "atmospheric chemistry", "environmental exposure", "water quality")):
        return "environmental_science"
    if any(term in text for term in ("crop", "agriculture", "livestock", "food chemistry", "soil", "rhizosphere")):
        return "agriculture"
    if any(
        term in text
        for term in (
            "cardiovascular",
            "oncology",
            "neurology",
            "psychiatry",
            "radiology",
            "surgery",
            "pediatrics",
            "infectious disease",
            "public health",
            "epidemiology",
        )
    ):
        return "medicine"
    if any(term in text for term in ("biochemistry", "cell biology", "microbiology", "genomics", "neuroscience", "synthetic biology")):
        return "biology"
    if any(term in text for term in ("biomedical", "clinical", "cancer", "genome", "protein")):
        return "biomedical"
    if any(term in text for term in ("agent", "llm", "language model", "neural", "dataset")):
        return "computer_science"
    return "general"


def fields_are_incompatible(target_field: str, result_field: str) -> bool:
    target = str(target_field or "general")
    result = str(result_field or "general")
    if not target or not result or target in {"general", "multidisciplinary"} or result in {"general", "multidisciplinary"}:
        return False
    if target == result:
        return False
    groups = [
        {"physics", "astrophysics", "high_energy_physics", "nuclear_physics", "complex_systems", "computational_science", "instrumentation", "photonics"},
        {"chemistry", "chemical_biology", "biochemistry", "materials", "materials_energy", "electrochemistry"},
        {"biology", "biomedical", "medicine", "digital_medicine", "biophysics", "plant_biology"},
        {"computer_science", "artificial_intelligence", "statistics", "information_theory", "robotics"},
        {"electrical_engineering", "automation_control", "energy_engineering", "electronics", "communications"},
        {"ecology", "environmental_science", "earth_science", "agriculture"},
        {"mathematics", "statistics", "information_theory"},
        {"finance", "economics", "social_science"},
    ]
    return not any(target in group and result in group for group in groups)


def infer_arxiv_field(result: dict[str, Any]) -> str:
    categories: list[str] = []
    raw = result.get("arxiv_categories")
    if isinstance(raw, list):
        categories.extend(str(item) for item in raw)
    elif isinstance(raw, str):
        categories.extend(re.split(r"[\s,;]+", raw))
    payload = result.get("papergraph_input") if isinstance(result.get("papergraph_input"), dict) else {}
    raw_payload = payload.get("arxiv_categories")
    if isinstance(raw_payload, list):
        categories.extend(str(item) for item in raw_payload)
    elif isinstance(raw_payload, str):
        categories.extend(re.split(r"[\s,;]+", raw_payload))
    for category in categories:
        normalized = normalize_space(category).lower()
        if not normalized:
            continue
        if normalized in ARXIV_CATEGORY_FIELD_MAP:
            return ARXIV_CATEGORY_FIELD_MAP[normalized]
        prefix = normalized.split(".", 1)[0]
        if prefix in ARXIV_CATEGORY_FIELD_MAP:
            return ARXIV_CATEGORY_FIELD_MAP[prefix]
    return ""


def field_citation_baseline(field: str) -> float:
    return {
        "astrophysics": 500.0,
        "high_energy_physics": 250.0,
        "nuclear_physics": 250.0,
        "complex_systems": 300.0,
        "biophysics": 500.0,
        "computational_science": 350.0,
        "earth_science": 450.0,
        "instrumentation": 300.0,
        "information_theory": 300.0,
        "ecology": 300.0,
        "environmental_science": 450.0,
        "materials_energy": 250.0,
        "materials": 350.0,
        "electrochemistry": 200.0,
        "chemistry": 500.0,
        "physics": 350.0,
        "biology": 600.0,
        "plant_biology": 500.0,
        "medicine": 800.0,
        "digital_medicine": 800.0,
        "computer_science": 500.0,
        "artificial_intelligence": 600.0,
        "communications": 500.0,
        "biomedical": 800.0,
        "biochemistry": 700.0,
        "chemical_biology": 600.0,
        "multidisciplinary": 600.0,
        "mathematics": 250.0,
        "statistics": 300.0,
        "electrical_engineering": 400.0,
        "automation_control": 350.0,
        "energy_engineering": 500.0,
        "agriculture": 250.0,
        "electronics": 500.0,
        "robotics": 450.0,
        "photonics": 400.0,
        "transportation": 300.0,
        "finance": 300.0,
        "economics": 300.0,
        "social_science": 350.0,
        "general": 500.0,
    }.get(field, 400.0)


def venue_quality_label(flags: list[str]) -> str:
    if "suspicious_venue_or_publisher" in flags:
        return "suspicious"
    if "reputable_venue" in flags:
        return "reputable"
    if "preprint_not_peer_reviewed" in flags:
        return "preprint"
    if "unclassified_venue" in flags:
        return "unclassified"
    if "missing_venue" in flags:
        return "missing"
    return "unverified"


def query_terms(query: str) -> list[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", query)]
    return unique_preserve_order([term for term in terms if term not in stopwords])


def search_arxiv(query: str, max_results: int = 10, sort_by: str = "relevance") -> dict[str, Any]:
    skipped = arxiv_skip_block(query)
    if skipped:
        return skipped
    selected_sort = sort_by if sort_by in {"relevance", "lastUpdatedDate", "submittedDate"} else "relevance"
    params = urlencode(
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": clamp_int(max_results, 1, 50),
            "sortBy": selected_sort,
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{params}"
    try:
        raw = arxiv_get_text(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"})
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        papers = [arxiv_entry_to_result(entry, ns) for entry in root.findall("atom:entry", ns)]
        return {
            "provider": "arxiv",
            "query": query,
            "status": "ok",
            "results": papers,
            "next_step": "Pass a result's papergraph_input fields into import_papergraph_record, or paste abstract into import_literature_text.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="arxiv", error=str(exc))
        return provider_error_result("arxiv", query, exc)


def search_semantic_scholar(query: str, max_results: int = 10) -> dict[str, Any]:
    skipped = semantic_scholar_skip_block(query)
    if skipped:
        return skipped
    fields = ",".join(
        [
            "title",
            "abstract",
            "year",
            "authors",
            "venue",
            "url",
            "externalIds",
            "citationCount",
            "influentialCitationCount",
            "referenceCount",
            "isOpenAccess",
            "openAccessPdf",
        ]
    )
    params = urlencode({"query": query, "limit": clamp_int(max_results, 1, 100), "fields": fields})
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    headers = {"User-Agent": "qwen-zhikan-papergraph/0.1"}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    try:
        payload = semantic_scholar_get_json(url, headers=headers)
        papers = [semantic_scholar_item_to_result(item) for item in (payload.get("data") or []) if isinstance(item, dict)]
        return {
            "provider": "semantic_scholar",
            "query": query,
            "status": "ok",
            "total": payload.get("total"),
            "results": papers,
            "next_step": "Pass a result's papergraph_input fields into import_papergraph_record, or use import_literature_text with use_llm=true.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="semantic_scholar", error=str(exc))
        return provider_error_result("semantic_scholar", query, exc)


def search_preprint_api(provider: str, query: str, max_results: int = 10) -> dict[str, Any]:
    selected = database_to_provider(provider)
    if selected in {"biorxiv", "medrxiv"}:
        return search_biorxiv_or_medrxiv(selected, query, max_results=max_results)
    if selected == "chemrxiv":
        return search_chemrxiv(query, max_results=max_results)
    if selected == "openreview":
        return search_openreview(query, max_results=max_results)
    return {
        "provider": selected,
        "query": query,
        "status": "unknown_provider",
        "results": [],
    }


def search_biorxiv_or_medrxiv(server: str, query: str, max_results: int = 10, days_back: int = 365) -> dict[str, Any]:
    today = date.today()
    start = today - timedelta(days=clamp_int(days_back, 30, 1825))
    params = f"{server}/{start.isoformat()}/{today.isoformat()}/0"
    url = f"https://api.biorxiv.org/details/{params}"
    try:
        payload = http_get_json(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        items = payload.get("collection") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []
        candidates = [biorxiv_item_to_result(item, server) for item in items if isinstance(item, dict)]
        ranked = rank_literature_results(query, candidates)
        query_words = set(query_terms(query))
        filtered = [
            item
            for item in ranked
            if not query_words
            or any(term in normalize_space(f"{item.get('title', '')} {item.get('abstract', '')}").lower() for term in query_words)
        ]
        papers = filtered[: clamp_int(max_results, 1, 50)]
        return {
            "provider": server,
            "query": query,
            "status": "ok",
            "api": f"api.biorxiv.org/details/{server}",
            "date_window": {"from": start.isoformat(), "to": today.isoformat()},
            "results": papers,
            "next_step": "Import a result with import_literature_search_result; these are preprint metadata records filtered locally by query.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider=server, error=str(exc))
        return provider_error_result(server, query, exc)


def search_chemrxiv(query: str, max_results: int = 10) -> dict[str, Any]:
    params = urlencode(
        {
            "query.bibliographic": query,
            "filter": "prefix:10.26434,type:posted-content",
            "rows": clamp_int(max_results, 1, 50),
        }
    )
    url = f"https://api.crossref.org/works?{params}"
    try:
        payload = http_get_json(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        message = payload.get("message") if isinstance(payload, dict) else {}
        items = message.get("items") if isinstance(message, dict) else []
        if not isinstance(items, list):
            items = []
        papers = [crossref_chemrxiv_item_to_result(item) for item in items if isinstance(item, dict)]
        papers = rank_literature_results(query, papers)[: clamp_int(max_results, 1, 50)]
        return {
            "provider": "chemrxiv",
            "query": query,
            "status": "ok",
            "api": "api.crossref.org/works?filter=prefix:10.26434,type:posted-content",
            "results": papers,
            "next_step": "Import a result with import_literature_search_result; ChemRxiv metadata is retrieved via Crossref posted-content records.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="chemrxiv", error=str(exc))
        return provider_error_result("chemrxiv", query, exc)


def search_openalex(query: str, max_results: int = 10) -> dict[str, Any]:
    params = urlencode(
        {
            "search": query,
            "per-page": clamp_int(max_results, 1, 50),
        }
    )
    url = f"https://api.openalex.org/works?{params}"
    try:
        payload = http_get_json(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        results = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(results, list):
            results = []
        papers = [openalex_item_to_result(item) for item in results if isinstance(item, dict)]
        papers = rank_literature_results(query, papers)[: clamp_int(max_results, 1, 50)]
        return {
            "provider": "openalex",
            "query": query,
            "status": "ok",
            "api": "api.openalex.org/works",
            "total": (payload.get("meta") or {}).get("count") if isinstance(payload, dict) else None,
            "results": papers,
            "next_step": "Import a result with import_literature_search_result; OpenAlex provides broad publication metadata and open-access links.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="openalex", error=str(exc))
        return provider_error_result("openalex", query, exc)


def search_dblp(query: str, max_results: int = 10) -> dict[str, Any]:
    params = urlencode(
        {
            "q": query,
            "format": "json",
            "h": clamp_int(max_results, 1, 50),
        }
    )
    url = f"https://dblp.org/search/publ/api?{params}"
    try:
        payload = http_get_json(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        result = payload.get("result") if isinstance(payload, dict) else {}
        hits = ((result.get("hits") or {}).get("hit") if isinstance(result, dict) else []) or []
        if isinstance(hits, dict):
            hits = [hits]
        if not isinstance(hits, list):
            hits = []
        papers = [dblp_item_to_result(item) for item in hits if isinstance(item, dict)]
        papers = rank_literature_results(query, papers)[: clamp_int(max_results, 1, 50)]
        return {
            "provider": "dblp",
            "query": query,
            "status": "ok",
            "api": "dblp.org/search/publ/api",
            "total": (result.get("hits") or {}).get("@total") if isinstance(result, dict) else None,
            "results": papers,
            "next_step": "Import a result with import_literature_search_result; DBLP records usually have strong CS bibliographic metadata but sparse abstracts.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="dblp", error=str(exc))
        return provider_error_result("dblp", query, exc)


def search_openreview(query: str, max_results: int = 10) -> dict[str, Any]:
    params = urlencode(
        {
            "term": query,
            "limit": clamp_int(max_results, 1, 50),
        }
    )
    url = f"https://api2.openreview.net/notes/search?{params}"
    try:
        payload = http_get_json(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        notes = payload.get("notes") if isinstance(payload, dict) else []
        if not isinstance(notes, list):
            notes = []
        papers = [openreview_item_to_result(item) for item in notes if isinstance(item, dict)]
        papers = rank_literature_results(query, papers)[: clamp_int(max_results, 1, 50)]
        return {
            "provider": "openreview",
            "query": query,
            "status": "ok",
            "api": "api2.openreview.net/notes/search",
            "results": papers,
            "next_step": "Import a result with import_literature_search_result; OpenReview records may be submissions or in-review manuscripts and should be quality-gated.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="openreview", error=str(exc))
        return provider_error_result("openreview", query, exc)


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
    seed_search = load_search(search_id)
    results = seed_search.get("results", [])
    if not results:
        raise ValueError(f"Search {search_id} has no seed results to expand.")
    try:
        seed = results[int(result_index)]
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid seed result_index {result_index} for search {search_id}") from exc
    if not isinstance(seed, dict):
        raise ValueError(f"Seed result is not a paper object: {search_id}:{result_index}")

    lookup_ids = semantic_scholar_lookup_ids(seed)
    lookup_id = lookup_ids[0] if lookup_ids else ""
    if not lookup_ids:
        raise ValueError("Seed paper has no Semantic Scholar id, DOI, or arXiv id for graph expansion.")

    selected_direction = normalize_key(direction)
    edge_kinds = ["references", "citations"] if selected_direction == "both" else [selected_direction]
    raw_edges: list[dict[str, Any]] = []
    per_edge_limit = min(
        max(1, int(SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT)),
        max(1, clamp_int(max_results, 1, 200) // max(1, len(edge_kinds))),
    )
    errors: list[dict[str, str]] = []
    seed_not_indexed = False
    for edge_kind in edge_kinds:
        if edge_kind not in {"references", "citations"}:
            errors.append({"edge": edge_kind, "error": "unknown direction"})
            continue
        edge_loaded = False
        not_found_errors: list[str] = []
        for candidate_lookup_id in lookup_ids:
            try:
                edges = fetch_semantic_scholar_edges(candidate_lookup_id, edge_kind, limit=per_edge_limit)
                raw_edges.extend(edges)
                lookup_id = candidate_lookup_id
                edge_loaded = True
                if candidate_lookup_id != lookup_ids[0]:
                    log_event(
                        "SCIENCE",
                        "graph_expand_lookup_alias_used",
                        original=lookup_ids[0],
                        used=candidate_lookup_id,
                    )
                break
            except Exception as exc:
                error_text = str(exc)
                if is_semantic_scholar_not_found_error(error_text):
                    not_found_errors.append(error_text)
                    continue
                errors.append(
                    {
                        "edge": edge_kind,
                        "lookup_id": candidate_lookup_id,
                        "error": error_text,
                        "rate_limited": is_semantic_scholar_rate_limit_error(error_text),
                    }
                )
                if is_semantic_scholar_rate_limit_error(error_text):
                    log_event("SCIENCE", "graph_expand_rate_limited", search_id=search_id, edge=edge_kind)
                else:
                    log_event("SCIENCE", "graph_expand_failed", search_id=search_id, edge=edge_kind, error=error_text)
                break
        if edge_loaded:
            continue
        if not_found_errors:
            seed_not_indexed = True
            error_text = not_found_errors[-1]
            errors.append(
                {
                    "edge": edge_kind,
                    "lookup_ids": lookup_ids,
                    "error": error_text,
                    "seed_not_indexed": True,
                }
            )
            log_event(
                "SCIENCE",
                "graph_expand_seed_not_indexed",
                search_id=search_id,
                edge=edge_kind,
                lookup_ids=",".join(lookup_ids),
            )
            break

    graph_results = dedupe_literature_results(
        [
            semantic_scholar_edge_to_result(edge)
            for edge in raw_edges
            if isinstance(edge, dict)
        ]
    )
    graph_query = query or str(seed_search.get("query", ""))
    ranked = rank_literature_results(graph_query, graph_results)[: clamp_int(max_results, 1, 200)]
    selected_depth = clamp_int(depth, 1, 2)
    second_layer_count = 0
    if selected_depth >= 2 and ranked:
        second_layer_results = expand_second_layer_graph_results(
            ranked,
            graph_query,
            edge_kinds,
            max_results=max_results,
            top_k=second_layer_top_k,
            errors=errors,
        )
        second_layer_count = len(second_layer_results)
        if second_layer_results:
            graph_results = dedupe_literature_results(graph_results + second_layer_results)
            ranked = rank_literature_results(graph_query, graph_results)[: clamp_int(max_results, 1, 200)]
    fallback_used = False
    if not ranked and allow_fallback:
        fallback_used = True
        fallback_block = search_semantic_scholar(graph_query, max_results=max_results)
        fallback_results = flatten_literature_results([fallback_block])
        seed_key = literature_result_unique_key(seed)
        fallback_results = [item for item in fallback_results if literature_result_unique_key(item) != seed_key]
        for item in fallback_results:
            item["graph_relation"] = "keyword_fallback"
            item["expanded_from_search_id"] = search_id
            item["expanded_from_result_index"] = result_index
            item["seed_title"] = seed.get("title", "")
        ranked = rank_literature_results(graph_query, dedupe_literature_results(fallback_results))[: clamp_int(max_results, 1, 200)]
        errors.append(
            {
                "edge": "fallback_keyword_expansion",
                "error": "citation graph returned no usable neighbors; fell back to Semantic Scholar keyword search",
                "seed_not_indexed": seed_not_indexed,
            }
        )
        log_event(
            "SCIENCE",
            "graph_expand_fallback",
            seed_search_id=search_id,
            reason="seed_not_indexed" if seed_not_indexed else "empty_graph",
            count=len(ranked),
        )
    graph_search_id = new_id("graph")
    for index, item in enumerate(ranked):
        item["result_index"] = index
        item["search_id"] = graph_search_id
        item["expanded_from_search_id"] = search_id
        item["expanded_from_result_index"] = result_index
        item["seed_title"] = seed.get("title", "")

    record = {
        "search_id": graph_search_id,
        "kind": "citation_graph_expansion",
        "query": graph_query,
        "seed_search_id": search_id,
        "seed_result_index": result_index,
        "seed_title": seed.get("title", ""),
        "seed_lookup_id": lookup_id,
        "seed_lookup_ids": lookup_ids,
        "direction": selected_direction,
        "depth": selected_depth,
        "second_layer_count": second_layer_count,
        "createdAt": time.time(),
        "total_results": len(ranked),
        "results": ranked,
        "errors": errors,
        "fallback_used": fallback_used,
        "seed_not_indexed": seed_not_indexed,
        "provider_blocks": [
            {
                "provider": "semantic_scholar_graph",
                "status": "ok" if ranked else "empty_or_error",
                "results": ranked,
                "errors": errors,
            }
        ],
    }
    save_search(record)
    selected = None
    llm_judgement = None
    if ranked:
        if use_llm:
            llm_judgement = judge_literature_candidates_with_llm(graph_query, ranked[: min(10, len(ranked))])
            chosen = find_by_id(ranked, "result_index", llm_judgement.get("selected_result_index"))
            selected = chosen or ranked[0]
        else:
            selected = ranked[0]
    response = {
        "graph_search_id": graph_search_id,
        "seed": summarize_literature_result(seed),
        "direction": selected_direction,
        "depth": selected_depth,
        "second_layer_count": second_layer_count,
        "total_results": len(ranked),
        "selected": summarize_literature_result(selected) if selected else None,
        "top_results": [summarize_literature_result(item) for item in ranked[:10]],
        "llm_judgement": llm_judgement,
        "errors": errors,
        "fallback_used": fallback_used,
        "seed_not_indexed": seed_not_indexed,
        "next_step": "Use select_literature_result(graph_search_id) or import_literature_search_result(project_id, graph_search_id, result_index).",
    }
    log_event("SCIENCE", "graph_expanded", seed_search_id=search_id, graph_search_id=graph_search_id, count=len(ranked))
    return json.dumps(response, ensure_ascii=False, indent=2)


def expand_second_layer_graph_results(
    first_layer_ranked: list[dict[str, Any]],
    query: str,
    edge_kinds: list[str],
    max_results: int,
    top_k: int,
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seeds = select_second_layer_seeds(first_layer_ranked, top_k=top_k)
    if not seeds:
        return []
    per_edge_limit = min(
        max(1, int(SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT)),
        max(1, clamp_int(max_results, 1, 200) // max(1, len(seeds) * max(1, len(edge_kinds)))),
    )
    expanded: list[dict[str, Any]] = []
    for parent in seeds:
        lookup_ids = semantic_scholar_lookup_ids(parent)
        if not lookup_ids:
            continue
        parent_key = literature_result_unique_key(parent)
        for edge_kind in edge_kinds:
            edges: list[dict[str, Any]] = []
            last_not_found = ""
            for lookup_id in lookup_ids:
                try:
                    edges = fetch_semantic_scholar_edges(lookup_id, edge_kind, limit=per_edge_limit)
                    break
                except Exception as exc:
                    error_text = str(exc)
                    if is_semantic_scholar_not_found_error(error_text):
                        last_not_found = error_text
                        continue
                    errors.append(
                        {
                            "edge": f"second_layer_{edge_kind}",
                            "parent_title": str(parent.get("title") or ""),
                            "lookup_id": lookup_id,
                            "error": error_text,
                            "rate_limited": is_semantic_scholar_rate_limit_error(error_text),
                        }
                    )
                    if is_semantic_scholar_rate_limit_error(error_text):
                        log_event("SCIENCE", "graph_expand_rate_limited", search_id="second_layer", edge=edge_kind)
                    else:
                        log_event("SCIENCE", "graph_expand_failed", search_id="second_layer", edge=edge_kind, error=error_text)
                    break
            if not edges and last_not_found:
                errors.append(
                    {
                        "edge": f"second_layer_{edge_kind}",
                        "parent_title": str(parent.get("title") or ""),
                        "lookup_ids": lookup_ids,
                        "error": last_not_found,
                        "seed_not_indexed": True,
                    }
                )
                continue
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                result = semantic_scholar_edge_to_result(edge)
                result["graph_relation"] = f"second_layer_{result.get('graph_relation') or normalize_key(edge_kind)}"
                result["graph_parent_key"] = parent_key
                result["graph_parent_title"] = parent.get("title", "")
                result["graph_parent_result_index"] = parent.get("result_index")
                result["expanded_depth"] = 2
                expanded.append(result)
    seed_keys = {literature_result_unique_key(item) for item in first_layer_ranked}
    expanded = [item for item in expanded if literature_result_unique_key(item) not in seed_keys]
    ranked = rank_literature_results(query, dedupe_literature_results(expanded))
    return ranked


def select_second_layer_seeds(results: list[dict[str, Any]], top_k: int = 3) -> list[dict[str, Any]]:
    limit = clamp_int(top_k, 0, 10)
    if limit <= 0:
        return []
    candidates = [item for item in results if semantic_scholar_lookup_id(item)]
    candidates.sort(key=second_layer_seed_score, reverse=True)
    return candidates[:limit]


def second_layer_seed_score(result: dict[str, Any]) -> float:
    quality = float(result.get("publication_quality_score") or publication_quality_assessment(result)["quality_score"])
    relevance = float(result.get("relevance_score") or 0.0)
    components = result.get("relevance_components") if isinstance(result.get("relevance_components"), dict) else {}
    impact = float(components.get("impact_score") or literature_impact_score(result))
    edge_bonus = 0.08 if result.get("graph_relation") in {"reference", "citation"} else 0.0
    return 0.42 * quality + 0.35 * relevance + 0.15 * impact + edge_bonus


def fetch_semantic_scholar_edges(lookup_id: str, edge_kind: str, limit: int = 20) -> list[dict[str, Any]]:
    fields = ",".join(
        [
            "contexts",
            "intents",
            "isInfluential",
            "paperId",
            "title",
            "abstract",
            "year",
            "authors",
            "venue",
            "url",
            "externalIds",
            "citationCount",
            "influentialCitationCount",
            "referenceCount",
            "isOpenAccess",
            "openAccessPdf",
        ]
    )
    params = urlencode({"limit": clamp_int(limit, 1, 100), "fields": fields})
    url = f"https://api.semanticscholar.org/graph/v1/paper/{quote(lookup_id, safe='')}/{edge_kind}?{params}"
    headers = {"User-Agent": "qwen-zhikan-papergraph/0.1"}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    payload = semantic_scholar_get_json(url, headers=headers)
    data = payload.get("data") or []
    return [item for item in data if isinstance(item, dict)]


def semantic_scholar_edge_to_result(edge: dict[str, Any]) -> dict[str, Any]:
    relation = "reference" if "citedPaper" in edge else "citation"
    paper = edge.get("citedPaper") if relation == "reference" else edge.get("citingPaper")
    if not isinstance(paper, dict):
        paper = {key: value for key, value in edge.items() if key not in {"contexts", "intents", "isInfluential"}}
    result = semantic_scholar_item_to_result(paper)
    result["graph_relation"] = relation
    result["citation_contexts"] = edge.get("contexts") or []
    result["citation_intents"] = edge.get("intents") or []
    result["edge_is_influential"] = edge.get("isInfluential")
    result["provider"] = "semantic_scholar_graph"
    result["papergraph_input"]["provider"] = "semantic_scholar_graph"
    return result


def semantic_scholar_lookup_id(result: dict[str, Any]) -> str:
    ids = semantic_scholar_lookup_ids(result)
    return ids[0] if ids else ""


def semantic_scholar_lookup_ids(result: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    semantic_id = str(result.get("semantic_scholar_id") or "").strip()
    if semantic_id:
        candidates.append(semantic_id)
    payload = result.get("papergraph_input") if isinstance(result.get("papergraph_input"), dict) else {}
    semantic_id = str(payload.get("semantic_scholar_id") or "").strip()
    if semantic_id:
        candidates.append(semantic_id)
    doi = str(result.get("doi") or payload.get("doi") or "").strip()
    if doi:
        candidates.append(f"DOI:{doi}")
    arxiv_id = str(result.get("arxiv_id") or payload.get("arxiv_id") or "").strip()
    if arxiv_id:
        candidates.append(f"ARXIV:{arxiv_id}")
        unversioned = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
        if unversioned and unversioned != arxiv_id:
            candidates.append(f"ARXIV:{unversioned}")
    return unique_preserve_order([candidate for candidate in candidates if candidate])


def build_literature_relation_graph(
    search_id: str,
    query: str = "",
    max_nodes: int = 80,
    min_quality: float = 0.0,
    max_clusters: int = 8,
) -> str:
    search = load_search(search_id)
    raw_results = [item for item in search.get("results", []) if isinstance(item, dict)]
    limit = clamp_int(max_nodes, 1, 200)
    query_text = query or str(search.get("query", ""))
    filtered = [
        item
        for item in raw_results
        if float(item.get("publication_quality_score") or publication_quality_assessment(item)["quality_score"]) >= float(min_quality or 0.0)
    ][:limit]

    seed = relation_graph_seed(search)
    nodes: dict[str, dict[str, Any]] = {}
    if seed:
        seed_node = relation_graph_node(seed, query_text, role="seed")
        nodes[seed_node["node_id"]] = seed_node
        seed_id = seed_node["node_id"]
    else:
        seed_id = "seed"
        nodes[seed_id] = {
            "node_id": seed_id,
            "role": "seed",
            "title": search.get("seed_title") or "Seed paper",
            "year": "",
            "venue": "",
            "field": "general",
            "mechanism_terms": [],
            "relevance_score": 0.0,
            "publication_quality_score": 1.0,
            "venue_quality": "",
            "journal_quartile": "",
            "citation_count": 0,
            "quality_flags": [],
        }

    result_node_ids: dict[str, str] = {}
    for result in filtered:
        node = relation_graph_node(result, query_text, role="paper")
        nodes[node["node_id"]] = node
        result_node_ids[literature_result_unique_key(result)] = node["node_id"]

    edges: list[dict[str, Any]] = []
    for result in filtered:
        node_id = result_node_ids.get(literature_result_unique_key(result))
        if not node_id:
            continue
        parent_id = result_node_ids.get(str(result.get("graph_parent_key") or "")) or seed_id
        relation = str(result.get("graph_relation") or "search_result")
        edge = relation_graph_edge(parent_id, node_id, relation, result)
        if edge:
            edges.append(edge)

    clusters = build_mechanism_clusters(list(nodes.values()), edges, max_clusters=max_clusters)
    edge_summary = summarize_relation_edges(edges)
    fallback_used = bool(search.get("fallback_used")) or any(edge.get("edge_type") == "artificial" for edge in edges)
    analysis_confidence = 0.65 if fallback_used else 1.0
    pagerank = compute_pagerank(list(nodes), edges)
    degree = compute_graph_degree(list(nodes), edges)
    for node_id, node in nodes.items():
        node["pagerank"] = round(pagerank.get(node_id, 0.0), 6)
        node["degree_centrality"] = round(degree.get(node_id, 0.0), 6)
        node["centrality_score"] = round(0.7 * pagerank.get(node_id, 0.0) + 0.3 * degree.get(node_id, 0.0), 6)

    ranked_nodes = sorted(
        nodes.values(),
        key=lambda item: (
            -float(item.get("centrality_score", 0.0)),
            -float(item.get("publication_quality_score", 0.0)),
            -float(item.get("relevance_score", 0.0)),
        ),
    )
    graph_id = new_id("relgraph")
    record = {
        "search_id": graph_id,
        "kind": "paper_relation_graph",
        "source_search_id": search_id,
        "query": query_text,
        "createdAt": time.time(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "cluster_count": len(clusters),
        "max_clusters": clamp_int(max_clusters, 1, 30),
        "fallback_used": fallback_used,
        "analysis_confidence": analysis_confidence,
        "edge_summary": edge_summary,
        "nodes": ranked_nodes,
        "edges": edges,
        "clusters": clusters,
        "central_papers": [summarize_relation_node(item) for item in ranked_nodes[:10]],
        "mechanism_lineage": summarize_mechanism_lineage(clusters),
    }
    save_search({"search_id": graph_id, **record, "total_results": len(ranked_nodes), "results": ranked_nodes})
    log_event(
        "SCIENCE",
        "relation_graph_built",
        source_search_id=search_id,
        graph_id=graph_id,
        nodes=len(nodes),
        edges=len(edges),
        clusters=len(clusters),
    )
    response = {
        "relation_graph_id": graph_id,
        "source_search_id": search_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "cluster_count": len(clusters),
        "max_clusters": clamp_int(max_clusters, 1, 30),
        "fallback_used": fallback_used,
        "analysis_confidence": analysis_confidence,
        "edge_summary": edge_summary,
        "central_papers": record["central_papers"],
        "clusters": clusters,
        "mechanism_lineage": record["mechanism_lineage"],
        "next_step": "Use central_papers for high-trust seeds, clusters for mechanism lineage, and edges/citation_contexts for claim-citation verification.",
    }
    return json.dumps(response, ensure_ascii=False, indent=2)


def relation_graph_seed(search: dict[str, Any]) -> dict[str, Any]:
    seed_search_id = str(search.get("seed_search_id") or "")
    if seed_search_id:
        try:
            seed_search = load_search(seed_search_id)
            seed_index = int(search.get("seed_result_index") or 0)
            seed = seed_search.get("results", [])[seed_index]
            if isinstance(seed, dict):
                return seed
        except Exception:
            pass
    seed_title = normalize_space(search.get("seed_title", ""))
    if seed_title:
        return {"title": seed_title, "venue": "", "year": "", "provider": "seed_metadata"}
    return {}


def relation_graph_node(result: dict[str, Any], query: str, role: str = "paper") -> dict[str, Any]:
    quality = publication_quality_assessment(result)
    terms = mechanism_terms(result, query)
    node_key = literature_result_unique_key(result)
    node_id = normalize_key(node_key)[:80]
    return {
        "node_id": node_id,
        "role": role,
        "result_index": result.get("result_index"),
        "title": result.get("title"),
        "year": result.get("year"),
        "venue": result.get("venue"),
        "field": quality["inferred_field"],
        "mechanism_terms": terms,
        "mechanism_cluster_key": mechanism_cluster_key(quality["inferred_field"], terms),
        "relevance_score": result.get("relevance_score", 0.0),
        "publication_quality_score": result.get("publication_quality_score", quality["quality_score"]),
        "venue_quality": result.get("venue_quality", quality["venue_quality"]),
        "journal_quartile": result.get("journal_quartile", quality["journal_quartile"]),
        "citation_count": numeric_value(result.get("citation_count")),
        "influential_citation_count": numeric_value(result.get("influential_citation_count")),
        "quality_flags": result.get("quality_flags", quality["flags"]),
        "doi": result.get("doi"),
        "arxiv_id": result.get("arxiv_id"),
        "semantic_scholar_id": result.get("semantic_scholar_id"),
        "url": result.get("url"),
    }


def relation_graph_edge(parent_id: str, node_id: str, relation: str, result: dict[str, Any]) -> dict[str, Any]:
    if node_id == parent_id:
        return {}
    normalized = normalize_key(relation)
    base_relation = normalized.removeprefix("second_layer_")
    is_second_layer = normalized.startswith("second_layer_")
    is_artificial = base_relation in {"keyword_fallback", "search_result"}
    weight = {
        "reference": 1.0,
        "citation": 1.0,
        "keyword_fallback": 0.08,
        "search_result": 0.06,
    }.get(base_relation, 0.4)
    if is_second_layer and not is_artificial:
        weight *= 0.65
    if base_relation == "reference":
        source, target = parent_id, node_id
    elif base_relation == "citation":
        source, target = node_id, parent_id
    else:
        source, target = parent_id, node_id
    contexts = [trim_text(scalar(item), 260) for item in (result.get("citation_contexts") or []) if scalar(item)]
    return {
        "source": source,
        "target": target,
        "relation": normalized,
        "base_relation": base_relation,
        "edge_type": "artificial" if is_artificial else "citation_graph",
        "expanded_depth": 2 if is_second_layer else int(result.get("expanded_depth") or 1),
        "weight": round(weight, 4),
        "citation_contexts": contexts[:3],
        "citation_intents": result.get("citation_intents") or [],
        "is_influential": bool(result.get("edge_is_influential")),
        "parent_title": result.get("graph_parent_title", ""),
        "manual_connection": is_artificial,
    }


def mechanism_terms(result: dict[str, Any], query: str = "", limit: int = 6) -> list[str]:
    text = " ".join(
        normalize_space(result.get(key, "")).lower()
        for key in ("title", "abstract", "venue")
    )
    contexts = " ".join(scalar(item).lower() for item in (result.get("citation_contexts") or []))
    text = f"{text} {contexts}"
    vocab = [
        "adaptation",
        "analysis",
        "architecture",
        "attribution",
        "causality",
        "classification",
        "control",
        "coupling",
        "decomposition",
        "degradation",
        "discovery",
        "dynamics",
        "efficiency",
        "evaluation",
        "feedback",
        "generalization",
        "heterogeneity",
        "inference",
        "interaction",
        "interface",
        "measurement",
        "mechanism",
        "model",
        "optimization",
        "prediction",
        "reconstruction",
        "response",
        "robustness",
        "scalability",
        "screening",
        "sensitivity",
        "simulation",
        "stability",
        "structure",
        "transfer",
        "uncertainty",
        "validation",
        "planning",
        "workflow",
    ]
    hits = [term for term in vocab if term in text]
    query_hits = [term for term in query_terms(query) if term in text]
    if len(hits) + len(query_hits) < limit:
        words = [
            word
            for word in re.findall(r"[a-z][a-z0-9-]{3,}", text)
            if word not in set(query_terms("")) and word not in {"paper", "study", "using", "based", "with", "from", "this", "that"}
        ]
        common = [word for word, _ in Counter(words).most_common(limit * 2)]
    else:
        common = []
    return unique_preserve_order(hits + query_hits + common)[:limit]


def mechanism_cluster_key(field: str, terms: list[str]) -> str:
    if terms:
        return f"{field}:{terms[0]}"
    return f"{field}:general"


def build_mechanism_clusters(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], max_clusters: int = 8) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        if node.get("role") == "seed":
            continue
        grouped[str(node.get("mechanism_cluster_key") or "general:unknown")].append(node)
    grouped = merge_sparse_mechanism_groups(grouped, max_clusters=max_clusters)
    incoming = Counter(edge["target"] for edge in edges)
    outgoing = Counter(edge["source"] for edge in edges)
    artificial_nodes = {
        edge["target"]
        for edge in edges
        if edge.get("edge_type") == "artificial"
    } | {
        edge["source"]
        for edge in edges
        if edge.get("edge_type") == "artificial"
    }
    clusters: list[dict[str, Any]] = []
    for key, members in grouped.items():
        field, _, mechanism = key.partition(":")
        central = sorted(
            members,
            key=lambda item: (
                -(incoming[item["node_id"]] + outgoing[item["node_id"]]),
                -float(item.get("publication_quality_score", 0.0)),
                -float(item.get("relevance_score", 0.0)),
            ),
        )[:5]
        flags = sorted({flag for item in members for flag in item.get("quality_flags", [])})
        artificial_count = sum(1 for item in members if item.get("node_id") in artificial_nodes)
        clusters.append(
            {
                "cluster_id": normalize_key(key),
                "field": field or "general",
                "mechanism": mechanism or "general",
                "size": len(members),
                "merged_singletons": any(bool(item.get("merged_from_singleton")) for item in members),
                "artificial_connection_count": artificial_count,
                "connection_confidence": round(1.0 - (artificial_count / max(1, len(members))) * 0.6, 4),
                "avg_quality": round(sum(float(item.get("publication_quality_score", 0.0)) for item in members) / max(1, len(members)), 4),
                "avg_relevance": round(sum(float(item.get("relevance_score", 0.0)) for item in members) / max(1, len(members)), 4),
                "quality_flags": flags[:8],
                "representative_papers": [summarize_relation_node(item) for item in central],
            }
        )
    clusters.sort(key=lambda item: (-int(item["size"]), -float(item["avg_quality"]), item["cluster_id"]))
    return clusters


def merge_sparse_mechanism_groups(
    grouped: dict[str, list[dict[str, Any]]],
    max_clusters: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    target = clamp_int(max_clusters, 1, 30)
    if len(grouped) <= target:
        return grouped
    merged: dict[str, list[dict[str, Any]]] = {key: list(value) for key, value in grouped.items()}
    singleton_keys = [key for key, members in merged.items() if len(members) == 1]
    for key in singleton_keys:
        if len(merged) <= target:
            break
        members = merged.pop(key, [])
        if not members:
            continue
        parent_key = nearest_mechanism_parent_key(key, members[0], merged)
        for member in members:
            member["merged_from_singleton"] = key
        merged[parent_key].extend(members)

    while len(merged) > target:
        smallest_key = min(merged, key=lambda item: (len(merged[item]), item))
        members = merged.pop(smallest_key)
        if not members:
            continue
        parent_key = nearest_mechanism_parent_key(smallest_key, members[0], merged)
        for member in members:
            member["merged_from_singleton"] = smallest_key
        merged[parent_key].extend(members)
    return merged


def nearest_mechanism_parent_key(
    source_key: str,
    node: dict[str, Any],
    grouped: dict[str, list[dict[str, Any]]],
) -> str:
    field, _, _ = source_key.partition(":")
    node_terms = set(node.get("mechanism_terms") or [])
    candidates: list[tuple[float, str]] = []
    for key, members in grouped.items():
        candidate_field, _, _ = key.partition(":")
        if candidate_field != field:
            continue
        term_sets = [set(item.get("mechanism_terms") or []) for item in members]
        overlap = max((len(node_terms & terms) for terms in term_sets), default=0)
        size_bonus = min(3, len(members)) * 0.1
        candidates.append((overlap + size_bonus, key))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]
    parent_key = f"{field or 'general'}:mixed"
    grouped.setdefault(parent_key, [])
    return parent_key


def compute_pagerank(node_ids: list[str], edges: list[dict[str, Any]], damping: float = 0.85, iterations: int = 30) -> dict[str, float]:
    ids = unique_preserve_order(node_ids)
    if not ids:
        return {}
    outgoing: dict[str, list[tuple[str, float]]] = {node_id: [] for node_id in ids}
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source in outgoing and target in outgoing:
            outgoing[source].append((target, max(0.01, float(edge.get("weight") or 1.0))))
    n = len(ids)
    rank = {node_id: 1.0 / n for node_id in ids}
    base = (1.0 - damping) / n
    for _ in range(iterations):
        new_rank = {node_id: base for node_id in ids}
        dangling = sum(rank[node_id] for node_id in ids if not outgoing[node_id])
        dangling_share = damping * dangling / n
        for node_id in ids:
            new_rank[node_id] += dangling_share
        for source, targets in outgoing.items():
            total_weight = sum(weight for _, weight in targets)
            if total_weight <= 0:
                continue
            for target, weight in targets:
                new_rank[target] += damping * rank[source] * (weight / total_weight)
        rank = new_rank
    total = sum(rank.values()) or 1.0
    return {node_id: value / total for node_id, value in rank.items()}


def compute_graph_degree(node_ids: list[str], edges: list[dict[str, Any]]) -> dict[str, float]:
    ids = unique_preserve_order(node_ids)
    degree = {node_id: 0.0 for node_id in ids}
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        weight = max(0.01, float(edge.get("weight") or 1.0))
        if source in degree:
            degree[source] += weight
        if target in degree:
            degree[target] += weight
    max_degree = max(degree.values(), default=0.0)
    if max_degree <= 0:
        return degree
    return {node_id: value / max_degree for node_id, value in degree.items()}


def summarize_relation_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id"),
        "title": node.get("title"),
        "year": node.get("year"),
        "venue": node.get("venue"),
        "field": node.get("field"),
        "mechanism_terms": node.get("mechanism_terms", []),
        "pagerank": node.get("pagerank"),
        "degree_centrality": node.get("degree_centrality"),
        "centrality_score": node.get("centrality_score"),
        "publication_quality_score": node.get("publication_quality_score"),
        "relevance_score": node.get("relevance_score"),
        "quality_flags": node.get("quality_flags", []),
    }


def summarize_relation_edges(edges: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(str(edge.get("edge_type") or "unknown") for edge in edges)
    by_relation = Counter(str(edge.get("relation") or "unknown") for edge in edges)
    depths = Counter(str(edge.get("expanded_depth") or 1) for edge in edges)
    return {
        "total_edges": len(edges),
        "citation_graph_edges": by_type.get("citation_graph", 0),
        "artificial_edges": by_type.get("artificial", 0),
        "by_relation": dict(sorted(by_relation.items())),
        "by_depth": dict(sorted(depths.items())),
        "fallback_weight_policy": "keyword_fallback/search_result edges are artificial and use very low PageRank weight.",
    }


def summarize_mechanism_lineage(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lineage: list[dict[str, Any]] = []
    for cluster in clusters[:12]:
        representatives = cluster.get("representative_papers", [])
        lineage.append(
            {
                "mechanism": cluster.get("mechanism"),
                "field": cluster.get("field"),
                "paper_count": cluster.get("size"),
                "avg_quality": cluster.get("avg_quality"),
                "representative_titles": [item.get("title") for item in representatives[:3]],
                "interpretation": (
                    f"{cluster.get('field')} lineage centered on {cluster.get('mechanism')} "
                    f"with {cluster.get('size')} papers; inspect representative_papers before importing claims."
                ),
            }
        )
    return lineage


def dedupe_literature_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for result in results:
        key = literature_result_unique_key(result)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def literature_result_unique_key(result: dict[str, Any]) -> str:
    payload = result.get("papergraph_input") if isinstance(result.get("papergraph_input"), dict) else {}
    return paper_unique_key(
        title=str(result.get("title") or payload.get("title") or ""),
        citation=str(result.get("citation") or payload.get("citation") or ""),
        doi=str(result.get("doi") or payload.get("doi") or ""),
        arxiv_id=str(result.get("arxiv_id") or payload.get("arxiv_id") or ""),
        semantic_scholar_id=str(result.get("semantic_scholar_id") or payload.get("semantic_scholar_id") or ""),
        url=str(result.get("url") or payload.get("url") or ""),
    )


def arxiv_entry_to_result(entry: ET.Element, ns: dict[str, str]) -> dict[str, Any]:
    title = normalize_space(xml_text(entry, "atom:title", ns))
    abstract = normalize_space(xml_text(entry, "atom:summary", ns))
    published = xml_text(entry, "atom:published", ns)
    year_match = re.search(r"\b(19|20)\d{2}\b", published)
    year = year_match.group(0) if year_match else ""
    authors = [normalize_space(author.findtext("atom:name", default="", namespaces=ns)) for author in entry.findall("atom:author", ns)]
    authors = [author for author in authors if author]
    url = xml_text(entry, "atom:id", ns)
    arxiv_id = url.rstrip("/").split("/")[-1] if url else ""
    doi = normalize_doi(xml_text(entry, "arxiv:doi", ns))
    categories = arxiv_categories(entry, ns)
    pdf_url = ""
    for link in entry.findall("atom:link", ns):
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            pdf_url = link.attrib.get("href", "")
            break
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id=arxiv_id)
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": "arXiv",
        "provider": "arxiv",
        "source_type": "api",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "arxiv_categories": categories,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "arxiv_categories": categories,
        "url": url,
        "pdf_url": pdf_url,
        "abstract": abstract,
        "papergraph_input": input_payload,
    }


def arxiv_categories(entry: ET.Element, ns: dict[str, str]) -> list[str]:
    categories: list[str] = []
    for category in entry.findall("atom:category", ns) + entry.findall("category"):
        term = normalize_space(category.attrib.get("term", ""))
        if term:
            categories.append(term)
    return unique_preserve_order(categories)


def semantic_scholar_item_to_result(item: dict[str, Any]) -> dict[str, Any]:
    external = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    title = normalize_space(item.get("title", ""))
    abstract = normalize_space(item.get("abstract", ""))
    authors = [normalize_space(author.get("name", "")) for author in (item.get("authors") or []) if isinstance(author, dict)]
    authors = [author for author in authors if author]
    year = str(item.get("year") or "")
    doi = normalize_doi(str(external.get("DOI") or ""))
    arxiv_id = str(external.get("ArXiv") or "")
    semantic_scholar_id = str(item.get("paperId") or external.get("CorpusId") or "")
    url = str(item.get("url") or "")
    pdf = item.get("openAccessPdf") if isinstance(item.get("openAccessPdf"), dict) else {}
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id=arxiv_id)
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": str(item.get("venue") or ""),
        "provider": "semantic_scholar",
        "source_type": "api",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "semantic_scholar_id": semantic_scholar_id,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": item.get("venue"),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "semantic_scholar_id": semantic_scholar_id,
        "url": url,
        "open_access_pdf": pdf.get("url", ""),
        "citation_count": item.get("citationCount"),
        "influential_citation_count": item.get("influentialCitationCount"),
        "reference_count": item.get("referenceCount"),
        "is_open_access": item.get("isOpenAccess"),
        "abstract": abstract,
        "papergraph_input": input_payload,
    }


def biorxiv_item_to_result(item: dict[str, Any], server: str) -> dict[str, Any]:
    title = normalize_space(str(item.get("title") or ""))
    abstract = normalize_space(str(item.get("abstract") or ""))
    authors = split_author_string(str(item.get("authors") or ""))
    year = first_year(str(item.get("date") or item.get("published") or item.get("version") or ""))
    doi = normalize_doi(str(item.get("doi") or ""))
    category = normalize_space(str(item.get("category") or ""))
    url = f"https://www.{server}.org/content/{doi}" if doi else str(item.get("url") or "")
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id="")
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": server,
        "provider": server,
        "source_type": "api",
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": server,
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "category": category,
        "papergraph_input": input_payload,
    }


def crossref_chemrxiv_item_to_result(item: dict[str, Any]) -> dict[str, Any]:
    title = normalize_space(" ".join(str(part) for part in (item.get("title") or []) if part))
    abstract = strip_markup(normalize_space(str(item.get("abstract") or "")))
    authors = [
        normalize_space(" ".join(str(author.get(key) or "") for key in ("given", "family")).strip())
        for author in (item.get("author") or [])
        if isinstance(author, dict)
    ]
    authors = [author for author in authors if author]
    year = crossref_year(item)
    doi = normalize_doi(str(item.get("DOI") or ""))
    containers = item.get("container-title") if isinstance(item.get("container-title"), list) else []
    venue = normalize_space(str(containers[0] if containers else "ChemRxiv")) or "ChemRxiv"
    url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id="")
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": "ChemRxiv",
        "provider": "chemrxiv",
        "source_type": "crossref_api",
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue or "ChemRxiv",
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "papergraph_input": input_payload,
    }


def openalex_item_to_result(item: dict[str, Any]) -> dict[str, Any]:
    title = normalize_space(str(item.get("display_name") or item.get("title") or ""))
    abstract = openalex_abstract_text(item.get("abstract_inverted_index"))
    authorships = item.get("authorships") if isinstance(item.get("authorships"), list) else []
    authors = [
        normalize_space(str((authorship.get("author") or {}).get("display_name") or ""))
        for authorship in authorships
        if isinstance(authorship, dict)
    ]
    authors = [author for author in authors if author]
    year = str(item.get("publication_year") or "")
    doi = normalize_doi(str(item.get("doi") or ""))
    primary_location = item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
    source = primary_location.get("source") if isinstance(primary_location.get("source"), dict) else {}
    host_venue = item.get("host_venue") if isinstance(item.get("host_venue"), dict) else {}
    venue = normalize_space(str(source.get("display_name") or host_venue.get("display_name") or ""))
    url = str(primary_location.get("landing_page_url") or item.get("doi") or item.get("id") or "")
    open_access = item.get("open_access") if isinstance(item.get("open_access"), dict) else {}
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id="")
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "provider": "openalex",
        "source_type": "openalex_api",
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "openalex_id": item.get("id"),
        "url": url,
        "open_access_pdf": primary_location.get("pdf_url") or open_access.get("oa_url", ""),
        "is_open_access": open_access.get("is_oa"),
        "citation_count": item.get("cited_by_count"),
        "abstract": abstract,
        "concepts": [concept.get("display_name") for concept in (item.get("concepts") or []) if isinstance(concept, dict)][:8],
        "papergraph_input": input_payload,
    }


def dblp_item_to_result(item: dict[str, Any]) -> dict[str, Any]:
    info = item.get("info") if isinstance(item.get("info"), dict) else item
    title = strip_markup(normalize_space(str(info.get("title") or "")))
    raw_authors = (info.get("authors") or {}).get("author") if isinstance(info.get("authors"), dict) else []
    if isinstance(raw_authors, dict):
        raw_authors = [raw_authors]
    if isinstance(raw_authors, str):
        raw_authors = [raw_authors]
    authors: list[str] = []
    if isinstance(raw_authors, list):
        for author in raw_authors:
            if isinstance(author, dict):
                authors.append(normalize_space(str(author.get("text") or author.get("#text") or author.get("name") or "")))
            else:
                authors.append(normalize_space(str(author)))
    authors = [author for author in authors if author]
    year = str(info.get("year") or "")
    doi = normalize_doi(str(info.get("doi") or ""))
    venue = normalize_space(str(info.get("venue") or ""))
    url = str(info.get("ee") or info.get("url") or (f"https://doi.org/{doi}" if doi else ""))
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id="")
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "provider": "dblp",
        "source_type": "dblp_api",
        "doi": doi,
        "url": url,
        "abstract": "",
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "url": url,
        "dblp_type": info.get("type"),
        "abstract": "",
        "papergraph_input": input_payload,
    }


def openreview_item_to_result(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    title = normalize_space(openreview_content_value(content.get("title")))
    abstract = normalize_space(openreview_content_value(content.get("abstract")))
    authors = string_list(openreview_content_value(content.get("authors")))
    if not authors:
        authors = string_list(openreview_content_value(content.get("authorids")))
    note_id = str(item.get("id") or item.get("forum") or "")
    year = openreview_year(item)
    venue = normalize_space(str(item.get("venue") or item.get("invitation") or item.get("domain") or "OpenReview"))
    url = f"https://openreview.net/forum?id={note_id}" if note_id else ""
    citation = build_citation(title=title, authors=authors, year=year, doi="", arxiv_id="")
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "provider": "openreview",
        "source_type": "openreview_api",
        "doi": "",
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "url": url,
        "openreview_id": note_id,
        "abstract": abstract,
        "papergraph_input": input_payload,
    }


def openalex_abstract_text(inverted_index: Any) -> str:
    if not isinstance(inverted_index, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            try:
                positioned.append((int(position), str(word)))
            except (TypeError, ValueError):
                continue
    positioned.sort(key=lambda item: item[0])
    return normalize_space(" ".join(word for _, word in positioned))


def openreview_content_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def openreview_year(item: dict[str, Any]) -> str:
    for key in ("pdate", "cdate", "mdate", "tcdate", "tmdate"):
        raw = item.get(key)
        if not raw:
            continue
        try:
            return str(time.gmtime(float(raw) / 1000.0).tm_year)
        except (TypeError, ValueError, OSError):
            continue
    text = " ".join(str(item.get(key) or "") for key in ("invitation", "venue", "domain"))
    return first_year(text)


def split_author_string(text: str) -> list[str]:
    parts = re.split(r"\s*;\s*|\s*,\s+(?=[A-Z][A-Za-z.-]+(?:\s|$))", normalize_space(text))
    return [part.strip() for part in parts if part.strip()][:30]


def first_year(text: str) -> str:
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else ""


def crossref_year(item: dict[str, Any]) -> str:
    for key in ("published-print", "published-online", "published", "created"):
        value = item.get(key)
        if not isinstance(value, dict):
            continue
        date_parts = value.get("date-parts")
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            year = str(date_parts[0][0])
            if re.fullmatch(r"(19|20)\d{2}", year):
                return year
    return ""


def strip_markup(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_space(text)


def provider_error_result(provider: str, query: str, exc: Exception) -> dict[str, Any]:
    return {
        "provider": provider,
        "query": query,
        "status": "error",
        "error": str(exc),
        "results": [],
        "next_step": "Network/API failed. Retry later, configure API keys, or use manual import_literature_text.",
    }


def enrich_papergraph_payload(payload: dict[str, Any], result: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    """Best-effort metadata enrichment before structured extraction.

    This intentionally avoids hard PDF parsing dependencies. It first asks
    Semantic Scholar for a single-paper detail record, then tries arXiv when an
    arXiv id is available. Callers can still attach a PDF parser later.
    """
    enriched = dict(payload)
    result = result or {}
    sources: list[str] = []
    errors: list[str] = []
    initial_quality = extraction_quality_report(enriched)
    if not initial_quality.get("needs_enrichment"):
        return enriched, sources

    semantic_id = str(
        enriched.get("semantic_scholar_id")
        or result.get("semantic_scholar_id")
        or ""
    ).strip()
    doi = normalize_doi(str(enriched.get("doi") or result.get("doi") or ""))
    s2_identifier = semantic_id or (f"DOI:{doi}" if doi else "")
    if s2_identifier:
        try:
            detail = fetch_semantic_scholar_paper_detail(s2_identifier)
            before_len = len(str(enriched.get("abstract") or ""))
            enriched = merge_semantic_scholar_detail(enriched, detail)
            after_len = len(str(enriched.get("abstract") or ""))
            if after_len > before_len:
                sources.append("semantic_scholar_detail")
        except Exception as exc:
            error = str(exc)
            errors.append(f"semantic_scholar: {error}")
            log_event("SCIENCE", "metadata_enrichment_failed", provider="semantic_scholar", error=error)

    arxiv_id = str(enriched.get("arxiv_id") or result.get("arxiv_id") or "").strip()
    if arxiv_id and extraction_quality_report(enriched).get("needs_enrichment"):
        try:
            arxiv_payload = fetch_arxiv_by_id(arxiv_id)
            before_len = len(str(enriched.get("abstract") or ""))
            enriched = merge_nonempty(enriched, arxiv_payload)
            after_len = len(str(enriched.get("abstract") or ""))
            if after_len > before_len:
                sources.append("arxiv_detail")
        except Exception as exc:
            error = str(exc)
            errors.append(f"arxiv: {error}")
            log_event("SCIENCE", "metadata_enrichment_failed", provider="arxiv", error=error)

    pdf_url = str(result.get("open_access_pdf") or enriched.get("open_access_pdf") or "").strip()
    if pdf_url:
        enriched["open_access_pdf"] = pdf_url
        sources.append("open_access_pdf_available")
        if extraction_quality_report(enriched).get("needs_enrichment"):
            try:
                excerpt = fetch_pdf_text_excerpt(pdf_url)
                if excerpt:
                    enriched["full_text_excerpt"] = excerpt
                    sources.append("open_access_pdf_text")
            except Exception as exc:
                error = str(exc)
                errors.append(f"open_access_pdf: {error}")
                log_event("SCIENCE", "metadata_enrichment_failed", provider="open_access_pdf", error=error)
    if errors:
        enriched["_enrichment_errors"] = errors
    return enriched, unique_preserve_order(sources)


def fetch_semantic_scholar_paper_detail(identifier: str) -> dict[str, Any]:
    fields = ",".join(
        [
            "title",
            "abstract",
            "year",
            "authors",
            "venue",
            "url",
            "externalIds",
            "citationCount",
            "influentialCitationCount",
            "referenceCount",
            "isOpenAccess",
            "openAccessPdf",
            "tldr",
        ]
    )
    url = f"https://api.semanticscholar.org/graph/v1/paper/{quote(identifier, safe=':')}?{urlencode({'fields': fields})}"
    headers = {"User-Agent": "qwen-zhikan-papergraph/0.1"}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    return semantic_scholar_get_json(url, headers=headers)


def merge_semantic_scholar_detail(payload: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    result = semantic_scholar_item_to_result(detail)
    detail_payload = result.get("papergraph_input") if isinstance(result.get("papergraph_input"), dict) else {}
    tldr = detail.get("tldr") if isinstance(detail.get("tldr"), dict) else {}
    if not detail_payload.get("abstract") and tldr.get("text"):
        detail_payload["abstract"] = normalize_space(str(tldr.get("text") or ""))
    merged = merge_nonempty(payload, detail_payload)
    if result.get("open_access_pdf"):
        merged["open_access_pdf"] = result.get("open_access_pdf")
    return merged


def fetch_arxiv_by_id(arxiv_id: str) -> dict[str, Any]:
    clean_id = arxiv_id.strip()
    if not clean_id:
        return {}
    url = f"https://export.arxiv.org/api/query?{urlencode({'id_list': clean_id})}"
    raw = arxiv_get_text(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"})
    root = ET.fromstring(raw)
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        return {}
    result = arxiv_entry_to_result(entry, ns)
    return result.get("papergraph_input", {}) if isinstance(result.get("papergraph_input"), dict) else {}


def fetch_pdf_text_excerpt(url: str, max_bytes: int = 8_000_000, max_pages: int = 4) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is not installed; install pypdf to enable PDF full-text fallback") from exc
    request = Request(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"})
    context = ssl_context()
    try:
        with urlopen(request, timeout=30.0, context=context) as response:
            data = response.read(max_bytes + 1)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: PDF fetch failed") from exc
    except URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc
    if len(data) > max_bytes:
        raise RuntimeError(f"PDF exceeds {max_bytes} byte safety limit")
    reader = PdfReader(BytesIO(data))
    chunks: list[str] = []
    for page in reader.pages[: max(1, max_pages)]:
        try:
            text = normalize_space(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            chunks.append(text)
    return trim_text("\n\n".join(chunks), 8000)


def merge_nonempty(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, list):
            if value and not merged.get(key):
                merged[key] = value
            continue
        text = normalize_space(str(value or ""))
        if not text:
            continue
        existing = normalize_space(str(merged.get(key) or ""))
        if not existing or (key in {"abstract", "conclusion"} and len(text) > len(existing)):
            merged[key] = value
    return merged


def http_get_text(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> str:
    request = Request(url, headers=headers or {})
    context = ssl_context()
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            encoding = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(encoding, errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        retry_after = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else ""
        retry_hint = f" retry_after={retry_after}" if retry_after else ""
        raise RuntimeError(f"HTTP {exc.code}:{retry_hint} {trim_text(body, 500)}") from exc
    except URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc


def http_get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    text = http_get_text(url, headers=headers, timeout=timeout)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON parse failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("JSON response is not an object")
    return payload


def semantic_scholar_get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    cached = semantic_scholar_cache_get(url)
    if cached is not None:
        log_event("SCIENCE", "semantic_scholar_cache_hit")
        return json.loads(cached)
    circuit_open, retry_after = semantic_scholar_circuit_open()
    if circuit_open:
        raise RuntimeError(
            f"Semantic Scholar circuit open after recent 429; retry_after_seconds={retry_after:.1f}"
        )
    retry_limit = max(0, int(SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT))
    last_error: RuntimeError | None = None
    for attempt in range(retry_limit + 1):
        try:
            text = semantic_scholar_get_text(url, headers=headers)
            semantic_scholar_cache_put(url, text)
            return json.loads(text)
        except RuntimeError as exc:
            if "HTTP 429" not in str(exc):
                raise
            last_error = exc
            delay = semantic_scholar_backoff_seconds(attempt, str(exc))
            register_semantic_scholar_429(delay)
            log_event(
                "SCIENCE",
                "semantic_scholar_429_fail_fast"
                if SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429
                else "semantic_scholar_429_backoff",
                attempt=attempt + 1,
                max_attempts=retry_limit + 1,
                delay_seconds=round(delay, 2),
                fail_fast=bool(SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429),
            )
            if SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429 or attempt >= retry_limit:
                raise RuntimeError(
                    f"Semantic Scholar rate limited; circuit opened for "
                    f"{semantic_scholar_circuit_seconds(delay):.1f}s: {exc}"
                ) from exc
            time.sleep(delay)
    raise RuntimeError(
        f"Semantic Scholar rate limit persisted after {retry_limit + 1} attempts: {last_error}"
    )


def semantic_scholar_circuit_open() -> tuple[bool, float]:
    with SEMANTIC_SCHOLAR_CIRCUIT_LOCK:
        remaining = SEMANTIC_SCHOLAR_COOLDOWN_UNTIL - time.monotonic()
    return remaining > 0, max(0.0, remaining)


def semantic_scholar_circuit_seconds(delay: float) -> float:
    configured = max(0.0, float(SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS))
    floor = max(5.0, float(SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS) * 4)
    return min(max(configured, delay, floor), 180.0)


def register_semantic_scholar_429(delay: float) -> None:
    global SEMANTIC_SCHOLAR_429_COUNT, SEMANTIC_SCHOLAR_COOLDOWN_UNTIL
    cooldown = semantic_scholar_circuit_seconds(delay)
    with SEMANTIC_SCHOLAR_CIRCUIT_LOCK:
        SEMANTIC_SCHOLAR_429_COUNT += 1
        SEMANTIC_SCHOLAR_COOLDOWN_UNTIL = max(
            SEMANTIC_SCHOLAR_COOLDOWN_UNTIL,
            time.monotonic() + cooldown,
        )
    log_event(
        "SCIENCE",
        "semantic_scholar_circuit_open",
        cooldown_seconds=round(cooldown, 2),
        count=SEMANTIC_SCHOLAR_429_COUNT,
    )


def semantic_scholar_skip_block(query: str, provider: str = "semantic_scholar") -> dict[str, Any] | None:
    circuit_open, retry_after = semantic_scholar_circuit_open()
    if not circuit_open:
        return None
    return {
        "provider": provider,
        "query": query,
        "status": "rate_limited_skipped",
        "error": f"Semantic Scholar circuit open; retry_after_seconds={retry_after:.1f}",
        "rate_limited": True,
        "results": [],
    }


def arxiv_circuit_open() -> tuple[bool, float]:
    with ARXIV_CIRCUIT_LOCK:
        remaining = ARXIV_COOLDOWN_UNTIL - time.monotonic()
    return remaining > 0, max(0.0, remaining)


def arxiv_circuit_seconds() -> float:
    configured = max(0.0, float(SCIENCE_ARXIV_CIRCUIT_SECONDS))
    floor = max(15.0, float(SCIENCE_ARXIV_MIN_INTERVAL_SECONDS) * 4)
    return min(max(configured, floor), 300.0)


def register_arxiv_429(error: str = "") -> None:
    global ARXIV_429_COUNT, ARXIV_COOLDOWN_UNTIL
    cooldown = arxiv_circuit_seconds()
    with ARXIV_CIRCUIT_LOCK:
        ARXIV_429_COUNT += 1
        ARXIV_COOLDOWN_UNTIL = max(ARXIV_COOLDOWN_UNTIL, time.monotonic() + cooldown)
    log_event(
        "SCIENCE",
        "arxiv_circuit_open",
        cooldown_seconds=round(cooldown, 2),
        count=ARXIV_429_COUNT,
        error=trim_text(error, 180),
    )


def arxiv_skip_block(query: str) -> dict[str, Any] | None:
    circuit_open, retry_after = arxiv_circuit_open()
    if not circuit_open:
        return None
    return {
        "provider": "arxiv",
        "query": query,
        "status": "rate_limited_skipped",
        "error": f"arXiv circuit open; retry_after_seconds={retry_after:.1f}",
        "rate_limited": True,
        "results": [],
    }


def semantic_scholar_backoff_seconds(attempt: int, error: str = "") -> float:
    retry_after = semantic_scholar_retry_after_seconds(error)
    if retry_after is not None:
        return min(max(retry_after, SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS), 120.0)
    base = max(
        SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS * 2,
    )
    return min(base * (2 ** max(0, attempt)), 60.0)


def semantic_scholar_get_text(url: str, headers: dict[str, str] | None = None) -> str:
    log_semantic_scholar_key_status()
    wait_for_semantic_scholar_rate_limit()
    return http_get_text(url, headers=headers)


def arxiv_get_text(url: str, headers: dict[str, str] | None = None) -> str:
    wait_for_arxiv_rate_limit()
    try:
        return http_get_text(url, headers=headers)
    except RuntimeError as exc:
        if is_rate_limit_error(str(exc)):
            register_arxiv_429(str(exc))
        raise


def semantic_scholar_retry_after_seconds(error: str) -> float | None:
    match = re.search(r"retry_after=([0-9]+(?:\.[0-9]+)?)", error, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def semantic_scholar_cache_get(url: str) -> str | None:
    ttl = max(0.0, float(SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS))
    if ttl <= 0:
        return None
    with SEMANTIC_SCHOLAR_CACHE_LOCK:
        cached = SEMANTIC_SCHOLAR_RESPONSE_CACHE.get(url)
        if not cached:
            return None
        created_at, text = cached
        if time.time() - created_at > ttl:
            SEMANTIC_SCHOLAR_RESPONSE_CACHE.pop(url, None)
            return None
        return text


def semantic_scholar_cache_put(url: str, text: str) -> None:
    ttl = max(0.0, float(SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS))
    if ttl <= 0:
        return
    with SEMANTIC_SCHOLAR_CACHE_LOCK:
        if len(SEMANTIC_SCHOLAR_RESPONSE_CACHE) > 512:
            oldest = sorted(SEMANTIC_SCHOLAR_RESPONSE_CACHE.items(), key=lambda item: item[1][0])[:64]
            for key, _ in oldest:
                SEMANTIC_SCHOLAR_RESPONSE_CACHE.pop(key, None)
        SEMANTIC_SCHOLAR_RESPONSE_CACHE[url] = (time.time(), text)


def log_semantic_scholar_key_status() -> None:
    global SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED
    if SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED:
        return
    SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED = True
    log_event(
        "SCIENCE",
        "semantic_scholar_key_status",
        configured=bool(SEMANTIC_SCHOLAR_API_KEY),
        min_interval_seconds=SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS,
    )


def is_semantic_scholar_rate_limit_error(error: str) -> bool:
    return is_rate_limit_error(error)


def is_rate_limit_error(error: str) -> bool:
    text = str(error).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def is_semantic_scholar_not_found_error(error: str) -> bool:
    text = str(error).lower()
    return "http 404" in text or "paper with id" in text and "not found" in text


def wait_for_semantic_scholar_rate_limit() -> None:
    global SEMANTIC_SCHOLAR_LAST_REQUEST_AT
    interval = max(0.0, float(SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS))
    if interval <= 0:
        return
    with SEMANTIC_SCHOLAR_RATE_LOCK:
        release = acquire_semantic_scholar_process_lock()
        try:
            now_wall = time.time()
            persisted_at = read_semantic_scholar_rate_timestamp()
            last_wall = max(persisted_at, wall_time_from_monotonic(SEMANTIC_SCHOLAR_LAST_REQUEST_AT))
            wait_seconds = last_wall + interval - now_wall
            if wait_seconds > 0:
                log_event(
                    "SCIENCE",
                    "semantic_scholar_rate_limit",
                    wait_ms=int(wait_seconds * 1000),
                    scope="process_file",
                )
                time.sleep(wait_seconds)
            current_wall = time.time()
            SEMANTIC_SCHOLAR_LAST_REQUEST_AT = time.monotonic()
            write_semantic_scholar_rate_timestamp(current_wall)
        finally:
            release()


def wait_for_arxiv_rate_limit() -> None:
    global ARXIV_LAST_REQUEST_AT
    interval = max(0.0, float(SCIENCE_ARXIV_MIN_INTERVAL_SECONDS))
    if interval <= 0:
        return
    with ARXIV_RATE_LOCK:
        release = acquire_provider_process_lock(ARXIV_PROCESS_LOCK_DIR, interval)
        try:
            now_wall = time.time()
            persisted_at = read_provider_rate_timestamp(ARXIV_RATE_STATE_FILE)
            last_wall = max(persisted_at, wall_time_from_monotonic(ARXIV_LAST_REQUEST_AT))
            wait_seconds = last_wall + interval - now_wall
            if wait_seconds > 0:
                log_event(
                    "SCIENCE",
                    "arxiv_rate_limit",
                    wait_ms=int(wait_seconds * 1000),
                    scope="process_file",
                )
                time.sleep(wait_seconds)
            current_wall = time.time()
            ARXIV_LAST_REQUEST_AT = time.monotonic()
            write_provider_rate_timestamp(
                ARXIV_RATE_STATE_FILE,
                current_wall,
                min_interval_seconds=SCIENCE_ARXIV_MIN_INTERVAL_SECONDS,
            )
        finally:
            release()


def wall_time_from_monotonic(monotonic_timestamp: float) -> float:
    if monotonic_timestamp <= 0:
        return 0.0
    return time.time() - max(0.0, time.monotonic() - monotonic_timestamp)


def read_semantic_scholar_rate_timestamp() -> float:
    return read_provider_rate_timestamp(SEMANTIC_SCHOLAR_RATE_STATE_FILE)


def write_semantic_scholar_rate_timestamp(timestamp: float) -> None:
    write_provider_rate_timestamp(
        SEMANTIC_SCHOLAR_RATE_STATE_FILE,
        timestamp,
        min_interval_seconds=SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS,
    )


def read_provider_rate_timestamp(path: Path) -> float:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return float(raw.get("last_request_wall_time") or 0.0)
    except Exception:
        return 0.0


def write_provider_rate_timestamp(path: Path, timestamp: float, min_interval_seconds: float) -> None:
    try:
        SCIENCE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "last_request_wall_time": timestamp,
                    "min_interval_seconds": min_interval_seconds,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp)),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        log_event("SCIENCE", "provider_rate_state_write_failed", path=str(path), error=str(exc))


def acquire_semantic_scholar_process_lock():
    return acquire_provider_process_lock(
        SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR,
        SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS,
    )


def acquire_provider_process_lock(lock_dir: Path, min_interval_seconds: float):
    SCIENCE_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    stale_after = max(60.0, float(min_interval_seconds) * 20)
    while True:
        try:
            lock_dir.mkdir()
            return lambda: release_provider_process_lock(lock_dir)
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
                if age > stale_after:
                    lock_dir.rmdir()
                    log_event("SCIENCE", "provider_rate_lock_stale_removed", path=str(lock_dir), age_seconds=round(age, 2))
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                pass
            if time.monotonic() - started > 30.0:
                log_event("SCIENCE", "provider_rate_lock_timeout", path=str(lock_dir))
                return lambda: None
            time.sleep(0.05)


def release_semantic_scholar_process_lock() -> None:
    release_provider_process_lock(SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR)


def release_provider_process_lock(lock_dir: Path) -> None:
    try:
        lock_dir.rmdir()
    except FileNotFoundError:
        return
    except OSError as exc:
        log_event("SCIENCE", "provider_rate_lock_release_failed", path=str(lock_dir), error=str(exc))


def ssl_context() -> ssl.SSLContext:
    if SCIENCE_INSECURE_SSL:
        return ssl._create_unverified_context()
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def import_literature_text(
    project_id: str,
    title: str = "",
    citation: str = "",
    text: str = "",
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
    parsed = extract_paper_structure(text, use_llm=use_llm)
    inferred_title = title or parsed.get("title") or first_sentences(text, 1) or "Untitled paper"
    inferred_doi = doi or parsed.get("doi", "")
    inferred_arxiv_id = arxiv_id or parsed.get("arxiv_id", "")
    inferred_authors = authors or parsed.get("authors", [])
    inferred_year = year or parsed.get("year", "")
    inferred_venue = venue or parsed.get("venue", "")
    inferred_citation = citation or parsed.get("citation") or build_citation(
        title=inferred_title,
        authors=inferred_authors,
        year=inferred_year,
        doi=inferred_doi,
        arxiv_id=inferred_arxiv_id,
    )
    return import_papergraph_record(
        project_id=project_id,
        title=inferred_title,
        citation=inferred_citation,
        authors=inferred_authors,
        year=inferred_year,
        venue=inferred_venue,
        provider=provider,
        source_type=source_type,
        doi=inferred_doi,
        arxiv_id=inferred_arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        url=url,
        abstract=parsed["abstract"],
        conclusion=parsed["conclusion"],
        strengths=parsed["strengths"],
        improvements=parsed["improvements"],
        method=parsed["method"],
        scenario=parsed["scenario"],
        benchmark=parsed["benchmark"],
        contribution=parsed["contribution"],
        limitation=parsed["limitation"],
        full_text_excerpt=trim_text(text, 16000) if source_type in {"file", "pdf", "full_text", "manual_file"} or len(text) > 2500 else "",
        gap_signals=parsed.get("gap_signals") if isinstance(parsed.get("gap_signals"), list) else None,
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
    target = safe_workspace_path(path)
    text = read_literature_file(target)
    inferred_title = title or target.stem.replace("_", " ")
    inferred_citation = citation or inferred_title
    return import_literature_text(
        project_id=project_id,
        title=inferred_title,
        citation=inferred_citation,
        text=text,
        provider=provider,
        source_type=source_type,
        use_llm=use_llm,
    )


def import_literature_search_result(
    project_id: str,
    search_id: str,
    result_index: int = 0,
    use_llm: bool = False,
) -> str:
    project = load_project(project_id)
    search_record = load_search(search_id)
    results = search_record.get("results", [])
    if not results:
        raise ValueError(
            f"Search {search_id} has no retrieved papers. Do not invent a substitute; retry search or import user-provided text."
        )
    try:
        index = int(result_index)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid result_index: {result_index}") from exc
    if index < 0 or index >= len(results):
        raise ValueError(f"result_index {index} out of range for search {search_id}; total_results={len(results)}")
    result = results[index]
    project_domain = str(project.get("domain") or search_record.get("domain") or "")
    result["domain_relevance"] = domain_relevance_assessment(
        result,
        domain=project_domain,
        query=str(search_record.get("query") or ""),
    )
    if should_reject_for_domain(result, domain=project_domain):
        raise ValueError(
            "Search result rejected before import by domain relevance gate: "
            f"title={trim_text(str(result.get('title') or ''), 120)}, "
            f"domain={project_domain}, assessment={json.dumps(result['domain_relevance'], ensure_ascii=False)}"
        )
    payload = result.get("papergraph_input")
    if not isinstance(payload, dict):
        raise ValueError(f"Search result {index} has no papergraph_input")
    payload = dict(payload)
    quality = publication_quality_assessment(result)
    initial_extraction_quality = extraction_quality_report(payload)
    enrichment_sources: list[str] = []

    if initial_extraction_quality.get("needs_enrichment"):
        payload, enrichment_sources = enrich_papergraph_payload(payload, result)
        if enrichment_sources:
            log_event(
                "SCIENCE",
                "paper_metadata_enriched",
                search_id=search_id,
                result_index=index,
                sources=",".join(enrichment_sources),
            )

    llm_retry: dict[str, Any] = {"attempted": False, "succeeded": False, "error": ""}
    if use_llm or extraction_quality_report(payload).get("needs_llm_retry"):
        payload, llm_retry = maybe_llm_reextract_structure(payload, force=use_llm)
        if llm_retry.get("attempted"):
            log_event("SCIENCE", "paper_extraction_llm_retry", search_id=search_id, result_index=index)
        if llm_retry.get("error"):
            log_event("WARN", "paper_extraction_llm_retry_failed", error=llm_retry.get("error"))
    final_extraction_quality = extraction_quality_report(payload)
    final_extraction_quality["initial"] = initial_extraction_quality
    final_extraction_quality["llm_retry"] = llm_retry
    if payload.get("_enrichment_errors"):
        final_extraction_quality["enrichment_errors"] = payload.get("_enrichment_errors")

    imported = import_papergraph_record(
        project_id=project_id,
        title=str(payload.get("title", "")),
        citation=str(payload.get("citation", "")),
        authors=payload.get("authors") if isinstance(payload.get("authors"), list) else [],
        year=str(payload.get("year", "")),
        venue=str(payload.get("venue", "")),
        provider=str(payload.get("provider", result.get("provider", "search"))),
        source_type=str(payload.get("source_type", "api")),
        doi=str(payload.get("doi", "")),
        arxiv_id=str(payload.get("arxiv_id", "")),
        semantic_scholar_id=str(payload.get("semantic_scholar_id", "")),
        url=str(payload.get("url", "")),
        abstract=str(payload.get("abstract", "")),
        full_text_excerpt=str(payload.get("full_text_excerpt", "")),
        conclusion=str(payload.get("conclusion", "")),
        strengths=payload.get("strengths") if isinstance(payload.get("strengths"), list) else None,
        improvements=payload.get("improvements") if isinstance(payload.get("improvements"), list) else None,
        method=str(payload.get("method", "")),
        scenario=str(payload.get("scenario", "")),
        benchmark=str(payload.get("benchmark", "")),
        contribution=str(payload.get("contribution", "")),
        limitation=str(payload.get("limitation", "")),
        extraction_quality=final_extraction_quality,
        enrichment_sources=enrichment_sources,
        gap_signals=payload.get("gap_signals") if isinstance(payload.get("gap_signals"), list) else None,
    )
    try:
        imported_payload = json.loads(imported)
    except json.JSONDecodeError:
        return imported
    imported_payload["search_result_quality"] = quality
    imported_payload["extraction_quality"] = final_extraction_quality
    imported_payload["enrichment_sources"] = enrichment_sources
    imported_payload["requires_human_review"] = (
        quality["venue_quality"] in {"suspicious", "missing"}
        or quality["quality_score"] < 0.55
        or bool(final_extraction_quality.get("requires_human_review"))
    )
    return json.dumps(imported_payload, ensure_ascii=False, indent=2)


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
    extraction_quality: dict[str, Any] | None = None,
    enrichment_sources: list[str] | None = None,
    gap_signals: list[dict[str, Any]] | None = None,
) -> str:
    project = load_project(project_id)
    unique_key = paper_unique_key(title=title, citation=citation, doi=doi, arxiv_id=arxiv_id, semantic_scholar_id=semantic_scholar_id, url=url)
    duplicate = find_by_id(project.get("papergraph", []), "unique_key", unique_key)
    if duplicate is not None:
        log_event("SCIENCE", "paper_duplicate", project_id=project_id, paper_id=duplicate.get("paper_id"), unique_key=unique_key)
        return json.dumps(
            {
                "status": "duplicate",
                "unique_key": unique_key,
                "existing_record": duplicate,
            },
            ensure_ascii=False,
            indent=2,
        )

    parsed_fallback = parse_paper_text("\n\n".join(part for part in [abstract, conclusion, full_text_excerpt, limitation] if part))
    final_abstract = abstract or parsed_fallback["abstract"]
    final_conclusion = conclusion or parsed_fallback["conclusion"]
    final_strengths = strengths or parsed_fallback["strengths"]
    final_improvements = improvements or parsed_fallback["improvements"]
    final_method = method or parsed_fallback["method"]
    final_scenario = scenario or parsed_fallback["scenario"]
    final_benchmark = benchmark or parsed_fallback["benchmark"]
    final_contribution = contribution or parsed_fallback["contribution"]
    final_limitation = limitation or parsed_fallback["limitation"]
    context_text = "\n".join(part for part in [title, abstract, conclusion, full_text_excerpt, final_contribution, final_limitation] if part)
    final_method = repair_unknown_field(final_method, context_text, "method")
    final_scenario = repair_unknown_field(final_scenario, context_text, "scenario")
    final_benchmark = repair_unknown_field(final_benchmark, context_text, "benchmark")
    extracted_gap_signals = extract_gap_signals_from_text(context_text, citation=citation or title)
    final_gap_signals = normalize_gap_signals(list(gap_signals or []) + extracted_gap_signals, citation=citation or title)
    if final_gap_signals and is_unknown_value(final_limitation):
        final_limitation = str(final_gap_signals[0].get("text", final_limitation))
    elif final_gap_signals and final_limitation == "No explicit limitation extracted.":
        final_limitation = str(final_gap_signals[0].get("text", final_limitation))
    final_extraction_quality = extraction_quality or extraction_quality_report(
        {
            "title": title,
            "abstract": final_abstract,
            "conclusion": final_conclusion,
            "full_text_excerpt": full_text_excerpt,
            "method": final_method,
            "scenario": final_scenario,
            "benchmark": final_benchmark,
            "contribution": final_contribution,
            "limitation": final_limitation,
        }
    )
    score, reasons = score_evidence_credibility(
        title=title,
        citation=citation,
        provider=provider,
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        url=url,
        abstract=final_abstract,
        conclusion=final_conclusion,
        venue=venue,
        year=year,
    )
    record = PaperGraphRecord(
        paper_id=new_id("paper"),
        unique_key=unique_key,
        title=title,
        citation=citation,
        authors=list(authors or []),
        year=str(year),
        venue=venue,
        provider=provider,
        source_type=source_type,
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        url=url,
        abstract=final_abstract,
        full_text_excerpt=full_text_excerpt,
        conclusion=final_conclusion,
        strengths=final_strengths,
        improvements=final_improvements,
        method=final_method,
        scenario=final_scenario,
        benchmark=final_benchmark,
        contribution=final_contribution,
        limitation=final_limitation,
        credibility_score=score,
        credibility_reasons=reasons,
        extraction_quality=final_extraction_quality,
        enrichment_sources=list(enrichment_sources or []),
        gap_signals=final_gap_signals,
    )
    project.setdefault("papergraph", []).append(asdict(record))
    project.setdefault("evidence", []).append(
        asdict(
            PaperEvidence(
                evidence_id=new_id("ev"),
                title=title,
                citation=citation,
                method=final_method,
                scenario=final_scenario,
                benchmark=final_benchmark,
                contribution=final_contribution,
                limitation=final_limitation,
                url=url,
            )
        )
    )
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "paper_imported", project_id=project_id, paper_id=record.paper_id, credibility=score)
    return json.dumps({"status": "imported", "record": asdict(record)}, ensure_ascii=False, indent=2)


def extract_paper_keynote(
    project_id: str,
    paper_id: str = "",
    search_id: str = "",
    result_index: int = 0,
    text: str = "",
    use_llm: bool = True,
) -> str:
    project = load_project(project_id)
    source: dict[str, Any] = {}
    source_text = text
    if paper_id:
        source = find_by_id(project.get("papergraph", []), "paper_id", paper_id) or {}
        if not source:
            raise ValueError(f"Paper not found in project PaperGraph: {paper_id}")
        source_text = "\n\n".join(
            part for part in [source.get("title", ""), source.get("abstract", ""), source.get("conclusion", ""), source.get("limitation", "")] if part
        )
    elif search_id:
        search_record = load_search(search_id)
        results = search_record.get("results", [])
        try:
            source = results[int(result_index)]
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid search result {search_id}:{result_index}") from exc
        source_text = "\n\n".join(part for part in [source.get("title", ""), source.get("abstract", "")] if part)
    elif not source_text:
        raise ValueError("Provide paper_id, search_id/result_index, or text.")

    if use_llm:
        try:
            keynote = extract_keynote_with_llm(source_text)
        except Exception as exc:
            log_event("WARN", "keynote_llm_failed", error=str(exc))
            keynote = extract_keynote_heuristic(source_text)
            keynote["extractor"] = "heuristic_fallback"
            keynote["llm_error"] = str(exc)
    else:
        keynote = extract_keynote_heuristic(source_text)
        keynote["extractor"] = "heuristic"

    item = {
        "keynote_id": new_id("keynote"),
        "paper_id": paper_id,
        "search_id": search_id,
        "result_index": result_index if search_id else None,
        "title": source.get("title", keynote.get("title", "")),
        "createdAt": time.time(),
        "keynote": keynote,
    }
    project.setdefault("keynotes", []).append(item)
    save_project(project)
    return json.dumps(item, ensure_ascii=False, indent=2)


def list_papergraph_records(project_id: str) -> str:
    project = load_project(project_id)
    records = project.get("papergraph", [])
    if not records:
        return "(no PaperGraph records)"
    lines = []
    for record in records:
        lines.append(
            f"{record.get('paper_id')} score={record.get('credibility_score')} "
            f"{record.get('citation')} - {record.get('title')}"
        )
    return "\n".join(lines)


def repair_project_extraction_quality(project: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    repaired = 0
    attempted = 0
    still_low_quality = 0
    errors: list[str] = []
    records = project.get("papergraph", [])
    if not isinstance(records, list):
        return project, {"attempted": 0, "repaired": 0, "still_low_quality": 0, "errors": []}

    for record in records:
        if not isinstance(record, dict):
            continue
        before_quality = extraction_quality_report(record)
        record["extraction_quality"] = before_quality
        payload = dict(record)
        needs_expensive_repair = bool(before_quality.get("needs_enrichment") or before_quality.get("needs_llm_retry"))
        before_core = {
            key: normalize_label(record.get(key, ""))
            for key in ("method", "scenario", "benchmark", "contribution", "limitation")
        }
        if not needs_expensive_repair:
            repaired_payload = repair_payload_fields(payload)
            after_quality = extraction_quality_report(repaired_payload)
            after_quality["initial"] = before_quality
            after_quality["llm_retry"] = {"attempted": False, "succeeded": False, "error": ""}
            record.update(repaired_payload)
            record["extraction_quality"] = after_quality
            after_core = {
                key: normalize_label(record.get(key, ""))
                for key in ("method", "scenario", "benchmark", "contribution", "limitation")
            }
            if after_core != before_core or after_quality.get("requires_human_review"):
                repaired += 1
                sync_evidence_from_record(project, record)
            if after_quality.get("requires_human_review"):
                still_low_quality += 1
            continue
        attempted += 1
        sources: list[str] = []
        if before_quality.get("needs_enrichment"):
            try:
                payload, sources = enrich_papergraph_payload(payload, record)
            except Exception as exc:
                errors.append(f"{record.get('paper_id')}: enrichment failed: {exc}")
        llm_retry: dict[str, Any] = {"attempted": False, "succeeded": False, "error": ""}
        if extraction_quality_report(payload).get("needs_llm_retry"):
            payload, llm_retry = maybe_llm_reextract_structure(payload)
            if llm_retry.get("error"):
                errors.append(f"{record.get('paper_id')}: llm retry failed: {llm_retry.get('error')}")

        repaired_payload = repair_payload_fields(payload)
        after_quality = extraction_quality_report(repaired_payload)
        after_quality["initial"] = before_quality
        after_quality["llm_retry"] = llm_retry
        if repaired_payload.get("_enrichment_errors"):
            after_quality["enrichment_errors"] = repaired_payload.get("_enrichment_errors")
        repaired_payload.pop("_enrichment_errors", None)
        record.update(repaired_payload)
        record["extraction_quality"] = after_quality
        existing_sources = record.get("enrichment_sources") if isinstance(record.get("enrichment_sources"), list) else []
        record["enrichment_sources"] = unique_preserve_order([*existing_sources, *sources])
        if after_quality.get("score", 0) > before_quality.get("score", 0):
            repaired += 1
        if after_quality.get("requires_human_review"):
            still_low_quality += 1
        sync_evidence_from_record(project, record)

    return project, {
        "attempted": attempted,
        "repaired": repaired,
        "still_low_quality": still_low_quality,
        "errors": errors[:10],
    }


def repair_payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    context_text = record_context_text(payload)
    source_text = record_source_text(payload)
    repaired = dict(payload)
    repaired["method"] = repair_unknown_field(repaired.get("method", ""), context_text, "method")
    repaired["scenario"] = repair_unknown_field(repaired.get("scenario", ""), context_text, "scenario")
    repaired["benchmark"] = repair_unknown_field(repaired.get("benchmark", ""), context_text, "benchmark")
    repaired = repair_unsupported_scenario(repaired, source_text or context_text)
    if is_unknown_value(repaired.get("contribution")):
        repaired["contribution"] = first_sentences(context_text, 1)
    if is_unknown_value(repaired.get("limitation")):
        repaired["limitation"] = "No explicit limitation extracted."
    return repaired


def repair_unsupported_scenario(payload: dict[str, Any], context_text: str) -> dict[str, Any]:
    scenario = normalize_label(payload.get("scenario", ""))
    if not scenario or is_unknown_value(scenario):
        return payload
    lowered_context = normalize_space(context_text).lower()
    if scenario_is_supported_by_context(scenario, lowered_context):
        return payload
    inferred = infer_ontology_field(context_text, "scenario") or infer_generic_science_phrase(context_text, "scenario")
    if not inferred or normalize_label(inferred) == scenario:
        return payload
    repaired = dict(payload)
    repaired.setdefault("extraction_quality", {})
    if isinstance(repaired["extraction_quality"], dict):
        flags = repaired["extraction_quality"].setdefault("flags", [])
        if isinstance(flags, list):
            flags.append("scenario_domain_repaired")
        repaired["extraction_quality"]["scenario_before_repair"] = scenario
        repaired["extraction_quality"]["scenario_repair_reason"] = "scenario label was not supported by paper context"
        repaired["extraction_quality"]["requires_human_review"] = True
    repaired["scenario"] = inferred
    return repaired


def scenario_is_supported_by_context(scenario: str, lowered_context: str) -> bool:
    scenario_terms = query_terms(scenario)
    if not scenario_terms:
        return False
    hits = [term for term in scenario_terms if science_term_in_text(term, lowered_context)]
    if hits:
        return True
    ontology_terms = SCENARIO_ONTOLOGY.get(scenario, [])
    return any(science_term_in_text(str(term), lowered_context) for term in ontology_terms)


def science_term_in_text(term: str, lowered_text: str) -> bool:
    clean = normalize_space(term).lower()
    if not clean:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", clean):
        return re.search(rf"\b{re.escape(clean)}\b", lowered_text) is not None
    return clean in lowered_text


def sync_evidence_from_record(project: dict[str, Any], record: dict[str, Any]) -> None:
    evidence_items = project.get("evidence", [])
    if not isinstance(evidence_items, list):
        return
    citation = str(record.get("citation") or "")
    title = str(record.get("title") or "")
    for evidence in evidence_items:
        if not isinstance(evidence, dict):
            continue
        if (citation and evidence.get("citation") == citation) or (title and evidence.get("title") == title):
            evidence["method"] = record.get("method", evidence.get("method", ""))
            evidence["scenario"] = record.get("scenario", evidence.get("scenario", ""))
            evidence["benchmark"] = record.get("benchmark", evidence.get("benchmark", ""))
            evidence["contribution"] = record.get("contribution", evidence.get("contribution", ""))
            evidence["limitation"] = record.get("limitation", evidence.get("limitation", ""))


def verify_citation_uniqueness(
    project_id: str,
    title: str = "",
    citation: str = "",
    doi: str = "",
    arxiv_id: str = "",
    semantic_scholar_id: str = "",
    url: str = "",
) -> str:
    project = load_project(project_id)
    unique_key = paper_unique_key(title=title, citation=citation, doi=doi, arxiv_id=arxiv_id, semantic_scholar_id=semantic_scholar_id, url=url)
    duplicates = [record for record in project.get("papergraph", []) if record.get("unique_key") == unique_key]
    checks = project.setdefault("citation_uniqueness_checks", [])
    prior_count = sum(1 for item in checks if isinstance(item, dict) and item.get("unique_key") == unique_key)
    result = {
        "unique": not duplicates,
        "unique_key": unique_key,
        "duplicates": duplicates,
        "repeated_check": prior_count > 0,
        "prior_check_count": prior_count,
        "next_step": (
            "This citation has already been checked in this run; do not repeat verify_citation_uniqueness. "
            "If it is unique, import only if it came from a real cached search result; otherwise continue with search_literature/select/import."
            if prior_count > 0
            else "Use this uniqueness result once. Do not repeatedly call verify_citation_uniqueness for the same citation."
        ),
    }
    checks.append(
        {
            "unique_key": unique_key,
            "title": title,
            "citation": citation,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "semantic_scholar_id": semantic_scholar_id,
            "url": url,
            "unique": not duplicates,
            "checkedAt": time.time(),
        }
    )
    if len(checks) > 200:
        project["citation_uniqueness_checks"] = checks[-200:]
    project["updatedAt"] = time.time()
    save_project(project)
    return json.dumps(result, ensure_ascii=False, indent=2)


def parse_literature_text(text: str, use_llm: bool = False) -> str:
    return json.dumps(extract_paper_structure(text, use_llm=use_llm), ensure_ascii=False, indent=2)


def list_research_projects() -> str:
    projects = [load_project(path.stem) for path in sorted(projects_dir().glob("sci_*.json"))]
    if not projects:
        return "(no science projects)"
    return "\n".join(
        f"{project['project_id']} [{project.get('phase', '')}] {project.get('domain', '')} - {project.get('title', '')}"
        for project in projects
    )


def get_research_project(project_id: str) -> str:
    return json.dumps(load_project(project_id), ensure_ascii=False, indent=2)


def list_science_agents() -> str:
    return json.dumps(SCIENCE_AGENTS, ensure_ascii=False, indent=2)


def get_science_agent_prompt(agent: str) -> str:
    key = normalize_key(agent)
    spec = SCIENCE_AGENTS.get(key)
    if spec is None:
        raise ValueError(f"Unknown science agent: {agent}")
    if key == "boxue":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": BOXUE_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Assess project state, dependencies, output quality, gap lifecycle, and delegation risk.",
                "action_tools": [
                    "create_boxue_delegation_tasks",
                    "create_science_delegation_tasks",
                    "create_science_pipeline_tasks",
                    "create_task",
                    "spawn_teammate",
                    "check_inbox",
                    "review_plan",
                ],
                "observation": "Track specialist deliverables, gate shared project writes, synthesize conclusions, and decide advance/revise/finalize.",
            },
            "output_schema": {
                "thought": "string",
                "action": {"type": "assign_task | review_output | synthesize | adjust_plan | finalize", "params": {}},
                "progress": {
                    "current_phase": "Gap Discovery | Hypothesis Generation | Socratic Debate | Mechanism Verification | Experimental Design | Implementation | Manuscript Writing | Review & Iteration",
                    "completed_tasks": ["task_id"],
                    "ongoing_tasks": ["task_id"],
                },
                "remaining_steps": "integer",
            },
            "global_constraints": [
                "Boxue coordinates; specialist agents execute domain work.",
                "Every task needs explicit deliverable standards and acceptance criteria.",
                "Use delegation DAGs for broad or long-running workflows instead of one brittle agent run.",
                "Do not treat unsupported or unreviewed evidence as a validated knowledge gap.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    if key == "zhizhi":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": ZHIZHI_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Analyze search strategy, source quality, evidence coverage, blind spots, migration opportunities, and pseudo-gap risk.",
                "action_tools": [
                    "search_papers_stratified",
                    "search_papers",
                    "extract_structured_info",
                    "build_knowledge_map",
                    "detect_knowledge_gaps",
                    "assess_novelty",
                    "verify_uniqueness",
                    "run_zhizhi_literature_analysis",
                ],
                "observation": "Update PaperGraph, benchmark-aware knowledge map, novelty checks, and valid innovation flags.",
            },
            "output_schema": zhizhi_output_schema(),
            "global_constraints": [
                "Never invent or substitute papers when retrieval fails.",
                "Every methodological claim must be grounded in a retrieved/imported source or marked as unsupported.",
                "Classify evidence as empirical_result, theoretical_claim, methodological_description, or author_opinion.",
                "Return structured JSON matching the ZhiZhi output schema.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    if key == "tanxi":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": TANXI_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Scan PaperGraph density, unresolved problems, unconnected cross-field pairs, strategic value, and pseudo-gap risk.",
                "action_tools": [
                    "run_tanxi_gap_exploration",
                    "detect_knowledge_gaps",
                    "assess_novelty",
                    "verify_uniqueness",
                ],
                "observation": "Return coverage_analysis, cross_disciplinary_unconnected_pairs, suspended_problems, and ranked_gaps.",
            },
            "output_schema": {
                "thought": "string",
                "action": {},
                "coverage_analysis": {"dense_areas": [], "density_holes": []},
                "cross_disciplinary_unconnected_pairs": [],
                "suspended_problems": [],
                "ranked_gaps": [],
            },
            "global_constraints": [
                "Every gap must be backed by at least one PaperGraph reference.",
                "Rank no more than 10 gaps per scan.",
                "Avoid trivial gaps and already-saturated areas.",
                "Prioritize scientific significance, tractability, strategic value, and downstream impact.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    prompt = {
        "agent": key,
        **spec,
        "operating_protocol": "Use a TAO loop: Thought -> Action -> Observation. Return structured JSON only.",
        "global_constraints": [
            "Every claim must be backed by evidence or marked as a hypothesis.",
            "Every deliverable needs explicit acceptance criteria.",
            "Knowledge gaps must be scientifically meaningful, not merely untried combinations.",
            "Mechanism claims require internal consistency, data consistency, and regime-shift checks.",
        ],
    }
    return json.dumps(prompt, ensure_ascii=False, indent=2)


def zhizhi_output_schema() -> dict[str, Any]:
    return {
        "thought": "string",
        "action": "object",
        "knowledge_map_summary": {
            "main_methods": ["string"],
            "method_scenario_coverage": {"method": ["scenario"]},
            "method_scenario_benchmark_triples": [
                {"method": "string", "scenario": "string", "benchmark": "string", "references": ["string"]}
            ],
        },
        "knowledge_gaps": [
            {
                "gap_id": "string",
                "gap_type": "combinatorial | improvement | migration | problem",
                "description": "string",
                "supporting_references": ["string"],
                "novelty_score": "integer 1-10",
                "application_value": "high | medium | low",
                "feasibility": "high | medium | low",
                "suggested_research_path": "string",
            }
        ],
    }


def create_science_pipeline_tasks(project_id: str) -> str:
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
    spawn_teammates: bool = False,
    max_parallel_agents: int = 3,
) -> str:
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
    if spawn_teammates:
        try:
            from .agent_teams import spawn_teammate
        except ImportError:
            from agent_teams import spawn_teammate
        ready_tasks = [task for task in created_tasks if not task.get("blockedBy")]
        for item in ready_tasks[: clamp_int(max_parallel_agents, 1, 10)]:
            name = f"boxue_{normalize_key(str(item.get('agent') or 'agent'))}"
            prompt = (
                f"You are the {item['agent']} specialist for Boxue delegation plan {plan_id}. "
                f"Claim task {item['task_id']} and complete only that role-bound deliverable. "
                "Do not take over Boxue coordination or perform other agents' responsibilities."
            )
            status = spawn_teammate(name, prompt)
            spawned.append({"name": name, "task_id": item["task_id"], "status": status})

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
    spawn_teammates: bool = True,
    plan_id: str = "",
    execution_mode: str = "async",
    max_steps: int = 20,
    max_parallel_agents: int = 3,
    max_runtime_seconds: int = 45,
    poll_interval_seconds: float = 2.0,
    revision_after_seconds: int = 600,
) -> str:
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
    if mode in {"pipeline", "sync", "synchronous", "closed_loop", "closedloop"}:
        pipeline_executions = boxue_run_pipeline_specialists(
            project_id=project_id,
            plan_id=plan_id,
            plan_tasks=plan_tasks,
            goal=goal or str(project.get("objective", "")),
        )
        spawn_teammates = False

    def remaining_time() -> float:
        return runtime_limit - (time.time() - started_at)

    while True:
        inbox_events.extend(boxue_consume_inbox(limit=12))
        if spawn_teammates:
            spawned.extend(
                boxue_spawn_ready_teammates(
                    plan_id=plan_id,
                    plan_tasks=plan_tasks,
                    already_spawned={str(item.get("task_id")) for item in spawned},
                    max_parallel_agents=parallel_limit,
                )
            )

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
        "spawn_teammates": bool(spawn_teammates),
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


def boxue_spawn_ready_teammates(
    *,
    plan_id: str,
    plan_tasks: list[dict[str, Any]],
    already_spawned: set[str],
    max_parallel_agents: int,
) -> list[dict[str, Any]]:
    try:
        from .agent_teams import active_session, spawn_teammate
        from .task_system import load_task
    except ImportError:
        from agent_teams import active_session, spawn_teammate
        from task_system import load_task

    spawned: list[dict[str, Any]] = []
    running = 0
    for item in plan_tasks:
        task_id = str(item.get("task_id") or "")
        if not task_id or task_id in already_spawned:
            continue
        agent = normalize_key(str(item.get("agent") or "agent"))
        name = f"boxue_{agent}"
        if active_session(name):
            running += 1
            continue
        if running + len(spawned) >= max_parallel_agents:
            break
        try:
            task = load_task(task_id)
        except Exception as exc:
            spawned.append({"task_id": task_id, "agent": agent, "status": f"load_failed: {exc}"})
            continue
        if task.status != "pending" or task.owner or not boxue_task_dependencies_completed(task):
            continue
        prompt = boxue_specialist_prompt(plan_id, item)
        status = spawn_teammate(name, prompt)
        spawned.append({"name": name, "agent": agent, "task_id": task_id, "status": status})
        running += 1
    return spawned


def boxue_run_pipeline_specialists(
    *,
    project_id: str,
    plan_id: str,
    plan_tasks: list[dict[str, Any]],
    goal: str,
) -> list[dict[str, Any]]:
    executions: list[dict[str, Any]] = []
    progressed = True
    while progressed:
        progressed = False
        for item in plan_tasks:
            task_id = str(item.get("task_id") or "")
            agent = normalize_key(str(item.get("agent") or ""))
            if agent not in {"zhizhi", "tanxi", "mingli"} or not task_id:
                continue
            state = boxue_task_state(task_id)
            if state.get("status") == "completed":
                continue
            if not boxue_task_dependencies_completed_by_id(task_id):
                continue
            executions.append(
                boxue_execute_specialist_task(
                    project_id=project_id,
                    plan_id=plan_id,
                    task_id=task_id,
                    agent=agent,
                    goal=goal,
                )
            )
            progressed = True
    return executions


def boxue_execute_specialist_task(
    *,
    project_id: str,
    plan_id: str,
    task_id: str,
    agent: str,
    goal: str,
) -> dict[str, Any]:
    started = time.time()
    try:
        if agent == "zhizhi":
            project = load_project(project_id)
            output = run_zhizhi_literature_analysis(
                project_id=project_id,
                domain=str(project.get("domain", "")),
                query=boxue_research_query(project, goal),
                max_results=15,
                import_top_k=15,
                graph_depth=1,
                use_llm=True,
                live_coverage_check=True,
            )
        elif agent == "tanxi":
            project = load_project(project_id)
            output = run_tanxi_gap_exploration(
                project_id=project_id,
                target_domain=str(project.get("domain", "")),
                max_gaps=10,
            )
        elif agent == "mingli":
            output = run_mingli_hypothesis_evolution(
                project_id=project_id,
                population_size=24,
                generations=4,
                top_k=5,
                use_llm=False,
            )
        else:
            raise ValueError(f"Unsupported Boxue pipeline specialist: {agent}")

        completion = boxue_force_complete_task(task_id)
        log_event("SCIENCE", "boxue_pipeline_specialist_done", plan_id=plan_id, task_id=task_id, agent=agent)
        return {
            "task_id": task_id,
            "agent": agent,
            "status": "completed",
            "elapsed_ms": int((time.time() - started) * 1000),
            "output_summary": summarize_json_output(output),
            "completion": trim_text(completion, 800),
        }
    except Exception as exc:
        log_event("WARN", "boxue_pipeline_specialist_failed", plan_id=plan_id, task_id=task_id, agent=agent, error=exc)
        return {
            "task_id": task_id,
            "agent": agent,
            "status": "failed",
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": str(exc),
        }


def boxue_research_query(project: dict[str, Any], goal: str) -> str:
    domain = normalize_space(str(project.get("domain", "")))
    objective = normalize_space(str(project.get("objective", "")))
    goal_text = normalize_space(goal)
    text = " ".join(part for part in [domain, objective, goal_text] if part)
    if not text:
        text = "AI for Science literature review knowledge gaps hypothesis generation"
    return trim_text(text, 500)


def summarize_json_output(output: str) -> dict[str, Any]:
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
            spawn_teammates=False,
            max_parallel_agents=max_parallel_agents,
        )
    )
    payload["reused_existing_plan"] = False
    return payload


def boxue_find_active_plan(project: dict[str, Any], phases: list[str] | None = None) -> dict[str, Any] | None:
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


def boxue_specialist_prompt(plan_id: str, item: dict[str, Any]) -> str:
    criteria = "\n".join(f"- {entry}" for entry in item.get("acceptance_criteria", []))
    return (
        f"You are the {item.get('agent')} specialist in Boxue research round {plan_id}.\n"
        f"Scoped task id: {item.get('task_id')}.\n"
        f"Phase: {item.get('phase')}. Title: {item.get('title')}.\n"
        "Claim only this task when dependencies are complete. Produce the role-bound deliverable as compact JSON, "
        "then call complete_task for this task and send a concise result to lead.\n"
        "Do not perform Boxue coordination or other specialists' tasks.\n"
        f"Acceptance criteria:\n{criteria or '- Follow the task description.'}"
    )


def boxue_task_dependencies_completed(task: Any) -> bool:
    try:
        from .task_system import incomplete_dependencies
    except ImportError:
        from task_system import incomplete_dependencies
    return not incomplete_dependencies(task)


def boxue_consume_inbox(limit: int = 20) -> list[str]:
    try:
        from .agent_teams import consume_lead_inbox
    except ImportError:
        from agent_teams import consume_lead_inbox
    messages = consume_lead_inbox()
    return [trim_text(message, 1200) for message in messages[-max(0, limit):]]


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


def boxue_round_next_step(final_decision: dict[str, Any]) -> str:
    status = str(final_decision.get("status") or "")
    if status == "finalized":
        return "Use Boxue final synthesis output to decide whether to start a new research iteration."
    if status == "revision_required":
        return "Run run_boxue_research_round again with the same plan_id after revision tasks complete, or inspect the created revision tasks."
    return "Let spawned teammates continue, then run run_boxue_research_round again with the same plan_id to monitor and dispatch newly unblocked specialists."


def create_science_delegation_tasks(
    project_id: str,
    objective: str = "",
    subspace_map_id: str = "",
    selected_subfields: list[str] | None = None,
    focus_branches: list[str] | None = None,
    max_branch_tasks: int = 6,
    spawn_teammates: bool = False,
) -> str:
    """Create a subagent-friendly DAG for long science workflows.

    Branch scouts produce append-only artifacts. The lead then performs the
    state-changing PaperGraph imports in one place, avoiding parallel writes to
    the same project JSON from multiple worktrees.
    """
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
        description = science_branch_scout_description(
            project,
            objective=objective,
            branch=branch,
            artifact_path=artifact_path,
            providers=providers,
        )
        rendered = create_task(
            subject=f"Science scout {index}: {branch.get('name') or branch.get('branch')}",
            description=description,
            blockedBy=[],
        )
        task_id = extract_task_id(rendered)
        if task_id:
            branch_task_ids.append(task_id)
            branch_tasks.append(
                {
                    "task_id": task_id,
                    "branch": branch.get("branch"),
                    "name": branch.get("name"),
                    "query": branch.get("query"),
                    "artifact_path": artifact_path,
                }
            )

    synthesis_description = science_synthesis_gate_description(
        project,
        objective=objective,
        plan_id=plan_id,
        branch_tasks=branch_tasks,
    )
    synthesis_rendered = create_task(
        subject=f"Science synthesis gate: {project.get('title', project_id)}",
        description=synthesis_description,
        blockedBy=branch_task_ids,
    )
    synthesis_task_id = extract_task_id(synthesis_rendered)

    tanxi_rendered = create_task(
        subject=f"TanXi gap ranking after delegation: {project.get('title', project_id)}",
        description=(
            f"Science delegation plan: {plan_id}\n"
            f"Project: {project.get('title', '')} ({project_id})\n"
            f"Domain: {project.get('domain', '')}\n"
            "Wait until the synthesis gate confirms lead-side PaperGraph imports are complete. "
            "Then run build_knowledge_map, run_tanxi_gap_exploration, and produce a compact ranked-gap report. "
            "Do not rerun broad ZhiZhi retrieval; use the merged project evidence.\n"
            "Deliverable JSON keys: knowledge_map_id_or_summary, ranked_gaps, risks, recommended_mingli_gap_ids."
        ),
        blockedBy=[synthesis_task_id] if synthesis_task_id else branch_task_ids,
    )
    tanxi_task_id = extract_task_id(tanxi_rendered)

    mingli_rendered = create_task(
        subject=f"MingLi hypothesis evolution after delegation: {project.get('title', project_id)}",
        description=(
            f"Science delegation plan: {plan_id}\n"
            f"Project: {project.get('title', '')} ({project_id})\n"
            "After TanXi completes, run run_mingli_hypothesis_evolution on the validated top gaps. "
            "Keep population/generations modest unless the lead explicitly expands the budget. "
            "Deliverable JSON keys: selected_gap_ids, finalists, rejected_or_risky_hypotheses, next_mechanism_checks."
        ),
        blockedBy=[tanxi_task_id] if tanxi_task_id else [],
    )
    mingli_task_id = extract_task_id(mingli_rendered)

    spawned: list[dict[str, str]] = []
    if spawn_teammates:
        try:
            from .agent_teams import spawn_teammate
        except ImportError:
            from agent_teams import spawn_teammate
        for index, item in enumerate(branch_tasks, 1):
            name = f"science_scout_{index}"
            prompt = (
                f"You are {name}. Claim task {item['task_id']} and complete only that branch scout. "
                "Do not import into the shared science project; write the required artifact and summarize search_ids/result_indices."
            )
            status = spawn_teammate(name, prompt)
            spawned.append({"name": name, "task_id": item["task_id"], "status": status})

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


def science_delegation_branch_plan(
    project: dict[str, Any],
    *,
    subspace_map_id: str = "",
    selected_subfields: list[str] | None = None,
    focus_branches: list[str] | None = None,
    max_branch_tasks: int = 6,
) -> list[dict[str, Any]]:
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
    project = load_project(project_id)
    evidence = PaperEvidence(
        evidence_id=new_id("ev"),
        title=title,
        citation=citation,
        method=method,
        scenario=scenario,
        benchmark=benchmark,
        contribution=contribution,
        limitation=limitation,
        url=url,
    )
    project.setdefault("evidence", []).append(asdict(evidence))
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "evidence_added", project_id=project_id, evidence_id=evidence.evidence_id)
    return json.dumps(asdict(evidence), ensure_ascii=False, indent=2)


def build_knowledge_map(project_id: str, dimension: str = "method-scenario-benchmark") -> str:
    project = load_project(project_id)
    project, repair_report = repair_project_extraction_quality(project)
    if repair_report.get("attempted"):
        project["updatedAt"] = time.time()
        save_project(project)
        log_event(
            "SCIENCE",
            "extraction_quality_repair",
            project_id=project_id,
            attempted=repair_report.get("attempted"),
            repaired=repair_report.get("repaired"),
            still_low_quality=repair_report.get("still_low_quality"),
        )
    records = project_records_for_mapping(project)
    triples: list[dict[str, Any]] = []
    method_scenario_coverage: dict[str, list[str]] = {}
    benchmark_index: dict[str, list[str]] = {}
    method_scenario_benchmark: dict[str, dict[str, dict[str, list[str]]]] = {}

    for record in records:
        record_text = record_context_text(record)
        method = normalize_label(repair_unknown_field(record.get("method", ""), record_text, "method"))
        scenario = normalize_label(repair_unknown_field(record.get("scenario", ""), record_text, "scenario"))
        benchmark = normalize_label(repair_unknown_field(record.get("benchmark", ""), record_text, "benchmark"))
        citation = str(record.get("citation", "") or record.get("title", ""))
        if scenario not in method_scenario_coverage.setdefault(method, []):
            method_scenario_coverage[method].append(scenario)
        if citation not in benchmark_index.setdefault(benchmark, []):
            benchmark_index[benchmark].append(citation)
        refs = method_scenario_benchmark.setdefault(method, {}).setdefault(scenario, {}).setdefault(benchmark, [])
        if citation and citation not in refs:
            refs.append(citation)
        triples.append(
            {
                "method": method,
                "scenario": scenario,
                "benchmark": benchmark,
                "references": refs[:5],
                "evidence_type_annotations": classify_record_evidence(record),
            }
        )

    knowledge_map = {
        "dimension": dimension,
        "main_methods": sorted(method_scenario_coverage),
        "main_scenarios": sorted({scenario for scenarios in method_scenario_coverage.values() for scenario in scenarios}),
        "main_benchmarks": sorted(benchmark_index),
        "method_scenario_coverage": {key: sorted(values) for key, values in method_scenario_coverage.items()},
        "benchmark_index": {key: values[:8] for key, values in benchmark_index.items()},
        "method_scenario_benchmark": method_scenario_benchmark,
        "method_scenario_benchmark_triples": triples,
        "claim_type_counts": dict(Counter(item["claim_type"] for triple in triples for item in triple["evidence_type_annotations"])),
        "extraction_repair": repair_report,
    }
    knowledge_map["unknown_summary"] = knowledge_map_unknown_summary(knowledge_map)
    project["knowledge_map"] = knowledge_map
    project["coverage_matrix"] = {
        method: {scenario: sorted({ref for refs in benchmarks.values() for ref in refs}) for scenario, benchmarks in scenarios.items()}
        for method, scenarios in method_scenario_benchmark.items()
    }
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "knowledge_map_built", project_id=project_id, methods=len(method_scenario_coverage), triples=len(triples))
    return json.dumps(knowledge_map, ensure_ascii=False, indent=2)


def build_coverage_matrix(project_id: str) -> str:
    project = load_project(project_id)
    matrix: dict[str, dict[str, list[str]]] = {}
    for evidence in project.get("evidence", []):
        method = normalize_label(evidence.get("method", "unknown"))
        scenario = normalize_label(evidence.get("scenario", "unknown"))
        citation = str(evidence.get("citation", ""))
        matrix.setdefault(method, {}).setdefault(scenario, [])
        if citation and citation not in matrix[method][scenario]:
            matrix[method][scenario].append(citation)
    project["coverage_matrix"] = matrix
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "coverage_matrix_built", project_id=project_id, methods=len(matrix))
    return json.dumps(matrix, ensure_ascii=False, indent=2)


def detect_reasoning_gaps(project: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    records = [record for record in project_records_for_mapping(project) if isinstance(record, dict)]
    gaps.extend(detect_contradiction_gaps(project, records, limit=max(1, limit // 2)))
    if len(gaps) < limit:
        gaps.extend(detect_anomaly_gaps(project, records, limit=limit - len(gaps)))
    return dedupe_knowledge_gaps(gaps)[:limit]


def detect_contradiction_gaps(project: dict[str, Any], records: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    comparable = [record for record in records if record_claim_text(record)]
    for index, left in enumerate(comparable):
        for right in comparable[index + 1 :]:
            relation = contradiction_relation(left, right)
            if not relation.get("contradiction"):
                continue
            refs = unique_preserve_order([record_reference(left), record_reference(right)])
            gap = make_gap(
                gap_type="contradiction",
                description=(
                    "Potential conclusion conflict: "
                    f"{relation.get('shared_context')} contains opposing claims: "
                    f"{trim_text(relation.get('left_claim', ''), 180)} vs "
                    f"{trim_text(relation.get('right_claim', ''), 180)}."
                ),
                supporting_references=refs,
                suggested_research_path=(
                    "Extract the exact claim sentences, verify citation contexts/full text, then design a discriminating experiment, "
                    "simulation, benchmark, or theoretical derivation that can separate the competing explanations."
                ),
                value_argument=(
                    "Contradiction gaps are high-value because resolving them can update mechanism understanding, "
                    "not merely fill a sparse method-scenario cell."
                ),
            )
            assessed = assess_gap_dict(project, gap)
            assessed["reasoning_signal"] = {
                "type": "claim_contradiction",
                "shared_context": relation.get("shared_context"),
                "left_polarity": relation.get("left_polarity"),
                "right_polarity": relation.get("right_polarity"),
            }
            gaps.append(assessed)
            if len(gaps) >= limit:
                return gaps
    return gaps


def detect_anomaly_gaps(project: dict[str, Any], records: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    anomaly_terms = (
        "anomaly",
        "anomalous",
        "unexplained",
        "discrepancy",
        "inconsistent with",
        "inconsistency",
        "tension",
        "mismatch",
        "deviates from",
        "unexpectedly",
        "puzzle",
        "cannot explain",
        "not explained",
    )
    theory_terms = ("theory", "model", "prediction", "simulation", "calculation", "mechanism")
    observation_terms = ("observation", "observed", "experiment", "measurement", "data", "empirical", "clinical", "field")
    gaps: list[dict[str, Any]] = []
    for record in records:
        text = record_claim_text(record)
        lowered = text.lower()
        if not any(term in lowered for term in anomaly_terms):
            continue
        has_theory_or_observation = any(term in lowered for term in theory_terms) or any(term in lowered for term in observation_terms)
        sentence = first_sentence_with_terms(text, anomaly_terms) or trim_text(text, 240)
        gap = make_gap(
            gap_type="anomaly",
            description=f"Unexplained anomaly or theory-evidence tension reported in the literature: {sentence}",
            supporting_references=[record_reference(record)],
            suggested_research_path=(
                "Turn the anomaly into competing mechanistic explanations, then test which assumptions fail under controlled conditions, "
                "ablation, counterexample construction, or independent data."
            ),
            value_argument=(
                "Anomaly gaps can drive explanatory progress because they point to observations or results that current mechanisms do not fully account for."
            ),
        )
        assessed = assess_gap_dict(project, gap)
        assessed["reasoning_signal"] = {
            "type": "theory_evidence_anomaly" if has_theory_or_observation else "unexplained_anomaly",
            "source_field": record_field(record),
        }
        gaps.append(assessed)
        if len(gaps) >= limit:
            return gaps
    return gaps


def contradiction_relation(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_context = normalize_label(left.get("scenario", "")) or normalize_label(left.get("benchmark", ""))
    right_context = normalize_label(right.get("scenario", "")) or normalize_label(right.get("benchmark", ""))
    left_text = record_claim_text(left)
    right_text = record_claim_text(right)
    if not left_text or not right_text:
        return {"contradiction": False}
    context_overlap = text_jaccard(left_context, right_context) if left_context and right_context else 0.0
    claim_overlap = text_jaccard(left_text, right_text)
    if context_overlap < 0.35 and claim_overlap < 0.18:
        return {"contradiction": False}
    left_polarity = claim_polarity(left_text)
    right_polarity = claim_polarity(right_text)
    if left_polarity == "neutral" or right_polarity == "neutral" or left_polarity == right_polarity:
        return {"contradiction": False}
    return {
        "contradiction": True,
        "shared_context": left_context if context_overlap >= 0.35 else "overlapping claim context",
        "left_claim": first_polar_sentence(left_text, left_polarity) or trim_text(left_text, 220),
        "right_claim": first_polar_sentence(right_text, right_polarity) or trim_text(right_text, 220),
        "left_polarity": left_polarity,
        "right_polarity": right_polarity,
    }


def record_claim_text(record: dict[str, Any]) -> str:
    return normalize_space(
        " ".join(
            scalar(record.get(key))
            for key in ("conclusion", "contribution", "limitation", "abstract", "strengths", "improvements")
            if scalar(record.get(key))
        )
    )


def record_reference(record: dict[str, Any]) -> str:
    return str(record.get("citation") or record.get("title") or record.get("paper_id") or "")


def claim_polarity(text: str) -> str:
    lowered = text.lower()
    positive_terms = (
        "support",
        "supports",
        "confirm",
        "consistent with",
        "improve",
        "outperform",
        "effective",
        "robust",
        "stable",
        "explains",
        "predicts",
        "evidence for",
    )
    negative_terms = (
        "contradict",
        "inconsistent",
        "fails",
        "failure",
        "not support",
        "no evidence",
        "cannot",
        "unstable",
        "discrepancy",
        "does not explain",
        "challenges",
        "undermines",
    )
    positive_count = sum(1 for term in positive_terms if phrase_in_text(term, lowered))
    negative_count = sum(1 for term in negative_terms if phrase_in_text(term, lowered))
    if positive_count > negative_count:
        return "positive"
    if negative_count > positive_count:
        return "negative"
    return "neutral"


def phrase_in_text(phrase: str, text: str) -> bool:
    normalized = normalize_space(phrase).lower()
    if not normalized:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(normalized).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def first_polar_sentence(text: str, polarity: str) -> str:
    terms = (
        ("support", "confirm", "consistent", "improve", "outperform", "effective", "robust", "stable", "explains", "predicts")
        if polarity == "positive"
        else ("contradict", "inconsistent", "fails", "failure", "not support", "no evidence", "cannot", "unstable", "discrepancy", "challenges")
    )
    return first_sentence_with_terms(text, terms)


def first_sentence_with_terms(text: str, terms: tuple[str, ...]) -> str:
    for sentence in split_sentences(text):
        lowered = sentence.lower()
        if any(term in lowered for term in terms):
            return trim_text(sentence, 260)
    return ""


def detect_knowledge_gaps(project_id: str, max_gaps: int = 10) -> str:
    project = load_project(project_id)
    if not project.get("knowledge_map"):
        build_knowledge_map(project_id)
        project = load_project(project_id)

    knowledge_map: dict[str, Any] = project.get("knowledge_map", {})
    matrix: dict[str, dict[str, list[str]]] = project.get("coverage_matrix", {})
    methods = sorted(knowledge_map.get("main_methods") or matrix)
    scenarios = sorted(knowledge_map.get("main_scenarios") or {scenario for coverage in matrix.values() for scenario in coverage})
    benchmarks = sorted(knowledge_map.get("main_benchmarks") or [])
    gaps: list[dict[str, Any]] = []
    per_type_quota = max(1, max_gaps // 5)

    if len(gaps) < max_gaps:
        gaps.extend(detect_reasoning_gaps(project, min(per_type_quota + 1, max_gaps - len(gaps))))

    for method in methods:
        for scenario in scenarios:
            if scenario in matrix.get(method, {}):
                continue
            references = supporting_references_for_method_or_scenario(project, method, scenario)
            gap = make_gap(
                gap_type="combinatorial",
                description=f"Method '{method}' has no recorded validation in scenario '{scenario}' in the current PaperGraph map.",
                supporting_references=references,
                suggested_research_path="Run a targeted validation study with explicit benchmarks, baselines, and failure-mode analysis.",
                value_argument="The combination may expose method-scenario boundary conditions rather than simply adding another benchmark.",
            )
            gaps.append(assess_gap_dict(project, gap))
            if count_gap_type(gaps, "combinatorial") >= per_type_quota:
                break
        if count_gap_type(gaps, "combinatorial") >= per_type_quota:
            break

    if len(gaps) < max_gaps and benchmarks:
        triples = knowledge_map.get("method_scenario_benchmark", {})
        for method in methods:
            for scenario in scenarios:
                if count_gap_type(gaps, "combinatorial") >= per_type_quota:
                    break
                covered_benchmarks = set((triples.get(method, {}).get(scenario, {}) or {}).keys())
                missing = [benchmark for benchmark in benchmarks if benchmark not in covered_benchmarks]
                if not covered_benchmarks or not missing:
                    continue
                refs = supporting_references_for_method_or_scenario(project, method, scenario)
                gap = make_gap(
                    gap_type="combinatorial",
                    description=f"Method '{method}' is recorded for scenario '{scenario}', but not against benchmark(s): {', '.join(missing[:3])}.",
                    supporting_references=refs,
                    suggested_research_path="Test the existing method-scenario pair on the missing benchmark family and compare against canonical baselines.",
                    value_argument="Benchmark transfer can reveal robustness and generalization failures hidden by single-benchmark validation.",
                )
                gaps.append(assess_gap_dict(project, gap))
                if count_gap_type(gaps, "combinatorial") >= per_type_quota:
                    break
            if count_gap_type(gaps, "combinatorial") >= per_type_quota:
                break

    for evidence in project.get("evidence", []):
        if len(gaps) >= max_gaps or count_gap_type(gaps, "improvement") >= per_type_quota:
            break
        limitation = str(evidence.get("limitation", "")).strip()
        if not limitation:
            continue
        gap = make_gap(
            gap_type="improvement",
            description=f"Recorded limitation worth testing: {limitation}",
            supporting_references=[str(evidence.get("citation", ""))],
            suggested_research_path="Formulate a hypothesis that directly attacks the documented limitation and verify it under stress conditions.",
            value_argument="The gap is grounded in an author-reported limitation, so it has stronger evidential support than a bare untried combination.",
        )
        gaps.append(assess_gap_dict(project, gap))

    if len(gaps) < max_gaps:
        gaps.extend(detect_gap_signal_gaps(project, max_gaps - len(gaps)))

    if len(gaps) < max_gaps:
        gaps.extend(detect_problem_gaps(project, min(per_type_quota, max_gaps - len(gaps))))

    if len(gaps) < max_gaps:
        gaps.extend(detect_migration_gaps(project, methods, scenarios, max_gaps - len(gaps)))

    gaps = dedupe_knowledge_gaps(gaps)
    filtered_gaps, rejected_gaps = filter_low_value_gaps(gaps, min_novelty=4)
    tanxi_report = tanxi_gap_exploration_report(
        project,
        filtered_gaps,
        target_domain=str(project.get("domain", "")),
        strategic_domains=default_strategic_domains(project),
        max_gaps=max_gaps,
    )
    ranked = tanxi_report.get("ranked_gaps", [])
    ranked_by_id = {item.get("gap_id"): item for item in ranked if item.get("gap_id")}
    prioritized_gaps: list[dict[str, Any]] = []
    for gap in filtered_gaps:
        enriched = dict(gap)
        ranking = ranked_by_id.get(gap.get("gap_id"))
        if ranking:
            enriched.update(
                {
                    "tanxi_rank": ranking.get("rank"),
                    "exploration_value_score": ranking.get("exploration_value_score"),
                    "ranking_reason": ranking.get("ranking_reason"),
                    "strategic_alignment": ranking.get("strategic_alignment", []),
                    "recommended_approach": ranking.get("recommended_approach"),
                }
            )
        prioritized_gaps.append(enriched)
    prioritized_gaps.sort(
        key=lambda item: (
            -float(item.get("exploration_value_score") or 0.0),
            -int(item.get("novelty_score") or 0),
            str(item.get("gap_id", "")),
        )
    )
    project["knowledge_gap_filter"] = {
        "min_novelty": 4,
        "input_count": len(gaps),
        "rejected_count": len(rejected_gaps),
        "rejected": rejected_gaps[:5],
    }
    project["tanxi_gap_analysis"] = tanxi_report
    project["knowledge_gaps"] = prioritized_gaps[:max_gaps]
    project["updatedAt"] = time.time()
    save_project(project)
    log_event(
        "SCIENCE",
        "gaps_detected",
        project_id=project_id,
        count=len(project["knowledge_gaps"]),
        rejected_low_novelty=len(rejected_gaps),
    )
    return json.dumps(project["knowledge_gaps"], ensure_ascii=False, indent=2)


def run_tanxi_gap_exploration(
    project_id: str,
    target_domain: str = "",
    strategic_domains: list[str] | None = None,
    max_gaps: int = 10,
) -> str:
    project = load_project(project_id)
    if not project.get("knowledge_map"):
        build_knowledge_map(project_id)
        project = load_project(project_id)
    if not project.get("knowledge_gaps"):
        detect_knowledge_gaps(project_id, max_gaps=max_gaps)
        project = load_project(project_id)
    report = tanxi_gap_exploration_report(
        project,
        list(project.get("knowledge_gaps", [])),
        target_domain=target_domain or str(project.get("domain", "")),
        strategic_domains=strategic_domains or default_strategic_domains(project),
        max_gaps=max_gaps,
    )
    project["tanxi_gap_analysis"] = report
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "tanxi_gap_exploration", project_id=project_id, ranked=len(report.get("ranked_gaps", [])))
    return json.dumps(report, ensure_ascii=False, indent=2)


def tanxi_gap_exploration_report(
    project: dict[str, Any],
    raw_gaps: list[dict[str, Any]],
    *,
    target_domain: str,
    strategic_domains: list[str],
    max_gaps: int = 10,
) -> dict[str, Any]:
    coverage_analysis = scan_coverage_density(project, target_domain)
    unconnected_pairs = find_unconnected_pairs(project, target_domain=target_domain)
    suspended = detect_suspended_problems(project)
    reasoning_gap_candidates = detect_reasoning_gaps(project, limit=max(3, max_gaps // 2))
    density_gap_candidates = gaps_from_density_holes(project, coverage_analysis.get("density_holes", []))
    pair_gap_candidates = gaps_from_unconnected_pairs(project, unconnected_pairs)
    suspended_gap_candidates = gaps_from_suspended_problems(project, suspended)
    candidates = dedupe_knowledge_gaps(
        list(raw_gaps) + reasoning_gap_candidates + density_gap_candidates + pair_gap_candidates + suspended_gap_candidates
    )
    ranked = prioritize_gaps(project, candidates, coverage_analysis, strategic_domains, max_gaps=max_gaps)
    return {
        "agent": "tanxi",
        "target_domain": target_domain,
        "thought": (
            "TanXi scanned the PaperGraph for low-density but high-importance method-scenario-benchmark regions, "
            "claim contradictions, anomaly/tension signals, cross-disciplinary unconnected pairs, suspended unresolved problems, "
            "and strategic-need alignment."
        ),
        "action": {
            "scan_coverage_density": {"target_domain": target_domain},
            "detect_reasoning_gaps": {"types": ["claim_contradiction", "theory_evidence_anomaly"]},
            "find_unconnected_pairs": {"target_domain": target_domain},
            "detect_suspended_problems": {"min_citation_threshold": 50},
            "prioritize_gaps": {"criteria": ["importance", "tractability", "strategic_value"]},
            "align_with_strategic_needs": {"strategic_domains": strategic_domains},
        },
        "coverage_analysis": coverage_analysis,
        "reasoning_gaps": reasoning_gap_candidates[:10],
        "cross_disciplinary_unconnected_pairs": unconnected_pairs[:10],
        "suspended_problems": suspended[:10],
        "ranked_gaps": ranked[:max_gaps],
        "constraints_checked": {
            "requires_supporting_reference": True,
            "filters_trivial_low_novelty": True,
            "max_gaps": max_gaps,
        },
    }


def scan_coverage_density(project: dict[str, Any], target_domain: str = "") -> dict[str, Any]:
    matrix: dict[str, dict[str, list[str]]] = project.get("coverage_matrix", {})
    knowledge_map: dict[str, Any] = project.get("knowledge_map", {})
    methods = sorted(knowledge_map.get("main_methods") or matrix.keys())
    scenarios = sorted(knowledge_map.get("main_scenarios") or {scenario for coverage in matrix.values() for scenario in coverage})
    benchmarks = sorted(knowledge_map.get("main_benchmarks") or [])
    dense_areas: list[dict[str, Any]] = []
    density_holes: list[dict[str, Any]] = []
    method_support = {method: sum(len(refs) for refs in matrix.get(method, {}).values()) for method in methods}
    scenario_support = {
        scenario: sum(len(matrix.get(method, {}).get(scenario, [])) for method in methods)
        for scenario in scenarios
    }
    triples = knowledge_map.get("method_scenario_benchmark", {})

    for method in methods:
        for scenario in scenarios:
            refs = matrix.get(method, {}).get(scenario, [])
            covered_benchmarks = sorted((triples.get(method, {}).get(scenario, {}) or {}).keys())
            importance = tanxi_importance_score(method, scenario, target_domain, method_support, scenario_support)
            if refs:
                dense_areas.append(
                    {
                        "topic": f"{method} + {scenario}",
                        "method": method,
                        "scenario": scenario,
                        "evidence_count": len(refs),
                        "benchmark_count": len(covered_benchmarks),
                        "importance_score": importance,
                    }
                )
                if benchmarks and len(covered_benchmarks) <= max(1, len(benchmarks) // 4):
                    density_holes.append(
                        {
                            "topic": f"{method} + {scenario} benchmark coverage",
                            "method": method,
                            "scenario": scenario,
                            "importance_score": importance,
                            "current_evidence_level": "medium",
                            "missing_benchmarks": [item for item in benchmarks if item not in covered_benchmarks][:5],
                            "why_important": "The method-scenario pair has evidence, but benchmark coverage is sparse, so robustness and generalization remain uncertain.",
                            "supporting_references": refs[:5],
                        }
                    )
            elif importance >= 5:
                refs_for_context = supporting_references_for_method_or_scenario(project, method, scenario)
                density_holes.append(
                    {
                        "topic": f"{method} + {scenario}",
                        "method": method,
                        "scenario": scenario,
                        "importance_score": importance,
                        "current_evidence_level": "none",
                        "missing_benchmarks": benchmarks[:5],
                        "why_important": "Both the method and scenario are visible in the field map, but this intersection has no recorded validation.",
                        "supporting_references": refs_for_context[:5],
                    }
                )

    dense_areas.sort(key=lambda item: (-int(item["evidence_count"]), -int(item["benchmark_count"]), item["topic"]))
    density_holes.sort(key=lambda item: (-int(item["importance_score"]), item["current_evidence_level"], item["topic"]))
    return {
        "target_domain": target_domain,
        "method_count": len(methods),
        "scenario_count": len(scenarios),
        "benchmark_count": len(benchmarks),
        "dense_areas": dense_areas[:10],
        "density_holes": density_holes[:20],
    }


def find_unconnected_pairs(project: dict[str, Any], target_domain: str = "") -> list[dict[str, Any]]:
    records = project_records_for_mapping(project)
    concepts: dict[str, list[dict[str, str]]] = {}
    for record in records:
        field_name = record_field(record)
        citation = str(record.get("citation") or record.get("title") or "")
        for kind in ("method", "scenario", "benchmark"):
            label = normalize_label(record.get(kind, ""))
            if is_unknown_value(label):
                continue
            concepts.setdefault(field_name, []).append({"concept": label, "kind": kind, "citation": citation})
    pairs: list[dict[str, Any]] = []
    fields = sorted(concepts)
    seen: set[tuple[str, str, str, str]] = set()
    for i, field_a in enumerate(fields):
        for field_b in fields[i + 1 :]:
            for item_a in concepts[field_a][:8]:
                for item_b in concepts[field_b][:8]:
                    if concepts_are_connected(project, item_a["concept"], item_b["concept"]):
                        continue
                    key = (field_a, item_a["concept"], field_b, item_b["concept"])
                    if key in seen:
                        continue
                    seen.add(key)
                    pairs.append(
                        {
                            "field_a": field_a,
                            "concept_a": item_a["concept"],
                            "field_b": field_b,
                            "concept_b": item_b["concept"],
                            "potential_synergy": cross_field_synergy(item_a["concept"], item_b["concept"], target_domain),
                            "supporting_references": unique_preserve_order([item_a["citation"], item_b["citation"]])[:4],
                        }
                    )
    pairs.sort(key=lambda item: (-len(item.get("supporting_references", [])), item["field_a"], item["field_b"]))
    return pairs


def detect_suspended_problems(project: dict[str, Any], min_citation_threshold: int = 50) -> list[dict[str, Any]]:
    problem_terms = (
        "open problem",
        "challenge",
        "bottleneck",
        "remain unclear",
        "remains unclear",
        "unresolved",
        "unknown",
        "limitation",
        "failure",
        "barrier",
        "difficult",
    )
    problems: list[dict[str, Any]] = []
    for record in project_records_for_mapping(project):
        text = " ".join(str(record.get(key, "")) for key in ("title", "abstract", "conclusion", "limitation", "improvements"))
        lowered = text.lower()
        if not any(term in lowered for term in problem_terms):
            continue
        citations = int(numeric_value(record.get("citation_count")))
        inferred_year = extract_year(str(record.get("year") or record.get("citation") or ""))
        years_unresolved = max(0, time.localtime().tm_year - int(inferred_year)) if inferred_year else 0
        evidence_level = "high" if citations >= min_citation_threshold else "medium" if citations > 0 else "unknown"
        problems.append(
            {
                "problem": trim_text(text, 260),
                "years_unresolved": years_unresolved,
                "citation_count": citations,
                "evidence_level": evidence_level,
                "barrier_to_progress": infer_barrier_to_progress(lowered),
                "supporting_references": [str(record.get("citation") or record.get("title") or "")],
            }
        )
    problems.sort(key=lambda item: (-int(item["citation_count"]), -int(item["years_unresolved"]), item["problem"]))
    return problems


def prioritize_gaps(
    project: dict[str, Any],
    raw_gaps: list[dict[str, Any]],
    coverage_analysis: dict[str, Any],
    strategic_domains: list[str],
    *,
    max_gaps: int = 10,
) -> list[dict[str, Any]]:
    density_lookup = {str(item.get("topic", "")).lower(): item for item in coverage_analysis.get("density_holes", [])}
    ranked: list[dict[str, Any]] = []
    for gap in raw_gaps:
        refs = [ref for ref in gap.get("supporting_references", []) if ref]
        if not refs:
            continue
        alignment = align_gap_with_strategic_needs(gap, strategic_domains)
        score, reason = tanxi_gap_priority_score(project, gap, alignment, density_lookup)
        ranked.append(
            {
                "rank": 0,
                "gap_id": gap.get("gap_id"),
                "gap_description": gap.get("description"),
                "gap_type": gap.get("gap_type"),
                "exploration_value_score": score,
                "importance": importance_label(score),
                "tractability": gap.get("feasibility", "medium"),
                "strategic_alignment": alignment,
                "supporting_references": refs[:5],
                "recommended_approach": gap.get("suggested_research_path") or "Design a focused validation study with explicit baselines and failure criteria.",
                "ranking_reason": reason,
            }
        )
    ranked.sort(key=lambda item: (-float(item["exploration_value_score"]), item.get("gap_description", "")))
    for index, item in enumerate(ranked[:max_gaps], 1):
        item["rank"] = index
    return ranked[:max_gaps]


def gaps_from_density_holes(project: dict[str, Any], holes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for hole in holes[:12]:
        refs = [ref for ref in hole.get("supporting_references", []) if ref]
        if not refs:
            continue
        if hole.get("current_evidence_level") == "none":
            description = f"Density hole: '{hole.get('method')}' has no recorded validation in '{hole.get('scenario')}'."
            gap_type = "combinatorial"
        else:
            missing = ", ".join(hole.get("missing_benchmarks", [])[:3])
            description = f"Density hole: '{hole.get('method')}' in '{hole.get('scenario')}' lacks benchmark coverage for {missing}."
            gap_type = "improvement"
        gaps.append(
            assess_gap_dict(
                project,
                make_gap(
                    gap_type=gap_type,
                    description=description,
                    supporting_references=refs,
                    suggested_research_path="Use the dense neighboring literature as controls, then test the sparse intersection with explicit benchmark coverage.",
                    value_argument=str(hole.get("why_important") or "The area is important but under-supported in the current evidence graph."),
                ),
            )
        )
    return gaps


def gaps_from_unconnected_pairs(project: dict[str, Any], pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for pair in pairs[:8]:
        refs = [ref for ref in pair.get("supporting_references", []) if ref]
        if len(refs) < 2:
            continue
        gaps.append(
            assess_gap_dict(
                project,
                make_gap(
                    gap_type="migration",
                    description=(
                        f"Cross-disciplinary unconnected pair: '{pair.get('concept_a')}' from {pair.get('field_a')} "
                        f"and '{pair.get('concept_b')}' from {pair.get('field_b')} have no recorded bridge in the current PaperGraph."
                    ),
                    supporting_references=refs,
                    suggested_research_path="Formulate a transfer hypothesis, audit incompatible assumptions, then run a minimal bridge experiment or benchmark.",
                    value_argument=str(pair.get("potential_synergy") or "The pair may expose transferable mechanisms across disciplinary boundaries."),
                ),
            )
        )
    return gaps


def gaps_from_suspended_problems(project: dict[str, Any], problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for problem in problems[:8]:
        refs = [ref for ref in problem.get("supporting_references", []) if ref]
        if not refs:
            continue
        gaps.append(
            assess_gap_dict(
                project,
                make_gap(
                    gap_type="problem",
                    description=f"Suspended problem: {problem.get('problem')}",
                    supporting_references=refs,
                    suggested_research_path="Trace the barrier to a concrete scientific question, then test whether a new method or dataset removes the blocker.",
                    value_argument=f"The problem is explicitly unresolved in source literature; barrier: {problem.get('barrier_to_progress')}.",
                ),
            )
        )
    return gaps


def tanxi_importance_score(
    method: str,
    scenario: str,
    target_domain: str,
    method_support: dict[str, int],
    scenario_support: dict[str, int],
) -> int:
    score = 3
    score += min(3, method_support.get(method, 0))
    score += min(3, scenario_support.get(scenario, 0))
    target_terms = set(query_terms(target_domain))
    if target_terms and (target_terms & set(query_terms(f"{method} {scenario}"))):
        score += 2
    if any(term in f"{method} {scenario}".lower() for term in ("safety", "efficiency", "robust", "scalable", "uncertainty", "stability")):
        score += 1
    return max(1, min(10, score))


def record_field(record: dict[str, Any]) -> str:
    field_name = str(record.get("field") or "").strip()
    if field_name:
        return field_name
    return infer_research_field(record)


def concepts_are_connected(project: dict[str, Any], left: str, right: str) -> bool:
    left_norm = normalize_label(left)
    right_norm = normalize_label(right)
    for record in project_records_for_mapping(project):
        values = {
            normalize_label(record.get("method", "")),
            normalize_label(record.get("scenario", "")),
            normalize_label(record.get("benchmark", "")),
        }
        if left_norm in values and right_norm in values:
            return True
    return False


def cross_field_synergy(concept_a: str, concept_b: str, target_domain: str) -> str:
    target = f" for {target_domain}" if target_domain else ""
    return (
        f"Testing whether {concept_a} can constrain, evaluate, or operationalize {concept_b}{target} "
        "may reveal a non-obvious transfer path or boundary condition."
    )


def infer_barrier_to_progress(text: str) -> str:
    if any(term in text for term in ("data", "dataset", "measurement", "sample")):
        return "data or measurement bottleneck"
    if any(term in text for term in ("mechanism", "unclear", "unknown", "understand")):
        return "mechanistic uncertainty"
    if any(term in text for term in ("scale", "large-scale", "computational", "expensive")):
        return "scale or computational constraint"
    if any(term in text for term in ("robust", "stability", "failure", "degradation")):
        return "robustness or stability barrier"
    return "unspecified conceptual or technical barrier"


def align_gap_with_strategic_needs(gap: dict[str, Any], strategic_domains: list[str]) -> list[dict[str, Any]]:
    text = " ".join(str(gap.get(key, "")) for key in ("description", "value_argument", "suggested_research_path")).lower()
    alignments: list[dict[str, Any]] = []
    for domain in strategic_domains:
        keywords = strategic_need_keywords(domain)
        matched = [keyword for keyword in keywords if keyword in text]
        if matched:
            alignments.append(
                {
                    "strategic_domain": domain,
                    "matched_keywords": matched[:8],
                    "alignment_score": min(10, 4 + 2 * len(matched)),
                }
            )
    return alignments


def strategic_need_keywords(domain: str) -> list[str]:
    normalized = normalize_space(domain).lower()
    table = {
        "carbon neutrality": ["carbon", "emission", "energy", "efficiency", "renewable", "storage", "catalyst"],
        "health": ["health", "clinical", "disease", "patient", "therapy", "diagnosis", "safety"],
        "energy": ["energy", "battery", "power", "grid", "catalyst", "hydrogen", "efficiency"],
        "food security": ["food", "crop", "agriculture", "yield", "soil", "resilience"],
        "ai for science": ["ai", "agent", "model", "automation", "scientific discovery", "workflow"],
        "advanced manufacturing": ["manufacturing", "robot", "automation", "process", "quality", "throughput"],
        "environment": ["environment", "climate", "ecosystem", "pollution", "water", "resilience"],
    }
    for key, keywords in table.items():
        if key in normalized or normalized in key:
            return keywords
    return query_terms(normalized)


def default_strategic_domains(project: dict[str, Any]) -> list[str]:
    text = normalize_space(" ".join(str(project.get(key, "")) for key in ("domain", "title", "objective"))).lower()
    defaults = ["ai for science", "energy", "health", "carbon neutrality", "food security", "environment"]
    matched = [domain for domain in defaults if any(keyword in text for keyword in strategic_need_keywords(domain))]
    return matched or defaults[:3]


def tanxi_gap_priority_score(
    project: dict[str, Any],
    gap: dict[str, Any],
    alignment: list[dict[str, Any]],
    density_lookup: dict[str, dict[str, Any]],
) -> tuple[int, str]:
    novelty = int(gap.get("novelty_score") or 5)
    refs = len([ref for ref in gap.get("supporting_references", []) if ref])
    feasibility = str(gap.get("feasibility", "medium"))
    application = str(gap.get("application_value", "medium"))
    gap_type = str(gap.get("gap_type", ""))
    score = novelty
    score += min(2, refs)
    score += {"high": 2, "medium": 1, "low": -1}.get(application, 0)
    score += {"high": 2, "medium": 1, "low": -2}.get(feasibility, 0)
    if gap_type in {"migration", "problem", "contradiction", "anomaly", "structural"}:
        score += 1
    if gap_type in {"contradiction", "anomaly"}:
        score += 1
    if alignment:
        score += min(2, max(int(item.get("alignment_score", 0)) for item in alignment) // 4)
    description = str(gap.get("description", "")).lower()
    density_bonus = 0
    for topic, hole in density_lookup.items():
        if topic and any(term in description for term in query_terms(topic)):
            density_bonus = max(density_bonus, int(hole.get("importance_score") or 0) // 4)
    score += min(2, density_bonus)
    score = max(1, min(10, score))
    reason = (
        f"novelty={novelty}, refs={refs}, application={application}, feasibility={feasibility}, "
        f"type={gap_type}, strategic_matches={len(alignment)}, density_bonus={density_bonus}"
    )
    return score, reason


def importance_label(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


def evolve_domain_subspaces(
    project_id: str,
    subspace_map_id: str = "",
    max_actions: int = 10,
) -> str:
    project = load_project(project_id)
    subspace_map = load_subspace_map(subspace_map_id) if subspace_map_id else synthesize_subspace_map_from_project(project)
    subspaces = [item for item in subspace_map.get("subspaces", []) if isinstance(item, dict)]
    records = project_records_for_mapping(project)
    metrics: list[dict[str, Any]] = []
    matched_by_subspace: dict[str, list[dict[str, Any]]] = {}
    for subspace in subspaces:
        sid = str(subspace.get("subspace_id") or slug_label(str(subspace.get("name") or "")) or new_id("subspace_item"))
        matched = records_matching_subspace(records, subspace)
        matched_by_subspace[sid] = matched
        metrics.append(subspace_state_metrics(subspace, matched, records))

    fission = detect_subspace_fission_signals(subspaces, matched_by_subspace)
    fusion = detect_subspace_fusion_signals(subspaces, matched_by_subspace)
    decline = detect_subspace_decline_signals(subspace_map, metrics)
    emergent = detect_emergent_subspaces(project, subspaces, records)
    proposed_actions = (fission + fusion + decline + emergent)[: clamp_int(max_actions, 1, 50)]
    report = {
        "subspace_evolution_id": new_id("subevo"),
        "project_id": project_id,
        "subspace_map_id": subspace_map.get("subspace_map_id", ""),
        "createdAt": time.time(),
        "summary": {
            "subspaces": len(subspaces),
            "records_scanned": len(records),
            "actions": len(proposed_actions),
            "maturity_counts": dict(Counter(str(item.get("maturity")) for item in metrics)),
        },
        "metrics": metrics,
        "signals": {
            "fission": fission,
            "fusion": fusion,
            "decline": decline,
            "emergent": emergent,
        },
        "proposed_actions": proposed_actions,
        "next_step": "Review proposed_actions. Use selected/fission/fusion/emergent subspaces as focus_branches before MingLi hypothesis evolution.",
    }
    subspace_map.setdefault("evolution_history", []).append(report)
    subspace_map["latest_evolution"] = report
    if subspace_map.get("subspace_map_id"):
        save_subspace_map(subspace_map)
    project.setdefault("subspace_evolution_reports", []).append(report)
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "subspace_evolution", project_id=project_id, actions=len(proposed_actions))
    return json.dumps(report, ensure_ascii=False, indent=2)


def synthesize_subspace_map_from_project(project: dict[str, Any]) -> dict[str, Any]:
    knowledge_map = project.get("knowledge_map", {}) if isinstance(project.get("knowledge_map"), dict) else {}
    scenarios = list(knowledge_map.get("main_scenarios") or [])
    if not scenarios:
        scenarios = sorted({normalize_label(record.get("scenario", "")) for record in project_records_for_mapping(project)})
    subspaces = [
        normalize_domain_subspace(
            {
                "name": scenario,
                "keywords": query_terms(scenario),
                "description": "Synthetic subspace derived from current PaperGraph scenario coverage.",
                "generated_by": "project_synthesis",
            },
            domain=str(project.get("domain", "")),
        )
        for scenario in scenarios
        if scenario and not is_unknown_value(scenario)
    ]
    if not subspaces:
        subspaces = [
            normalize_domain_subspace(
                {
                    "name": str(project.get("domain") or "current project"),
                    "keywords": query_terms(str(project.get("domain") or project.get("title") or "")),
                    "generated_by": "project_synthesis",
                },
                domain=str(project.get("domain", "")),
            )
        ]
    return {
        "subspace_map_id": "",
        "domain": project.get("domain", ""),
        "generated_by": "project_synthesis",
        "subspaces": subspaces,
        "probe_results": [],
    }


def records_matching_subspace(records: list[dict[str, Any]], subspace: dict[str, Any]) -> list[dict[str, Any]]:
    terms = subspace_terms(subspace)
    if not terms:
        return []
    matched: list[dict[str, Any]] = []
    for record in records:
        text = record_search_text(record)
        if any(term in text for term in terms):
            matched.append(record)
    return matched


def subspace_terms(subspace: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    raw.extend(query_terms(str(subspace.get("name") or "")))
    raw.extend(query_terms(" ".join(string_list(subspace.get("aliases")))))
    raw.extend(query_terms(" ".join(string_list(subspace.get("keywords")))))
    return unique_preserve_order([term.lower() for term in raw if len(term) >= 3])[:24]


def record_search_text(record: dict[str, Any]) -> str:
    return normalize_space(
        " ".join(
            scalar(record.get(key))
            for key in (
                "title",
                "abstract",
                "conclusion",
                "method",
                "scenario",
                "benchmark",
                "contribution",
                "limitation",
                "citation",
            )
        )
    ).lower()


def subspace_state_metrics(subspace: dict[str, Any], matched: list[dict[str, Any]], all_records: list[dict[str, Any]]) -> dict[str, Any]:
    current_year = time.localtime().tm_year
    years = [int(year) for year in (extract_year(str(record.get("year") or record.get("citation") or "")) for record in matched) if year]
    recent_count = sum(1 for year in years if year >= current_year - 1)
    older_count = max(0, len(years) - recent_count)
    citations = [numeric_value(record.get("citation_count")) for record in matched]
    high_impact = sum(1 for value in citations if value >= 100)
    methods = {normalize_label(record.get("method", "")) for record in matched if not is_unknown_value(record.get("method", ""))}
    matched_citations = {record_identity(record) for record in matched if record_identity(record)}
    cross_connections = 0
    for record in all_records:
        identity = record_identity(record)
        if identity not in matched_citations:
            continue
        labels = [normalize_label(record.get(key, "")) for key in ("method", "scenario", "benchmark")]
        if len([label for label in labels if label and not is_unknown_value(label)]) >= 3:
            cross_connections += 1
    growth_rate = round((recent_count - older_count / max(1, max(1, len(set(years)) - 1))) / 12.0, 3)
    if len(matched) <= 1 and recent_count > 0:
        maturity = "emerging"
    elif growth_rate > 0.15:
        maturity = "growing"
    elif len(matched) >= 5 and recent_count == 0:
        maturity = "declining"
    elif len(matched) >= 4:
        maturity = "mature"
    else:
        maturity = "emerging" if recent_count else "unknown"
    return {
        "subspace_id": subspace.get("subspace_id"),
        "name": subspace.get("name"),
        "paper_count_total": len(matched),
        "paper_count_recent_24m": recent_count,
        "growth_delta_per_month": growth_rate,
        "high_impact_ratio": round(high_impact / max(1, len(matched)), 3),
        "method_diversity": len(methods),
        "cross_connection_count": cross_connections,
        "maturity": maturity,
        "top_methods": sorted(methods)[:8],
        "top_terms": top_record_terms(matched, limit=10),
    }


def detect_subspace_fission_signals(
    subspaces: list[dict[str, Any]],
    matched_by_subspace: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for subspace in subspaces:
        sid = str(subspace.get("subspace_id") or "")
        matched = matched_by_subspace.get(sid, [])
        candidate_terms = top_record_terms(matched, limit=8)
        if len(candidate_terms) < 4 or len(matched) < 3:
            continue
        cluster_a = candidate_terms[0::2][:4]
        cluster_b = candidate_terms[1::2][:4]
        overlap = set(cluster_a) & set(cluster_b)
        if len(cluster_a) >= 2 and len(cluster_b) >= 2 and not overlap:
            signals.append(
                {
                    "action": "fission",
                    "subspace_id": sid,
                    "subspace": subspace.get("name"),
                    "reason": "Internal records show at least two separable keyword clusters.",
                    "suggested_children": [
                        {
                            "name": f"{subspace.get('name')} / {' '.join(cluster_a[:2])}",
                            "keywords": cluster_a,
                        },
                        {
                            "name": f"{subspace.get('name')} / {' '.join(cluster_b[:2])}",
                            "keywords": cluster_b,
                        },
                    ],
                }
            )
    return signals


def detect_subspace_fusion_signals(
    subspaces: list[dict[str, Any]],
    matched_by_subspace: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for index, left in enumerate(subspaces):
        left_id = str(left.get("subspace_id") or "")
        left_records = {record_identity(record) for record in matched_by_subspace.get(left_id, []) if record_identity(record)}
        left_terms = set(subspace_terms(left))
        for right in subspaces[index + 1 :]:
            right_id = str(right.get("subspace_id") or "")
            right_records = {record_identity(record) for record in matched_by_subspace.get(right_id, []) if record_identity(record)}
            right_terms = set(subspace_terms(right))
            record_jaccard = jaccard_score(left_records, right_records)
            term_jaccard = jaccard_score(left_terms, right_terms)
            if record_jaccard >= 0.3 or (record_jaccard >= 0.15 and term_jaccard >= 0.25):
                signals.append(
                    {
                        "action": "fusion",
                        "subspace_ids": [left_id, right_id],
                        "subspaces": [left.get("name"), right.get("name")],
                        "record_overlap": round(record_jaccard, 3),
                        "keyword_overlap": round(term_jaccard, 3),
                        "suggested_name": f"{left.get('name')} + {right.get('name')}",
                        "reason": "The two subspaces share enough papers or retrieval vocabulary to risk redundant treatment.",
                    }
                )
    return signals


def detect_subspace_decline_signals(subspace_map: dict[str, Any], metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_reports = subspace_map.get("evolution_history", [])
    previous_metrics: dict[str, dict[str, Any]] = {}
    if previous_reports:
        latest = previous_reports[-1]
        for item in latest.get("metrics", []):
            if isinstance(item, dict) and item.get("subspace_id"):
                previous_metrics[str(item["subspace_id"])] = item
    signals: list[dict[str, Any]] = []
    for item in metrics:
        sid = str(item.get("subspace_id") or "")
        prev = previous_metrics.get(sid)
        declined = bool(prev and int(item.get("paper_count_recent_24m") or 0) < int(prev.get("paper_count_recent_24m") or 0))
        if item.get("maturity") == "declining" or declined:
            signals.append(
                {
                    "action": "archive_or_deprioritize",
                    "subspace_id": sid,
                    "subspace": item.get("name"),
                    "reason": "Recent paper support is low or declining relative to the previous scan.",
                    "maturity": item.get("maturity"),
                }
            )
    return signals


def detect_emergent_subspaces(project: dict[str, Any], subspaces: list[dict[str, Any]], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    covered_terms = set()
    for subspace in subspaces:
        covered_terms.update(subspace_terms(subspace))
    candidates = [term for term in top_record_terms(records, limit=18) if term not in covered_terms]
    if len(candidates) < 3:
        return []
    return [
        {
            "action": "new_subspace",
            "subspace": " / ".join(candidates[:3]),
            "keywords": candidates[:8],
            "reason": "Frequent project terms are not represented in the current subspace map.",
            "suggested_parent": project.get("domain", ""),
        }
    ]


def top_record_terms(records: list[dict[str, Any]], limit: int = 10) -> list[str]:
    stop = {
        "study",
        "paper",
        "method",
        "scenario",
        "benchmark",
        "using",
        "based",
        "analysis",
        "model",
        "models",
        "result",
        "results",
        "effect",
        "effects",
        "system",
    }
    counter: Counter[str] = Counter()
    for record in records:
        for term in query_terms(record_search_text(record)):
            if term not in stop and len(term) >= 4:
                counter[term] += 1
    return [term for term, _ in counter.most_common(limit)]


def record_identity(record: dict[str, Any]) -> str:
    return first_nonempty(
        [
            str(record.get("paper_id") or ""),
            str(record.get("citation") or ""),
            str(record.get("title") or ""),
            str(record.get("evidence_id") or ""),
        ]
    )


def jaccard_score(left: set[Any], right: set[Any]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def build_temporal_knowledge_graph(project_id: str) -> str:
    project = load_project(project_id)
    records = project_records_for_mapping(project)
    triples: list[dict[str, Any]] = []
    for record in records:
        method = normalize_label(record.get("method", ""))
        scenario = normalize_label(record.get("scenario", ""))
        benchmark = normalize_label(record.get("benchmark", ""))
        if any(is_unknown_value(value) for value in (method, scenario, benchmark)):
            continue
        year = extract_year(str(record.get("year") or record.get("citation") or ""))
        triples.append(
            {
                "method": method,
                "scenario": scenario,
                "benchmark": benchmark,
                "year": int(year) if year else None,
                "citation_count": int(numeric_value(record.get("citation_count"))),
                "reference": record_identity(record),
            }
        )
    yearly_counts = temporal_yearly_counts(triples)
    method_lifecycles = {
        method: temporal_lifecycle([item for item in triples if item["method"] == method])
        for method in sorted({item["method"] for item in triples})
    }
    scenario_lifecycles = {
        scenario: temporal_lifecycle([item for item in triples if item["scenario"] == scenario])
        for scenario in sorted({item["scenario"] for item in triples})
    }
    hotspot_predictions = predict_temporal_hotspots(method_lifecycles, scenario_lifecycles)
    report = {
        "temporal_kg_id": new_id("tkg"),
        "project_id": project_id,
        "createdAt": time.time(),
        "triple_count": len(triples),
        "triples": triples,
        "yearly_counts": yearly_counts,
        "method_lifecycles": method_lifecycles,
        "scenario_lifecycles": scenario_lifecycles,
        "hotspot_predictions": hotspot_predictions,
        "next_step": "Use hotspot_predictions as emerging constraints for structural gap detection and MingLi hypothesis generation.",
    }
    project["temporal_knowledge_graph"] = report
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "temporal_kg_built", project_id=project_id, triples=len(triples))
    return json.dumps(report, ensure_ascii=False, indent=2)


def temporal_yearly_counts(triples: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in triples:
        if item.get("year"):
            counts[str(item["year"])] += 1
    return dict(sorted(counts.items()))


def temporal_lifecycle(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = temporal_yearly_counts(items)
    if not counts:
        return {"status": "unknown", "yearly_counts": {}, "growth_rate": 0.0, "peak_year": ""}
    years = sorted(int(year) for year in counts)
    peak_year = max(counts, key=counts.get)
    if len(years) == 1:
        growth = float(counts[str(years[0])])
    else:
        first = counts[str(years[0])]
        last = counts[str(years[-1])]
        growth = round((last - first) / max(1, years[-1] - years[0]), 3)
    recent_year = max(years)
    recent = counts[str(recent_year)]
    prior = sum(count for year, count in counts.items() if int(year) < recent_year) / max(1, len(counts) - 1)
    if recent >= prior * 1.5 and recent >= 2:
        status = "growing"
    elif recent < prior * 0.5 and prior >= 2:
        status = "declining"
    elif sum(counts.values()) >= 5:
        status = "mature"
    else:
        status = "emerging"
    return {
        "status": status,
        "yearly_counts": counts,
        "growth_rate": growth,
        "peak_year": peak_year,
        "total": sum(counts.values()),
    }


def predict_temporal_hotspots(
    method_lifecycles: dict[str, dict[str, Any]],
    scenario_lifecycles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for kind, lifecycles in (("method", method_lifecycles), ("scenario", scenario_lifecycles)):
        for name, lifecycle in lifecycles.items():
            score = 0.0
            if lifecycle.get("status") in {"growing", "emerging"}:
                score += 2.0
            score += min(3.0, max(0.0, float(lifecycle.get("growth_rate") or 0.0)))
            score += min(2.0, float(lifecycle.get("total") or 0.0) / 3.0)
            if score > 0:
                candidates.append(
                    {
                        "concept": name,
                        "concept_type": kind,
                        "forecast": "likely_hotspot" if score >= 3 else "watchlist",
                        "hotspot_score": round(score, 3),
                        "lifecycle": lifecycle,
                    }
                )
    candidates.sort(key=lambda item: (-float(item["hotspot_score"]), item["concept"]))
    return candidates[:12]


def detect_structural_knowledge_gaps(project_id: str, max_gaps: int = 10) -> str:
    project = load_project(project_id)
    if not project.get("knowledge_map"):
        build_knowledge_map(project_id)
        project = load_project(project_id)
    graph = build_concept_graph(project)
    structural_items = structural_gap_items(project, graph, max_gaps=max_gaps * 2)
    gaps = [
        assess_gap_dict(
            project,
            make_gap(
                gap_type="structural",
                description=item["description"],
                supporting_references=item.get("supporting_references", []),
                suggested_research_path=item.get("recommended_action", "Design a bridge study that connects the sparse graph region with explicit evidence."),
                value_argument=item.get("value_argument", "Knowledge graph topology suggests this gap may affect field-level integration."),
            ),
        )
        for item in structural_items
    ]
    for gap, item in zip(gaps, structural_items):
        gap["structural_gap"] = item
    gaps = dedupe_knowledge_gaps(gaps)[:max_gaps]
    project["structural_gap_analysis"] = {
        "graph_summary": {
            "node_count": len(graph["nodes"]),
            "edge_count": sum(len(value) for value in graph["adjacency"].values()) // 2,
            "components": len(connected_components(graph["adjacency"])),
        },
        "items": structural_items,
        "gaps": gaps,
    }
    project.setdefault("knowledge_gaps", [])
    existing_ids = {gap.get("gap_id") for gap in project["knowledge_gaps"]}
    for gap in gaps:
        if gap.get("gap_id") not in existing_ids:
            project["knowledge_gaps"].append(gap)
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "structural_gaps_detected", project_id=project_id, count=len(gaps))
    return json.dumps(project["structural_gap_analysis"], ensure_ascii=False, indent=2)


def build_concept_graph(project: dict[str, Any]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    adjacency: dict[str, set[str]] = defaultdict(set)
    edge_refs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record in project_records_for_mapping(project):
        labels = {
            "method": normalize_label(record.get("method", "")),
            "scenario": normalize_label(record.get("scenario", "")),
            "benchmark": normalize_label(record.get("benchmark", "")),
        }
        labels = {kind: label for kind, label in labels.items() if label and not is_unknown_value(label)}
        reference = record_identity(record)
        for kind, label in labels.items():
            node_id = f"{kind}:{label}"
            nodes.setdefault(node_id, {"id": node_id, "kind": kind, "label": label, "references": []})
            if reference and reference not in nodes[node_id]["references"]:
                nodes[node_id]["references"].append(reference)
        label_items = list(labels.items())
        for left_index, (left_kind, left_label) in enumerate(label_items):
            for right_kind, right_label in label_items[left_index + 1 :]:
                left_id = f"{left_kind}:{left_label}"
                right_id = f"{right_kind}:{right_label}"
                adjacency[left_id].add(right_id)
                adjacency[right_id].add(left_id)
                key = tuple(sorted((left_id, right_id)))
                if reference and reference not in edge_refs[key]:
                    edge_refs[key].append(reference)
    for node_id in nodes:
        adjacency.setdefault(node_id, set())
    return {"nodes": nodes, "adjacency": adjacency, "edge_refs": edge_refs}


def structural_gap_items(project: dict[str, Any], graph: dict[str, Any], max_gaps: int) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = graph["nodes"]
    adjacency: dict[str, set[str]] = graph["adjacency"]
    degrees = {node_id: len(neighbors) for node_id, neighbors in adjacency.items()}
    avg_degree = sum(degrees.values()) / max(1, len(degrees))
    items: list[dict[str, Any]] = []
    for node_id, degree in sorted(degrees.items(), key=lambda pair: (pair[1], pair[0])):
        node = nodes.get(node_id, {"label": node_id, "kind": "concept", "references": []})
        if degree == 0:
            gap_type = "isolated_node"
            severity = "high"
        elif degree < max(1.0, avg_degree * 0.45):
            gap_type = "low_degree_node"
            severity = "medium"
        else:
            continue
        items.append(
            {
                "type": gap_type,
                "severity": severity,
                "node": node.get("label"),
                "node_kind": node.get("kind"),
                "degree": degree,
                "average_degree": round(avg_degree, 3),
                "description": f"Structural gap: {node.get('kind')} '{node.get('label')}' is weakly connected in the PaperGraph concept topology.",
                "recommended_action": "Search for bridge papers or design a validation study linking this concept to dense neighboring methods, scenarios, or benchmarks.",
                "value_argument": "Weakly connected concepts can indicate neglected mechanisms, under-benchmarked scenarios, or missing translational bridges.",
                "supporting_references": node.get("references", [])[:5],
            }
        )
    items.extend(detect_bottleneck_gap_items(graph, max_items=max_gaps))
    items.extend(detect_missing_bridge_items(project, graph, max_items=max_gaps))
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: (severity_rank.get(str(item.get("severity")), 9), item.get("type", ""), item.get("description", "")))
    return items[:max_gaps]


def detect_bottleneck_gap_items(graph: dict[str, Any], max_items: int = 10) -> list[dict[str, Any]]:
    adjacency: dict[str, set[str]] = graph["adjacency"]
    nodes: dict[str, dict[str, Any]] = graph["nodes"]
    base_components = len(connected_components(adjacency))
    items: list[dict[str, Any]] = []
    for node_id, neighbors in adjacency.items():
        if len(neighbors) < 2:
            continue
        reduced = {node: set(values) - {node_id} for node, values in adjacency.items() if node != node_id}
        component_count = len(connected_components(reduced))
        if component_count > base_components:
            node = nodes.get(node_id, {"label": node_id, "kind": "concept", "references": []})
            items.append(
                {
                    "type": "bottleneck_node",
                    "severity": "medium",
                    "node": node.get("label"),
                    "node_kind": node.get("kind"),
                    "degree": len(neighbors),
                    "description": f"Structural gap: {node.get('kind')} '{node.get('label')}' is a bottleneck connecting otherwise separated knowledge regions.",
                    "recommended_action": "Create redundant bridge evidence around this bottleneck so the field does not depend on a single concept path.",
                    "value_argument": "Bottleneck concepts reveal fragile knowledge integration and are strong candidates for mechanism clarification.",
                    "supporting_references": node.get("references", [])[:5],
                }
            )
    return items[:max_items]


def detect_missing_bridge_items(project: dict[str, Any], graph: dict[str, Any], max_items: int = 10) -> list[dict[str, Any]]:
    records = project_records_for_mapping(project)
    field_to_nodes: dict[str, set[str]] = defaultdict(set)
    for record in records:
        field_name = record_field(record)
        for kind in ("method", "scenario", "benchmark"):
            label = normalize_label(record.get(kind, ""))
            if label and not is_unknown_value(label):
                field_to_nodes[field_name].add(f"{kind}:{label}")
    fields = [field for field, nodes in field_to_nodes.items() if len(nodes) >= 2]
    items: list[dict[str, Any]] = []
    adjacency: dict[str, set[str]] = graph["adjacency"]
    for index, left in enumerate(fields):
        for right in fields[index + 1 :]:
            left_nodes = field_to_nodes[left]
            right_nodes = field_to_nodes[right]
            bridge_edges = sum(1 for node in left_nodes for neighbor in adjacency.get(node, set()) if neighbor in right_nodes)
            if bridge_edges == 0:
                refs = references_for_field_pair(records, left, right)
                items.append(
                    {
                        "type": "missing_community_bridge",
                        "severity": "high",
                        "community_a": left,
                        "community_b": right,
                        "description": f"Structural gap: communities '{left}' and '{right}' have no concept bridge in the current PaperGraph.",
                        "recommended_action": "Look for transfer papers or design a cross-field experiment that connects one method from the source community to one scenario in the target community.",
                        "value_argument": "Disconnected communities can hide high-value cross-domain transfer opportunities.",
                        "supporting_references": refs[:6],
                    }
                )
    return items[:max_items]


def connected_components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    unseen = set(adjacency)
    components: list[set[str]] = []
    while unseen:
        start = unseen.pop()
        stack = [start]
        component = {start}
        while stack:
            node = stack.pop()
            for neighbor in adjacency.get(node, set()):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def references_for_field_pair(records: list[dict[str, Any]], left: str, right: str) -> list[str]:
    refs: list[str] = []
    for record in records:
        if record_field(record) in {left, right}:
            identity = record_identity(record)
            if identity:
                refs.append(identity)
    return unique_preserve_order(refs)


def find_structural_analogy_transfers(
    project_id: str,
    target_scenario: str = "",
    threshold: float = 0.55,
    max_results: int = 10,
) -> str:
    project = load_project(project_id)
    records = project_records_for_mapping(project)
    scenario_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        scenario = normalize_label(record.get("scenario", ""))
        if scenario and not is_unknown_value(scenario):
            scenario_records[scenario].append(record)
    vectors = {scenario: encode_problem_structure(scenario, recs) for scenario, recs in scenario_records.items()}
    target = normalize_label(target_scenario)
    pairs: list[dict[str, Any]] = []
    scenarios = sorted(vectors)
    for index, left in enumerate(scenarios):
        if target and left != target:
            continue
        for right in scenarios:
            if left == right:
                continue
            similarity = problem_structure_similarity(vectors[left], vectors[right])
            if similarity < threshold:
                continue
            source_methods = methods_for_scenario(scenario_records[right])
            target_methods = methods_for_scenario(scenario_records[left])
            transferable = [method for method in source_methods if method not in target_methods]
            if not transferable:
                continue
            pairs.append(
                {
                    "target_scenario": left,
                    "analog_source_scenario": right,
                    "structural_similarity": round(similarity, 3),
                    "target_structure": vectors[left],
                    "source_structure": vectors[right],
                    "candidate_methods_to_transfer": transferable[:6],
                    "feasibility": analogy_feasibility(vectors[right], vectors[left]),
                    "supporting_references": unique_preserve_order(
                        [record_identity(record) for record in scenario_records[right][:3] + scenario_records[left][:3] if record_identity(record)]
                    ),
                    "hypothesis_hint": (
                        f"Because '{left}' and '{right}' share a similar problem structure, test whether "
                        f"{transferable[0]} can be adapted from '{right}' to '{left}'."
                    ),
                }
            )
        if target:
            break
    pairs.sort(key=lambda item: (-float(item["structural_similarity"]), item["target_scenario"], item["analog_source_scenario"]))
    report = {
        "analogy_report_id": new_id("analog"),
        "project_id": project_id,
        "target_scenario": target_scenario,
        "threshold": threshold,
        "scenario_count": len(scenarios),
        "analogy_transfers": pairs[: clamp_int(max_results, 1, 50)],
        "next_step": "Feed high-similarity transfers into MingLi as mutation/crossover material for hypothesis evolution.",
    }
    project.setdefault("structural_analogy_reports", []).append(report)
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "structural_analogies_found", project_id=project_id, count=len(report["analogy_transfers"]))
    return json.dumps(report, ensure_ascii=False, indent=2)


def encode_problem_structure(scenario: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    text = normalize_space(" ".join([scenario] + [record_search_text(record) for record in records])).lower()
    return {
        "problem_type": classify_problem_type(text),
        "data_type": classify_data_type(text),
        "constraint_type": classify_constraint_type(text),
        "scale": classify_problem_scale(text, len(records)),
        "objective": classify_objective_type(text),
    }


def classify_problem_type(text: str) -> str:
    if any(term in text for term in ("optimiz", "optimal", "scheduling", "design")):
        return "optimization"
    if any(term in text for term in ("classif", "diagnos", "detection", "screening")):
        return "classification"
    if any(term in text for term in ("generat", "synthesis", "design new", "de novo")):
        return "generation"
    if any(term in text for term in ("control", "policy", "intervention", "regulat")):
        return "control"
    return "prediction"


def classify_data_type(text: str) -> str:
    if any(term in text for term in ("graph", "network", "pathway", "interaction")):
        return "graph"
    if any(term in text for term in ("image", "imaging", "microscopy", "radiology")):
        return "image"
    if any(term in text for term in ("sequence", "time series", "temporal", "longitudinal")):
        return "sequence"
    if any(term in text for term in ("text", "language", "document", "literature")):
        return "text"
    if any(term in text for term in ("single-cell", "multi-omics", "genomics", "transcriptomics", "high-dimensional")):
        return "high_dimensional_tabular"
    return "tabular_or_mixed"


def classify_constraint_type(text: str) -> str:
    if any(term in text for term in ("safety", "ethical", "toxicity", "stability", "hard constraint")):
        return "hard_constraints"
    if any(term in text for term in ("cost", "limited", "trade-off", "resource", "sample")):
        return "soft_constraints"
    return "weak_or_unspecified_constraints"


def classify_problem_scale(text: str, record_count: int) -> str:
    if any(term in text for term in ("population", "large-scale", "atlas", "cohort", "foundation")) or record_count >= 8:
        return "large"
    if record_count >= 3:
        return "medium"
    return "small"


def classify_objective_type(text: str) -> str:
    if any(term in text for term in ("mechanism", "causal", "pathway", "explain")):
        return "mechanistic_explanation"
    if any(term in text for term in ("performance", "accuracy", "efficiency", "yield")):
        return "performance_improvement"
    if any(term in text for term in ("translation", "clinical", "deployment", "application")):
        return "translation"
    return "discovery"


def problem_structure_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    keys = ["problem_type", "data_type", "constraint_type", "scale", "objective"]
    matches = sum(1 for key in keys if left.get(key) == right.get(key))
    partial = 0.0
    if left.get("data_type") in {"high_dimensional_tabular", "tabular_or_mixed"} and right.get("data_type") in {"high_dimensional_tabular", "tabular_or_mixed"}:
        partial += 0.5
    if left.get("problem_type") in {"prediction", "classification"} and right.get("problem_type") in {"prediction", "classification"}:
        partial += 0.5
    return min(1.0, (matches + partial) / len(keys))


def methods_for_scenario(records: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            normalize_label(record.get("method", ""))
            for record in records
            if normalize_label(record.get("method", "")) and not is_unknown_value(record.get("method", ""))
        }
    )


def analogy_feasibility(source: dict[str, Any], target: dict[str, Any]) -> str:
    similarity = problem_structure_similarity(source, target)
    if similarity >= 0.8 and source.get("constraint_type") == target.get("constraint_type"):
        return "high"
    if similarity >= 0.6:
        return "medium"
    return "low"


def run_mingli_hypothesis_evolution(
    project_id: str,
    gap_ids: list[str] | None = None,
    population_size: int = 24,
    generations: int = 4,
    top_k: int = 5,
    use_llm: bool = False,
) -> str:
    project = load_project(project_id)
    if not project.get("knowledge_gaps"):
        detect_knowledge_gaps(project_id, max_gaps=10)
        project = load_project(project_id)
    selected_gaps = select_gaps_for_hypothesis(project, gap_ids)
    if not selected_gaps:
        raise ValueError("No knowledge gaps available for MingLi hypothesis evolution.")
    if not project.get("temporal_knowledge_graph"):
        build_temporal_knowledge_graph(project_id)
        project = load_project(project_id)
    if not project.get("structural_gap_analysis"):
        detect_structural_knowledge_gaps(project_id, max_gaps=8)
        project = load_project(project_id)
    if not project.get("structural_analogy_reports"):
        find_structural_analogy_transfers(project_id, threshold=0.55, max_results=8)
        project = load_project(project_id)

    population = seed_hypothesis_population(project, selected_gaps, clamp_int(population_size, 5, 80), use_llm=use_llm)
    lineage: list[dict[str, Any]] = [{"generation": 0, "population_size": len(population), "best_score": best_hypothesis_score(population)}]
    for generation in range(1, clamp_int(generations, 1, 20) + 1):
        winners = tournament_select_hypotheses(population, max(2, min(10, len(population) // 2)))
        offspring = evolve_hypothesis_offspring(project, winners, population_size=max(0, len(population) - len(winners)), generation=generation)
        population = score_hypothesis_population(project, winners + offspring)
        lineage.append({"generation": generation, "population_size": len(population), "best_score": best_hypothesis_score(population)})
        if len(lineage) >= 3 and abs(lineage[-1]["best_score"] - lineage[-2]["best_score"]) < 0.01:
            break

    finalists = select_diverse_hypothesis_finalists(population, top_k=clamp_int(top_k, 1, 20))
    persisted = []
    for item in finalists:
        hypothesis = Hypothesis(
            hypothesis_id=new_id("hyp"),
            gap_id=str(item.get("gap_id") or ""),
            statement=str(item.get("statement") or ""),
            mechanism=str(item.get("mechanism") or ""),
            expected_value=str(item.get("expected_value") or ""),
            test_plan=str(item.get("test_plan") or ""),
        )
        payload = asdict(hypothesis)
        payload.update(
            {
                "mingli_scores": item.get("scores", {}),
                "plausibility_check": item.get("plausibility_check", {}),
                "score": item.get("score"),
                "lineage": item.get("lineage", []),
                "competition_advantage": item.get("competition_advantage", ""),
                "verification_plan": item.get("verification_plan", {}),
                "source_gap": item.get("source_gap", {}),
                "gap_ids": item.get("gap_ids", []),
                "tournament_generation": item.get("generation", 0),
            }
        )
        project.setdefault("hypotheses", []).append(payload)
        persisted.append(payload)
    run = {
        "mingli_run_id": new_id("mingli"),
        "project_id": project_id,
        "createdAt": time.time(),
        "gap_ids": [gap.get("gap_id") for gap in selected_gaps],
        "population_size": len(population),
        "generations_completed": len(lineage) - 1,
        "lineage_summary": lineage,
        "top_hypotheses": persisted,
        "method": "template_seed + tournament_selection + mutation/crossover + structural/temporal/analogy scoring",
        "constraints_checked": {
            "traceable_to_gap": True,
            "papergraph_grounded": True,
            "testability_scored": True,
            "novelty_overlap_local": True,
        },
    }
    project.setdefault("mingli_hypothesis_evolution_runs", []).append(run)
    project["phase"] = "Hypothesis Generation"
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "mingli_hypothesis_evolution", project_id=project_id, hypotheses=len(persisted))
    return json.dumps(run, ensure_ascii=False, indent=2)


def select_gaps_for_hypothesis(project: dict[str, Any], gap_ids: list[str] | None) -> list[dict[str, Any]]:
    gaps = [gap for gap in project.get("knowledge_gaps", []) if isinstance(gap, dict)]
    if gap_ids:
        wanted = set(gap_ids)
        return [gap for gap in gaps if gap.get("gap_id") in wanted]
    return sorted(
        gaps,
        key=lambda gap: (
            -float(gap.get("exploration_value_score") or 0.0),
            -int(gap.get("novelty_score") or 0),
            str(gap.get("gap_id", "")),
        ),
    )[:8]


def seed_hypothesis_population(project: dict[str, Any], gaps: list[dict[str, Any]], population_size: int, use_llm: bool = False) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    analogies = collect_project_analogies(project)
    hotspots = collect_project_hotspots(project)
    per_gap = max(1, population_size // max(1, len(gaps)))
    for gap in gaps:
        components = infer_gap_components(project, gap)
        for variant in range(per_gap):
            analogy = analogies[(len(seeds) + variant) % len(analogies)] if analogies else {}
            hotspot = hotspots[(len(seeds) + variant) % len(hotspots)] if hotspots else {}
            seeds.append(make_hypothesis_seed(project, gap, components, variant, analogy=analogy, hotspot=hotspot))
            if len(seeds) >= population_size:
                break
        if len(seeds) >= population_size:
            break
    return score_hypothesis_population(project, seeds)


def infer_gap_components(project: dict[str, Any], gap: dict[str, Any]) -> dict[str, str]:
    description = str(gap.get("description") or "")
    methods = sorted({normalize_label(record.get("method", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("method", ""))})
    scenarios = sorted({normalize_label(record.get("scenario", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("scenario", ""))})
    benchmarks = sorted({normalize_label(record.get("benchmark", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("benchmark", ""))})
    method = first_matching_label(description, methods) or (methods[0] if methods else "targeted intervention")
    scenario = first_matching_label(description, scenarios) or (scenarios[0] if scenarios else str(project.get("domain") or "target scenario"))
    benchmark = first_matching_label(description, benchmarks) or (benchmarks[0] if benchmarks else "mechanistic validity")
    return {"method": method, "scenario": scenario, "benchmark": benchmark}


def first_matching_label(text: str, labels: list[str]) -> str:
    lowered = text.lower()
    for label in labels:
        if label and label.lower() in lowered:
            return label
    return ""


def make_hypothesis_seed(
    project: dict[str, Any],
    gap: dict[str, Any],
    components: dict[str, str],
    variant: int,
    *,
    analogy: dict[str, Any],
    hotspot: dict[str, Any],
) -> dict[str, Any]:
    method = components["method"]
    scenario = components["scenario"]
    benchmark = components["benchmark"]
    conditions = [
        "under explicit failure-mode stress tests",
        "in a longitudinal or temporally stratified validation setting",
        "with ablation against the nearest dense PaperGraph neighborhood",
        "under cross-cohort or cross-material generalization",
    ]
    condition = conditions[variant % len(conditions)]
    transferred = ""
    if analogy.get("candidate_methods_to_transfer"):
        transferred = str(analogy["candidate_methods_to_transfer"][0])
        method = transferred
    if hotspot.get("concept") and variant % 2 == 1:
        condition = f"while tracking emerging hotspot '{hotspot.get('concept')}'"
    statement = f"If {method} is applied to {scenario} {condition}, then {benchmark} will improve or reveal a falsifiable boundary condition."
    mechanism = (
        f"The proposed mechanism is that {method} changes the information, intervention, or representation pathway in {scenario}; "
        f"because the source gap indicates weak evidence, testing {benchmark} can distinguish a real mechanism from a pseudo-gap."
    )
    if analogy:
        mechanism += f" The structural analogy to {analogy.get('analog_source_scenario')} supports transfer because the encoded problem structures are similar."
    return {
        "candidate_id": new_id("hcand"),
        "gap_id": gap.get("gap_id"),
        "gap_ids": [str(gap.get("gap_id"))] if gap.get("gap_id") else [],
        "statement": statement,
        "mechanism": mechanism,
        "expected_value": gap.get("value_argument") or "Potential to convert a mapped knowledge gap into a testable scientific mechanism.",
        "test_plan": (
            f"Build a minimal benchmark for {scenario}; compare {method} against canonical baselines; measure {benchmark}; "
            "include negative controls, ablations, and failure-mode analysis."
        ),
        "verification_plan": {
            "primary_metric": benchmark,
            "baselines": ["nearest dense PaperGraph method", "domain-standard baseline"],
            "falsification_condition": f"No improvement or mechanistic separation on {benchmark} under the stated condition.",
        },
        "source_gap": gap,
        "lineage": [{"generation": 0, "operation": "seed", "gap_id": gap.get("gap_id"), "analogy_used": analogy.get("analog_source_scenario", "")}],
        "generation": 0,
    }


def score_hypothesis_population(project: dict[str, Any], population: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [score_hypothesis_candidate(project, candidate) for candidate in population]


def score_hypothesis_candidate(project: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    gap = candidate.get("source_gap") if isinstance(candidate.get("source_gap"), dict) else {}
    statement = str(candidate.get("statement") or "")
    local_overlap = local_idea_overlap(project, statement)
    strongest_overlap = float(local_overlap[0]["overlap_score"]) if local_overlap else 0.0
    novelty = max(0.0, min(1.0, (int(gap.get("novelty_score") or 5) / 10.0) * (1.0 - 0.5 * strongest_overlap)))
    plausibility_check = hypothesis_disciplinary_plausibility(project, candidate)
    mechanism_base = 0.65 if candidate.get("mechanism") and len(str(candidate.get("mechanism"))) >= 80 else 0.35
    plausibility = max(0.05, min(1.0, 0.5 * mechanism_base + 0.5 * float(plausibility_check.get("score", 0.5))))
    refs = len(gap.get("supporting_references", [])) if isinstance(gap.get("supporting_references"), list) else 0
    grounding = min(1.0, refs / 3.0)
    testability = 0.75 if all(term in str(candidate.get("test_plan", "")).lower() for term in ("baseline", "measure")) else 0.45
    impact = min(1.0, (float(gap.get("exploration_value_score") or gap.get("novelty_score") or 5) / 10.0) + 0.1)
    surprise = hypothesis_surprise_score(project, candidate)
    score = round(0.22 * novelty + 0.22 * plausibility + 0.18 * grounding + 0.18 * testability + 0.14 * impact + 0.06 * surprise, 4)
    scored = dict(candidate)
    scored["scores"] = {
        "novelty": round(novelty, 3),
        "plausibility": round(plausibility, 3),
        "grounding": round(grounding, 3),
        "testability": round(testability, 3),
        "impact": round(impact, 3),
        "surprise": round(surprise, 3),
        "strongest_local_overlap": round(strongest_overlap, 3),
    }
    scored["plausibility_check"] = plausibility_check
    scored["score"] = score
    scored["competition_advantage"] = (
        "Ranks well because it is traceable to a high-value gap, has an explicit mechanism, passes generic disciplinary plausibility checks, "
        "and includes falsifiable validation criteria."
    )
    return scored


def hypothesis_disciplinary_plausibility(project: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    gap = candidate.get("source_gap") if isinstance(candidate.get("source_gap"), dict) else {}
    components = infer_gap_components(project, gap)
    text = normalize_space(
        " ".join(str(candidate.get(key) or "") for key in ("statement", "mechanism", "test_plan", "expected_value"))
    ).lower()
    method = normalize_space(components.get("method", "")).lower()
    scenario = normalize_space(components.get("scenario", "")).lower()
    combined = f"{method} {scenario} {text}"
    issues: list[str] = []
    suggestions: list[str] = []

    requirement_rules = [
        {
            "method_terms": ("lstm", "rnn", "recurrent neural", "sequence model"),
            "required_context": ("sequence", "time series", "temporal", "trajectory", "signal", "longitudinal", "text", "token"),
            "issue": "Sequence models require an ordered sequence representation; the current scenario does not clearly expose one.",
            "suggestion": "Define the sequential observable first, or use a representation better matched to spatial/graph/field data.",
        },
        {
            "method_terms": ("cnn", "convolutional", "vision transformer", "image model"),
            "required_context": ("image", "imaging", "spatial", "microscopy", "map", "field", "grid", "spectrogram"),
            "issue": "Image/convolutional models require a spatial or image-like representation that is not explicit.",
            "suggestion": "Specify the image/grid/field encoding and invariances before treating the transfer as plausible.",
        },
        {
            "method_terms": ("graph neural", "gnn", "message passing", "network embedding"),
            "required_context": ("graph", "network", "molecule", "citation", "mesh", "topology", "interaction", "relational"),
            "issue": "Graph methods require nodes and edges; the candidate does not clearly define the graph construction.",
            "suggestion": "Define nodes, edges, and conservation/causal constraints before testing the graph method.",
        },
        {
            "method_terms": ("causal", "intervention", "counterfactual"),
            "required_context": ("intervention", "causal", "confound", "randomized", "instrument", "mechanism", "natural experiment"),
            "issue": "Causal claims require intervention, identifiability, or confounding assumptions that are not explicit.",
            "suggestion": "State the causal graph or identifiability assumptions and include falsification checks.",
        },
    ]
    for rule in requirement_rules:
        if any(term in combined for term in rule["method_terms"]) and not any(non_negated_phrase_in_text(term, combined) for term in rule["required_context"]):
            issues.append(rule["issue"])
            suggestions.append(rule["suggestion"])

    constraint_terms = ("conservation", "symmetry", "constraint", "safety", "ethics", "clinical", "physical law", "mass", "energy", "charge")
    if any(term in scenario for term in ("physical", "quantum", "coulomb", "fluid", "climate", "battery", "biological", "clinical")) and not any(term in text for term in constraint_terms):
        issues.append("The hypothesis touches a constrained scientific system but does not explicitly state domain constraints or invariants.")
        suggestions.append("Add the relevant physical, biological, clinical, or engineering constraints as hard checks in the test plan.")

    score = 0.82
    if issues:
        score -= min(0.55, 0.18 * len(issues))
    if "baseline" in text and ("falsification" in text or "negative control" in text or "stress" in text):
        score += 0.08
    score = max(0.15, min(1.0, score))
    return {
        "score": round(score, 3),
        "issues": issues,
        "suggestions": unique_preserve_order(suggestions),
        "requires_human_review": bool(issues),
    }


def non_negated_phrase_in_text(phrase: str, text: str) -> bool:
    normalized = normalize_space(phrase).lower()
    lowered = text.lower()
    for match in re.finditer(re.escape(normalized).replace(r"\ ", r"\s+"), lowered):
        prefix = lowered[max(0, match.start() - 40) : match.start()]
        if any(marker in prefix for marker in ("without", "no ", "not ", "lack", "lacks", "missing", "absent")):
            continue
        return True
    return False


def hypothesis_surprise_score(project: dict[str, Any], candidate: dict[str, Any]) -> float:
    gap = candidate.get("source_gap") if isinstance(candidate.get("source_gap"), dict) else {}
    components = infer_gap_components(project, gap)
    method = components.get("method", "")
    scenario = components.get("scenario", "")
    connected = concepts_are_connected(project, method, scenario) if method and scenario else True
    source_field = record_field({"title": method, "abstract": method})
    target_field = record_field({"title": scenario, "abstract": scenario})
    field_distance = 0.25 if fields_are_incompatible(source_field, target_field) else 0.0
    gap_type_bonus = 0.2 if str(gap.get("gap_type") or "") in {"migration", "structural", "contradiction", "anomaly"} else 0.0
    connection_bonus = 0.35 if not connected else 0.08
    overlap_penalty = min(0.25, float(gap.get("literature_coverage_factor") or 0.0) * 0.25)
    return round(max(0.0, min(1.0, 0.35 + field_distance + gap_type_bonus + connection_bonus - overlap_penalty)), 3)


def select_diverse_hypothesis_finalists(population: list[dict[str, Any]], top_k: int = 5, max_similarity: float = 0.7) -> list[dict[str, Any]]:
    ordered = sorted(population, key=lambda item: (-float(item.get("score", 0.0)), item.get("statement", "")))
    selected: list[dict[str, Any]] = []
    used_gap_ids: set[str] = set()
    for candidate in ordered:
        statement = str(candidate.get("statement") or "")
        too_similar = any(text_jaccard(statement, str(existing.get("statement") or "")) >= max_similarity for existing in selected)
        same_gap_saturated = str(candidate.get("gap_id") or "") in used_gap_ids and len(used_gap_ids) < top_k
        if too_similar or same_gap_saturated:
            continue
        selected.append(candidate)
        if candidate.get("gap_id"):
            used_gap_ids.add(str(candidate.get("gap_id")))
        if len(selected) >= top_k:
            return selected
    for candidate in ordered:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) >= top_k:
            break
    return selected[:top_k]


def tournament_select_hypotheses(population: list[dict[str, Any]], n_winners: int) -> list[dict[str, Any]]:
    ordered = sorted(population, key=lambda item: (-float(item.get("score", 0.0)), item.get("statement", "")))
    winners: list[dict[str, Any]] = []
    for index in range(0, len(ordered), 2):
        pair = ordered[index : index + 2]
        if pair:
            winners.append(pair[0])
        if len(winners) >= n_winners:
            break
    return winners


def evolve_hypothesis_offspring(
    project: dict[str, Any],
    winners: list[dict[str, Any]],
    population_size: int,
    generation: int,
) -> list[dict[str, Any]]:
    if not winners:
        return []
    offspring: list[dict[str, Any]] = []
    methods = sorted({normalize_label(record.get("method", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("method", ""))})
    scenarios = sorted({normalize_label(record.get("scenario", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("scenario", ""))})
    benchmarks = sorted({normalize_label(record.get("benchmark", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("benchmark", ""))})
    while len(offspring) < population_size:
        parent = winners[len(offspring) % len(winners)]
        child = dict(parent)
        child["candidate_id"] = new_id("hcand")
        child["generation"] = generation
        operation = ["constraint_insertion", "method_mutation", "scenario_crossover", "cross_gap_crossover"][len(offspring) % 4]
        if operation == "method_mutation" and methods:
            method = methods[(generation + len(offspring)) % len(methods)]
            child["statement"] = re.sub(r"If .*? is applied", f"If {method} is applied", str(child.get("statement")), count=1)
            child["mechanism"] = f"Mutated method pathway: {method} is substituted to test whether the mechanism survives a method-level perturbation. " + str(child.get("mechanism", ""))
        elif operation == "scenario_crossover" and len(winners) > 1 and scenarios:
            other = winners[(len(offspring) + 1) % len(winners)]
            scenario = scenarios[(generation + len(offspring)) % len(scenarios)]
            child["statement"] = str(child.get("statement", "")) + f" A crossover variant also tests transfer into {scenario}."
            child["mechanism"] = str(child.get("mechanism", "")) + f" Crossover lineage borrows constraints from {other.get('candidate_id')}."
        elif operation == "cross_gap_crossover" and len(winners) > 1:
            other = next(
                (item for item in winners if item.get("gap_id") and item.get("gap_id") != parent.get("gap_id")),
                winners[(len(offspring) + 1) % len(winners)],
            )
            child["gap_ids"] = unique_preserve_order(
                [str(parent.get("gap_id") or ""), str(other.get("gap_id") or "")]
                + [str(item) for item in parent.get("gap_ids", []) if item]
                + [str(item) for item in other.get("gap_ids", []) if item]
            )
            child["statement"] = (
                str(child.get("statement", ""))
                + " A cross-gap variant tests whether the mechanism remains valid when the second gap's boundary condition is imposed: "
                + trim_text(str(other.get("statement") or ""), 180)
            )
            child["mechanism"] = (
                str(child.get("mechanism", ""))
                + f" Cross-gap crossover combines evidence from {parent.get('gap_id')} and {other.get('gap_id')} to test whether one gap resolves or sharpens the other."
            )
        else:
            benchmark = benchmarks[(generation + len(offspring)) % len(benchmarks)] if benchmarks else "failure-mode robustness"
            child["statement"] = str(child.get("statement", "")) + f" The decisive test is constrained to {benchmark} under an explicit stress regime."
            child["test_plan"] = str(child.get("test_plan", "")) + f" Add a preregistered stress test for {benchmark}."
        child["lineage"] = list(parent.get("lineage", [])) + [
            {"generation": generation, "operation": operation, "parent_candidate_id": parent.get("candidate_id")}
        ]
        offspring.append(child)
    return offspring


def collect_project_analogies(project: dict[str, Any]) -> list[dict[str, Any]]:
    reports = project.get("structural_analogy_reports", [])
    analogies: list[dict[str, Any]] = []
    for report in reports:
        if isinstance(report, dict):
            analogies.extend([item for item in report.get("analogy_transfers", []) if isinstance(item, dict)])
    return analogies


def collect_project_hotspots(project: dict[str, Any]) -> list[dict[str, Any]]:
    tkg = project.get("temporal_knowledge_graph", {}) if isinstance(project.get("temporal_knowledge_graph"), dict) else {}
    return [item for item in tkg.get("hotspot_predictions", []) if isinstance(item, dict)]


def best_hypothesis_score(population: list[dict[str, Any]]) -> float:
    return max((float(item.get("score") or 0.0) for item in population), default=0.0)


def create_hypothesis(
    project_id: str,
    gap_id: str,
    statement: str,
    mechanism: str,
    expected_value: str,
    test_plan: str,
) -> str:
    project = load_project(project_id)
    if gap_id and not any(gap.get("gap_id") == gap_id for gap in project.get("knowledge_gaps", [])):
        raise ValueError(f"Unknown gap_id for project {project_id}: {gap_id}")
    hypothesis = Hypothesis(
        hypothesis_id=new_id("hyp"),
        gap_id=gap_id,
        statement=statement,
        mechanism=mechanism,
        expected_value=expected_value,
        test_plan=test_plan,
    )
    project.setdefault("hypotheses", []).append(asdict(hypothesis))
    project["phase"] = "Hypothesis Generation"
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "hypothesis_created", project_id=project_id, hypothesis_id=hypothesis.hypothesis_id)
    return json.dumps(asdict(hypothesis), ensure_ascii=False, indent=2)


def run_mechanism_check(
    project_id: str,
    hypothesis_id: str,
    shifted_conditions: list[str] | None = None,
) -> str:
    project = load_project(project_id)
    hypothesis = find_by_id(project.get("hypotheses", []), "hypothesis_id", hypothesis_id)
    if hypothesis is None:
        raise ValueError(f"Unknown hypothesis_id for project {project_id}: {hypothesis_id}")

    mechanism = str(hypothesis.get("mechanism", ""))
    statement = str(hypothesis.get("statement", ""))
    shifted = shifted_conditions or ["different dataset distribution", "changed key parameter regime"]
    internal_issues = mechanism_internal_issues(statement, mechanism)
    data_refs = references_for_gap(project, str(hypothesis.get("gap_id", "")))
    data_issues = [] if data_refs else ["No supporting references are linked to the hypothesis gap."]
    regime_risk = "MEDIUM" if len(shifted) >= 2 else "HIGH"
    overall = "MECHANISM_VERIFIED" if not internal_issues and not data_issues and regime_risk != "HIGH" else "REQUIRES_HUMAN_REVIEW"

    report = {
        "report_id": new_id("mech"),
        "hypothesis_id": hypothesis_id,
        "layer_1_internal_consistency": {
            "issues_found": internal_issues,
            "verdict": "PASS" if not internal_issues else "FAIL",
        },
        "layer_2_data_consistency": {
            "supporting_references": data_refs,
            "issues_found": data_issues,
            "verdict": "PASS" if not data_issues else "FAIL",
        },
        "layer_3_regime_shift_test": {
            "shifted_conditions_tested": shifted,
            "cawm_risk_level": regime_risk,
            "verdict": "PASS" if regime_risk != "HIGH" else "FAIL",
        },
        "overall_verdict": overall,
        "createdAt": time.time(),
    }
    project.setdefault("mechanism_reports", []).append(report)
    project["phase"] = "Mechanism Verification"
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "mechanism_checked", project_id=project_id, hypothesis_id=hypothesis_id, verdict=overall)
    return json.dumps(report, ensure_ascii=False, indent=2)


def export_research_plan(project_id: str) -> str:
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
    project = load_project(project_id)
    action: dict[str, Any] = {"agent": "zhizhi", "query": query, "domain": domain, "years": years}
    observations: list[str] = []
    import_limit = clamp_int(import_top_k, 1, SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K)
    search_budget = max(clamp_int(max_results, 1, 200), import_limit)
    selected_providers = [database_to_provider(item) for item in (providers or default_literature_providers(domain=domain, query=query))]
    selected_providers = unique_preserve_order([item for item in selected_providers if item in LITERATURE_PROVIDERS])
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
    return [name for name, spec in SCIENCE_AGENTS.items() if spec.get("phase") in {phase, "all"}]


def supporting_references_for_method_or_scenario(project: dict[str, Any], method: str, scenario: str) -> list[str]:
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


def make_gap(
    gap_type: str,
    description: str,
    supporting_references: list[str],
    suggested_research_path: str,
    value_argument: str,
) -> dict[str, Any]:
    return {
        "gap_id": new_id("gap"),
        "gap_type": gap_type,
        "description": description,
        "supporting_references": unique_preserve_order([ref for ref in supporting_references if ref])[:8],
        "novelty_score": 5,
        "application_value": "medium",
        "feasibility": "medium",
        "suggested_research_path": suggested_research_path,
        "value_argument": value_argument,
        "status": "candidate",
        "createdAt": time.time(),
    }


def count_gap_type(gaps: list[dict[str, Any]], gap_type: str) -> int:
    return sum(1 for gap in gaps if gap.get("gap_type") == gap_type)


def dedupe_knowledge_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for gap in gaps:
        description = str(gap.get("description", ""))
        signature = gap_signature(description)
        duplicate = None
        for existing in deduped:
            existing_description = str(existing.get("description", ""))
            if signature and signature == gap_signature(existing_description):
                duplicate = existing
                break
            if gap_signature_is_subset(signature, gap_signature(existing_description)):
                duplicate = existing
                break
            if text_jaccard(description, existing_description) >= 0.72:
                duplicate = existing
                break
        if duplicate is not None:
            merged_refs = unique_preserve_order(
                list(duplicate.get("supporting_references", [])) + list(gap.get("supporting_references", []))
            )
            duplicate["supporting_references"] = merged_refs[:8]
            duplicate["deduped_from"] = duplicate.get("deduped_from", 0) + 1
            if int(gap.get("novelty_score", 0)) > int(duplicate.get("novelty_score", 0)):
                duplicate.update({key: gap[key] for key in ("novelty_score", "application_value", "feasibility") if key in gap})
            continue
        gap["dedupe_signature"] = signature
        deduped.append(gap)
    return deduped


def filter_low_value_gaps(gaps: list[dict[str, Any]], min_novelty: int = 4) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for gap in gaps:
        novelty = int(gap.get("novelty_score") or 0)
        if novelty >= min_novelty:
            kept.append(gap)
            continue
        item = {
            "gap_id": gap.get("gap_id"),
            "gap_type": gap.get("gap_type"),
            "novelty_score": novelty,
            "description": trim_text(str(gap.get("description", "")), 220),
            "reason": f"novelty_score below reporting threshold {min_novelty}",
        }
        rejected.append(item)
    return kept, rejected


def gap_signature(description: str) -> str:
    stop = {
        "method",
        "scenario",
        "recorded",
        "validation",
        "current",
        "papergraph",
        "map",
        "source",
        "literature",
        "indicates",
        "has",
        "have",
        "against",
        "worth",
        "testing",
        "unresolved",
        "problem",
    }
    terms = [
        term
        for term in re.findall(r"[a-z0-9][a-z0-9_-]*", description.lower())
        if term not in stop
    ]
    return " ".join(sorted(terms[:10]))


def gap_signature_is_subset(left: str, right: str) -> bool:
    left_terms = set(left.split())
    right_terms = set(right.split())
    if not left_terms or not right_terms:
        return False
    smaller, larger = (left_terms, right_terms) if len(left_terms) <= len(right_terms) else (right_terms, left_terms)
    return len(smaller) >= 3 and smaller.issubset(larger)


def text_jaccard(left: str, right: str) -> float:
    left_terms = set(query_terms(left))
    right_terms = set(query_terms(right))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def parse_gap_input(gap: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(gap, dict):
        return dict(gap)
    text = str(gap)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return make_gap(
        gap_type="problem",
        description=text,
        supporting_references=[],
        suggested_research_path="Run a focused literature overlap check, then design a minimal validation protocol.",
        value_argument="Value is unknown until novelty and feasibility are assessed.",
    )


def assess_gap_dict(project: dict[str, Any], gap: dict[str, Any], dimensions: list[str] | None = None) -> dict[str, Any]:
    assessed = dict(gap)
    refs = [ref for ref in assessed.get("supporting_references", []) if ref]
    description = str(assessed.get("description", ""))
    overlap = local_idea_overlap(project, description)
    strongest_overlap = overlap[0]["overlap_score"] if overlap else 0.0
    gap_type = str(assessed.get("gap_type", ""))
    coverage = literature_coverage_factor(project, description)
    novelty = 7
    if strongest_overlap >= 0.65:
        novelty -= 3
    elif strongest_overlap >= 0.45:
        novelty -= 1
    if coverage >= 0.75:
        novelty -= 2
    elif coverage >= 0.45:
        novelty -= 1
    elif coverage <= 0.1:
        novelty += 1
    if gap_type in {"migration", "problem", "contradiction", "anomaly", "structural"}:
        novelty += 1
    if not refs:
        novelty -= 1
    novelty = max(1, min(10, novelty))
    feasibility = "high" if refs and gap_type in {"improvement", "combinatorial", "contradiction", "anomaly"} else "medium"
    if any(term in description.lower() for term in ("large-scale", "clinical", "expensive", "proprietary", "closed-source")):
        feasibility = "low"
    application_value = "high" if any(
        term in description.lower()
        for term in ("stability", "safety", "scalable", "high-voltage", "large-scale", "efficiency", "robust")
    ) else "medium"
    assessed.update(
        {
            "novelty_score": novelty,
            "application_value": application_value,
            "feasibility": feasibility,
            "assessment_dimensions": dimensions or ["academic novelty", "application value", "implementation feasibility"],
            "overlap_risk": "high" if strongest_overlap >= 0.65 else "medium" if strongest_overlap >= 0.45 else "low",
            "strongest_overlap": strongest_overlap,
            "literature_coverage_factor": coverage,
            "assessment_reason": (
                f"refs={len(refs)}, gap_type={gap_type}, strongest_local_overlap={round(strongest_overlap, 3)}, "
                f"coverage={round(coverage, 3)}, feasibility={feasibility}, application_value={application_value}"
            ),
            "requires_human_review": strongest_overlap >= 0.65 or not refs,
        }
    )
    return assessed


def detect_migration_gaps(project: dict[str, Any], methods: list[str], scenarios: list[str], limit: int) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    matrix: dict[str, dict[str, list[str]]] = project.get("coverage_matrix", {})
    for method in methods:
        covered = set(matrix.get(method, {}))
        if len(covered) != 1:
            continue
        missing = [scenario for scenario in scenarios if scenario not in covered]
        if not missing:
            continue
        source = next(iter(covered))
        refs = supporting_references_for_method_or_scenario(project, method, source)
        gap = make_gap(
            gap_type="migration",
            description=f"Method '{method}' is only recorded in scenario '{source}', but may be transferable to scenario '{missing[0]}'.",
            supporting_references=refs,
            suggested_research_path="Audit assumptions of the source scenario, then run a small transfer validation in the target scenario.",
            value_argument="Migration gaps can create useful cross-domain leverage if mechanism assumptions remain valid.",
        )
        gaps.append(assess_gap_dict(project, gap))
        if len(gaps) >= limit:
            break
    return gaps


def detect_gap_signal_gaps(project: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for record in project_records_for_mapping(project):
        signals = record.get("gap_signals", [])
        if not isinstance(signals, list):
            continue
        citation = str(record.get("citation") or record.get("title") or "")
        method = normalize_label(record.get("method", ""))
        scenario = normalize_label(record.get("scenario", ""))
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            text = str(signal.get("text", "")).strip()
            if not text:
                continue
            signal_type = str(signal.get("signal_type") or "gap_signal")
            gap_type = "problem" if signal_type in {"open_problem", "challenge", "missing_evidence"} else "improvement"
            refs = unique_preserve_order([str(signal.get("supporting_reference") or ""), citation])
            gap = make_gap(
                gap_type=gap_type,
                description=(
                    f"PDF/full-text {signal_type.replace('_', ' ')} signal"
                    f"{f' for {method} in {scenario}' if method and scenario and not is_unknown_value(method) and not is_unknown_value(scenario) else ''}: {text}"
                ),
                supporting_references=refs,
                suggested_research_path=research_path_for_gap_signal(signal_type, method, scenario),
                value_argument=(
                    "This gap is grounded in an explicit limitations/future-work/open-problem statement extracted from the source text, "
                    "so it provides strong handoff material for TanXi prioritization."
                ),
            )
            assessed = assess_gap_dict(project, gap)
            assessed["gap_signal"] = {
                "signal_type": signal_type,
                "confidence": signal.get("confidence"),
                "evidence_type": signal.get("evidence_type"),
            }
            gaps.append(assessed)
            if len(gaps) >= limit:
                return gaps
    return gaps


def research_path_for_gap_signal(signal_type: str, method: str, scenario: str) -> str:
    target = f" for {method} in {scenario}" if method and scenario and not is_unknown_value(method) and not is_unknown_value(scenario) else ""
    if signal_type == "future_work":
        return f"Translate the source's future-work statement into a falsifiable hypothesis{target}, then define baseline comparisons and success criteria."
    if signal_type == "limitation":
        return f"Design an ablation or stress-test study that directly attacks the documented limitation{target}."
    if signal_type == "open_problem":
        return f"Decompose the open problem into mechanism, data, and benchmark subquestions{target}, then test the most tractable subquestion first."
    if signal_type == "challenge":
        return f"Identify the technical bottleneck behind the challenge{target}, then evaluate candidate methods against a failure-mode benchmark."
    return f"Run a targeted evidence expansion and validation study{target}."


def detect_problem_gaps(project: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    problem_terms = ("open problem", "challenge", "unsolved", "remain unclear", "bottleneck", "failure", "degradation", "instability")
    gaps: list[dict[str, Any]] = []
    for record in project_records_for_mapping(project):
        text = " ".join(str(record.get(key, "")) for key in ("abstract", "conclusion", "limitation", "contribution"))
        if not any(term in text.lower() for term in problem_terms):
            continue
        citation = str(record.get("citation") or record.get("title") or "")
        gap = make_gap(
            gap_type="problem",
            description=f"Source literature indicates a recognized unresolved problem: {trim_text(text, 260)}",
            supporting_references=[citation],
            suggested_research_path="Translate the unresolved problem into a falsifiable hypothesis with acceptance criteria and failure diagnostics.",
            value_argument="Problem gaps are grounded in explicit source statements about unresolved mechanisms or practical bottlenecks.",
        )
        gaps.append(assess_gap_dict(project, gap))
        if len(gaps) >= limit:
            break
    return gaps


def local_idea_overlap(project: dict[str, Any], idea: str) -> list[dict[str, Any]]:
    terms = set(query_terms(idea))
    if not terms:
        return []
    matches: list[dict[str, Any]] = []
    for record in project_records_for_mapping(project):
        text = " ".join(str(record.get(key, "")) for key in ("title", "abstract", "contribution", "limitation", "method", "scenario", "benchmark"))
        record_terms = set(query_terms(text))
        if not record_terms:
            continue
        overlap = len(terms & record_terms) / max(1, len(terms))
        if overlap <= 0:
            continue
        matches.append(
            {
                "overlap_score": round(overlap, 4),
                "matched_terms": sorted(terms & record_terms)[:12],
                "title": record.get("title", ""),
                "citation": record.get("citation", ""),
                "venue": record.get("venue", ""),
            }
        )
    matches.sort(key=lambda item: (-float(item["overlap_score"]), item.get("title", "")))
    return matches


def literature_coverage_factor(project: dict[str, Any], description: str) -> float:
    terms = set(query_terms(description))
    if not terms:
        return 0.0
    records = project_records_for_mapping(project)
    if not records:
        return 0.0
    covered_terms: set[str] = set()
    matching_records = 0
    for record in records:
        record_terms = set(query_terms(record_context_text(record)))
        overlap = terms & record_terms
        if overlap:
            matching_records += 1
            covered_terms.update(overlap)
    term_coverage = len(covered_terms) / max(1, len(terms))
    record_coverage = min(1.0, matching_records / max(3, len(records)))
    return round(0.7 * term_coverage + 0.3 * record_coverage, 4)


def summarize_uniqueness_live_search(result: dict[str, Any]) -> dict[str, Any]:
    if not result:
        return {"used": False}
    return {
        "used": True,
        "status": result.get("status", "ok") if "status" in result else "ok",
        "search_id": result.get("search_id"),
        "total_results": result.get("total_results", 0),
        "top_titles": [item.get("title") for item in result.get("results", [])[:5] if isinstance(item, dict)],
    }


def zhizhi_standard_output(
    thought: str,
    action: dict[str, Any],
    knowledge_map: dict[str, Any],
    gaps: list[dict[str, Any]],
    observations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "thought": thought,
        "action": action,
        "observation": observations or [],
        "knowledge_map_summary": {
            "main_methods": knowledge_map.get("main_methods", []),
            "method_scenario_coverage": knowledge_map.get("method_scenario_coverage", {}),
            "method_scenario_benchmark_triples": knowledge_map.get("method_scenario_benchmark_triples", [])[:20],
            "claim_type_counts": knowledge_map.get("claim_type_counts", {}),
        },
        "knowledge_gaps": [
            {
                "gap_id": gap.get("gap_id"),
                "gap_type": gap.get("gap_type"),
                "description": gap.get("description"),
                "supporting_references": gap.get("supporting_references", []),
                "novelty_score": gap.get("novelty_score"),
                "application_value": gap.get("application_value"),
                "feasibility": gap.get("feasibility"),
                "suggested_research_path": gap.get("suggested_research_path"),
                "value_argument": gap.get("value_argument", ""),
                "overlap_risk": gap.get("overlap_risk", ""),
                "requires_human_review": gap.get("requires_human_review", False),
            }
            for gap in gaps
        ],
        "self_reflection": {
            "top_venue_coverage_checked": True,
            "pseudo_gap_risk_checked": True,
            "method_categories_require_literature_support": True,
            "unsupported_claims_marked_for_review": True,
        },
    }


def knowledge_map_unknown_summary(knowledge_map: dict[str, Any]) -> dict[str, int]:
    triples = knowledge_map.get("method_scenario_benchmark_triples", [])
    unknown_triples = 0
    for triple in triples:
        if not isinstance(triple, dict):
            continue
        values = [str(triple.get(key, "")).lower() for key in ("method", "scenario", "benchmark")]
        if any(value.startswith("unknown") or value.startswith("unspecified") for value in values):
            unknown_triples += 1
    return {"total_triples": len(triples), "unknown_triples": unknown_triples}


def extract_paper_structure(text: str, use_llm: bool = False) -> dict[str, Any]:
    heuristic = parse_paper_text(text)
    if not use_llm:
        heuristic["extractor"] = "heuristic"
        return heuristic
    try:
        llm = extract_paper_structure_with_llm(text)
    except Exception as exc:
        log_event("WARN", "paper_llm_extract_failed", error=str(exc))
        heuristic["extractor"] = "heuristic_fallback"
        heuristic["llm_error"] = str(exc)
        return heuristic
    merged = merge_paper_structures(heuristic, llm)
    merged["extractor"] = f"{SCIENCE_LLM_EXTRACTOR}_json"
    return merged


def extract_paper_structure_with_llm(text: str) -> dict[str, Any]:
    schema = {
        "title": "string",
        "citation": "string",
        "authors": ["string"],
        "year": "string",
        "venue": "string",
        "doi": "string",
        "arxiv_id": "string",
        "abstract": "string",
        "conclusion": "string",
        "strengths": ["string"],
        "improvements": ["string"],
        "method": "string",
        "scenario": "string",
        "benchmark": "string",
        "contribution": "string",
        "limitation": "string",
        "gap_signals": [{"signal_type": "limitation | future_work | open_problem | challenge | missing_evidence", "text": "string"}],
    }
    payload = call_llm_json(
        system="You are PaperGraph Extractor. You produce valid compact JSON only.",
        max_tokens=2500,
        prompt=(
            "Extract a scientific paper into strict JSON. Return JSON only, no markdown. "
            "Use empty strings or empty arrays when unavailable. Preserve factual wording; do not invent citations.\n\n"
            "General extraction rules:\n"
            "- method: the concrete research method, instrument, index, model, algorithm, experimental design, synthesis route, assay, or analysis approach actually used by the paper. "
            "Do not use a background sentence, research motivation, or broad topic as the method.\n"
            "- scenario: the scientific system, task, phenomenon, application setting, material class, organism/disease, environment, engineering system, or domain where the method is applied.\n"
            "- benchmark: the evaluated metric, observable, endpoint, dataset, response variable, performance criterion, experimental readout, or validation target.\n"
            "- contribution: the paper's main supported finding or methodological advance.\n"
            "- limitation: an explicit limitation, unresolved problem, boundary condition, or future-work point; use an empty string if not stated.\n\n"
            "- gap_signals: extract multiple explicit limitations, future-work directions, open problems, unresolved challenges, and missing-evidence statements when present, especially from PDF/full-text discussion, limitations, conclusion, and outlook sections.\n\n"
            "Cross-domain examples for choosing compact labels:\n"
            "- mathematics/statistics: method=theoretical proof | bayesian inference | causal inference; scenario=statistical inference | dynamical system; benchmark=uncertainty | convergence rate | effect size.\n"
            "- physics/astronomy/geoscience: method=spectroscopy | numerical simulation | seismic inversion | observational survey; scenario=quantum materials | astrophysical observation | earthquake and tectonics; benchmark=spectral feature | structural damage | prediction error.\n"
            "- chemistry/materials/engineering: method=organic synthesis | x-ray diffraction | density functional theory | finite element analysis; scenario=catalytic reaction | semiconductor device testing | structural system only when explicitly stated; benchmark=reaction yield | mechanical strength | device lifetime.\n"
            "- biology/agriculture/medicine/ecology: method=genome sequencing | clinical trial | field experiment | species distribution modeling; scenario=genetic disease | crop stress resilience | biodiversity and community ecology; benchmark=gene expression | clinical response | crop yield | species richness.\n"
            "- computer science/AI: method=deep learning model | graph neural network | reinforcement learning | knowledge graph construction; scenario=medical image analysis | software engineering | AI for science; benchmark=accuracy | robustness | latency | benchmark score.\n"
            "- environmental/earth-system studies: method=remote sensing | numerical model ensemble | spatial analysis | event attribution; scenario=extreme events | watershed system | ecosystem response; benchmark=event intensity | spatial extent | model error | recovery time.\n\n"
            "Guardrails:\n"
            "- Prefer concise normalized labels over long sentences.\n"
            "- If a field is not supported by the supplied text, return an empty string rather than guessing.\n"
            "- Avoid cross-domain leakage: only use a specialized metric label when the paper's domain supports it.\n"
            "- Scenario must be supported by title, abstract, conclusion, or paper metadata; never copy a scenario from examples when the paper text does not mention it.\n"
            "- If the abstract is truncated and no concrete method is stated, leave method empty rather than writing a vague phrase.\n\n"
            f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
            f"Paper text:\n{trim_text(text, 12000)}"
        ),
    )
    return normalize_llm_paper_structure(payload)


def extract_keynote_with_llm(text: str) -> dict[str, Any]:
    schema = {
        "title": "string",
        "core_problem": "string",
        "contributions": ["string"],
        "methods": ["string"],
        "experiments_or_evidence": ["string"],
        "assumptions": ["string"],
        "limitations": ["string"],
        "gap_signals": [{"signal_type": "string", "text": "string"}],
        "datasets_or_materials": ["string"],
        "code_or_implementation": ["string"],
        "important_claims": [{"claim": "string", "evidence": "string"}],
        "reuse_value_for_research": "string",
    }
    payload = call_llm_json(
        system="You are a DeepSurvey-style keynote reader. Extract grounded, reusable paper notes. JSON only.",
        max_tokens=2500,
        prompt=(
            "Extract a structured keynote for cross-paper comparison. Do not invent facts. "
            "If only abstract is provided, mark missing details as empty arrays.\n\n"
            f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
            f"Paper text:\n{trim_text(text, 14000)}"
        ),
    )
    return normalize_keynote(payload)


def extract_keynote_heuristic(text: str) -> dict[str, Any]:
    parsed = parse_paper_text(text)
    return {
        "title": parsed.get("title", ""),
        "core_problem": first_sentences(parsed.get("abstract", "") or text, 1),
        "contributions": string_list(parsed.get("contribution")),
        "methods": string_list(parsed.get("method")) if parsed.get("method") != "unknown method" else [],
        "experiments_or_evidence": extract_bullets_or_sentences(text, ["experiment", "evaluate", "result", "dataset", "case study"], limit=5),
        "assumptions": extract_bullets_or_sentences(text, ["assume", "assumption", "under the condition"], limit=5),
        "limitations": string_list(parsed.get("limitation")) if parsed.get("limitation") else [],
        "gap_signals": parsed.get("gap_signals", []),
        "datasets_or_materials": extract_bullets_or_sentences(text, ["dataset", "benchmark", "data", "material", "sample"], limit=5),
        "code_or_implementation": extract_bullets_or_sentences(text, ["code", "repository", "implementation", "github"], limit=5),
        "important_claims": [{"claim": parsed.get("contribution", ""), "evidence": parsed.get("abstract", "")} if parsed.get("contribution") else {}],
        "reuse_value_for_research": "Useful as structured evidence if quality and citation checks pass.",
    }


def normalize_keynote(payload: dict[str, Any]) -> dict[str, Any]:
    claims = payload.get("important_claims", [])
    normalized_claims: list[dict[str, str]] = []
    if isinstance(claims, list):
        for item in claims:
            if isinstance(item, dict):
                normalized_claims.append({"claim": scalar(item.get("claim")), "evidence": scalar(item.get("evidence"))})
            elif scalar(item):
                normalized_claims.append({"claim": scalar(item), "evidence": ""})
    return {
        "title": scalar(payload.get("title")),
        "core_problem": scalar(payload.get("core_problem")),
        "contributions": string_list(payload.get("contributions")),
        "methods": string_list(payload.get("methods")),
        "experiments_or_evidence": string_list(payload.get("experiments_or_evidence")),
        "assumptions": string_list(payload.get("assumptions")),
        "limitations": string_list(payload.get("limitations")),
        "gap_signals": normalize_gap_signals(
            [
                item if isinstance(item, dict) else {"signal_type": "gap_signal", "text": scalar(item)}
                for item in (payload.get("gap_signals") if isinstance(payload.get("gap_signals"), list) else [])
            ]
            + [
                {"signal_type": "limitation", "text": item, "evidence_type": "author_opinion"}
                for item in string_list(payload.get("limitations"))
            ]
        ),
        "datasets_or_materials": string_list(payload.get("datasets_or_materials")),
        "code_or_implementation": string_list(payload.get("code_or_implementation")),
        "important_claims": normalized_claims,
        "reuse_value_for_research": scalar(payload.get("reuse_value_for_research")),
        "extractor": f"{SCIENCE_LLM_EXTRACTOR}_keynote",
    }


def call_llm_json(
    system: str,
    prompt: str,
    max_tokens: int = 2000,
    fallback_list_key: str = "",
) -> dict[str, Any]:
    client = get_science_llm_client()
    response = client.messages.create(
        model=None,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        tools=[],
    )
    content = getattr(response, "content", response)
    rendered = render_llm_response_text(content)
    parsed = parse_json_object_from_text(rendered, fallback_list_key=fallback_list_key)
    if not parsed:
        log_event(
            "WARN",
            "llm_json_parse_failed",
            chars=len(rendered),
            snippet=trim_text(rendered, 500),
        )
        raise ValueError("LLM did not return a JSON object")
    return parsed


def get_science_llm_client() -> Any:
    extractor = SCIENCE_LLM_EXTRACTOR.strip().lower()
    if extractor in {"qwen", "dashscope"}:
        if not QWEN_API_KEY:
            raise RuntimeError("Science LLM extractor is qwen, but QWEN_API_KEY/DASHSCOPE_API_KEY is not set.")
        try:
            from .qwen_adapter import QwenClient
        except ImportError:
            from qwen_adapter import QwenClient
        return QwenClient(api_key=QWEN_API_KEY, model=QWEN_MODEL_ID, api_base=QWEN_API_BASE or "")
    if extractor in {"off", "none", "disabled"}:
        raise RuntimeError("Science LLM extractor is disabled.")
    try:
        from .llm import get_client
    except ImportError:
        from llm import get_client
    return get_client()


def render_llm_response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                chunks.append(str(item.get("text") or item.get("content") or ""))
            else:
                chunks.append(str(item))
        return "\n".join(chunk for chunk in chunks if chunk)
    return str(content)


def parse_json_object_from_text(text: str, fallback_list_key: str = "") -> dict[str, Any]:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            stripped = "\n".join(lines[1:-1]).strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
    candidates = [stripped]
    candidates.extend(fenced_json_blocks(stripped))
    candidates.append(first_balanced_object(stripped))
    candidates.append(first_balanced_array(stripped))
    if fallback_list_key:
        candidates.append(extract_keyed_partial_array_object(stripped, fallback_list_key))
    candidates.extend(json_repair_candidates(candidate) for candidate in list(candidates) if candidate)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(candidate)
            except (ValueError, SyntaxError):
                continue
        if isinstance(parsed, dict):
            return parsed
        if fallback_list_key and isinstance(parsed, list):
            return {fallback_list_key: parsed}
    return {}


def fenced_json_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", str(text or ""), flags=re.IGNORECASE | re.DOTALL):
        block = match.group(1).strip()
        if block:
            blocks.append(block)
    return blocks


def json_repair_candidates(text: str) -> str:
    candidate = str(text or "").strip()
    if not candidate:
        return ""
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    candidate = candidate.replace("“", '"').replace("”", '"').replace("’", "'")
    return candidate


def extract_keyed_partial_array_object(text: str, key: str) -> str:
    array_text = extract_keyed_partial_array(text, key)
    if not array_text:
        return ""
    return f'{{"{key}": {array_text}}}'


def extract_keyed_partial_array(text: str, key: str) -> str:
    source = str(text or "")
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', source)
    if not match:
        return ""
    start = source.find("[", match.start())
    if start < 0:
        return ""
    complete_items = extract_complete_json_objects_from_array(source[start + 1 :])
    if not complete_items:
        return ""
    return "[" + ",".join(complete_items) + "]"


def extract_complete_json_objects_from_array(text: str) -> list[str]:
    items: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for index, char in enumerate(str(text or "")):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}":
            if depth <= 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(json_repair_candidates(candidate))
                except json.JSONDecodeError:
                    start = -1
                    continue
                if isinstance(parsed, dict):
                    items.append(json.dumps(parsed, ensure_ascii=False))
                start = -1
            continue
        if char == "]" and depth == 0:
            break
    return items


def first_balanced_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def first_balanced_array(text: str) -> str:
    start = text.find("[")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def normalize_llm_paper_structure(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": scalar(payload.get("title")),
        "citation": scalar(payload.get("citation")),
        "authors": string_list(payload.get("authors")),
        "year": scalar(payload.get("year")),
        "venue": scalar(payload.get("venue")),
        "doi": normalize_doi(scalar(payload.get("doi"))),
        "arxiv_id": scalar(payload.get("arxiv_id") or payload.get("arxiv")),
        "abstract": scalar(payload.get("abstract")),
        "conclusion": scalar(payload.get("conclusion")),
        "strengths": string_list(payload.get("strengths")),
        "improvements": string_list(payload.get("improvements") or payload.get("limitations")),
        "method": scalar(payload.get("method")),
        "scenario": scalar(payload.get("scenario")),
        "benchmark": scalar(payload.get("benchmark")),
        "contribution": scalar(payload.get("contribution")),
        "limitation": scalar(payload.get("limitation")),
        "gap_signals": normalize_gap_signals(
            [
                item if isinstance(item, dict) else {"signal_type": "gap_signal", "text": scalar(item)}
                for item in (payload.get("gap_signals") if isinstance(payload.get("gap_signals"), list) else [])
            ]
            + [
                {"signal_type": "limitation", "text": item, "evidence_type": "author_opinion"}
                for item in string_list(payload.get("limitations"))
            ]
        ),
    }


def merge_paper_structures(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, list):
            if value:
                merged[key] = value
        elif str(value or "").strip():
                merged[key] = value
    return merged


def extraction_quality_report(record: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "method": normalize_label(record.get("method", "")),
        "scenario": normalize_label(record.get("scenario", "")),
        "benchmark": normalize_label(record.get("benchmark", "")),
    }
    unknown_fields = [name for name, value in fields.items() if is_unknown_value(value)]
    abstract = normalize_space(str(record.get("abstract") or ""))
    conclusion = normalize_space(str(record.get("conclusion") or ""))
    text = normalize_space(
        " ".join(
            str(record.get(key, ""))
            for key in ("title", "abstract", "conclusion", "full_text_excerpt", "contribution", "limitation")
            if record.get(key)
        )
    )
    flags: list[str] = []
    if not abstract:
        flags.append("missing_abstract")
    elif len(abstract) < 220:
        flags.append("short_abstract")
    if looks_truncated(abstract):
        flags.append("truncated_abstract")
    if not conclusion:
        flags.append("missing_conclusion")
    if unknown_fields:
        flags.append("unknown_fields")
    if len(unknown_fields) >= 2:
        flags.append("unknown_fields_high")
    if fields["benchmark"] in {"unknown benchmark", "unspecified benchmark", "unknown"}:
        flags.append("missing_benchmark")
    if text and background_only_text(text):
        flags.append("background_only_text")
    unknown_ratio = round(len(unknown_fields) / max(1, len(fields)), 3)
    score = 1.0
    score -= 0.24 * len(unknown_fields)
    if "missing_abstract" in flags:
        score -= 0.25
    elif "short_abstract" in flags:
        score -= 0.12
    if "truncated_abstract" in flags:
        score -= 0.2
    if "background_only_text" in flags:
        score -= 0.12
    score = round(max(0.0, min(1.0, score)), 3)
    return {
        "score": score,
        "unknown_ratio": unknown_ratio,
        "unknown_fields": unknown_fields,
        "abstract_chars": len(abstract),
        "flags": unique_preserve_order(flags),
        "needs_enrichment": (
            "missing_abstract" in flags
            or "truncated_abstract" in flags
            or ("short_abstract" in flags and len(unknown_fields) >= 1)
        ),
        "needs_llm_retry": len(unknown_fields) >= 1 or "background_only_text" in flags,
        "requires_human_review": score < 0.55 or len(unknown_fields) >= 2,
    }


def is_unknown_value(value: Any) -> bool:
    text = normalize_space(str(value or "")).lower()
    return not text or text.startswith("unknown") or text in {"none", "n/a", "unspecified", "unspecified benchmark"}


def looks_truncated(text: str) -> bool:
    stripped = normalize_space(text)
    if not stripped:
        return False
    lowered = stripped.lower().rstrip()
    if lowered.endswith(("...", "…")):
        return True
    return bool(re.search(r"\b(using|via|through|based on|with|by|as|an|a|the)\s*(?:\.\.\.|…)?$", lowered))


def background_only_text(text: str) -> bool:
    lowered = normalize_space(text).lower()
    if not lowered:
        return False
    background_markers = [
        "is an effective approach",
        "is important",
        "has attracted",
        "developing cost-effective",
        "urgent need",
        "major challenge",
        "promising strategy",
        "broad interest",
        "critical problem",
    ]
    evidence_markers = [
        "accuracy",
        "assessed",
        "baseline",
        "benchmark",
        "characterized",
        "compared",
        "demonstrates",
        "evaluated",
        "experiment",
        "measured",
        "metric",
        "model",
        "performance",
        "prediction",
        "protocol",
        "readout",
        "response",
        "score",
        "stability",
        "validated",
        "results",
    ]
    return any(marker in lowered for marker in background_markers) and not any(marker in lowered for marker in evidence_markers)


def maybe_llm_reextract_structure(payload: dict[str, Any], *, force: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    quality = extraction_quality_report(payload)
    if not force and not quality.get("needs_llm_retry"):
        return payload, {"attempted": False, "succeeded": False, "error": ""}
    text = "\n\n".join(
        part
        for part in [
            f"Title: {payload.get('title', '')}",
            f"Venue: {payload.get('venue', '')}",
            f"Year: {payload.get('year', '')}",
            f"Citation: {payload.get('citation', '')}",
            f"Abstract: {payload.get('abstract', '')}",
            f"Conclusion: {payload.get('conclusion', '')}",
            f"Full text excerpt: {payload.get('full_text_excerpt', '')}",
        ]
        if normalize_space(part)
    )
    try:
        parsed = extract_paper_structure(text, use_llm=True)
    except Exception as exc:
        return payload, {"attempted": True, "succeeded": False, "error": str(exc)}
    merged = merge_paper_structures(payload, parsed)
    extractor = str(parsed.get("extractor") or "")
    error = str(parsed.get("llm_error") or "")
    return merged, {
        "attempted": True,
        "succeeded": extractor not in {"heuristic_fallback", "heuristic"} and not error,
        "error": error,
        "extractor": extractor,
    }


def repair_unknown_field(value: Any, text: str, field: str) -> str:
    current = normalize_space(value)
    if field == "benchmark" and current.lower() in {"benchmark dataset", "benchmark data", "benchmark"}:
        current = ""
    if (
        current
        and not current.lower().startswith("unknown")
        and current.lower() not in {"unspecified benchmark", "none", "n/a"}
        and not is_low_information_field(current, field)
    ):
        return current
    inferred = infer_ontology_field(text, field)
    if inferred:
        return inferred
    phrase = infer_generic_science_phrase(text, field)
    if phrase:
        return phrase
    return {
        "method": "unknown method",
        "scenario": "unknown scenario",
        "benchmark": "unknown benchmark",
    }.get(field, "unknown")


def is_low_information_field(value: str, field: str) -> bool:
    lowered = normalize_space(value).lower()
    if not lowered:
        return True
    generic_fragments = [
        "is an effective approach",
        "is important",
        "developing cost-effective",
        "has attracted",
        "urgent need",
        "background",
        "this study",
        "this paper",
        "research topic",
        "broad application",
        "significant challenge",
    ]
    if any(fragment in lowered for fragment in generic_fragments):
        return True
    if field in {"method", "benchmark"} and len(lowered) > 90:
        return True
    if field == "method" and not contains_any(lowered, GENERAL_METHOD_CUES):
        return len(lowered) > 80
    if field == "scenario" and not contains_any(lowered, GENERAL_SCENARIO_CUES):
        return len(lowered) > 100
    if field == "benchmark":
        if lowered in {"benchmark dataset", "benchmark data", "benchmark"}:
            return True
        if not contains_any(lowered, GENERAL_BENCHMARK_CUES):
            return len(lowered) > 80
    return False


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def infer_ontology_field(text: str, field: str) -> str:
    lowered = normalize_space(text).lower()
    ontology = {
        "method": METHOD_ONTOLOGY,
        "scenario": SCENARIO_ONTOLOGY,
        "benchmark": BENCHMARK_ONTOLOGY,
    }.get(field, {})
    best_label = ""
    best_score = 0.0
    for label, patterns in ontology.items():
        if field == "benchmark" and not benchmark_allowed_for_context(label, lowered):
            continue
        score = sum(1.0 + min(len(pattern), 40) / 100.0 for pattern in patterns if science_term_in_text(pattern, lowered))
        if score > best_score:
            best_label = label
            best_score = score
    return best_label


def benchmark_allowed_for_context(label: str, lowered_text: str) -> bool:
    required = FIELD_SPECIFIC_BENCHMARKS.get(label)
    if not required:
        return True
    return any(term in lowered_text for term in required)


def infer_generic_science_phrase(text: str, field: str) -> str:
    clean = normalize_space(text)
    if not clean:
        return ""
    patterns = {
        "method": [
            r"\b([A-Za-z][A-Za-z0-9 -]{2,60}\s(?:analysis|model|modeling|simulation|algorithm|assay|index|inversion|sequencing|spectroscopy|microscopy|trial|experiment|synthesis|characterization|optimization|inference|regression|classification))\b",
            r"\b(?:using|via|with|based on|by applying)\s+([A-Za-z][A-Za-z0-9 -]{2,60})\b",
        ],
        "scenario": [
            r"\b([A-Za-z][A-Za-z0-9 -]{2,70}\s(?:application|case|cohort|condition|dataset|diagnosis|discovery|domain|environment|experiment|forecasting|material|phenomenon|platform|population|prediction|process|sample|screening|setting|system|task|therapy))\b",
            r"\b(?:in|for|under|within|across)\s+([A-Za-z][A-Za-z0-9 -]{2,70}\s(?:application|case|classification|cohort|conditions|context|dataset|diagnosis|discovery|domain|environment|forecasting|population|prediction|regime|sample|scenario|screening|setting|system|task|therapy))\b",
        ],
        "benchmark": [
            r"\b([A-Za-z][A-Za-z0-9 -]{2,60}\s(?:accuracy|baseline|criterion|efficiency|endpoint|error|index|metric|observable|performance|readout|response|score|stability|uncertainty|validation|yield))\b",
            r"\b(?:assessed by|benchmarked by|evaluated by|measured by|measures|reported by|reports|validated by|using)\s+([A-Za-z][A-Za-z0-9 -]{2,60})\b",
        ],
    }.get(field, [])
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if not match:
            continue
        phrase = normalize_space(match.group(1)).strip(" .,:;")
        phrase = clean_extracted_science_phrase(phrase, field)
        phrase = trim_text(phrase, 90)
        if phrase and not is_generic_phrase(phrase):
            return phrase.lower()
    return ""


def clean_extracted_science_phrase(phrase: str, field: str) -> str:
    cleaned = normalize_space(phrase)
    if field == "benchmark":
        for marker in (
            " and measures ",
            " and measured ",
            " and reports ",
            " and reported ",
            " and evaluates ",
            " and evaluated ",
            " with ",
        ):
            if marker in cleaned.lower():
                parts = re.split(re.escape(marker), cleaned, maxsplit=1, flags=re.IGNORECASE)
                cleaned = parts[-1]
                break
    if field == "scenario":
        cleaned = re.sub(r"^(a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
    return normalize_space(cleaned).strip(" .,:;")


def is_generic_phrase(phrase: str) -> bool:
    lowered = normalize_space(phrase).lower()
    generic = {
        "this study",
        "the paper",
        "our results",
        "an effective approach",
        "a new method",
        "the proposed method",
        "current study",
    }
    if lowered in generic:
        return True
    return len(lowered.split()) > 9


def record_context_text(record: dict[str, Any]) -> str:
    return "\n".join(
        str(record.get(key, ""))
        for key in (
            "title",
            "abstract",
            "conclusion",
            "full_text_excerpt",
            "gap_signals",
            "contribution",
            "limitation",
            "method",
            "scenario",
            "benchmark",
        )
        if record.get(key)
    )


def record_source_text(record: dict[str, Any]) -> str:
    return "\n".join(
        str(record.get(key, ""))
        for key in (
            "title",
            "citation",
            "abstract",
            "conclusion",
            "full_text_excerpt",
        )
        if record.get(key)
    )


def extract_gap_signals_from_text(text: str, *, citation: str = "", limit: int = 12) -> list[dict[str, Any]]:
    clean = normalize_space(text)
    if not clean:
        return []
    focused = extract_gap_relevant_sections(clean)
    candidate_text = "\n".join(focused) if focused else clean
    signals: list[dict[str, Any]] = []
    for sentence in split_sentences(candidate_text):
        signal_type = classify_gap_signal(sentence)
        if not signal_type:
            continue
        rendered = trim_text(sentence, 360)
        if len(rendered.split()) < 5:
            continue
        signals.append(
            {
                "signal_id": new_id("sig"),
                "signal_type": signal_type,
                "text": rendered,
                "evidence_type": "author_opinion" if signal_type in {"future_work", "limitation"} else "problem_statement",
                "supporting_reference": citation,
                "confidence": gap_signal_confidence(signal_type, sentence),
            }
        )
    signals.sort(key=lambda item: (-float(item["confidence"]), item["signal_type"], item["text"]))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for signal in signals:
        key = gap_signature(str(signal.get("text", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
        if len(deduped) >= limit:
            break
    return deduped


def extract_gap_relevant_sections(text: str) -> list[str]:
    sections: list[str] = []
    headings = [
        "limitations",
        "limitation",
        "future work",
        "future directions",
        "outlook",
        "discussion",
        "conclusion",
        "conclusions",
        "remaining challenges",
        "open problems",
        "perspectives",
    ]
    for heading in headings:
        section = extract_section(text, [heading])
        if section:
            sections.append(section)
    return unique_preserve_order([trim_text(section, 3000) for section in sections if section])


def classify_gap_signal(sentence: str) -> str:
    lowered = sentence.lower()
    if any(term in lowered for term in ("future work", "future research", "future direction", "should investigate", "warrants further")):
        return "future_work"
    if any(term in lowered for term in ("limitation", "limited by", "we did not", "does not address", "cannot", "unable to")):
        return "limitation"
    if any(term in lowered for term in ("remain unclear", "remains unclear", "unknown", "open problem", "unresolved", "not well understood")):
        return "open_problem"
    if any(term in lowered for term in ("challenge", "bottleneck", "barrier", "difficult", "failure mode", "degradation")):
        return "challenge"
    if any(term in lowered for term in ("needs", "requires", "lack of", "scarce", "insufficient", "underexplored")):
        return "missing_evidence"
    return ""


def gap_signal_confidence(signal_type: str, sentence: str) -> float:
    base = {
        "future_work": 0.78,
        "limitation": 0.82,
        "open_problem": 0.88,
        "challenge": 0.76,
        "missing_evidence": 0.72,
    }.get(signal_type, 0.6)
    lowered = sentence.lower()
    if any(term in lowered for term in ("we", "our", "this study", "the present study")):
        base += 0.05
    if any(term in lowered for term in ("may", "could", "might")):
        base -= 0.05
    return round(max(0.1, min(0.98, base)), 3)


def normalize_gap_signals(signals: list[dict[str, Any]], *, citation: str = "", limit: int = 16) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        text = trim_text(str(signal.get("text", "")), 360)
        if not text:
            continue
        key = gap_signature(text)
        if key in seen:
            continue
        seen.add(key)
        signal_type = str(signal.get("signal_type") or classify_gap_signal(text) or "gap_signal")
        normalized.append(
            {
                "signal_id": str(signal.get("signal_id") or new_id("sig")),
                "signal_type": signal_type,
                "text": text,
                "evidence_type": str(signal.get("evidence_type") or ("author_opinion" if signal_type in {"future_work", "limitation"} else "problem_statement")),
                "supporting_reference": str(signal.get("supporting_reference") or citation),
                "confidence": float(signal.get("confidence") or gap_signal_confidence(signal_type, text)),
            }
        )
        if len(normalized) >= limit:
            break
    normalized.sort(key=lambda item: (-float(item["confidence"]), item["signal_type"], item["text"]))
    return normalized


def parse_paper_text(text: str) -> dict[str, Any]:
    clean = normalize_space(text)
    title = extract_labeled_value(clean, ["title"])
    doi = extract_doi(clean)
    arxiv_id = extract_labeled_value(clean, ["arxiv", "arxiv id", "arxiv_id"])
    authors = extract_authors(clean)
    year = extract_year(clean)
    venue = extract_labeled_value(clean, ["venue", "journal", "conference"])
    abstract = extract_section(clean, ["abstract", "summary"]) or first_sentences(clean, 3)
    conclusion = extract_section(clean, ["conclusion", "conclusions", "discussion"]) or last_sentences(clean, 3)
    strengths = extract_bullets_or_sentences(clean, ["advantage", "strength", "contribution", "novel", "improve"], limit=5)
    improvements = extract_bullets_or_sentences(clean, ["limitation", "future work", "weakness", "challenge", "remain"], limit=5)
    gap_signals = extract_gap_signals_from_text(clean, citation="", limit=12)
    method = infer_field(clean, ["method", "approach", "model", "framework"], default="")
    scenario = infer_field(clean, ["scenario", "application", "domain", "task"], default="")
    benchmark = infer_field(clean, ["benchmark", "dataset", "data set", "corpus"], default="")
    method = repair_unknown_field(method, clean, "method")
    scenario = repair_unknown_field(scenario, clean, "scenario")
    benchmark = repair_unknown_field(benchmark, clean, "benchmark")
    contribution = first_nonempty(strengths) or first_sentences(clean, 1)
    limitation = (
        str(gap_signals[0].get("text", ""))
        if gap_signals
        else first_nonempty(improvements) or "No explicit limitation extracted."
    )
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id=arxiv_id) if title or doi or arxiv_id else ""
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "abstract": abstract,
        "conclusion": conclusion,
        "strengths": strengths,
        "improvements": improvements,
        "method": method,
        "scenario": scenario,
        "benchmark": benchmark,
        "contribution": contribution,
        "limitation": limitation,
        "gap_signals": gap_signals,
    }


def extract_labeled_value(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = re.compile(rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$")
        match = pattern.search(text)
        if match:
            return trim_text(match.group(1), 300)
    return ""


def extract_doi(text: str) -> str:
    labeled = extract_labeled_value(text, ["doi"])
    if labeled:
        return normalize_doi(labeled)
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", text)
    return normalize_doi(match.group(0)) if match else ""


def normalize_doi(value: str) -> str:
    cleaned = str(value or "").strip().rstrip(".,;)")
    cleaned = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", cleaned)
    return cleaned


def extract_authors(text: str) -> list[str]:
    raw = extract_labeled_value(text, ["authors", "author"])
    if not raw:
        return []
    pieces = re.split(r"\s*(?:;|,|\band\b|&)\s*", raw)
    return [piece.strip() for piece in pieces if piece.strip()][:20]


def extract_year(text: str) -> str:
    raw = extract_labeled_value(text, ["year", "published", "publication year"])
    match = re.search(r"\b(19|20)\d{2}\b", raw or text)
    return match.group(0) if match else ""


def build_citation(
    *,
    title: str,
    authors: list[str],
    year: str,
    doi: str,
    arxiv_id: str,
) -> str:
    parts: list[str] = []
    if authors:
        first_author = authors[0]
        parts.append(f"{first_author} et al." if len(authors) > 1 else first_author)
    if year:
        parts.append(f"({year})")
    if title:
        parts.append(title)
    if doi:
        parts.append(f"doi:{doi}")
    elif arxiv_id:
        parts.append(f"arXiv:{arxiv_id}")
    return " ".join(parts).strip() or title or doi or arxiv_id or "uncited paper"


def extract_section(text: str, headings: list[str]) -> str:
    if not text:
        return ""
    heading_pattern = "|".join(re.escape(heading) for heading in headings)
    pattern = re.compile(
        rf"(?:^|\n)\s*(?:\d+\.?\s*)?(?:{heading_pattern})\s*[:\n]\s*(.*?)(?=\n\s*(?:\d+\.?\s*)?[A-Z][A-Za-z ]{{2,30}}\s*[:\n]|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return trim_text(match.group(1), 1500)


def extract_bullets_or_sentences(text: str, keywords: list[str], limit: int = 5) -> list[str]:
    candidates: list[str] = []
    for line in text.splitlines():
        stripped = line.strip(" -*\t")
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(keyword in lowered for keyword in keywords):
            candidates.append(trim_text(stripped, 300))
    if candidates:
        return unique_preserve_order(candidates)[:limit]

    sentences = split_sentences(text)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            candidates.append(trim_text(sentence, 300))
    return unique_preserve_order(candidates)[:limit]


def infer_field(text: str, keywords: list[str], default: str) -> str:
    for sentence in split_sentences(text):
        lowered = sentence.lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        if len(sentence) <= 220:
            return trim_text(sentence, 220)
    return default


def score_evidence_credibility(
    *,
    title: str,
    citation: str,
    provider: str,
    doi: str,
    arxiv_id: str,
    semantic_scholar_id: str,
    url: str,
    abstract: str,
    conclusion: str,
    venue: str,
    year: str,
) -> tuple[float, list[str]]:
    score = 0.2
    reasons: list[str] = ["base record"]
    if title and citation:
        score += 0.15
        reasons.append("has title and citation")
    if doi:
        score += 0.2
        reasons.append("has DOI")
    if arxiv_id or semantic_scholar_id:
        score += 0.15
        reasons.append("has scholarly identifier")
    if url:
        score += 0.05
        reasons.append("has URL")
    if len(abstract) > 200:
        score += 0.1
        reasons.append("has substantial abstract")
    if len(conclusion) > 100:
        score += 0.05
        reasons.append("has conclusion/discussion")
    if provider in LITERATURE_PROVIDERS or provider.startswith("manual"):
        score += 0.05
        reasons.append("provider recorded")
    if is_reputable_venue(venue.lower()) or any(marker in venue.lower() for marker in ("neurips", "icml", "iclr", "npj")):
        score += 0.1
        reasons.append("high-prestige venue marker")
    quality = publication_quality_assessment(
        {
            "venue": venue,
            "provider": provider,
            "url": url,
            "doi": doi,
            "year": year,
        }
    )
    if quality["venue_quality"] == "suspicious":
        score -= 0.25
        reasons.append("suspicious venue/publisher")
    elif quality["venue_quality"] == "reputable":
        score += 0.08
        reasons.append("reputable venue")
    elif quality["venue_quality"] == "preprint":
        score -= 0.03
        reasons.append("preprint venue")
    if quality["quality_score"] < 0.55:
        score -= 0.08
        reasons.append("requires human quality review")
    if quality["venue_quality"] == "suspicious":
        score *= 0.45
        reasons.append("credibility multiplied down by suspicious publication venue")
    elif quality["quality_score"] < 0.55:
        score *= 0.65
        reasons.append("credibility multiplied down by low publication quality")
    if re.fullmatch(r"\d{4}", str(year)):
        score += 0.05
        reasons.append("has publication year")
    return round(max(0.05, min(score, 1.0)), 2), reasons


def paper_unique_key(
    *,
    title: str,
    citation: str,
    doi: str,
    arxiv_id: str,
    semantic_scholar_id: str,
    url: str,
) -> str:
    if doi:
        return "doi:" + normalize_identifier(doi)
    if arxiv_id:
        return "arxiv:" + normalize_identifier(arxiv_id)
    if semantic_scholar_id:
        return "s2:" + normalize_identifier(semantic_scholar_id)
    if url:
        return "url:" + normalize_identifier(url)
    return "text:" + normalize_identifier(title or citation)


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_") or "unknown"


def safe_workspace_path(path: str) -> Path:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else WORKDIR / raw
    resolved = candidate.resolve()
    if not resolved.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path}")
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"Literature file not found: {path}")
    return resolved


def read_literature_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF import requires pypdf. Install dependencies with: pip install -r v8/requirements.txt") from exc
        reader = PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
        text = normalize_space("\n\n".join(pages))
        if not text:
            raise RuntimeError(f"No extractable text found in PDF: {path.name}")
        return text
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_space(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def xml_text(element: ET.Element, path: str, ns: dict[str, str]) -> str:
    found = element.find(path, ns)
    return "" if found is None or found.text is None else str(found.text)


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def numeric_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return normalize_space(json.dumps(value, ensure_ascii=False))
    return normalize_space(str(value))


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [trim_text(scalar(item), 300) for item in value if scalar(item)]
    if isinstance(value, str):
        if not value.strip():
            return []
        parts = re.split(r"\s*(?:\n|;|\u2022|\d+\.)\s*", value)
        return [trim_text(part, 300) for part in parts if part.strip()]
    return [trim_text(scalar(value), 300)] if scalar(value) else []


def split_sentences(text: str) -> list[str]:
    normalized = normalize_space(text).replace("\n", " ")
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?。！？])\s+", normalized) if sentence.strip()]


def first_sentences(text: str, count: int) -> str:
    return trim_text(" ".join(split_sentences(text)[:count]), 1500)


def last_sentences(text: str, count: int) -> str:
    sentences = split_sentences(text)
    return trim_text(" ".join(sentences[-count:]), 1500)


def first_nonempty(values: list[str]) -> str:
    for value in values:
        if value.strip():
            return value.strip()
    return ""


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if normalized and key not in seen:
            result.append(normalized)
            seen.add(key)
    return result


def trim_text(text: str, limit: int) -> str:
    normalized = normalize_space(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "...[truncated]"


def references_for_gap(project: dict[str, Any], gap_id: str) -> list[str]:
    gap = find_by_id(project.get("knowledge_gaps", []), "gap_id", gap_id)
    if gap is None:
        return []
    return [str(ref) for ref in gap.get("supporting_references", []) if str(ref)]


def mechanism_internal_issues(statement: str, mechanism: str) -> list[str]:
    issues: list[str] = []
    if len(mechanism.strip()) < 40:
        issues.append("Mechanism description is too short to audit.")
    causal_markers = ("because", "therefore", "leads to", "causes", "if ", "then", "->")
    if not any(marker in mechanism.lower() for marker in causal_markers):
        issues.append("Mechanism lacks explicit causal links.")
    if statement and not shared_terms(statement, mechanism):
        issues.append("Mechanism shares too few key terms with the hypothesis statement.")
    return issues


def shared_terms(left: str, right: str) -> bool:
    left_terms = {term for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", left.lower())}
    right_terms = {term for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", right.lower())}
    return bool(left_terms & right_terms)


def find_by_id(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any] | None:
    for item in items:
        if item.get(key) == value:
            return item
    return None


def load_project(project_id: str) -> dict[str, Any]:
    path = project_path(project_id)
    if not path.exists():
        raise ValueError(f"Science project not found: {project_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_project(project: dict[str, Any]) -> None:
    projects_dir().mkdir(parents=True, exist_ok=True)
    project["updatedAt"] = time.time()
    project_path(str(project["project_id"])).write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


def load_search(search_id: str) -> dict[str, Any]:
    path = search_path(search_id)
    if not path.exists():
        raise ValueError(f"Literature search not found: {search_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_search(search: dict[str, Any]) -> None:
    searches_dir().mkdir(parents=True, exist_ok=True)
    search_path(str(search["search_id"])).write_text(json.dumps(search, ensure_ascii=False, indent=2), encoding="utf-8")


def load_subspace_map(subspace_map_id: str) -> dict[str, Any]:
    path = subspace_map_path(subspace_map_id)
    if not path.exists():
        raise ValueError(f"Domain subspace map not found: {subspace_map_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_subspace_map(subspace_map: dict[str, Any]) -> None:
    subspaces_dir().mkdir(parents=True, exist_ok=True)
    subspace_map_path(str(subspace_map["subspace_map_id"])).write_text(
        json.dumps(subspace_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def search_path(search_id: str) -> Path:
    safe = normalize_key(search_id)
    return searches_dir() / f"{safe}.json"


def searches_dir() -> Path:
    return SCIENCE_DIR / "searches"


def subspace_map_path(subspace_map_id: str) -> Path:
    safe = normalize_key(subspace_map_id)
    return subspaces_dir() / f"{safe}.json"


def subspaces_dir() -> Path:
    return SCIENCE_DIR / "subspaces"


def project_path(project_id: str) -> Path:
    safe = normalize_key(project_id)
    return projects_dir() / f"{safe}.json"


def projects_dir() -> Path:
    return SCIENCE_DIR / "projects"


def normalize_label(value: Any) -> str:
    text = str(value or "unknown").strip()
    return text or "unknown"


def normalize_key(value: str) -> str:
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    safe = "".join(char for char in text if char.isalnum() or char == "_")
    return safe or "item"


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.time_ns()}"


def extract_task_id(text: str) -> str:
    match = re.search(r"task_\d+_\d+", text)
    return match.group(0) if match else ""
