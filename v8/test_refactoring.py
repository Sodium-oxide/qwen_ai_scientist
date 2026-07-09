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
    "generate_idea", "design_experiment", "finalize_idea", "create_hypothesis",
    "run_mechanism_check", "check_internal_consistency", "check_data_consistency",
    "regime_shift_test", "detect_selective_citation", "causal_chain_audit",
    "run_yanzhen_mechanism_verification", "ask_socratic_questions",
    "ask_critical_questions", "find_counterexamples", "stress_test_assumptions",
    "moderate_round", "summarize_positions", "extract_emergent_method",
    "run_socratic_hypothesis_debate", "export_research_plan",
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
    "run_socratic_hypothesis_debate", "SCIENCE_AGENTS", "PHASES",
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
