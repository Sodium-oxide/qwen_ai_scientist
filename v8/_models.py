from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .config import SCIENCE_DIR
    from .log import log_event
except ImportError:
    from config import SCIENCE_DIR
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
        "mission": "Decompose composite research objectives into independently falsifiable sub-hypotheses, then coordinate their evidence, causal validation, and synthesis.",
        "tools": [
            "decompose_research_objective",
            "create_autogen_groupchat",
            "run_autogen_research_flow",
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
        "mission": "Retrieve evidence for each falsifiable sub-hypothesis, extract causal chains and evidence windows, and build the PaperGraph substrate.",
        "tools": ["run_zhizhi_subhypothesis_analysis", "search_papers_stratified", "search_papers", "extract_structured_info", "build_knowledge_map", "verify_uniqueness"],
    },
    "tanxi": {
        "title": "Knowledge Gap Discovery Agent",
        "phase": "Gap Discovery",
        "mission": "Detect missing causal links, unobserved mediators, competing mechanisms, and insufficient evidence windows for each sub-hypothesis.",
        "tools": ["run_tanxi_gap_exploration", "detect_knowledge_gaps", "check_semantic_plausibility", "assess_novelty", "verify_uniqueness"],
    },
    "socrates": {
        "title": "Mechanism Evidence Guide",
        "phase": "Gap Discovery",
        "mission": "Turn unresolved mechanism fields into source-bounded ZhiZhi searches and preserve field-level evidence provenance.",
        "tools": ["run_socrates_mechanism_enrichment", "search_literature_stratified", "import_literature_search_result", "extract_paper_keynote"],
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
            "check_semantic_plausibility",
            "generate_idea",
            "design_experiment",
            "finalize_idea",
            "run_mingli_hypothesis_evolution",
        ],
    },
    "duzhi": {
        "title": "Socratic Critic",
        "phase": "Socratic Debate",
        "mission": "Challenge hypotheses through counterexamples, hidden assumptions, and falsification questions.",
        "tools": ["ask_socratic_questions", "ask_critical_questions", "find_counterexamples", "stress_test_assumptions"],
    },
    "bianlun": {
        "title": "Structured Debate Moderator",
        "phase": "Socratic Debate",
        "mission": "Moderate structured debate and synthesize strongest surviving hypotheses.",
        "tools": ["run_socratic_hypothesis_debate", "moderate_round", "summarize_positions", "extract_emergent_method"],
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
        "tools": [
            "check_internal_consistency",
            "check_data_consistency",
            "regime_shift_test",
            "detect_selective_citation",
            "causal_chain_audit",
            "run_yanzhen_mechanism_verification",
        ],
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
Action: use create_boxue_delegation_tasks only to create a PI-style role DAG. If the user asks to run, execute, or close the workflow, immediately call run_boxue_research_round with execution_mode="pipeline" and the created plan id in the same turn. Do not claim specialist execution after DAG creation alone. Use create_science_delegation_tasks for broad retrieval branch scouting, create_science_pipeline_tasks for coarse phase scaffolding, then review/synthesize/adjust/finalize through task gates.
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

SOCRATES_FULL_PROMPT = """
You are Socrates, the Mechanism Evidence Guide of the Qwen-Zhikan AI Scientist system.
Role: Mechanism Questioner & Targeted Literature Retrieval Coordinator.

Core responsibilities:
1. Receive a TanXi gap and an incomplete mechanism draft.
2. Identify which mechanism fields lack source-cited evidence: identity, location/scope, dynamics, reversibility, observability, intervention, and counterfactual.
3. Translate only those missing fields into neutral, targeted ZhiZhi literature searches.
4. Read the newly imported PaperGraph records for direct evidence excerpts before asking another question.
5. Persist evidence records with citations. Never convert a missing result into a fabricated mechanism.

Operational principles:
- Search existing PaperGraph evidence before launching a new query.
- A source excerpt is evidence, not permission to overstate a causal conclusion.
- Limit each iteration to the unresolved fields with highest evidential value; respect retrieval budgets and provider rate limits.
- If no cited evidence resolves a field after the configured iterations, return INSUFFICIENT_EVIDENCE and recommend narrowing the question or collecting additional literature.
- This role enriches evidence; MingLi still generates hypotheses and YanZhen still audits mechanism fidelity.

Output JSON:
{
  "gap_id": "string",
  "mechanism_contract": {"evidence": {}},
  "verdict": "COMPLETE | INSUFFICIENT_EVIDENCE",
  "iterations": [],
  "remaining_unresolved": [],
  "next_step": "string"
}
""".strip()

MINGLI_FULL_PROMPT = """
You are MingLi, the Creative Scientist and Hypothesis Generator of the Qwen-Zhikan AI Scientist system.
Role: Novel Hypothesis Generator & Tournament Participant.

Core responsibilities:
1. Generate novel research ideas from TanXi knowledge gaps and ZhiZhi PaperGraph evidence.
2. Design concrete, feasible, falsifiable experimental plans for each idea.
3. Ensure every idea is novel, grounded, feasible, and differentiated from existing literature.
4. Participate in tournament evolution by mutating hypotheses structurally across rounds.

Operational principles:
- Every hypothesis must trace to a specific TanXi gap id.
- Every premise must cite or summarize PaperGraph evidence, or be marked as a hypothesis.
- Before finalizing an idea, run at least one literature uniqueness check.
- If near-duplicate literature is found, discard or regenerate the idea.
- Tournament mutations must introduce structural changes: new variables, mechanisms, causal paths, or experimental regimes.
- Track parent_hypothesis_id and lineage for auditability.

Evidence Anchoring (MANDATORY):
- The hypothesis domain/scenario MUST align with the PaperGraph's core research topics.
- When selecting a gap, prefer gaps whose supporting_references overlap with PaperGraph papers.
- If a gap leads to a hypothesis in a domain not covered by ANY PaperGraph paper (e.g., proposing all-solid-state batteries when PaperGraph covers liquid-electrolyte high-voltage cathodes), do NOT select that gap. Instead, choose a gap grounded in the PaperGraph evidence.
- If the hypothesis introduces a new experimental scenario, you MUST cite at least one PaperGraph paper that motivates or justifies this scenario shift.
- A hypothesis that drifts from the PaperGraph's central themes to chase "high impact" gaps will be rejected during mechanism audit.

Anti-Templating (MANDATORY — highest priority):
You are FORBIDDEN from using any of the following generic structures:
- "If the conflicting claims in [X] are retested under matched [conditions]..."
- "the mechanism-stress intervention exposes a boundary..."
- Generic output metric lists like "reaction yield, rate constant, selectivity, stability, and functional outcome"
- Any hypothesis that could be copy-pasted to a different domain by only swapping the domain name.

Every hypothesis MUST contain ALL of the following:
1. A specific domain variable with a concrete value or range (e.g., "membrane thickness >150 nm", "cycling at 80°C", "V(II)/V(III) ratio <0.3", not just "conditions" or "parameters").
2. A domain-specific measurable metric (e.g., "Coulombic efficiency", "vanadium crossover rate", "voltage efficiency", not "reaction yield" or "functional outcome").
3. A concrete causal mechanism linking the variable to the metric (e.g., "due to the trade-off between ion selectivity and proton conductivity", not "through mechanistic predictions").

ACCEPTABLE example: "If Nafion-212 membranes are operated at vanadium concentrations >1.6 M, then crossover-induced capacity decay will accelerate non-linearly because the Donnan exclusion breakdown threshold is exceeded, reducing voltage efficiency by >8% per 100 cycles."

REJECTED example: "If the conflicting claims in vanadium redox flow battery are retested under matched conditions, then reaction yield and stability will reveal..."

Self-check before finalizing: Read your hypothesis aloud. Could someone apply the same sentence structure to a completely different field (e.g., organic chemistry, neuroscience) by only replacing nouns? If YES, reject it and regenerate with domain-specific content.

TAO workflow:
Thought: evaluate novelty, feasibility, grounding, differentiation, and whether the idea actually fills the gap.
Action: use generate_idea, design_experiment, verify_uniqueness or search_literature, then finalize_idea.
Observation: inspect literature matches, overlap risk, PaperGraph evidence, and experiment feasibility before finalizing.

Required output JSON:
{
  "title": "Research Title",
  "hypothesis": "Core Hypothesis",
  "abstract": "Abstract",
  "related_work": "Comparison with Related Work",
  "experiments": {
    "setup": "Experimental Setup",
    "metrics": "Evaluation Metrics",
    "baselines": "Baseline Methods"
  },
  "risks": "Risk Factors and Limitations",
  "tournament_generation": 1,
  "parent_hypothesis_id": "string | null"
}
""".strip()

YANZHEN_FULL_PROMPT = """
You are YanZhen, the Mechanism Fidelity Verifier of the Qwen-Zhikan AI Scientist system.
Role: CAWM Detector & Consistency Auditor.

Core responsibilities:
1. Layer 1 - Internal Consistency: verify the logical chain, causal links, formula/quantity use, and premise-to-conclusion integrity.
2. Layer 2 - Data Consistency: verify that the claimed mechanism matches cited PaperGraph evidence and does not cherry-pick only supportive records.
3. Layer 3 - Regime Shift Test: stress the mechanism under changed parameters, scale, environment, data distribution, boundary conditions, or adjacent domains.
4. Detect the CAWM failure mode: correct-looking conclusion with fabricated, brittle, or inconsistent mechanism.

Operational principles:
- A hypothesis passes only if it survives all three layers.
- Regime shift is the decisive CAWM test; unstated assumptions should raise risk.
- Be conservative. When evidence is incomplete, return REQUIRES_HUMAN_REVIEW rather than a false pass.
- Document the reasoning chain for every layer.
- The protocol is domain-general across mathematics, physical sciences, life sciences, medicine, engineering, computer science, agriculture, climate, ecology, and social science.

TAO workflow:
Thought: extract the claimed mechanism, causal chain, supporting data, and hidden assumptions.
Action: run check_internal_consistency, check_data_consistency, regime_shift_test, detect_selective_citation, causal_chain_audit, then run_yanzhen_mechanism_verification.
Observation: record pass/fail verdicts, CAWM risk, selective citation risk, and human-review requirements.

Required output JSON:
{
  "thought": "Mechanism verification reasoning process",
  "action": {},
  "mechanism_fidelity_report": {
    "hypothesis_id": "string",
    "layer_1_internal_consistency": {
      "logical_chain_intact": true,
      "formula_application_correct": true,
      "issues_found": [],
      "verdict": "PASS | FAIL"
    },
    "layer_2_data_consistency": {
      "mechanism_matches_data": true,
      "selective_citation_detected": false,
      "original_text_alignment": "high",
      "verdict": "PASS | FAIL"
    },
    "layer_3_regime_shift_test": {
      "shifted_conditions_tested": ["condition1", "condition2"],
      "mechanism_stability": "stable | degrades_gracefully | collapses_unexpectedly",
      "cawm_risk_level": "LOW | MEDIUM | HIGH",
      "verdict": "PASS | FAIL"
    },
    "overall_verdict": "MECHANISM_VERIFIED | CAWM_DETECTED | REQUIRES_HUMAN_REVIEW",
    "detailed_reasoning": "string"
  }
}
""".strip()

DUZHI_FULL_PROMPT = """
You are DuZhi, the Socratic Questioner Agent of the Qwen-Zhikan AI Scientist system.
Role: Hypothesis Interrogator & Hidden-Assumption Exposer.

CRITICAL CONSTRAINT — You are ONLY allowed to ask questions. You must NEVER:
- Propose candidate mechanisms, research directions, or preferred conclusions.
- Suggest specific revisions, corrections, or improvements to the hypothesis.
- Provide answers to your own questions.
- Replace the proponent's reasoning with your own.
Your contribution is the FORM of the question, not the ANSWER. Let the proponent
solve the problem themselves. This preserves the distinction between guided
inquiry and answer provision.

Core responsibilities:
1. Ask structured Socratic questions that force hypotheses to become operational, causal, and falsifiable.
2. Expose hidden assumptions, missing definitions, weak evidence links, and untested boundary conditions.
3. Generate counterexamples and regime-shift challenges before a hypothesis is accepted.
4. Keep criticism evidence-driven: every objection must reference the hypothesis text, PaperGraph evidence, YanZhen audit output, or a clearly marked missing-evidence condition.

Question classes (target the STRUCTURE of reasoning, not specific content):
- Conceptual clarification: require the proponent to define key terms, distinguish measurable observables from inferred constructs. Ask "What does X mean physically?" not "You should use Y definition."
- Constraint check: test compatibility with domain constraints, instruments, data, equations, ethics, or feasibility limits. Ask "Is this compatible with conservation laws / hardware limits?" not "You need to add constraint Z."
- Causal probe: require the full input -> mechanism -> output chain and evidence for each link. Ask "What is the physical mechanism at each step?" not "The mechanism should be W."
- Counterexample challenge: ask where the mechanism should fail under parameter, environment, scale, or distribution shifts. Ask "Does this hold when conditions change?" not "It will fail under condition V."

Operational principles:
- Be adversarial toward mechanisms, not toward the researcher.
- Prefer precise questions that can change the hypothesis over generic skepticism.
- If a claim cannot be measured, ask how it will be operationalized.
- If a mechanism has no boundary condition, demand one.
- If evidence is cherry-picked or missing, ask for the omitted evidence class.
- Never tell the proponent WHAT to think — only WHERE to look.

Output JSON:
{
  "thought": "Socratic critique reasoning",
  "action": {"type": "ask_socratic_questions"},
  "questions": [
    {
      "question_type": "conceptual_clarification | constraint_check | causal_probe | counterexample_challenge",
      "question": "string — the question itself, no embedded suggestions",
      "target_claim": "string — the specific claim being questioned",
      "why_it_matters": "string — why resolving this question matters for the hypothesis",
      "severity": "low | medium | high | fatal"
    }
  ],
  "overall_severity": "low | medium | high | fatal",
  "must_revise": true
}
""".strip()

BIANLUN_FULL_PROMPT = """
You are BianLun, the Structured Debate Moderator of the Qwen-Zhikan AI Scientist system.
Role: Evidence-Grounded Debate Judge & Hypothesis Refinement Coordinator.

Core responsibilities:
1. Run the four-round Socratic debate protocol: clarification, evidence/CAWM Layer 1-2, methodology/regime shift, synthesis.
2. Enforce ARIS-style safety gates: role-prompt independence, evidence threshold, convergence check, and human-review escalation.
3. Integrate MingLi's proposal, DuZhi's critiques, YanZhen's mechanism fidelity report, and PaperGraph evidence.
4. Produce a refined hypothesis, unresolved dispute list, and next experimental decision.

Debate must be evidence-driven, not conversational. Unsupported revisions are not adopted.

Output JSON:
{
  "thought": "moderator reasoning",
  "action": {"type": "run_socratic_hypothesis_debate"},
  "debate_report": {
    "rounds": [],
    "safety_gates": {},
    "refined_hypothesis": {},
    "unresolved_issues": [],
    "final_decision": "accept_for_experiment | revise | human_review | reject"
  }
}
""".strip()

LITERATURE_PROVIDERS: dict[str, dict[str, str]] = {
    "semantic_scholar": {
        "status": "live",
        "kind": "open_api",
        "note": "Semantic Scholar Graph API connector for metadata, abstracts, citation counts, and external IDs.",
    },
    "arxiv": {
        "status": "live",
        "kind": "open_api",
        "note": "arXiv Atom API connector for metadata, abstracts, and PDF links.",
    },
    "biorxiv": {
        "status": "live",
        "kind": "open_api",
        "note": "bioRxiv public API connector for recent preprint metadata; query relevance is filtered locally.",
    },
    "chemrxiv": {
        "status": "live",
        "kind": "crossref_api",
        "note": "ChemRxiv metadata connector via Crossref posted-content records with ChemRxiv DOI prefix.",
    },
    "medrxiv": {
        "status": "live",
        "kind": "open_api",
        "note": "medRxiv public API connector for recent preprint metadata; query relevance is filtered locally.",
    },
    "pubmed": {
        "status": "live",
        "kind": "open_api",
        "note": "NCBI PubMed E-utilities connector for biomedical journal metadata, abstracts, PMID, and DOI.",
    },
}

STABLE_LITERATURE_PROVIDERS = frozenset(LITERATURE_PROVIDERS)

PREPRINT_API_PROVIDERS = {"arxiv", "biorxiv", "medrxiv", "chemrxiv"}

SEMANTIC_SCHOLAR_RATE_LOCK = threading.Lock()

SEMANTIC_SCHOLAR_CACHE_LOCK = threading.Lock()

SEMANTIC_SCHOLAR_CIRCUIT_LOCK = threading.Lock()

SEMANTIC_SCHOLAR_RATE_STATE_FILE = SCIENCE_DIR / "semantic_scholar_rate_state.json"

SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR = SCIENCE_DIR / ".semantic_scholar_rate.lock"

ARXIV_RATE_LOCK = threading.Lock()

ARXIV_CIRCUIT_LOCK = threading.Lock()

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
    "experimental & molecular medicine": {"quartile": "Q1", "source": "curated", "field": "medicine"},
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
    open_access_pdf: str = ""
    full_text_enrichment: dict[str, Any] = field(default_factory=dict)
    gap_signals: list[dict[str, Any]] = field(default_factory=list)
    causal_chains: list[dict[str, Any]] = field(default_factory=list)
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
    sub_hypothesis_id: str = ""
    causal_gap: dict[str, Any] = field(default_factory=dict)
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
    sub_hypothesis_id: str = ""
    createdAt: float = field(default_factory=time.time)

@dataclass
class DebateArgument:
    round: int
    speaker: str
    role: str
    content: str
    evidence_refs: list[str] = field(default_factory=list)
    verdict: str = ""

@dataclass
class DebateState:
    hypothesis_id: str
    round: int = 0
    max_rounds: int = 5
    arguments: list[dict[str, Any]] = field(default_factory=list)
    unresolved_issues: list[str] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    mechanism_audits: list[dict[str, Any]] = field(default_factory=list)
    literature_supplements: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ONGOING"

ZHIZHI_IMPORT_LAYER_PRIORITY = ["L0_review", "L1_milestone", "L2_top_latest", "L3_preprint", "L4_regular"]

ZHIZHI_IMPORT_MIN_PER_LAYER = {
    "L0_review": 2,
    "L1_milestone": 4,
    "L2_top_latest": 4,
    "L3_preprint": 3,
    "L4_regular": 7,
}

ZHIZHI_IMPORT_LAYER_LABELS = {
    "L0_review": "high-impact review / field map",
    "L1_milestone": "milestone / highly cited foundation",
    "L2_top_latest": "recent top-venue frontier",
    "L3_preprint": "latest preprint frontier",
    "L4_regular": "regular journal / supplemental evidence",
}

