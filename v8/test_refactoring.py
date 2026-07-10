"""Full test suite for refactored science_core."""
from __future__ import annotations
import json
import sys

import science_core as sc
import autogen_collab as ac

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

def t_cf_plan_validation():
    proj = {"papergraph": [
        {"method": "simulation", "scenario": "reaction dynamics", "benchmark": "trajectory error",
         "contribution": "Simulation explains the observed transition through an intermediate state.",
         "title": "A", "citation": "A"},
    ], "evidence": []}
    gap = sc.make_gap("mechanism_problem", "Mechanism remains unclear for reaction dynamics", ["A"], "path", "value")
    result = sc.validate_gap_resolution_plan(gap, proj["papergraph"])
    assert result["verdict"] == "INVALID_RESOLUTION_PATH"
    assert result["first_failed_step"] and result["minimal_missing_capabilities"]
    assert result["repair_classification"] in ("complement_or_composition", "out_of_distribution_capability")
test("counterfactual plan validation", t_cf_plan_validation)

def t_gap_hypothesis_preparation():
    project = {"papergraph": [
        {"title": "A", "citation": "A", "method": "time-resolved measurement", "scenario": "coupled process",
         "benchmark": "transition probability", "abstract": "Under 5 mM input, the measurement quantified transition probability.",
         "contribution": "An intermediate state precedes the outcome."},
    ], "evidence": []}
    gap = sc.make_gap("mechanism_problem", "The intermediate mechanism remains unclear in the coupled process.", ["A"], "path", "value")
    prepared = sc.prepare_gap_for_hypothesis(project, gap)
    assert prepared["hypothesis_readiness"]["ready"]
    assert prepared["evidence_packets"] and prepared["counterfactual_leaves"]
test("evidence-grounded gap preparation", t_gap_hypothesis_preparation)

def t_contradiction_gap_comparability():
    project = {"papergraph": [
        {"title": "Method A under target condition", "citation": "A doi:10.1000/demo-a", "scenario": "target system", "benchmark": "primary outcome", "abstract": "Method A improves the primary outcome in the target system."},
        {"title": "Method B under target condition", "citation": "B doi:10.1000/demo-b", "scenario": "target system", "benchmark": "primary outcome", "abstract": "Method B fails and degrades the primary outcome in the target system."},
    ]}
    gap = sc.make_gap("contradiction", "Two studies report opposing outcome claims.", ["A doi:10.1000/demo-a", "B doi:10.1000/demo-b"], "compare", "resolve")
    result = sc.validate_contradiction_gap(project, gap)
    assert result["verdict"] == "COMPARABLE_CONTRADICTION"
test("Contradiction validator accepts comparable opposing claims", t_contradiction_gap_comparability)

def t_contradiction_scope_mismatch():
    project = {"papergraph": [
        {"title": "Method A success", "citation": "A", "scenario": "system one", "benchmark": "outcome one", "abstract": "Method A improves outcome one."},
        {"title": "Method B success", "citation": "B", "scenario": "system two", "benchmark": "outcome two", "abstract": "Method B improves outcome two."},
    ]}
    gap = sc.make_gap("contradiction", "Potential conclusion conflict.", ["A", "B"], "compare", "resolve")
    result = sc.validate_contradiction_gap(project, gap)
    assert result["verdict"] == "NOT_COMPARABLE"
test("Contradiction validator rejects scope mismatch", t_contradiction_scope_mismatch)

def t_prefilter_resolves_citation_identity():
    project = {"papergraph": [
        {"title": "A source-grounded result", "citation": "Author (2024) A source-grounded result doi:10.1000/demo-c", "abstract": "The target variable changes under the stated condition."},
    ]}
    gap = sc.make_gap("mechanism_problem", "The target variable remains unclear under the stated condition.", ["Author (2024) A source-grounded result doi:10.1000/demo-c"], "test", "resolve")
    sufficient, _reason, coverage = sc.prefilter_gap_combination(project, [gap])
    assert sufficient and coverage >= 0.5
test("GRADE prefilter resolves DOI/title citation identity", t_prefilter_resolves_citation_identity)

def t_collision_population():
    project = {"domain": "generic scientific system", "papergraph": [
        {"title": "A", "citation": "A", "method": "time-resolved measurement", "scenario": "coupled process",
         "benchmark": "transition probability", "abstract": "Under 5 mM input, the measurement quantified transition probability.",
         "contribution": "An intermediate state precedes the outcome."},
    ], "evidence": []}
    gap = sc.prepare_gap_for_hypothesis(project, sc.make_gap("mechanism_problem", "The intermediate mechanism remains unclear in the coupled process.", ["A"], "path", "value"))
    population = sc.seed_hypothesis_population(project, [gap], population_size=2, use_llm=False)
    assert len(population) == 2
    assert population[0]["collision_source"]["role"] == "inspiration_only_not_evidence"
    assert "mechanism-stress intervention" not in population[0]["statement"].lower()
    assert "latent damage" not in population[0]["statement"].lower()
    assert population[0]["claim_scope"] == "phenomenological_pending_mechanism"
test("evidence-grounded collision population", t_collision_population)

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

def t_template_allows_experimental_terms():
    idea = {
        "title": "Time-resolved perturbation test of an intermediate state",
        "hypothesis": "At 5 mM input, perturbing the intermediate state changes transition probability before the final outcome.",
        "abstract": "The experiment uses matched baselines, negative controls, ablations, and calibrated metrics to test the causal ordering.",
        "related_work": "Prior measurements report the intermediate state before the transition.",
        "measurable_outputs": ["transition probability", "time to outcome"],
        "evidence_packets": [{"citation": "Paper A", "evidence": "The intermediate state precedes the outcome."}],
    }
    result = sc.detect_hypothesis_template(idea)
    assert not result["is_template"]
    assert result["severity"] != "REJECT"
    assert "baseline" in result["experimental_structure_terms"]
    assert result["evidence_grounded"]
test("Template checker permits normal experimental terminology", t_template_allows_experimental_terms)

def t_mechanism_operationalization_audit():
    incomplete = sc.mechanism_operationalization_audit("An intervention changes an unspecified mediator.", {})
    assert json.loads(incomplete)["verdict"] == "REQUIRES_REVISION"
    complete = sc.mechanism_operationalization_audit(
        "A controlled intervention changes state X before outcome Y.",
        {
            "identity": "State X is the normalized concentration difference between the treated and control components.",
            "location_or_scope": "The measured interface between the treated and control components at the defined observation boundary.",
            "dynamics": "State X is sampled every ten iterations and tested for a threshold change before outcome Y.",
            "reversibility": "Remove the intervention for one recovery interval; State X should return toward its pre-intervention value.",
            "observability": [
                {"modality": "time-resolved sensor", "signal": "State X exceeds the preregistered threshold before outcome Y changes."},
                {"modality": "independent assay", "signal": "The assay reproduces the direction of the State X change."},
            ],
        },
    )
    assert json.loads(complete)["verdict"] == "PASS"
test("Five-dimension mechanism operationalization audit", t_mechanism_operationalization_audit)

def t_mechanism_contract_rejects_generic_mediator():
    candidate = {
        "statement": "If an intervention is applied, outcome Y changes because cumulative damage mediates the effect.",
        "mechanism": "A narrative description of cumulative damage.",
        "causal_chain": ["intervention -> cumulative damage", "cumulative damage -> outcome Y"],
        "mechanism_specification": {
            "identity": "cumulative damage", "location_or_scope": "the defined interface",
            "dynamics": "increases over repeated cycles", "reversibility": "does not recover after removal",
            "intervention": "block the intervention", "counterfactual": "blocking intervention prevents the outcome",
            "observability": [
                {"modality": "measurement A", "signal": "damage marker increases"},
                {"modality": "measurement B", "signal": "independent damage marker increases"},
            ],
        },
        "cross_domain_bridge": {"source_domain": "ecology", "abstract_structure": "state transition", "target_role_mapping": [
            {"lens_role": "initial state", "papergraph_entity": "intervention"},
            {"lens_role": "later state", "papergraph_entity": "outcome Y"},
        ], "novel_mechanism_claim": "cumulative damage"},
    }
    result = sc.mechanism_contract_for_candidate(candidate)
    assert result["verdict"] == "NEEDS_MECHANISM_ENRICHMENT"
    assert not result["checks"]["concrete_mediator"]
test("Mechanism contract rejects generic mediator", t_mechanism_contract_rejects_generic_mediator)

def t_mechanism_contract_accepts_causal_commitment():
    candidate = {
        "statement": "If a controllable input is blocked, mediator X will not change and outcome Y remains at the matched baseline.",
        "mechanism": "Input changes the concentration difference X at the observation boundary, which in turn changes outcome Y.",
        "causal_chain": ["input blockade -> concentration difference X", "concentration difference X -> outcome Y"],
        "mechanism_specification": {
            "identity": "normalized concentration difference X between treated and control components",
            "location_or_scope": "the defined interface between treated and control components",
            "dynamics": "sample X every ten iterations and test whether it crosses the preregistered threshold before Y",
            "reversibility": "remove the input for one recovery interval; X should return toward the matched baseline",
            "intervention": "block the controllable input while holding the matched baseline fixed",
            "counterfactual": "if the input is blocked, X and outcome Y remain at the matched baseline",
            "observability": [
                {"modality": "time-resolved sensor", "signal": "X crosses the preregistered threshold before outcome Y changes"},
                {"modality": "independent assay", "signal": "the assay confirms the direction and timing of the X change"},
            ],
        },
        "cross_domain_bridge": {"source_domain": "ecology", "abstract_structure": "early changes enable later states", "target_role_mapping": [
            {"lens_role": "early state", "papergraph_entity": "concentration difference X"},
            {"lens_role": "later state", "papergraph_entity": "outcome Y"},
        ], "novel_mechanism_claim": "input-induced concentration difference X causes outcome Y"},
        "null_hypothesis": "Blocking the input leaves X and outcome Y unchanged.",
        "alternative_hypothesis": "The input changes X before outcome Y changes.",
        "testable_subhypotheses": ["input changes X", "X precedes outcome Y", "blocking input prevents X and outcome Y changes"],
        "evidence_assignment": [
            {"causal_link": "input -> concentration difference X", "citations": ["Paper Alpha 2024"], "support_level": "partial"},
            {"causal_link": "concentration difference X -> outcome Y", "citations": ["Paper Beta 2023"], "support_level": "novel_candidate"},
        ],
    }
    result = sc.mechanism_contract_for_candidate(candidate)
    assert result["verdict"] == "READY"
    assert result["claim_scope"] == "mechanistic"
test("Mechanism contract accepts causal commitment", t_mechanism_contract_accepts_causal_commitment)

def t_collision_lenses_are_cross_family():
    gaps = [{
        "description": "A high-voltage material interface shows an unresolved reaction and transport trade-off.",
        "hypothesis_ingredients": {"methods": ["controlled intervention"], "scenarios": ["material interface"]},
    }]
    lenses = sc.select_distant_collision_lenses(gaps, count=4)
    assert lenses
    assert all(item["source_domain"] != "materials science" for item in lenses)
test("Collision engine excludes same-family lenses", t_collision_lenses_are_cross_family)

def t_debate_brief_context():
    project = {"papergraph": [
        {
            "title": "Measurement of an intermediate state", "citation": "Paper A",
            "method": "time-resolved measurement", "scenario": "coupled process", "benchmark": "transition probability",
            "abstract": "The intermediate state precedes the outcome under 5 mM input.",
            "contribution": "Quantified the transition mechanism.",
        },
        {
            "title": "Limitation under shifted conditions", "citation": "Paper B",
            "method": "time-resolved measurement", "scenario": "coupled process", "benchmark": "transition probability",
            "abstract": "However, the effect fails under a shifted environment and remains unclear.",
            "contribution": "Reported a limitation of the proposed mechanism.",
        },
    ], "evidence": []}
    gap = sc.make_gap("mechanism_problem", "The intermediate mechanism remains unclear.", ["Paper A", "Paper B"], "path", "value")
    gap["counterfactual_leaves"] = ["If the intermediate is blocked, transition probability should not change."]
    record = {"source_gap": gap, "evidence_packets": [{"citation": "Paper A"}, {"citation": "Paper B"}]}
    brief = sc.generate_debate_brief(project, "The intermediate state causally controls transition probability.", record)
    assert brief["context_validation"]["ready"]
    assert brief["supporting_evidence"] and brief["contradicting_evidence"]
    questions = sc.duzhi_generate_questions(
        "The intermediate state causally controls transition probability.",
        "The intermediate causes the transition.",
        [],
        allowed_types=["constraint_check", "counterexample_challenge"],
        debate_brief=brief,
    )
    assert any("Paper B" in question["question"] for question in questions)
test("Debate brief supplies evidence-bounded counterarguments", t_debate_brief_context)

def t_deepsurvey_keynote_normalization():
    keynote = sc.normalize_keynote({
        "title": "Mechanism paper",
        "key_contributions": ["Contribution A"],
        "methodology": {"approach": ["Method A"], "design_choices": ["Choice A"], "assumptions": ["Assumption A"]},
        "experiments": {"setup": ["Setup A"], "baselines": ["Baseline A"], "main_results": ["Result A"], "additional_studies": ["Ablation A"]},
        "significance": "Significance A",
        "limitations": ["Limitation A"],
        "future_directions": [{"direction": "Test boundary B", "evidence": "Outlook sentence", "status": "author_stated"}],
        "critical_reflections": [{"reflection": "Result needs a matched control", "evidence": "Limitation A", "status": "source_supported"}],
        "tldr": "One-sentence summary.",
        "repository_artifacts": [{"url_or_name": "https://example.org/repo", "artifact_type": "repository", "evidence": "Code available", "analysis_status": "not_fetched"}],
    })
    assert keynote["key_contributions"] == ["Contribution A"]
    assert keynote["experiments"]["baselines"] == ["Baseline A"]
    assert keynote["future_directions"][0]["status"] == "author_stated"
    assert keynote["repository_artifacts"][0]["analysis_status"] == "not_fetched"
test("DeepSurvey Keynote normalization", t_deepsurvey_keynote_normalization)

def t_keynote_cluster_gap_is_evidence_bounded():
    project = {"keynote_knowledge_synthesis": {"cluster_level": [{
        "cluster_id": "cluster_a", "label": "target scenario", "methods": ["Method A", "Method B"],
        "scenarios": ["target scenario"], "benchmarks": ["metric A"], "citations": ["Paper A", "Paper B"],
        "comparison_table": [], "relations": [],
        "cluster_insights": {"common_limitations": ["The boundary mechanism remains unresolved."], "future_directions": []},
    }]}, "papergraph": [], "evidence": []}
    gaps = sc.detect_keynote_cluster_gaps(project, limit=2)
    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "cluster_comparison"
    assert gaps[0]["supporting_references"] == ["Paper A", "Paper B"]
test("Keynote cluster gaps retain evidence references", t_keynote_cluster_gap_is_evidence_bounded)

def t_autogen_hypothesis_text():
    project = {"hypotheses": [{
        "hypothesis_id": "hyp_demo",
        "statement": "A controllable input changes an intermediate state.",
        "mechanism": "The intermediate precedes the measurable outcome.",
    }]}
    text = ac.autogen_hypothesis_text(project, "hyp_demo")
    assert "controllable input" in text and "intermediate" in text
test("AutoGen reads persisted hypothesis before GRADE", t_autogen_hypothesis_text)

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
    "build_keynote_knowledge_synthesis",
    "verify_citation_uniqueness", "assess_novelty", "verify_uniqueness",
    "run_zhizhi_literature_analysis", "parse_literature_text", "build_coverage_matrix",
    "detect_knowledge_gaps", "run_tanxi_gap_exploration", "load_project",
    "semantic_plausibility_for_pair", "evolve_domain_subspaces",
    "build_temporal_knowledge_graph", "detect_structural_knowledge_gaps",
    "find_structural_analogy_transfers", "run_mingli_hypothesis_evolution",
    "generate_idea", "design_experiment", "finalize_idea", "create_hypothesis",
    "run_mechanism_check", "check_internal_consistency", "check_data_consistency",
    "regime_shift_test", "detect_selective_citation", "causal_chain_audit", "mechanism_operationalization_audit",
    "run_yanzhen_mechanism_verification", "ask_socratic_questions",
    "ask_critical_questions", "find_counterexamples", "stress_test_assumptions",
    "moderate_round", "summarize_positions", "extract_emergent_method",
    "run_socratic_hypothesis_debate", "export_research_plan",
    # v3.0
    "tabi_abductive_gap_detection", "extract_evidence_pairs_from_records",
    "counterfactual_gap_analysis", "build_counterfactual_tree",
    "grade_knowledge_sufficiency", "enforce_hypothesis_specificity", "generate_debate_brief",
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
    "run_socratic_hypothesis_debate", "SCIENCE_AGENTS", "PHASES",
    # v3.0
    "tabi_abductive_gap_detection", "counterfactual_gap_analysis",
    "grade_knowledge_sufficiency", "enforce_hypothesis_specificity",
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
