"""Full test suite for refactored science_core."""
from __future__ import annotations
import json
import sys

import science_core as sc

print("=" * 60)
print("FULL TEST SUITE -- science_core refactoring verification")
print("=" * 60)
passed = 0
failed = 0
errors: list[tuple[str, str]] = []


def test(name: str, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  PASS: {name}")
    except Exception as e:
        failed += 1
        errors.append((name, str(e)))
        print(f"  FAIL: {name} -> {str(e)[:120]}")


# --- Module Structure ---
print("\n-- Module Structure --")

def t_facade():
    assert hasattr(sc, "PHASES")
    assert hasattr(sc, "SCIENCE_AGENTS")
    assert hasattr(sc, "PaperEvidence")
    assert hasattr(sc, "Hypothesis")
test("Facade re-exports constants and classes", t_facade)

def t_submods():
    assert hasattr(sc, "_utils")
    assert hasattr(sc, "_models")
    assert hasattr(sc, "_literature_search")
test("Submodule references accessible", t_submods)

# --- Utils ---
print("\n-- Utils --")

def t_norm():
    assert sc.normalize_space("  hello   world  ") == "hello world"
test("normalize_space", t_norm)

def t_trim():
    r = sc.trim_text("a" * 100, 20)
    assert r.endswith("...[truncated]")
test("trim_text", t_trim)

def t_uniq():
    assert sc.unique_preserve_order(["a", "b", "a", "c"]) == ["a", "b", "c"]
test("unique_preserve_order", t_uniq)

def t_sent():
    s = sc.split_sentences("Hello world. This is a test! Done.")
    assert len(s) >= 2
test("split_sentences", t_sent)

def t_clamp():
    assert sc.clamp_int(5, 1, 10) == 5
    assert sc.clamp_int(-1, 0, 10) == 0
test("clamp_int", t_clamp)

def t_find():
    items = [{"gap_id": "G1", "v": 1}, {"gap_id": "G2", "v": 2}]
    assert sc.find_by_id(items, "gap_id", "G2")["v"] == 2
    assert sc.find_by_id(items, "gap_id", "X") is None
test("find_by_id", t_find)

# --- Models ---
print("\n-- Models --")

def t_phases():
    assert len(sc.PHASES) == 8
    assert sc.PHASES[0] == "Gap Discovery"
test("PHASES", t_phases)

def t_agents():
    assert isinstance(sc.SCIENCE_AGENTS, dict)
    assert "boxue" in sc.SCIENCE_AGENTS
    assert len(sc.SCIENCE_AGENTS) >= 10
test("SCIENCE_AGENTS", t_agents)

def t_dc():
    h = sc.Hypothesis(hypothesis_id="h1", gap_id="g1", statement="s", mechanism="m", expected_value="e", test_plan="p")
    assert h.hypothesis_id == "h1"
    pe = sc.PaperEvidence(
        evidence_id="e1", title="t", citation="c", method="m",
        scenario="s", benchmark="b", contribution="co", limitation="li",
    )
    assert hasattr(pe, "createdAt")
test("Dataclass instantiation", t_dc)

def t_prov():
    assert "semantic_scholar" in sc.LITERATURE_PROVIDERS
test("LITERATURE_PROVIDERS", t_prov)

def t_onto():
    assert isinstance(sc.METHOD_ONTOLOGY, dict)
    assert isinstance(sc.SCENARIO_ONTOLOGY, dict)
test("Ontology dicts", t_onto)

# --- LLM / JSON ---
print("\n-- LLM / JSON --")

def t_json():
    assert sc.parse_json_object_from_text('{"a": 1}') == {"a": 1}
    assert sc.parse_json_object_from_text('pre {"b": 2} post') == {"b": 2}
test("parse_json_object_from_text", t_json)

def t_bal():
    r = sc.first_balanced_object('{"x": {"y": 1}, "z": 2}')
    assert isinstance(r, str) and r.startswith("{")
test("first_balanced_object", t_bal)

# --- Literature Scoring ---
print("\n-- Literature Scoring --")

def t_rel():
    score, reasons, verdict, detail = sc.literature_relevance_score(
        "machine learning battery degradation",
        {"title": "ML for battery degradation prediction", "abstract": "neural networks for battery health"},
    )
    assert isinstance(score, (int, float))
test("literature_relevance_score", t_rel)

def t_pq():
    r = sc.publication_quality_assessment({"venue": "Nature", "citationCount": 500, "year": "2020"})
    assert isinstance(r, dict)
test("publication_quality_assessment", t_pq)

def t_dr():
    r = sc.domain_relevance_assessment(
        {"title": "Vanadium redox flow battery electrode", "abstract": "carbon felt"},
        "vanadium flow battery",
    )
    assert isinstance(r, dict)
test("domain_relevance_assessment", t_dr)

def t_preprint_l3_query_and_classification():
    import _literature_search as ls
    import _literature_scoring as scoring
    noisy_instruction = (
        "agent literature hypothesis debate validation cathode voltage degradation mechanism "
        "4.5V LiCoO2 NMC811 electrolyte interface"
    )
    compact = ls.compact_preprint_retrieval_query(
        noisy_instruction,
        domain="high-voltage lithium batteries cathode electrolyte interface stability",
    )
    assert "agent" not in compact and "hypothesis" not in compact
    expression = ls.arxiv_search_query_expression(compact)
    assert expression.startswith("all:") and " AND all:" in expression

    preprint = {
        "title": "High-voltage lithium battery cathode electrolyte interface stability",
        "abstract": "Operando studies of lithium battery cathode degradation and interphase stability.",
        "provider": "arxiv",
        "venue": "arXiv",
        "year": "2026",
        "publication_quality_score": 0.6,
        "quality_flags": [],
    }
    journal = {**preprint, "provider": "semantic_scholar", "venue": "Journal of Energy Storage"}
    assert ls.is_preprint_literature_result(preprint)
    assert ls.is_preprint_literature_result({**journal, "arxiv_id": "2601.01234"})
    assert ls.stratified_candidate_matches("L3_preprint", preprint)
    assert ls.preprint_result_matches_query(preprint, "lithium battery cathode electrolyte")
    assert not ls.preprint_result_matches_query({"title": "Unrelated robotics preprint", "abstract": "robot manipulation"}, "lithium battery cathode electrolyte")
    preprint_alignment = scoring.core_domain_alignment(preprint, domain="high voltage lithium battery cathode", query="high voltage lithium battery cathode")
    journal_alignment = scoring.core_domain_alignment(journal, domain="high voltage lithium battery cathode", query="high voltage lithium battery cathode")
    assert preprint_alignment["min_core_hits"] == journal_alignment["min_core_hits"]

    original_arxiv = ls.search_arxiv
    original_semantic = ls.search_semantic_scholar
    try:
        ls.search_arxiv = lambda *_args, **_kwargs: {"provider": "arxiv", "status": "ok", "results": []}
        ls.search_semantic_scholar = lambda *_args, **_kwargs: {
            "provider": "semantic_scholar",
            "status": "ok",
            "results": [{**journal, "arxiv_id": "2601.01234"}],
        }
        blocks = ls.fetch_stratified_layer_blocks(
            "lithium battery cathode degradation",
            ["arxiv", "semantic_scholar"],
            {"layer": "L3_preprint", "quota": 1, "query_suffix": ""},
            query_plan=[{"branch": "primary", "query": "lithium battery cathode degradation"}],
            domain="high-voltage lithium batteries",
        )
        fallback = [block for block in blocks if block.get("retrieval_strategy") == "preprint_metadata_fallback"]
        candidates = ls.flatten_literature_results(blocks)
        assert len(fallback) == 1
        assert any(ls.stratified_candidate_matches("L3_preprint", item) for item in candidates)
    finally:
        ls.search_arxiv = original_arxiv
        ls.search_semantic_scholar = original_semantic
test("L3 preprint uses compact provider query without stricter lexical gate", t_preprint_l3_query_and_classification)

# --- Supplement ---
print("\n-- Supplement --")

def t_kw():
    kws = sc.extract_academic_keyword("structural equation modeling leads to improved battery cathode stability")
    assert len(kws) > 0
    assert any("structural" in k for k in kws)
test("extract_academic_keyword", t_kw)

def t_cmd():
    assert sc.classify_method_domain("structural equation modeling regression") == "statistical_modeling"
    assert sc.classify_method_domain("neural network deep learning CNN") == "computational_simulation"
    assert sc.classify_method_domain("xps spectroscopy electrochemical impedance") == "experimental_characterization"
test("classify_method_domain", t_cmd)

def t_aq():
    q = sc.build_audit_supplement_query("lithium battery", "SEM applied to interface", "SEM causes CEI stability")
    assert "causal mechanism evidence limitation contradiction validation" not in q
test("build_audit_supplement_query (no boilerplate)", t_aq)

def t_ar():
    r = sc.audit_supplement_candidate_relevance(
        {"title": "SEM analysis of battery cathode", "abstract": "structural equation modeling applied to cathode"},
        "SEM causes CEI stability",
        "lithium battery",
        "SEM applied to battery interface",
    )
    assert isinstance(r, dict) and "pass" in r
test("audit_supplement_candidate_relevance", t_ar)

# --- Verification ---
print("\n-- Verification --")

def t_af():
    r = sc.check_method_scenario_adaptability(
        "Structural Equation Modeling applied to battery cathode electrolyte interphase stabilization",
        "SEM path analysis validates CEI formation mechanism",
        "high-voltage lithium battery cathode interface",
    )
    assert r["verdict"] == "FAIL"
test("check_method_scenario_adaptability (SEM-on-battery FAIL)", t_af)

def t_ap():
    r = sc.check_method_scenario_adaptability(
        "Electrochemical impedance spectroscopy analysis of battery degradation",
        "EIS reveals charge transfer resistance increase",
        "lithium battery cathode degradation",
    )
    assert r["verdict"] in ("PASS", "WARN")
test("check_method_scenario_adaptability (EIS-on-battery PASS)", t_ap)

def t_ya():
    mock = {
        "unsupported_claims": ["X -> Y"],
        "layer_1_internal_consistency": {"verdict": "FAIL", "issues_found": ["No causal links"]},
        "layer_2_data_consistency": {"verdict": "PASS"},
        "layer_3_regime_shift_test": {"verdict": "PASS"},
        "feasibility_audit": {"verdict": "PASS"},
        "domain_adaptability_audit": {
            "verdict": "FAIL", "method_family": "statistical_modeling",
            "scenario_family": "experimental", "incompatibilities": ["electrochemical"],
            "detected_scenario_data_types": ["electrochemical"],
            "adaptation_conditions": ["Define latent variables"], "issues_found": [],
        },
    }
    actions = sc.yanzhen_required_actions(mock)
    names = [a["action"] for a in actions]
    assert "resolve_method_scenario_incompatibility" in names
    assert "suggested_search_queries" in actions[0]
test("yanzhen_required_actions (adaptability + queries)", t_ya)

def t_cc():
    chain = sc.extract_causal_chain("If temperature increases then reaction rate accelerates because activation energy is overcome.")
    assert isinstance(chain, list)
test("extract_causal_chain", t_cc)

def t_rs():
    shifts = sc.default_regime_shifts("battery cathode material temperature pressure electrochemical")
    assert len(shifts) >= 2
test("default_regime_shifts", t_rs)

# --- Gap Detection ---
print("\n-- Gap Detection --")

def t_jac():
    score = sc.text_jaccard("hello world test", "hello world other")
    assert 0 < score < 1
test("text_jaccard", t_jac)

def t_gap():
    g = sc.make_gap("mechanism_gap", "test gap description", ["ref1"], "suggested path", "value argument")
    assert g["gap_type"] == "mechanism_gap"
test("make_gap", t_gap)

# --- Debate ---
print("\n-- Debate --")

def t_sg():
    r = sc.debate_safety_gates(
        proponent_model_family="qwen-max",
        opponent_model_family="qwen-plus",
        judge_model_family="qwen-deep-research",
        verifier_model_family="qwen-plus",
    )
    assert r["passed"] is True
test("debate_safety_gates", t_sg)

def t_ev():
    refined = {
        "hypothesis": "test hypothesis with measure and baseline",
        "causal_chain": ["input", "mechanism", "output"],
        "falsification_conditions": ["no effect observed"],
        "evidence_requirements": ["map claims to evidence"],
    }
    yanzhen = {
        "verdict": "PASS", "overall_verdict": "MECHANISM_VERIFIED",
        "unsupported_claims": [], "domain_adaptability_audit": {"verdict": "PASS"},
    }
    r = sc.execution_level_validation({}, refined, yanzhen, [])
    assert r["verdict"] in ("PASS", "REQUIRES_HUMAN_REVIEW")
test("execution_level_validation", t_ev)

# --- Pipeline ---
print("\n-- Pipeline --")

def t_afp():
    agents = sc.agents_for_phase("Gap Discovery")
    assert "zhizhi" in agents or "tanxi" in agents
test("agents_for_phase", t_afp)

def t_schemas():
    for fn in [sc.zhizhi_output_schema, sc.mingli_output_schema, sc.duzhi_output_schema,
               sc.bianlun_output_schema, sc.yanzhen_output_schema]:
        s = fn()
        assert isinstance(s, dict)
test("Output schemas", t_schemas)

# --- v3.0: Domain Exclusion ---
print("\n-- Domain Exclusion (v3.0) --")

def t_dem():
    markers = sc.domain_exclusion_markers("Ultra-High Voltage Power Transmission")
    assert isinstance(markers, (list, set, dict))
test("domain_exclusion_markers (UHV)", t_dem)

def t_dem_empty():
    markers = sc.domain_exclusion_markers("")
    assert isinstance(markers, (list, set, dict))
test("domain_exclusion_markers (empty)", t_dem_empty)

# --- v3.0: TABI Abductive Gap Detection ---
print("\n-- TABI Abductive Gap Detection (v3.0) --")

def t_tabi_fn():
    assert callable(sc.tabi_abductive_gap_detection) and callable(sc.extract_evidence_pairs_from_records)
test("TABI functions importable", t_tabi_fn)

def t_tabi_empty():
    r = sc.tabi_abductive_gap_detection({"papergraph": [], "evidence": []}, max_gaps=5)
    assert isinstance(r, list) and len(r) == 0
test("TABI empty project", t_tabi_empty)

def t_evpairs():
    proj = {"papergraph": [
        {"method": "GNN", "scenario": "fault diagnosis", "benchmark": "accuracy",
         "contribution": "GNN improves accuracy", "limitation": "low-voltage only", "title": "A", "citation": "A"},
        {"method": "GNN", "scenario": "fault diagnosis", "benchmark": "recall",
         "contribution": "GNN reduces false negatives", "limitation": "no UHV", "title": "B", "citation": "B"},
    ], "evidence": []}
    pairs = sc.extract_evidence_pairs_from_records(proj, limit=10)
    assert isinstance(pairs, list)
test("extract_evidence_pairs_from_records", t_evpairs)

# --- v3.0: Counterfactual Gap Analysis ---
print("\n-- Counterfactual Gap Analysis (v3.0) --")

def t_cf_fn():
    assert callable(sc.counterfactual_gap_analysis) and callable(sc.build_counterfactual_tree)
test("CG functions importable", t_cf_fn)

def t_cf_run():
    proj = {"papergraph": [
        {"method": "transformer", "scenario": "power grid", "benchmark": "reliability",
         "contribution": "Improved design", "limitation": "low voltage only", "title": "T", "citation": "X"},
    ], "evidence": []}
    gaps = [sc.make_gap("combinatorial", "Transformer not at UHV", ["X"], "path", "val")]
    enriched = sc.counterfactual_gap_analysis(proj, gaps, limit=5)
    assert len(enriched) == 1 and "counterfactual_tree" in enriched[0]
    assert enriched[0]["gap_resolution_type"] in ("complement_gap", "novel_concept_gap")
test("counterfactual_gap_analysis", t_cf_run)

def t_cf_type():
    assert sc.classify_gap_counterfactual_type({"related_evidence_count": 0}) == "novel_concept_gap"
    assert sc.classify_gap_counterfactual_type({"related_evidence_count": 3, "resolution_complexity": "low"}) == "complement_gap"
test("classify_gap_counterfactual_type", t_cf_type)

# --- v3.0: GRADE Knowledge Sufficiency ---
print("\n-- GRADE Knowledge Sufficiency (v3.0) --")

def t_grade_fn():
    assert callable(sc.grade_knowledge_sufficiency) and callable(sc.extract_grade_key_terms)
test("GRADE functions importable", t_grade_fn)

def t_grade_empty():
    r = sc.grade_knowledge_sufficiency("black hole formation", {"papergraph": [], "evidence": []})
    assert r["verdict"] == "knowledge_insufficient"
test("GRADE empty project", t_grade_empty)

def t_grade_cover():
    proj = {"papergraph": [
        {"title": "Black hole formation via collapse", "abstract": "stellar mass collapse singularity",
         "method": "simulation", "scenario": "collapse", "contribution": "demonstrated formation", "conclusion": "horizon forms"},
    ], "evidence": []}
    r = sc.grade_knowledge_sufficiency("black hole formation gravitational collapse", proj)
    assert r["verdict"] in ("knowledge_sufficient", "knowledge_partial") and r["rank_ratio"] < 0.6
test("GRADE with coverage", t_grade_cover)

# --- v3.0: Specificity Enforcement ---
print("\n-- Specificity Enforcement (v3.0) --")

def t_spec_good():
    idea = {"title": "UHV overvoltage", "hypothesis": "When impulse exceeds 1800 kV at 1000kV substation, resistor reduces overvoltage by 40%",
            "abstract": "resistor causes dissipation which leads to damping during energization", "related_work": "UHV studies"}
    r = sc.enforce_hypothesis_specificity(idea)
    assert r["verdict"] in ("PASS", "pass", "valid") or len(r.get("missing_dimensions", [])) <= 1
test("Specificity passes good hypothesis", t_spec_good)

def t_spec_bad():
    idea = {"title": "Performance study", "hypothesis": "The method improves the system outcome",
            "abstract": "Changes may affect behavior", "related_work": "Various approaches"}
    r = sc.enforce_hypothesis_specificity(idea)
    assert r["verdict"] in ("REJECT", "reject", "requires_revision") or len(r.get("missing_dimensions", [])) >= 2
test("Specificity rejects generic hypothesis", t_spec_bad)

def t_autogen_project_reload_uses_persisted_snapshot():
    import autogen_collab
    persisted = {"project_id": "sci_test", "papergraph": [{"paper_id": "paper_1"}]}
    reloaded = autogen_collab.autogen_reload_project_state("sci_test", lambda _: persisted)
    assert len(reloaded["papergraph"]) == 1
test("AutoGen reloads persisted PaperGraph after specialist writes", t_autogen_project_reload_uses_persisted_snapshot)

def t_mingli_acceptance_boundary():
    idea = {
        "hypothesis": "If the catalyst loading changes the surface intermediate coverage, then conversion will change; reject the claim if the intermediate does not precede conversion.",
        "abstract": "The proposed pathway is a measurable mechanism rather than a method comparison.",
        "causal_chain": ["Input: vary catalyst loading", "Mechanism: surface intermediate coverage", "Output: conversion"],
        "experiments": {"setup": "matched reactor study", "metrics": "conversion", "baselines": "uncatalyzed control"},
    }
    gap = {"supporting_references": ["Example et al. (2025)"]}
    r = sc.mingli_acceptance_check(idea, gap)
    assert r["verdict"] == "PASS"
    assert "dynamics" in r["deferred_to_yanzhen"]
test("MingLi accepts a grounded falsifiable draft without YanZhen checklist", t_mingli_acceptance_boundary)

def t_socrates_offline_evidence_flow():
    import _socrates as soc
    draft = soc.mechanism_draft_from_gap(
        {
            "gap_id": "gap_socrates",
            "description": "High-voltage lithium battery cathode interphase degradation",
            "hypothesis_ingredients": {
                "methods": ["interphase engineering"],
                "scenarios": ["high-voltage lithium batteries"],
                "benchmarks": ["capacity retention"],
            },
        },
        "high-voltage lithium batteries",
    )
    plan = soc.translate_unresolved_to_queries(["identity"], "high-voltage lithium batteries", mediator="interphase degradation")
    first = soc.select_untried_socrates_queries(["identity"], plan, set(), 1)
    second = soc.select_untried_socrates_queries(["identity"], plan, set(first), 1)
    assert first and second and first[0][1] != second[0][1]

    project = {"papergraph": [{
        "paper_id": "paper_socrates",
        "citation": "Example et al. (2025)",
        "title": "Operando XPS of high-voltage lithium battery interphases",
        "abstract": "In high-voltage lithium batteries, cathode electrolyte interphase formation at the cathode surface was characterized by operando XPS.",
    }]}
    evidence = soc.extract_mechanism_evidence(
        project, ["identity", "location_or_scope", "observability"],
        domain="high-voltage lithium batteries", method="interphase engineering", scenario="high-voltage lithium batteries", mediator="interphase",
    )
    assert evidence["identity"] and evidence["location_or_scope"] and evidence["observability"]
    assert soc._apply_evidence(draft, evidence) >= 3
    assert "identity" not in soc.unresolved_mechanism_fields(draft)

    gap = {
        "gap_id": "gap_socrates",
        "description": "High-voltage lithium battery cathode interphase degradation",
        "supporting_references": ["Example et al. (2025)"],
        "hypothesis_ingredients": {
            "methods": ["interphase engineering"],
            "scenarios": ["high-voltage lithium batteries"],
            "benchmarks": ["capacity retention"],
        },
    }
    candidate = sc.make_hypothesis_seed(
        {"domain": "high-voltage lithium batteries", "socrates_mechanism_contracts": {"gap_socrates": draft}},
        gap,
        {"method": "interphase engineering", "scenario": "high-voltage lithium batteries", "benchmark": "capacity retention"},
        0,
        analogy={},
        hotspot={},
    )
    assert candidate["socrates_mechanism_contract"] == draft
    assert "Example et al. (2025)" in candidate["mechanism"]
test("Socrates offline evidence loop retries queries and reaches MingLi", t_socrates_offline_evidence_flow)

def t_tanxi_mechanism_draft_is_source_bounded():
    draft = sc.build_tanxi_mechanism_draft({
        "gap_id": "gap_tanxi_draft",
        "description": "A mechanism tension requires source-grounded resolution.",
        "hypothesis_ingredients": {
            "methods": ["operando spectroscopy"],
            "scenarios": ["electrochemical interface"],
            "benchmarks": ["interfacial resistance"],
        },
        "mechanism_issue_signal": {"source_text": "The interphase formation pathway remains unresolved."},
    })
    assert draft["input"] == "operando spectroscopy electrochemical interface"
    assert draft["output"] == "interfacial resistance"
    assert draft["proposed_mediator"] == "The interphase formation pathway remains unresolved."
    assert draft["status"] == "draft_requires_socrates_evidence"
    assert len(draft["unresolved_fields"]) == 7
test("TanXi emits a conservative mechanism draft for Socrates", t_tanxi_mechanism_draft_is_source_bounded)

def t_autogen_includes_socrates_stage():
    import autogen_collab
    assert "socrates" in autogen_collab.DEFAULT_AUTOGEN_AGENTS
    assert any(tool["name"] == "run_socrates_mechanism_enrichment" for tool in autogen_collab.build_autogen_tool_registry())
    assert any(step["speaker"] == "Socrates_ToolAgent" for step in autogen_collab.build_socratic_groupchat_protocol())
test("AutoGen registers Socrates between TanXi and MingLi", t_autogen_includes_socrates_stage)

def t_autogen_requires_complete_socrates_contract_for_mingli():
    import autogen_collab
    assert autogen_collab.autogen_socrates_allows_mingli("COMPLETE")
    assert not autogen_collab.autogen_socrates_allows_mingli("INSUFFICIENT_EVIDENCE")
    assert not autogen_collab.autogen_socrates_allows_mingli("NO_GAP_AVAILABLE")
    assert not autogen_collab.autogen_socrates_allows_mingli("")
test("AutoGen blocks MingLi until Socrates returns COMPLETE", t_autogen_requires_complete_socrates_contract_for_mingli)

# --- Tools.py / autogen_collab.py compatibility ---
print("\n-- Backward Compatibility --")

TOOLS_IMPORTS = [
    "create_research_project", "list_research_projects", "get_research_project",
    "list_science_agents", "get_science_agent_prompt", "list_literature_providers",
    "explore_domain_subspaces", "search_literature", "search_literature_stratified",
    "search_papers", "search_papers_stratified", "extract_structured_info",
    "select_literature_result", "expand_literature_graph", "build_literature_relation_graph",
    "create_science_pipeline_tasks", "create_science_delegation_tasks",
    "create_boxue_delegation_tasks", "run_boxue_research_round",
    "build_knowledge_map", "add_literature_evidence", "import_literature_text",
    "import_literature_file", "import_literature_search_result",
    "extract_paper_keynote", "import_papergraph_record", "list_papergraph_records",
    "verify_citation_uniqueness", "assess_novelty", "verify_uniqueness",
    "run_zhizhi_literature_analysis", "parse_literature_text", "build_coverage_matrix",
    "detect_knowledge_gaps", "run_tanxi_gap_exploration", "load_project",
    "semantic_plausibility_for_pair", "evolve_domain_subspaces",
    "build_temporal_knowledge_graph", "detect_structural_knowledge_gaps",
    "find_structural_analogy_transfers", "run_mingli_hypothesis_evolution",
    "run_socrates_mechanism_enrichment",
    "generate_idea", "design_experiment", "finalize_idea", "create_hypothesis",
    "run_mechanism_check", "check_internal_consistency", "check_data_consistency",
    "regime_shift_test", "detect_selective_citation", "causal_chain_audit",
    "run_yanzhen_mechanism_verification", "ask_socratic_questions",
    "ask_critical_questions", "find_counterexamples", "stress_test_assumptions",
    "moderate_round", "summarize_positions", "extract_emergent_method",
    "run_socratic_hypothesis_debate", "export_research_plan",
    # v3.0
    "tabi_abductive_gap_detection", "extract_evidence_pairs_from_records",
    "counterfactual_gap_analysis", "build_counterfactual_tree",
    "grade_knowledge_sufficiency", "enforce_hypothesis_specificity", "mingli_acceptance_check",
    "domain_exclusion_markers",
]

def t_tools_compat():
    missing = [n for n in TOOLS_IMPORTS if not hasattr(sc, n)]
    assert not missing, f"tools.py would miss: {missing}"
test(f"tools.py compatibility ({len(TOOLS_IMPORTS)} imports)", t_tools_compat)

AUTOGEN_IMPORTS = [
    "create_research_project", "load_project", "search_literature_stratified",
    "select_zhizhi_import_results", "import_literature_search_result",
    "extract_paper_keynote", "detect_knowledge_gaps", "run_tanxi_gap_exploration",
    "run_mingli_hypothesis_evolution", "run_yanzhen_mechanism_verification",
    "run_socrates_mechanism_enrichment",
    "run_socratic_hypothesis_debate", "SCIENCE_AGENTS", "PHASES",
    # v3.0
    "tabi_abductive_gap_detection", "counterfactual_gap_analysis",
    "grade_knowledge_sufficiency", "enforce_hypothesis_specificity", "mingli_acceptance_check",
]

def t_autogen_compat():
    missing = [n for n in AUTOGEN_IMPORTS if not hasattr(sc, n)]
    assert not missing, f"autogen_collab.py would miss: {missing}"
test(f"autogen_collab.py compatibility ({len(AUTOGEN_IMPORTS)} imports)", t_autogen_compat)

# --- Summary ---
print()
print("=" * 60)
print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
if errors:
    print("Failures:")
    for name, err in errors:
        print(f"  - {name}: {err[:150]}")
else:
    print("ALL TESTS PASSED")
print("=" * 60)
sys.exit(1 if failed else 0)
