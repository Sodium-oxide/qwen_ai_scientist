"""Full test suite for refactored science_core."""
from __future__ import annotations
import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

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

def t_partial_subhypothesis_json_recovery():
    import _llm
    truncated = '{"sub_hypotheses":[{"focus":"dose rule","causal_chain":["genotype","exposure"]},{"focus":"response\nheterogeneity","causal_chain":["biomarker","outcome"]}],"combination_hypothesis":'
    parsed = _llm.parse_json_object_from_text(truncated, fallback_list_key="sub_hypotheses")
    assert len(parsed["sub_hypotheses"]) == 2
    assert parsed["sub_hypotheses"][1]["focus"] == "response\nheterogeneity"
test("partial sub-hypothesis JSON recovery", t_partial_subhypothesis_json_recovery)

def t_qwen_long_research_request_budget():
    import qwen_adapter as qa
    from tools import TOOLS

    original_prompt = "Boxue 科研闭环：" + "个体化医疗验证方案。" * 4_000
    source_messages = [{"role": "user", "content": original_prompt}]
    rendered, selected_tools, budget = qa.prepare_qwen_request("system", source_messages, TOOLS)

    selected_names = {tool["name"] for tool in selected_tools}
    assert budget["tool_mode"] == "contextual_compact"
    assert budget["estimated_input_units"] <= qa.DASHSCOPE_SAFE_INPUT_UNITS
    assert qa.estimate_qwen_messages_units(rendered) <= qa.DASHSCOPE_SAFE_INPUT_UNITS
    assert "create_research_project" in selected_names
    assert "decompose_research_objective" in selected_names
    assert source_messages[0]["content"] == original_prompt
test("Qwen long research request stays within context budget", t_qwen_long_research_request_budget)

def t_qwen_medium_research_request_uses_compact_tools():
    import qwen_adapter as qa
    from tools import TOOLS

    prompt = "Boxue 科研闭环：" + "个体化医疗验证方案。" * 1_500
    rendered, selected_tools, budget = qa.prepare_qwen_request("system", [{"role": "user", "content": prompt}], TOOLS)

    assert budget["tool_mode"] == "contextual_compact"
    assert not budget["messages_compacted_for_transport"]
    assert len(selected_tools) < len(TOOLS)
    assert qa.estimate_qwen_messages_units(rendered) <= qa.DASHSCOPE_SAFE_INPUT_UNITS
test("Qwen medium research request avoids the full tool catalog", t_qwen_medium_research_request_uses_compact_tools)

def t_qwen_project_tool_omits_auto_injected_brief():
    import qwen_adapter as qa
    from tools import TOOLS

    project_tool = next(tool for tool in TOOLS if tool["name"] == "create_research_project")
    full_definition = qa.transport_tool_definition(project_tool)
    compact_definition = qa.compact_tool_catalog([project_tool])[0]
    assert "research_brief" not in full_definition["input_schema"]["properties"]
    assert "research_brief" not in compact_definition["parameters"]
    assert "omit research_brief" in full_definition["description"]
test("Qwen project tool hides the auto-injected research brief", t_qwen_project_tool_omits_auto_injected_brief)

def t_qwen_truncated_tool_json_requires_retry():
    import qwen_adapter as qa

    truncated = '{"tool_uses":[{"name":"create_research_project","input":{"title":"T","research_brief":"long'
    valid = '{"tool_uses":[{"name":"create_research_project","input":{"title":"T","domain":"D","objective":"O"}}]}'
    assert qa.is_incomplete_tool_json(truncated)
    assert not qa.is_incomplete_tool_json(valid)
test("Qwen truncated tool JSON is retried instead of finalized", t_qwen_truncated_tool_json_requires_retry)

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

def t_domain_reviewer_rejects_surface_word_collisions():
    import _literature_scoring as scoring
    target = "superheavy element synthesis nuclear decay spectroscopy"
    optical = {
        "title": "High precision micro-optical elements on fiber facets",
        "abstract": "Focused-ion beam machining creates optical fiber elements.",
        "venue": "Optics Express",
    }
    kilonova = {
        "title": "A constraint on superheavy elements of a GRB-kilonova",
        "abstract": "Neutron-star merger r-process nucleosynthesis in an astrophysical transient.",
        "venue": "Astrophysical Journal",
    }
    nuclear = {
        "title": "Decay spectroscopy of heavy and superheavy nuclei",
        "abstract": "Nuclear decay chains and spectroscopy for transactinide nuclei.",
        "venue": "Nuclear Physics A",
    }
    assert scoring.domain_review_assessment(optical, target)["verdict"] == "reject"
    assert scoring.domain_review_assessment(kilonova, target)["verdict"] == "reject"
    assert scoring.domain_review_assessment(nuclear, target)["verdict"] != "reject"
    assert scoring.infer_research_field(nuclear) == "nuclear_physics"
    assert scoring.should_reject_for_domain(optical, target)
    assert scoring.should_reject_for_domain(kilonova, target)
    assert not scoring.should_reject_for_domain(nuclear, target)
test("domain reviewer rejects cross-field surface-word collisions", t_domain_reviewer_rejects_surface_word_collisions)

def t_arxiv_category_precedes_keyword_field_heuristics():
    import _literature_scoring as scoring

    misleading_title = {
        "title": "Lithium battery signatures in a cosmological survey",
        "abstract": "Lithium observations constrain early-universe cosmology.",
        "arxiv_categories": ["astro-ph.CO"],
    }
    assert scoring.infer_research_field(misleading_title) == "astrophysics"
test("arXiv category anchors field before keyword heuristics", t_arxiv_category_precedes_keyword_field_heuristics)

def t_interdisciplinary_domain_gate_uses_review_not_reject():
    import _literature_scoring as scoring

    domain = "superheavy element synthesis nuclear decay spectroscopy"
    candidate = {
        "title": "Machine learning prediction of superheavy element half-lives",
        "abstract": "A neural network predicts nuclear decay half-lives and reaction cross sections for superheavy nuclei.",
        "venue": "Physical Review C",
        "arxiv_categories": ["cs.LG"],
    }
    candidate["domain_relevance"] = scoring.domain_relevance_assessment(candidate, domain=domain, query=domain)
    assert not scoring.should_reject_for_domain(candidate, domain=domain, query=domain)
    assert candidate["domain_gate"]["verdict"] == "review"
    assert candidate["domain_gate"]["requires_human_review"]
test("interdisciplinary evidence receives a review decision", t_interdisciplinary_domain_gate_uses_review_not_reject)

def t_import_domain_gate_reports_secondary_rejection():
    import _literature_import as literature_import
    import _project as project_store

    domain = (
        "Stem Cell Biology; Developmental Biology; Cell Fate Determination; "
        "Epigenetics; Chromatin Biology; Cell Reprogramming; Regenerative Medicine"
    )
    query = "stem cell pluripotency differentiation cell fate chromatin epigenetic reprogramming"
    candidate = {
        "title": "Intrinsic plasticity underlies malleability of neural network heterogeneity",
        "abstract": "We study intrinsic plasticity in artificial neural networks and heterogeneous network dynamics.",
        "venue": "Neural Networks",
        "provider": "biorxiv",
        "domain_relevance": {"verdict": "keep", "score": 0.3913, "flags": []},
    }
    with (
        patch.object(project_store, "load_project", return_value={"domain": domain}),
        patch.object(project_store, "load_search", return_value={"query": query, "results": [candidate]}),
        patch.object(literature_import, "log_event") as log_event,
    ):
        try:
            literature_import.import_literature_search_result("project", "search", 0)
            raise AssertionError("Expected the secondary domain review to reject the unrelated neural-network paper.")
        except ValueError as exc:
            error_text = str(exc)

    logged = log_event.call_args.kwargs
    assert "final domain gate" in error_text
    assert logged["verdict"] == "reject"
    assert logged["primary_verdict"] == "keep"
    assert logged["gate_verdict"] == "reject"
    assert logged["rejecting_stage"] == "domain_review"
    assert logged["review_verdict"] == "reject"
    assert "field mismatch" in str(logged["reason"]).lower()
test("import domain gate records final verdict after primary keep", t_import_domain_gate_reports_secondary_rejection)

def t_biology_mechanism_gate_keeps_model_organism_evidence():
    import _literature_scoring as scoring

    domain = (
        "Stem Cell Biology; Developmental Biology; Cell Fate Determination; "
        "Epigenetics; Chromatin Biology; Cell Reprogramming; Regenerative Medicine"
    )
    query = "transcription factor network cellular plasticity"
    candidate = {
        "title": "A hierarchical transcription factor cascade regulates enteroendocrine cell diversity and plasticity in Drosophila",
        "abstract": (
            "Drosophila enteroendocrine cells are specified by a hierarchical transcription factor code. "
            "Changing the code switches subtype identity and demonstrates cellular plasticity during differentiation."
        ),
        "venue": "Nature Communications",
        "provider": "semantic_scholar",
    }
    relevance = scoring.domain_relevance_assessment(candidate, domain=domain, query=query)
    review = scoring.domain_review_assessment(
        {**candidate, "domain_relevance": relevance},
        domain=domain,
    )

    assert scoring.infer_research_field(candidate) == "biology"
    assert relevance["verdict"] == "keep"
    assert relevance["biological_mechanism_evidence"]["qualified"]
    assert review["verdict"] != "reject"
    assert review["retrieval_assessment_preserved"]
test("biology mechanism evidence survives domain review", t_biology_mechanism_gate_keeps_model_organism_evidence)

def t_domain_review_recovers_cached_retrieval_assessment():
    import _literature_import as literature_import
    import _literature_scoring as scoring
    import _project as project_store

    domain = "Stem Cell Biology; Developmental Biology; Cell Fate Determination; Epigenetics; Chromatin Biology"
    query = "transcription factor network cellular plasticity"
    title = "A hierarchical transcription factor cascade regulates enteroendocrine cell diversity and plasticity in Drosophila"
    source_result = {
        "title": title,
        "abstract": "Drosophila transcription factor codes switch enteroendocrine cell identities and reveal cellular plasticity.",
        "venue": "Nature Communications",
        "provider": "semantic_scholar",
    }
    source_result["domain_relevance"] = scoring.domain_relevance_assessment(source_result, domain=domain, query=query)
    project = {
        "domain": domain,
        "papergraph": [{
            "paper_id": "paper_recover",
            "title": title,
            "abstract": source_result["abstract"],
            "venue": source_result["venue"],
            "provider": source_result["provider"],
            "active": False,
            "import_context": {"search_id": "search_recover", "result_index": 0},
        }],
        "evidence": [],
    }
    with (
        patch.object(project_store, "load_project", return_value=project),
        patch.object(project_store, "load_search", return_value={"query": query, "results": [source_result]}),
        patch.object(project_store, "save_project"),
    ):
        review = literature_import.domain_review_paper("project_recover", "paper_recover")

    restored = project["papergraph"][0]
    assert review["verdict"] != "reject"
    assert restored["active"] is True
    assert restored["retrieval_query"] == query
    assert restored["domain_relevance"]["verdict"] == "keep"
test("domain review restores cached retrieval relevance", t_domain_review_recovers_cached_retrieval_assessment)

def t_force_import_preserves_user_override():
    import _literature_import as literature_import
    import _literature_search as literature_search
    import _project as project_store

    domain = "Stem Cell Biology; Developmental Biology; Cell Fate Determination"
    candidate = {
        "title": "Intrinsic plasticity underlies malleability of neural network heterogeneity",
        "abstract": "We study intrinsic plasticity in artificial neural networks and heterogeneous network dynamics.",
        "venue": "Neural Networks",
        "provider": "biorxiv",
        "domain_relevance": {"verdict": "keep", "score": 0.3913, "flags": []},
        "papergraph_input": {
            "title": "Intrinsic plasticity underlies malleability of neural network heterogeneity",
            "citation": "Example et al. (2025)",
            "abstract": "We study intrinsic plasticity in artificial neural networks and heterogeneous network dynamics.",
            "venue": "Neural Networks",
            "provider": "biorxiv",
            "year": "2025",
        },
    }
    with (
        patch.object(project_store, "load_project", return_value={"domain": domain}),
        patch.object(project_store, "load_search", return_value={"query": "stem cell plasticity", "results": [candidate]}),
        patch.object(literature_search, "enrich_papergraph_payload", side_effect=lambda payload, _result: (payload, [])),
        patch.object(literature_import, "import_papergraph_record", return_value=json.dumps({"status": "imported", "record": {"paper_id": "forced"}})) as store_record,
    ):
        response = json.loads(literature_import.import_literature_search_result("project", "search", 0, force_import=True))

    stored = store_record.call_args.kwargs
    assert response["status"] == "imported"
    assert stored["domain_gate"]["verdict"] == "override"
    assert stored["domain_override"]["force_import"] is True
test("force import records a reversible domain override", t_force_import_preserves_user_override)

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
    assert not ls.is_preprint_literature_result({**journal, "arxiv_id": "2601.01234"})
    assert not ls.is_preprint_literature_result({**preprint, "doi": "10.1103/PhysRevC.99.012345"})
    assert ls.stratified_candidate_matches("L3_preprint", preprint)
    assert ls.preprint_result_matches_query(preprint, "lithium battery cathode electrolyte")
    assert not ls.preprint_result_matches_query({"title": "Unrelated robotics preprint", "abstract": "robot manipulation"}, "lithium battery cathode electrolyte")
    treg_query = "genetic background Treg proliferation human studies"
    assert not ls.preprint_result_matches_query(
        {
            "title": "ALS-linked genetic variants in human motor neurons",
            "abstract": "Background studies examine genetic disease mechanisms in human neurons.",
        },
        treg_query,
    )
    assert ls.preprint_result_matches_query(
        {
            "title": "Genetic control of Treg proliferation in human immune homeostasis",
            "abstract": "Treg proliferation was quantified after cytokine stimulation.",
        },
        treg_query,
    )
    preprint_alignment = scoring.core_domain_alignment(preprint, domain="high voltage lithium battery cathode", query="high voltage lithium battery cathode")
    journal_alignment = scoring.core_domain_alignment(journal, domain="high voltage lithium battery cathode", query="high voltage lithium battery cathode")
    assert preprint_alignment["min_core_hits"] == journal_alignment["min_core_hits"]

    original_arxiv = ls.search_arxiv
    try:
        ls.search_arxiv = lambda *_args, **_kwargs: {
            "provider": "arxiv",
            "status": "ok",
            "results": [preprint],
        }
        blocks = ls.fetch_stratified_layer_blocks(
            "lithium battery cathode degradation",
            ["arxiv", "semantic_scholar"],
            {"layer": "L3_preprint", "quota": 1, "query_suffix": ""},
            query_plan=[{"branch": "primary", "query": "lithium battery cathode degradation"}],
            domain="high-voltage lithium batteries",
        )
        candidates = ls.flatten_literature_results(blocks)
        assert all(block.get("provider") != "semantic_scholar" for block in blocks)
        assert any(ls.stratified_candidate_matches("L3_preprint", item) for item in candidates)
    finally:
        ls.search_arxiv = original_arxiv
test("L3 preprint uses compact provider query without stricter lexical gate", t_preprint_l3_query_and_classification)

def t_preprint_domain_gate_uses_query_and_never_citation_count():
    import _literature_scoring as scoring

    domain = (
        "Immunology / Systems Biology / T Cell Biology / Cytokine Signaling / "
        "Regulatory T Cells (Tregs) / Inflammation / Autoimmunity"
    )
    query = "genetic background Treg proliferation human studies"
    relevant_preprint = {
        "title": "SIRPG modulates effector differentiation of human CD8 T Cells",
        "abstract": (
            "Genetic variation in SIRPG regulates cytokine production, immune homeostasis, "
            "and autoimmune T-cell differentiation."
        ),
        "provider": "biorxiv",
        "venue": "biorxiv",
        "year": "2025",
        "citation_count": 0,
    }
    primary = scoring.domain_relevance_assessment(relevant_preprint, domain=domain, query=query)
    relevant_preprint["domain_relevance"] = primary
    review = scoring.domain_review_assessment(relevant_preprint, domain=domain, query=query)
    assert primary["verdict"] == "keep"
    assert review["foreign_field"] is False
    assert review["verdict"] != "reject"
    assert not scoring.should_reject_for_domain(relevant_preprint, domain=domain, query=query)

    low_score_p0 = {
        "title": "Treg proliferation and cytokine signaling",
        "abstract": "Treg proliferation under IL-2 stimulation was measured in human cells.",
        "provider": "medrxiv",
        "venue": "medrxiv",
        "year": "2025",
        "citation_count": 0,
        "publication_quality_score": 0.4,
        "domain_relevance": {"verdict": "keep", "score": 0.05, "flags": ["field_mismatch"]},
    }
    assert not scoring.should_reject_for_domain(low_score_p0, domain=domain, query=query)
    quality = scoring.publication_quality_assessment(relevant_preprint)
    assert quality["quality_score"] >= 0.65
    assert "new_paper_protection" in quality["flags"]
test("P0 domain gate preserves relevant zero-citation preprints", t_preprint_domain_gate_uses_query_and_never_citation_count)

def t_medrxiv_paginates_before_local_query_filtering():
    import _literature_search as ls

    def make_item(index, matching):
        return {
            "title": "Genetic variants predict pharmacokinetics" if matching else f"Unrelated clinical preprint {index}",
            "abstract": "Genetic variants and pharmacokinetics determine drug exposure" if matching else "Unrelated topic",
            "authors": "Researcher A",
            "date": "2026-06-01",
            "doi": f"10.1101/2026.06.01.{index}",
            "category": "pharmacology",
        }

    def fake_get_json(url, headers=None, timeout=20.0):
        cursor = int(url.rsplit("/", 1)[-1])
        collection = [make_item(index + cursor, cursor >= 100) for index in range(100)]
        return {"collection": collection, "messages": [{"total": "200"}]}

    with patch.object(ls, "http_get_json", side_effect=fake_get_json):
        result = ls.search_biorxiv_or_medrxiv(
            "medrxiv",
            "genetic variants pharmacokinetics",
            max_results=5,
            days_back=365,
        )
    assert result["pages_scanned"] == 2
    assert result["scanned_result_count"] == 200
    assert result["matched_result_count"] >= 5
    assert len(result["results"]) == 5
test("medRxiv paginates beyond the first metadata page", t_medrxiv_paginates_before_local_query_filtering)

def t_preprint_recovery_retries_medrxiv_before_arxiv():
    import _literature_search as ls
    calls = []

    def fake_preprint(provider, query, max_results=10, days_back=365):
        calls.append((provider, days_back))
        return {"provider": provider, "query": query, "status": "ok", "results": [{"title": "match"}]}

    with patch.object(ls, "search_preprint_api", side_effect=fake_preprint), patch.object(ls, "search_arxiv") as arxiv:
        _, report = ls.recover_preprint_layer_candidates(
            query="genetic variants pharmacokinetics",
            query_plan=[],
            domain="precision medicine",
            max_results=5,
            providers=["medrxiv", "arxiv"],
        )
    assert calls[0] == ("medrxiv", 186)
    assert not arxiv.called
    assert report["outcome"] == "recovered"
test("P0 recovery retries medRxiv before arXiv", t_preprint_recovery_retries_medrxiv_before_arxiv)


def t_preprint_zero_result_cache_avoids_repeat_metadata_scan():
    import _literature_search as ls

    calls = []

    def fake_get_json(url, headers=None, timeout=20.0):
        calls.append(url)
        return {
            "collection": [{
                "title": "Unrelated clinical metadata record",
                "abstract": "No matching mechanism terms are present.",
                "authors": "Researcher A",
                "date": "2026-06-01",
                "doi": "10.1101/2026.06.01.999999",
                "category": "clinical trial",
            }],
            "messages": [{"total": "1"}],
        }

    ls.PREPRINT_ZERO_RESULT_CACHE.clear()
    try:
        with patch.object(ls, "http_get_json", side_effect=fake_get_json):
            first = ls.search_biorxiv_or_medrxiv(
                "medrxiv",
                "unique cache miss chromatin sentinel",
                max_results=5,
                days_back=365,
                scan_limit=30,
            )
            second = ls.search_biorxiv_or_medrxiv(
                "medrxiv",
                "unique cache miss chromatin sentinel",
                max_results=5,
                days_back=365,
                scan_limit=30,
            )
        assert first["matched_result_count"] == 0
        assert not first["zero_result_cache_hit"]
        assert second["matched_result_count"] == 0
        assert second["zero_result_cache_hit"]
        assert second["scanned_result_count"] == 0
        assert len(calls) == 1
    finally:
        ls.PREPRINT_ZERO_RESULT_CACHE.clear()
test("zero-result preprint cache prevents repeat metadata scans", t_preprint_zero_result_cache_avoids_repeat_metadata_scan)


def t_socrates_retrieval_limits_preprints_to_l3_with_small_budget():
    import _socrates as socrates
    import _literature_search as ls

    captured = {}

    def fake_stratified(**kwargs):
        captured.update(kwargs)
        return json.dumps({"search_id": "search_socrates_preprint", "total_results": 0})

    with patch.object(ls, "search_literature_stratified", side_effect=fake_stratified):
        report = socrates.socrates_call_zhizhi_targeted_search(
            project_id="project_socrates_preprint",
            query="chromatin accessibility cellular plasticity reversibility",
            domain="Stem Cell Biology",
            field="reversibility",
            question="What makes the mechanism reversible?",
            providers=["semantic_scholar", "biorxiv", "medrxiv"],
            max_results=12,
            imports_per_query=2,
            use_llm=False,
            preprint_scan_limit=180,
            preprint_provider_result_target=3,
        )
    assert report["searches"] == 1
    assert captured["preprint_layers"] == {"L3_preprint"}
    assert captured["preprint_scan_limit"] == 180
    assert captured["preprint_provider_result_target"] == 3
    assert captured["preprint_recovery_windows"] == (12,)
    assert captured["preprint_recovery_max_variants"] == 1
    assert captured["preprint_max_branches"] == 1
test("Socrates uses a bounded L3-only preprint policy", t_socrates_retrieval_limits_preprints_to_l3_with_small_budget)


def t_socrates_preprint_target_skips_extra_slow_providers():
    import _literature_search as ls

    arxiv_block = {
        "provider": "arxiv",
        "query": "chromatin plasticity",
        "status": "ok",
        "results": [{"title": "P1"}, {"title": "P2"}, {"title": "P3"}],
    }
    with patch.object(ls, "search_arxiv", return_value=arxiv_block), patch.object(ls, "search_preprint_api") as preprint_search:
        blocks = ls.fetch_stratified_layer_blocks(
            "chromatin plasticity",
            ["arxiv", "biorxiv", "medrxiv"],
            {"layer": "L3_preprint", "quota": 1, "query_suffix": ""},
            query_plan=[{"branch": "primary", "query": "chromatin plasticity"}],
            domain="Stem Cell Biology",
            preprint_layers={"L3_preprint"},
            preprint_provider_result_target=3,
        )
    assert not preprint_search.called
    skipped = [block for block in blocks if block.get("status") == "skipped"]
    assert {block["provider"] for block in skipped} == {"biorxiv", "medrxiv"}
    assert all("sufficient_preprint_candidates=3" in block.get("skipped_provider_reason", "") for block in skipped)
test("Socrates skips slow preprint providers after sufficient candidates", t_socrates_preprint_target_skips_extra_slow_providers)


def t_socrates_query_selection_deduplicates_across_fields():
    import _socrates as socrates

    selected = socrates.select_untried_socrates_queries(
        ["identity", "dynamics"],
        {
            "identity": ["shared mechanism evidence"],
            "dynamics": ["shared mechanism evidence"],
        },
        attempted_queries=set(),
        limit=2,
        attempted_retrieval_queries=set(),
    )
    assert selected == [("identity", "shared mechanism evidence")]
test("Socrates does not retrieve the same query for two fields", t_socrates_query_selection_deduplicates_across_fields)


def t_socrates_skips_generic_density_holes_without_external_search():
    import _socrates as socrates

    ready, reason = socrates.socrates_retrieval_ready({
        "proposed_mediator": "unresolved",
        "input": "Density hole: method in scenario",
        "output": "unresolved",
    })
    assert not ready
    assert "concrete proposed mediator" in reason
test("Socrates does not search a generic density-hole mechanism", t_socrates_skips_generic_density_holes_without_external_search)


def t_socrates_import_continues_after_top_duplicate_candidate():
    import _literature_import as literature_import
    import _literature_search as literature_search
    import _socrates as socrates

    attempted_indexes = []

    def fake_search(**kwargs):
        return json.dumps({"search_id": "search_duplicate_first", "total_results": 3})

    def fake_import(project_id, search_id, result_index, use_llm=False):
        attempted_indexes.append(result_index)
        if result_index == 0:
            return json.dumps({"status": "duplicate", "existing_record": {"paper_id": "old"}})
        return json.dumps({"status": "imported", "record": {"paper_id": "new_evidence"}})

    with patch.object(literature_search, "search_literature_stratified", side_effect=fake_search), patch.object(literature_import, "import_literature_search_result", side_effect=fake_import), patch.object(literature_import, "domain_review_paper", return_value={"verdict": "keep"}), patch.object(literature_import, "extract_paper_keynote"):
        report = socrates.socrates_call_zhizhi_targeted_search(
            project_id="project_duplicate_first",
            query="chromatin accessibility reprogramming reversibility",
            domain="Stem Cell Biology",
            field="reversibility",
            question="Is chromatin resetting reversible?",
            providers=["semantic_scholar"],
            max_results=8,
            imports_per_query=1,
            use_llm=False,
        )
    assert attempted_indexes == [0, 1]
    assert report["duplicate_candidates"] == 1
    assert report["imports"] == 1
    assert report["paper_ids"] == ["new_evidence"]
test("Socrates checks later candidates after a duplicate", t_socrates_import_continues_after_top_duplicate_candidate)


def t_method_scenario_benchmark_louvain_detects_weighted_research_branches():
    from _gap_detection import run_method_scenario_benchmark_louvain

    records = [
        {"paper_id": "p1", "citation": "P1", "method": "ATAC-seq", "scenario": "iPSC reprogramming", "benchmark": "chromatin accessibility", "publication_quality_score": 0.9, "citation_count": 20},
        {"paper_id": "p2", "citation": "P2", "method": "ATAC-seq", "scenario": "iPSC reprogramming", "benchmark": "pluripotency marker expression", "publication_quality_score": 0.8, "citation_count": 10},
        {"paper_id": "p3", "citation": "P3", "method": "lineage tracing", "scenario": "intestinal regeneration", "benchmark": "cell fate stability", "publication_quality_score": 0.9, "citation_count": 30},
        {"paper_id": "p4", "citation": "P4", "method": "lineage tracing", "scenario": "intestinal regeneration", "benchmark": "clonal persistence", "publication_quality_score": 0.8, "citation_count": 15},
    ]
    result = run_method_scenario_benchmark_louvain(records)
    assert result["status"] == "success"
    assert result["graph_type"] == "method_scenario_benchmark_evidence"
    assert result["edge_basis"].startswith("weighted co-occurrence")
    assert result["num_communities"] >= 2
    assert result["modularity"] is not None
    assert all("supporting_references" in item for item in result["communities"])
test("MSB Louvain identifies weighted research branches", t_method_scenario_benchmark_louvain_detects_weighted_research_branches)

def t_open_access_pdf_excerpt_is_not_gated_by_abstract_quality():
    import _literature_search as literature_search

    payload = {
        "title": "Complete abstract paper",
        "abstract": "A" * 500,
        "method": "clinical cohort study",
        "scenario": "precision medicine",
        "benchmark": "treatment response",
    }
    result = {"open_access_pdf": "https://example.org/open-paper.pdf"}
    with patch.object(literature_search, "fetch_pdf_text_excerpt", return_value="PDF methods and results excerpt.") as fetch:
        enriched, sources = literature_search.enrich_papergraph_payload(payload, result)
    assert fetch.called
    assert enriched["full_text_excerpt"] == "PDF methods and results excerpt."
    assert enriched["_full_text_enrichment"]["status"] == "extracted"
    assert "open_access_pdf_text" in sources
test("Open-access PDF excerpts are independent of abstract-quality repair", t_open_access_pdf_excerpt_is_not_gated_by_abstract_quality)

def t_arxiv_detail_supplies_pdf_for_full_text_extraction():
    import _literature_search as literature_search

    payload = {
        "title": "ArXiv paper",
        "abstract": "B" * 500,
        "method": "simulation",
        "scenario": "materials system",
        "benchmark": "transport coefficient",
        "arxiv_id": "2601.12345",
    }
    with (
        patch.object(literature_search, "fetch_arxiv_by_id", return_value={"open_access_pdf": "https://arxiv.org/pdf/2601.12345"}),
        patch.object(literature_search, "fetch_pdf_text_excerpt", return_value="ArXiv full-text excerpt."),
    ):
        enriched, sources = literature_search.enrich_papergraph_payload(payload, {})
    assert enriched["full_text_excerpt"] == "ArXiv full-text excerpt."
    assert enriched["_full_text_enrichment"]["source_url"] == "https://arxiv.org/pdf/2601.12345"
    assert "arxiv_detail" in sources and "open_access_pdf_text" in sources
test("arXiv detail can supply a missing full-text PDF link", t_arxiv_detail_supplies_pdf_for_full_text_extraction)

def t_failed_metadata_lookup_is_not_mislabeled_as_no_open_access():
    import _literature_search as literature_search

    payload = {
        "title": "Lookup-failure paper",
        "abstract": "D" * 500,
        "method": "clinical cohort study",
        "scenario": "precision medicine",
        "benchmark": "treatment response",
        "semantic_scholar_id": "unavailable-id",
    }
    with patch.object(literature_search, "fetch_semantic_scholar_paper_detail", side_effect=RuntimeError("network denied")):
        enriched, _ = literature_search.enrich_papergraph_payload(payload, {})
    assert enriched["_full_text_enrichment"]["status"] == "metadata_lookup_failed"
    assert "network denied" in enriched["_full_text_enrichment"]["error"]
test("Failed metadata lookup remains distinguishable from absent open access", t_failed_metadata_lookup_is_not_mislabeled_as_no_open_access)

def t_full_text_metadata_probe_uses_fast_fail_semantic_scholar_path():
    import _literature_search as literature_search

    with (
        patch.object(literature_search, "semantic_scholar_cache_get", return_value=None),
        patch.object(literature_search, "wait_for_semantic_scholar_rate_limit"),
        patch.object(literature_search, "http_get_json", return_value={"paperId": "paper-id"}) as fetch,
        patch.object(literature_search, "semantic_scholar_cache_put"),
        patch.object(literature_search, "semantic_scholar_get_json", side_effect=AssertionError("slow retry path used")),
    ):
        detail = literature_search.fetch_semantic_scholar_paper_detail("paper-id", fast_fail=True)
    assert detail["paperId"] == "paper-id"
    assert fetch.call_args.kwargs["timeout"] == 8.0
test("Full-text metadata probes bypass Semantic Scholar's long retry loop", t_full_text_metadata_probe_uses_fast_fail_semantic_scholar_path)

def t_full_text_backfill_repairs_existing_papergraph_records():
    import _literature_import as literature_import
    import _literature_search as literature_search
    import _utils as utils

    record = {
        "paper_id": "paper_full_text_backfill",
        "title": "Existing paper",
        "abstract": "C" * 500,
        "conclusion": "",
        "full_text_excerpt": "",
        "method": "clinical cohort study",
        "scenario": "precision medicine",
        "benchmark": "treatment response",
        "contribution": "Existing clinical evidence.",
        "limitation": "No explicit limitation extracted.",
        "semantic_scholar_id": "semantic-id",
        "enrichment_sources": [],
    }
    project = {"papergraph": [record], "evidence": []}
    llm_calls = []

    def fake_enrich(payload, _result, **_kwargs):
        enriched = dict(payload)
        enriched["open_access_pdf"] = "https://example.org/paper.pdf"
        enriched["full_text_excerpt"] = "Directly extracted full-text evidence."
        enriched["_full_text_enrichment"] = {
            "status": "extracted",
            "attempted": True,
            "source_url": "https://example.org/paper.pdf",
            "excerpt_chars": 38,
        }
        return enriched, ["open_access_pdf_available", "open_access_pdf_text"]

    def fake_reextract(payload, *, force=False):
        llm_calls.append(force)
        return payload, {"attempted": True, "succeeded": True, "error": "", "extractor": "test"}

    with (
        patch.object(literature_search, "enrich_papergraph_payload", side_effect=fake_enrich),
        patch.object(literature_import, "maybe_llm_reextract_structure", side_effect=fake_reextract),
    ):
        _, report = utils.repair_project_extraction_quality(project)
    assert report["attempted"] == 1
    assert record["full_text_excerpt"] == "Directly extracted full-text evidence."
    assert record["full_text_enrichment"]["status"] == "extracted"
    assert "open_access_pdf_text" in record["enrichment_sources"]
    assert llm_calls == [True]
test("Extraction repair backfills missing PaperGraph full-text excerpts", t_full_text_backfill_repairs_existing_papergraph_records)

def t_llm_retry_omits_empty_full_text_field_labels():
    import _literature_import as literature_import

    captured = {}

    def fake_extract(text, use_llm=True):
        captured["text"] = text
        return {"method": "clinical cohort study", "scenario": "precision medicine", "benchmark": "treatment response"}

    with patch.object(literature_import, "extract_paper_structure", side_effect=fake_extract):
        literature_import.maybe_llm_reextract_structure(
            {"title": "Paper", "abstract": "Abstract text", "conclusion": "", "full_text_excerpt": ""},
            force=True,
        )
    assert "Full text excerpt:" not in captured["text"]
    assert "Conclusion:" not in captured["text"]
test("LLM retry does not turn empty excerpt labels into conclusions", t_llm_retry_omits_empty_full_text_field_labels)

def t_full_text_backfill_is_bounded_per_repair_round():
    import _literature_search as literature_search
    import _utils as utils

    project = {
        "papergraph": [
            {
                "paper_id": f"paper_{index}",
                "title": f"Paper {index}",
                "abstract": "E" * 500,
                "method": "clinical cohort study",
                "scenario": "precision medicine",
                "benchmark": "treatment response",
                "contribution": "Existing evidence.",
                "limitation": "No explicit limitation extracted.",
                "semantic_scholar_id": f"semantic-{index}",
                "full_text_excerpt": "",
            }
            for index in range(4)
        ],
        "evidence": [],
    }
    calls = []

    def fake_enrich(payload, _result, **_kwargs):
        calls.append(payload["paper_id"])
        enriched = dict(payload)
        enriched["_full_text_enrichment"] = {"status": "no_open_access_pdf", "attempted": False}
        return enriched, []

    with patch.object(literature_search, "enrich_papergraph_payload", side_effect=fake_enrich):
        _, report = utils.repair_project_extraction_quality(project, max_full_text_attempts=3)
    assert len(calls) == 3
    assert report["full_text_attempted"] == 3
    assert report["full_text_deferred"] == 1
test("Full-text backfill is bounded per automatic repair round", t_full_text_backfill_is_bounded_per_repair_round)

def t_full_text_lookup_failures_retry_only_after_cooldown():
    import _utils as utils

    state = {
        "status": "metadata_lookup_failed",
        "attempted_at": 1_000.0,
        "retry_after_seconds": 900,
    }
    assert not utils.full_text_enrichment_retry_due(state, now=1_899.0)
    assert utils.full_text_enrichment_retry_due(state, now=1_900.0)
    assert not utils.full_text_enrichment_retry_due({"status": "no_open_access_pdf"}, now=9_999.0)
test("Full-text metadata failures respect a retry cooldown", t_full_text_lookup_failures_retry_only_after_cooldown)


def t_serial_subspace_retrieval_plan_and_quotas():
    import _literature_import as li
    import _literature_search as ls
    import _project as project_module

    subspace_map = {
        "generated_by": "test",
        "subspaces": [
            {"subspace_id": f"s{i}", "name": f"Subspace {i}", "keywords": [f"anchor{i}", "battery"]}
            for i in range(12)
        ],
    }
    plan = project_module.build_serial_subspace_query_plan(
        "energy storage",
        "A long brief whose explicit coverage must be decomposed before provider queries.",
        max_core_rounds=8,
        boundary_extension_rounds=3,
        subspace_map=subspace_map,
    )
    assert len(plan["core_branches"]) == 8
    assert len(plan["boundary_extensions"]) == 3
    assert all(item["phase"] == "core_subspace" for item in plan["core_branches"])
    assert all(item["phase"] == "boundary_extension" for item in plan["boundary_extensions"])
    assert sum(ls.normalize_stratified_layer_quotas(
        {"L0_review": 0, "L1_milestone": 2, "L2_top_latest": 4, "L3_preprint": 1, "L4_regular": 5},
        max_results=12,
    ).values()) == 12

    candidates = [
        {"title": f"{layer}-{index}", "stratified_layer": layer, "result_index": index}
        for index, layer in enumerate(("L1_milestone", "L2_top_latest", "L2_top_latest", "L3_preprint", "L4_regular", "L4_regular"))
    ]
    selected, report = li.select_zhizhi_import_results(
        candidates,
        6,
        layer_minimums={"L0_review": 0, "L1_milestone": 1, "L2_top_latest": 2, "L3_preprint": 1, "L4_regular": 2},
    )
    assert len(selected) == 6
    assert report["custom_layer_minimums"] is True
    assert report["selected_counts_by_layer"] == {"L1_milestone": 1, "L2_top_latest": 2, "L3_preprint": 1, "L4_regular": 2}
test("serial subspace retrieval uses independent quotas and import budgets", t_serial_subspace_retrieval_plan_and_quotas)


def t_search_query_is_not_globally_sanitized():
    import _literature_search as ls
    query = "superheavy elements island of stability nuclear shell model synthesis reaction"
    assert not hasattr(ls, "sanitize_search_query")
    plan = ls.build_domain_query_plan(query, domain="nuclear physics", max_branches=0)
    assert plan == [{"branch": "primary", "query": query}]
test("search preserves the supplied query without global sanitization", t_search_query_is_not_globally_sanitized)

def t_provider_queries_are_english_only():
    import _literature_search as ls
    source = "遗传变异 AND 药物代谢酶活性 AND 药代动力学参数"
    translated = ls.english_provider_query(source, domain="Personalized Medicine", allow_llm=False)
    assert translated["query"] == "genetic variation drug metabolizing enzyme activity pharmacokinetic parameters"
    assert translated["translation_method"] == "glossary"
    assert ls.is_english_provider_query(translated["query"])
    plan = ls.normalize_english_query_plan([{"branch": "pk", "query": source}], domain="Personalized Medicine", allow_llm=False)
    assert plan[0]["query"] == translated["query"]
    assert plan[0]["source_query"] == source
test("provider queries translate Chinese scientific terms to English", t_provider_queries_are_english_only)

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

def t_inactive_domain_review_records_are_excluded():
    import _pipeline as pipeline
    records = pipeline.project_records_for_mapping({
        "papergraph": [
            {"paper_id": "keep", "title": "Relevant", "active": True},
            {"paper_id": "reject", "title": "Noise", "active": False},
        ],
        "evidence": [
            {"paper_id": "keep", "citation": "Relevant", "active": True},
            {"paper_id": "reject", "citation": "Noise", "active": False},
        ],
    })
    assert {record.get("paper_id") for record in records} == {"keep"}
test("inactive domain-review records stay out of PaperGraph reasoning", t_inactive_domain_review_records_are_excluded)

def t_boxue_recovers_from_a_stale_requested_plan_id():
    import _pipeline as pipeline

    active_plan = {
        "boxue_delegation_plan_id": "boxue_current_plan",
        "tasks": [{"task_id": "task_1", "phase": "ZhiZhi"}],
    }
    with patch.object(pipeline, "boxue_find_active_plan", return_value=active_plan):
        payload = pipeline.boxue_load_or_create_plan(
            project={"boxue_delegation_plans": []},
            project_id="project_1",
            goal="test",
            phases=None,
            plan_id="boxue_plan_stale_identifier",
            max_steps=10,
            max_parallel_agents=3,
        )
    assert payload["boxue_delegation_plan_id"] == "boxue_current_plan"
    assert payload["reused_existing_plan"] is True
    assert payload["requested_plan_id"] == "boxue_plan_stale_identifier"
    assert payload["missing_plan_recovered"] is True
    assert payload["plan_recovery_reason"] == "requested_plan_id_not_found"
test("Boxue recovers a stale requested plan id", t_boxue_recovers_from_a_stale_requested_plan_id)

def t_boxue_first_run_instruction_forbids_invented_plan_ids():
    import skill
    import tools

    round_tool = next(tool for tool in tools.TOOLS if tool["name"] == "run_boxue_research_round")
    plan_description = round_tool["input_schema"]["properties"]["plan_id"]["description"]
    assert "Never invent" in plan_description
    assert "omit plan_id" in skill.BASE_SYSTEM_PROMPT
test("Boxue first-run guidance forbids invented plan ids", t_boxue_first_run_instruction_forbids_invented_plan_ids)

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

def t_tabi_substantive_audit():
    project = {"papergraph": [
        {"paper_id": "theory", "citation": "Theory (2024)", "title": "Model prediction for interface transport", "method": "interface transport", "scenario": "target material", "abstract": "A theoretical model predicts a threshold transport transition."},
        {"paper_id": "experiment", "citation": "Experiment (2025)", "title": "Operando measurement of interface transport", "method": "interface transport", "scenario": "target material", "abstract": "An operando experiment measured an unexpected transition under the same conditions."},
    ]}
    gap = sc.make_gap("contradiction", "Theory and experiment report conflicting interface transport thresholds.", ["Theory (2024)", "Experiment (2025)"], "matched test", "resolve tension")
    audit = sc.tabi_mechanism_assessment(project, gap)
    assert audit["substantive"] and audit["contradiction_score"] >= 8
test("TABI distinguishes substantive theory-evidence tension", t_tabi_substantive_audit)

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

def t_core_mechanism_filter_blocks_boundary_drift():
    project = {"papergraph": [
        {"paper_id": "core", "citation": "Core", "title": "Electrode interface transport", "abstract": "Electrode interface transport controls cycle stability.", "method": "interface engineering", "scenario": "electrode cycling", "benchmark": "cycle stability", "retrieval_phase": "core_subspace", "domain_review_verdict": "keep"},
        {"paper_id": "boundary", "citation": "Boundary", "title": "Auction optimization in a seafood supply chain", "abstract": "Auction optimization improves logistics.", "method": "electronic auction", "scenario": "supply chain", "benchmark": "cost", "retrieval_phase": "boundary_extension", "domain_review_verdict": "review"},
    ]}
    drifting = sc.make_gap("migration", "Cross-disciplinary unconnected pair: electronic auction and cycle stability.", ["Boundary", "Core"], "bridge", "novel")
    grounded = sc.make_gap("contradiction", "Interface transport and electrode cycle stability show conflicting threshold observations.", ["Core"], "matched test", "resolve")
    chosen = sc.select_mechanism_hypothesis_gaps(project, [drifting, grounded], limit=3)
    assert len(chosen) == 1 and chosen[0]["gap_id"] == grounded["gap_id"]
test("core mechanism selector excludes boundary-extension drift", t_core_mechanism_filter_blocks_boundary_drift)

def t_socrates_rejects_cross_context_evidence():
    import _socrates as soc
    project = {"papergraph": [
        {"paper_id": "core", "citation": "Core", "title": "Electrode interface measurement", "abstract": "Electrode interface transport was measured by operando spectroscopy.", "method": "interface engineering", "scenario": "electrode cycling", "benchmark": "cycle stability", "domain_review_verdict": "keep"},
        {"paper_id": "noise", "citation": "Noise", "title": "Solar stadium control economics", "abstract": "A control intervention reduces photovoltaic costs.", "method": "economic control", "scenario": "stadium photovoltaics", "benchmark": "cost", "domain_review_verdict": "review"},
    ]}
    contract = {"context": "electrode interface transport", "input": "interface engineering electrode cycling", "proposed_mediator": "interface transport", "output": "cycle stability", "evidence": {"intervention": [{"paper_id": "noise", "citation": "Noise", "excerpt": "A control intervention reduces photovoltaic costs."}]}}
    audit = soc.validate_mechanism_contract_evidence(project, contract)
    assert audit["rejected"] and not contract["evidence"]["intervention"]
test("Socrates rejects cross-context contract evidence", t_socrates_rejects_cross_context_evidence)

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

# --- Causal decomposition and evidence-first flow ---
print("\n-- Causal Decomposition and Evidence Flow --")

def t_objective_decomposition_splits_composite_problem():
    objective = "电极材料稳定性、电解液分解电位、离子迁移动力学以及SEI演化，是否共同决定能量密度上限和循环寿命阈值"
    brief = "完整任务书：只使用指定文献源；必须覆盖四个子空间；禁止把方法组合直接作为高价值Gap。"
    decomposition = sc.build_objective_decomposition(
        objective,
        domain="lithium-ion batteries",
        research_brief=brief,
        max_subhypotheses=6,
        use_llm=False,
    )
    sub_hypotheses = decomposition["sub_hypotheses"]
    assert len(sub_hypotheses) == 4
    assert [item["id"] for item in sub_hypotheses] == ["SH1", "SH2", "SH3", "SH4"]
    assert all(item["retrieval_query"] and item["evidence_window"]["P0_latest_preprint"] for item in sub_hypotheses)
    assert decomposition["combination_hypothesis"]["status"] == "blocked_on_component_evidence"
    assert decomposition["research_brief"] == brief
test("Decomposer splits a composite objective before retrieval", t_objective_decomposition_splits_composite_problem)

def t_cli_preserves_verbatim_research_brief_for_project_creation():
    import main
    raw_prompt = "创建项目。\n检索要求：只使用指定来源。\nGap要求：不要用组合空洞。"
    input_without_brief = {"title": "T", "domain": "D", "objective": "O"}
    enriched = main.inject_verbatim_research_brief("create_research_project", input_without_brief, raw_prompt)
    assert enriched["research_brief"] == raw_prompt
    assert input_without_brief.get("research_brief") is None
    explicit = main.inject_verbatim_research_brief(
        "create_research_project", {**input_without_brief, "research_brief": "explicit"}, raw_prompt
    )
    assert explicit["research_brief"] == "explicit"
test("CLI preserves the full task brief for project creation", t_cli_preserves_verbatim_research_brief_for_project_creation)

def t_cli_prints_agent_final_response():
    import main
    output = io.StringIO()
    args = argparse.Namespace(serve_cron=False, prompt=["status"])
    with patch.object(main, "parse_args", return_value=args), patch.object(main, "run_agent", return_value="completed response"):
        with redirect_stdout(output):
            main.main()
    assert output.getvalue().strip() == "completed response"
test("CLI prints the completed agent response", t_cli_prints_agent_final_response)

def t_causal_chain_normalization_and_break_detection():
    normalized = sc.normalize_causal_chains([{
        "trigger": "raise cutoff voltage",
        "steps": [{"claim": "cation mixing increases", "evidence": "operando XRD", "evidence_type": "experimental"}],
        "outcome": "capacity retention decreases",
        "observables": ["XRD peak ratio"],
        "interventions": ["cutoff voltage"],
    }])
    record = {"paper_id": "p1", "citation": "Example (2026)", "causal_chains": normalized}
    graph = sc.build_causal_evidence_graph([record])
    assert len(graph["nodes"]) >= 3 and any(edge["relation"] == "leads_to" for edge in graph["edges"])
    incomplete = {"paper_id": "p2", "citation": "Incomplete (2026)", "causal_chains": [{"chain_id": "C2", "trigger": "temperature", "outcome": "rate capability"}]}
    incomplete_graph = sc.build_causal_evidence_graph([incomplete])
    project = {"papergraph": [incomplete], "hypotheses": [], "sub_hypotheses": [], "causal_evidence_graph": incomplete_graph}
    gaps = sc.detect_causal_chain_break_gaps(project, limit=2)
    assert gaps and gaps[0]["gap_type"] == "causal_chain_break"
    assert gaps[0]["causal_gap"]["missing_kind"] == "intermediate_mechanism"
test("Causal graph retains evidence and TanXi detects broken links", t_causal_chain_normalization_and_break_detection)

def t_causal_gap_preserves_subhypothesis_provenance():
    import _gap_detection as gaps
    record = {
        "paper_id": "p1",
        "citation": "Example (2026)",
        "retrieval_branch": "SH2",
        "causal_chains": [{"trigger": "biomarker", "outcome": "drug response"}],
    }
    graph = gaps.build_causal_evidence_graph([record])
    project = {"sub_hypotheses": [{"id": "SH2", "focus": "response biomarker"}], "causal_evidence_graph": graph}
    detected = gaps.detect_causal_chain_break_gaps(project, limit=1)
    assert graph["chains"][0]["sub_hypothesis_id"] == "SH2"
    assert detected[0]["sub_hypothesis_id"] == "SH2"
test("TanXi causal gaps retain the retrieval sub-hypothesis", t_causal_gap_preserves_subhypothesis_provenance)

def t_preprint_priority_and_counterfactual_plan():
    import _literature_search as literature_search
    import _socrates as soc
    layers = literature_search.stratified_literature_layers(literature_search.stratified_literature_quotas(20))
    assert layers[0]["layer"] == "L3_preprint" and layers[0]["priority"] == "P0"
    plan = soc.build_causal_inference_plan(
        {"description": "mixed-cation degradation", "alternative_mechanisms": ["electrolyte oxidation"]},
        {"input": "cutoff voltage", "proposed_mediator": "cation mixing", "output": "capacity retention"},
    )
    assert plan["counterfactual_experiments"] and plan["mechanism_competition"]["alternatives"] == ["electrolyte oxidation"]
test("P0 preprints lead retrieval and Socrates emits causal experiments", t_preprint_priority_and_counterfactual_plan)

def t_subhypothesis_retrieval_uses_independent_branch_budget():
    import _pipeline as pipeline

    quotas = pipeline.subhypothesis_retrieval_layer_quotas(12)
    minimums = pipeline.subhypothesis_import_layer_minimums(5)
    payload = {
        "action": {
            "search_papers_stratified": {
                "search_id": "search_branch",
                "total_results": 12,
                "strata": [{"layer": "L3_preprint", "selected": 1}],
            }
        }
    }
    search = pipeline.zhizhi_search_action_from_output(payload)
    assert sum(quotas.values()) == 12
    assert quotas["L3_preprint"] >= 1 and quotas["L2_top_latest"] >= 2
    assert sum(minimums.values()) == 5
    assert search["search_id"] == "search_branch" and search["strata"][0]["layer"] == "L3_preprint"
test("sub-hypothesis retrieval has independent layer budgets", t_subhypothesis_retrieval_uses_independent_branch_budget)

def t_standard_fifteen_result_search_imports_at_least_ten_candidates():
    import _pipeline as pipeline

    assert pipeline.standard_retrieval_import_limit(5, 15) == 10
    assert pipeline.standard_retrieval_import_limit(8, 20) == 10
    assert pipeline.standard_retrieval_import_limit(12, 15) == 12
    assert pipeline.standard_retrieval_import_limit(5, 14) == 5
    assert pipeline.standard_retrieval_import_limit(5, 15, retrieval_phase="subhypothesis_evidence") == 5
test("standard 15-to-20-result retrieval enforces a ten-paper import floor", t_standard_fifteen_result_search_imports_at_least_ten_candidates)

def t_import_latency_policy_bounds_llm_and_semantic_scholar_retries():
    import _literature_search as literature_search
    import _pipeline as pipeline

    assert pipeline.zhizhi_import_llm_budget(True, 12) == 2
    assert pipeline.zhizhi_import_llm_budget(True, 1) == 1
    assert pipeline.zhizhi_import_llm_budget(False, 12) == 0
    original_limit = literature_search.SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT
    original_llm_limit = pipeline.SCIENCE_ZHIZHI_IMPORT_LLM_LIMIT
    try:
        literature_search.SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT = 20
        assert literature_search.semantic_scholar_retry_limit() == 3
        pipeline.SCIENCE_ZHIZHI_IMPORT_LLM_LIMIT = 20
        assert pipeline.zhizhi_import_llm_budget(True, 12) == 3
    finally:
        literature_search.SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT = original_limit
        pipeline.SCIENCE_ZHIZHI_IMPORT_LLM_LIMIT = original_llm_limit
test("import latency policy bounds LLM work and Semantic Scholar retries", t_import_latency_policy_bounds_llm_and_semantic_scholar_retries)

def t_semantic_scholar_search_result_does_not_reprobe_missing_pdf():
    import _literature_search as literature_search

    payload = {
        "title": "Complete Semantic Scholar record",
        "abstract": "A" * 500,
        "method": "clinical cohort study",
        "scenario": "precision medicine",
        "benchmark": "treatment response",
        "semantic_scholar_id": "semantic-id",
    }
    result = {"provider": "semantic_scholar", "semantic_scholar_id": "semantic-id"}
    with patch.object(literature_search, "fetch_semantic_scholar_paper_detail", side_effect=AssertionError("redundant detail probe")):
        enriched, _ = literature_search.enrich_papergraph_payload(payload, result)
    assert enriched["_full_text_enrichment"]["status"] == "no_open_access_pdf"
test("Semantic Scholar search records skip redundant detail probes", t_semantic_scholar_search_result_does_not_reprobe_missing_pdf)

def t_community_aware_graph_expansion_preserves_diversity_and_evidence_weight():
    import _literature_graph as literature_graph
    import tools

    medicine = {
        "title": "Clinical trial in patients with cancer treatment outcomes",
        "abstract": "A patient cohort study of therapeutic response and prognosis.",
        "semantic_scholar_id": "medicine-seed",
        "publication_quality_score": 0.95,
        "relevance_score": 0.92,
        "relevance_components": {"impact_score": 0.8},
    }
    biology = {
        "title": "Molecular gene pathway controls cytokine signaling in immune cells",
        "abstract": "Protein receptor mechanisms regulate cellular transcription.",
        "semantic_scholar_id": "biology-seed",
        "publication_quality_score": 0.83,
        "relevance_score": 0.79,
        "relevance_components": {"impact_score": 0.7},
    }
    translational = {
        "title": "Molecular biomarker stratifies clinical cancer patients",
        "abstract": "Gene pathway profiling predicts therapeutic response in a clinical cohort.",
        "semantic_scholar_id": "bridge-seed",
        "publication_quality_score": 0.88,
        "relevance_score": 0.85,
        "relevance_components": {"impact_score": 0.75},
    }
    assert literature_graph.infer_literature_community(medicine) == "medicine"
    assert literature_graph.infer_literature_community(biology) == "biology"
    assert literature_graph.infer_literature_community(translational) == "translational"
    selected = literature_graph.select_second_layer_seeds_with_community_awareness(
        [medicine, biology, translational],
        top_k=2,
        min_bridge_attempts=2,
    )
    assert len({literature_graph.infer_literature_community(item) for item in selected}) == 2
    assert literature_graph.graph_needs_cross_community_bridge([medicine], 20)
    ordinary = literature_graph.relation_graph_edge(
        "parent",
        "child",
        "second_layer_reference",
        {"graph_community": "biology"},
    )
    bridge = literature_graph.relation_graph_edge(
        "parent",
        "child",
        "cross_community_bridge_reference",
        {
            "graph_cross_community_bridge": True,
            "graph_parent_community": "medicine",
            "graph_community": "biology",
        },
    )
    assert bridge["is_cross_community_bridge"] and bridge["weight"] > ordinary["weight"]
    retained = literature_graph.retain_community_bridge_candidates(
        [medicine, biology, translational],
        [translational],
        max_results=2,
    )
    assert any(item.get("semantic_scholar_id") == "bridge-seed" for item in retained)
    assert tools.TOOL_HANDLERS["search_cross_community_bridges"] is tools.search_cross_community_bridges
test("Community-aware graph expansion diversifies seeds and weights bridge citations", t_community_aware_graph_expansion_preserves_diversity_and_evidence_weight)

def t_louvain_uses_structural_citation_edges_and_recommends_bridges():
    import _literature_graph as literature_graph

    nodes = [
        {
            "node_id": node_id,
            "title": node_id,
            "field": "physics" if node_id.startswith("p") else "mathematics",
            "publication_quality_score": 0.8,
            "relevance_score": 0.7,
        }
        for node_id in ("p1", "p2", "p3", "m1", "m2", "m3")
    ]
    edges = [
        {"source": "p1", "target": "p2", "weight": 1.0, "edge_type": "citation_graph"},
        {"source": "p2", "target": "p3", "weight": 1.0, "edge_type": "citation_graph"},
        {"source": "p1", "target": "p3", "weight": 1.0, "edge_type": "citation_graph"},
        {"source": "m1", "target": "m2", "weight": 1.0, "edge_type": "citation_graph"},
        {"source": "m2", "target": "m3", "weight": 1.0, "edge_type": "citation_graph"},
        {"source": "m1", "target": "m3", "weight": 1.0, "edge_type": "citation_graph"},
        {"source": "p3", "target": "m1", "weight": 1.0, "edge_type": "citation_graph"},
    ]
    analysis = literature_graph.run_louvain_community_analysis(nodes, edges)
    if literature_graph.LOUVAIN_AVAILABLE:
        assert analysis["status"] == "success"
        assert analysis["num_communities"] == 2
        assert analysis["modularity"] > 0.3
        bridge_ids = {item["node_id"] for item in analysis["bridge_nodes"]}
        assert {"p3", "m1"}.issubset(bridge_ids)
        recommendations = literature_graph.louvain_recommended_expansion_seeds(nodes, analysis)
        assert recommendations and recommendations[0]["bridge_score"] > 0.3

    artificial_only = literature_graph.run_louvain_community_analysis(
        nodes,
        [{"source": "p1", "target": "m1", "weight": 1.0, "edge_type": "artificial"}],
    )
    assert artificial_only["status"] in {"insufficient_structure", "disabled", "unavailable"}
    if artificial_only["status"] == "insufficient_structure":
        assert artificial_only["ignored_artificial_edge_count"] == 1
test("Louvain communities use citation structure and surface bridge papers", t_louvain_uses_structural_citation_edges_and_recommends_bridges)

def t_relation_graph_persists_louvain_analysis_and_node_annotations():
    import _literature_graph as literature_graph
    import _project as project_store
    from _literature_search import literature_result_unique_key

    papers = []
    parent_names = {"p1": "", "p2": "p1", "p3": "p1", "m1": "", "m2": "m1", "m3": "m1"}
    for node_id in parent_names:
        papers.append(
            {
                "title": f"{node_id} structural paper",
                "abstract": "Citation graph community structure.",
                "semantic_scholar_id": node_id,
                "publication_quality_score": 0.8,
                "relevance_score": 0.7,
                "graph_relation": "reference",
            }
        )
    by_name = {item["semantic_scholar_id"]: item for item in papers}
    for item in papers:
        parent_name = parent_names[item["semantic_scholar_id"]]
        if parent_name:
            item["graph_parent_key"] = literature_result_unique_key(by_name[parent_name])
    source = {
        "search_id": "synthetic_louvain",
        "query": "citation graph community",
        "seed_title": "Seed paper",
        "results": papers,
    }
    with (
        patch.object(project_store, "load_search", return_value=source),
        patch.object(project_store, "save_search") as save_search,
    ):
        response = json.loads(literature_graph.build_literature_relation_graph("synthetic_louvain"))
    stored = save_search.call_args.args[0]
    status = response["louvain_analysis"]["status"]
    assert status in {"success", "fallback_components", "disabled", "unavailable"}
    assert stored["louvain_analysis"]["status"] == status
    if status == "success":
        assert response["louvain_analysis"]["num_communities"] >= 2
        assert any("louvain_community" in node for node in stored["nodes"])
        assert "louvain_analysis.bridge_nodes" in response["next_step"]
test("Relation graph persists Louvain analysis and bridge-expansion annotations", t_relation_graph_persists_louvain_analysis_and_node_annotations)

def t_louvain_community_maps_are_evidence_bounded_and_emit_local_gaps():
    import _gap_detection as gaps
    import _project as project_store
    import tools

    project = {
        "project_id": "community_map_test",
        "papergraph": [
            {
                "paper_id": "paper_a",
                "unique_key": "s2:a",
                "semantic_scholar_id": "a",
                "title": "A",
                "citation": "A",
                "method": "spectroscopy",
                "scenario": "astrophysical observation",
                "benchmark": "discovery significance",
            },
            {
                "paper_id": "paper_b",
                "unique_key": "s2:b",
                "semantic_scholar_id": "b",
                "title": "B",
                "citation": "B",
                "method": "numerical simulation",
                "scenario": "detector simulation in high energy physics",
                "benchmark": "simulation fidelity",
            },
            {
                "paper_id": "paper_c",
                "unique_key": "s2:c",
                "semantic_scholar_id": "c",
                "title": "C",
                "citation": "C",
                "method": "theoretical proof",
                "scenario": "mathematical modeling",
                "benchmark": "theorem strength",
            },
        ],
        "evidence": [],
    }
    relation_graph = {
        "search_id": "relation_community_map",
        "nodes": [
            {"node_id": "s2_a", "semantic_scholar_id": "a", "title": "A", "louvain_community": 0, "field": "physics"},
            {"node_id": "s2_b", "semantic_scholar_id": "b", "title": "B", "louvain_community": 0, "field": "physics"},
            {"node_id": "s2_c", "semantic_scholar_id": "c", "title": "C", "louvain_community": 1, "field": "mathematics"},
            {"node_id": "s2_unimported", "semantic_scholar_id": "unimported", "title": "Unimported", "louvain_community": 1, "field": "mathematics"},
        ],
        "louvain_analysis": {
            "status": "success",
            "community_map": {"s2_a": 0, "s2_b": 0, "s2_c": 1, "s2_unimported": 1},
            "communities": [{"community_id": 0, "primary_field": "physics"}, {"community_id": 1, "primary_field": "mathematics"}],
            "topic_drift_assessment": [{"community_id": 0, "disposition": "core", "priority": "high"}, {"community_id": 1, "disposition": "connected", "priority": "normal"}],
            "outlier_communities": [],
        },
    }
    with (
        patch.object(project_store, "load_project", return_value=project),
        patch.object(project_store, "save_project"),
        patch.object(project_store, "load_search", return_value=relation_graph),
    ):
        report = json.loads(gaps.build_louvain_community_knowledge_maps("community_map_test", "relation_community_map", 2))
    assert report["status"] == "ready"
    assert report["communities"]["0"]["record_count"] == 2
    assert report["communities"]["0"]["eligible_for_gap_analysis"]
    assert not report["communities"]["1"]["eligible_for_gap_analysis"]
    assert report["unmapped_relation_node_count"] == 1
    assert report["representative_import_candidates"][0]["candidate"]["node_id"] == "s2_unimported"
    candidates = gaps.louvain_community_gap_candidates(project, report["communities"])
    assert candidates and all(item["gap_type"] == "community_combinatorial" for item in candidates)
    assert all(item["louvain_community"] == 0 for item in candidates)
    assert project["papergraph"][0]["louvain_priority"] == "high"
    assert tools.TOOL_HANDLERS["build_louvain_community_knowledge_maps"] is tools.build_louvain_community_knowledge_maps
test("Louvain community maps remain evidence-bounded and emit local gaps", t_louvain_community_maps_are_evidence_bounded_and_emit_local_gaps)

def t_louvain_bridge_priority_guides_later_second_layer_seed_selection():
    import _literature_graph as literature_graph

    candidates = [
        {
            "title": "Core paper",
            "semantic_scholar_id": "core",
            "publication_quality_score": 0.84,
            "relevance_score": 0.82,
            "relevance_components": {"impact_score": 0.7},
            "louvain_community": 0,
            "louvain_priority": "normal",
        },
        {
            "title": "Structural bridge paper",
            "semantic_scholar_id": "bridge",
            "publication_quality_score": 0.8,
            "relevance_score": 0.8,
            "relevance_components": {"impact_score": 0.7},
            "louvain_community": 1,
            "louvain_bridge_score": 0.9,
            "louvain_priority": "normal",
        },
        {
            "title": "Weak detached paper",
            "semantic_scholar_id": "weak",
            "publication_quality_score": 0.89,
            "relevance_score": 0.86,
            "relevance_components": {"impact_score": 0.75},
            "louvain_community": 2,
            "louvain_priority": "low",
        },
    ]
    selected = literature_graph.select_second_layer_seeds_with_community_awareness(
        candidates,
        top_k=2,
        min_bridge_attempts=2,
    )
    selected_ids = {item["semantic_scholar_id"] for item in selected}
    assert "bridge" in selected_ids
    assert "weak" not in selected_ids
test("Louvain bridge priority guides later second-layer seed selection", t_louvain_bridge_priority_guides_later_second_layer_seed_selection)

def t_research_domain_catalog_routes_and_gates_cross_domain_queries():
    import _literature_graph as literature_graph
    import _literature_scoring as scoring
    from _models import research_domain_profile
    from _project import default_literature_providers

    cases = {
        "Astrophysics cosmology gravitational waves": ("physics", {"semantic_scholar", "arxiv"}),
        "Quantum materials superconductivity condensed matter": ("physics", {"semantic_scholar", "arxiv"}),
        "Graph neural networks for information retrieval": ("computer_science", {"semantic_scholar", "arxiv"}),
        "Econometrics causal inference market design": ("economics", {"semantic_scholar", "arxiv"}),
        "Catalysis and polymer chemistry": ("chemistry", {"semantic_scholar", "chemrxiv"}),
        "Clinical oncology pharmacogenomics trial": ("medicine", {"semantic_scholar", "pubmed", "medrxiv", "biorxiv"}),
    }
    for query, (expected_domain, expected_providers) in cases.items():
        assert research_domain_profile(query)["domain"] == expected_domain
        assert expected_providers.issubset(set(default_literature_providers(domain=query)))
        assert literature_graph.infer_literature_community({"title": query}) in {expected_domain, "translational"}

    astrophysics_paper = {
        "title": "Gravitational wave cosmology with galaxy surveys",
        "abstract": "Astrophysical standard sirens constrain cosmology.",
    }
    assert not scoring.should_reject_for_domain(
        astrophysics_paper,
        domain="Astrophysics cosmology gravitational waves",
        query="Astrophysics cosmology gravitational waves",
    )
    assert not scoring.fields_are_incompatible("physics", "mathematics")
    assert scoring.fields_are_incompatible("medicine", "physics")
    assert "mathematical modeling" in literature_graph.bridge_query_plan("gravitational wave cosmology")[0]
test("Research-domain catalog routes, gates, and bridges across disciplines", t_research_domain_catalog_routes_and_gates_cross_domain_queries)

def t_bridge_search_is_bounded_and_never_imports_automatically():
    import _literature_graph as literature_graph
    import _literature_search as literature_search
    import _project as project_store

    candidate = {
        "title": "Molecular gene biomarker predicts clinical patient treatment response",
        "abstract": "A gene pathway mechanism supports clinical stratification in a patient cohort.",
        "semantic_scholar_id": "bridge-candidate",
        "publication_quality_score": 0.9,
        "relevance_score": 0.8,
    }
    with (
        patch.object(project_store, "load_search", return_value={"search_id": "source", "query": "Treg homeostasis"}),
        patch.object(project_store, "save_search") as mocked_save,
        patch.object(literature_search, "search_semantic_scholar", return_value={"provider": "semantic_scholar"}) as mocked_search,
        patch.object(literature_search, "flatten_literature_results", return_value=[candidate]),
        patch.object(literature_search, "dedupe_literature_results", side_effect=lambda items: items),
        patch.object(literature_search, "rank_literature_results", side_effect=lambda _query, items: items),
    ):
        payload = json.loads(literature_graph.search_cross_community_bridges("source", max_results=4))
    assert payload["total_results"] >= 1
    assert mocked_search.call_count <= literature_graph.SCIENCE_BRIDGE_SEARCH_QUERY_LIMIT
    assert mocked_save.call_args.args[0]["kind"] == "cross_community_bridge_search"
    assert "never imports" in payload["next_step"]
test("Bridge search is bounded and leaves import under explicit control", t_bridge_search_is_bounded_and_never_imports_automatically)

def t_subhypothesis_preprints_are_independent_and_nonblocking():
    import _gap_detection as gaps
    import _literature_search as literature_search
    import _pipeline as pipeline
    import _project as project_module

    project = {
        "project_id": "project_preprint_policy",
        "domain": "precision medicine",
        "objective": "Determine whether genetic variants improve drug dosing.",
        "research_brief": "Preprints are an independent frontier-signal search and must not gate branch eligibility.",
        "sub_hypotheses": [
            {
                "id": "SH1",
                "focus": "genotype-guided dosing",
                "retrieval_query": "genetic variants pharmacokinetics drug dosing toxicity",
                "evidence_window": {"P0_latest_preprint": {"minimum": 1}},
            }
        ],
    }
    primary_calls = []
    preprint_calls = []

    def fake_primary(**kwargs):
        primary_calls.append(kwargs)
        return json.dumps(
            {
                "action": {
                    "imported_records": 3,
                    "search_papers_stratified": {
                        "search_id": "primary_search",
                        "total_results": 3,
                        "strata": [{"layer": "L4_regular", "selected": 3}],
                    },
                }
            }
        )

    def fake_preprint(query, providers=None, max_results=50):
        preprint_calls.append({"query": query, "providers": providers, "max_results": max_results})
        return json.dumps(
            {
                "search_id": "preprint_search",
                "total_results": 0,
                "provider_blocks": [],
            }
        )

    with (
        patch.object(project_module, "load_project", side_effect=lambda _project_id: project),
        patch.object(project_module, "save_project", side_effect=lambda _project: None),
        patch.object(pipeline, "run_zhizhi_literature_analysis", side_effect=fake_primary),
        patch.object(literature_search, "search_literature", side_effect=fake_preprint),
        patch.object(gaps, "build_knowledge_map", return_value={}),
        patch.object(gaps, "detect_knowledge_gaps", return_value=[]),
    ):
        result = json.loads(
            pipeline.run_zhizhi_subhypothesis_analysis(
                "project_preprint_policy",
                providers=["semantic_scholar", "pubmed", "arxiv", "medrxiv", "biorxiv"],
            )
        )

    retrieval = result["reports"][0]
    assert primary_calls and set(primary_calls[0]["providers"]) == {"semantic_scholar", "pubmed"}
    assert primary_calls[0]["layer_quotas"]["L3_preprint"] == 0
    assert preprint_calls and set(preprint_calls[0]["providers"]) == {"arxiv", "medrxiv", "biorxiv"}
    assert retrieval["preprint_evidence"]["status"] == "not_available"
    assert retrieval["preprint_gate_enforced"] is False
    assert retrieval["status"] == "ready_for_causal_gap_detection"
    assert project["sub_hypotheses"][0]["status"] == "ready_for_causal_gap_detection"
test("Preprint retrieval is independent and does not reject a supported sub-hypothesis", t_subhypothesis_preprints_are_independent_and_nonblocking)

def t_subhypothesis_imports_preprints_before_peer_reviewed_search():
    import _gap_detection as gaps
    import _literature_import as literature_import
    import _literature_search as literature_search
    import _pipeline as pipeline
    import _project as project_module

    project = {
        "project_id": "project_preprint_import_order",
        "domain": "immunology",
        "objective": "Determine how IL-2 concentration affects Treg and Teff balance.",
        "sub_hypotheses": [
            {
                "id": "SH2",
                "focus": "IL-2 concentration and T-cell balance",
                "retrieval_query": "IL-2 concentration Treg Teff balance",
                "evidence_window": {"P0_latest_preprint": {"minimum": 1}},
            }
        ],
    }
    call_order = []
    primary_calls = []
    import_calls = []

    def fake_preprint(query, providers=None, max_results=50):
        call_order.append("preprint_search")
        return json.dumps(
            {
                "search_id": "preprint_search",
                "total_results": 4,
                "results": [
                    {
                        "result_index": index,
                        "title": f"IL-2 frontier preprint {index}",
                        "provider": "medrxiv",
                        "doi": f"10.1101/2026.01.01.{index}",
                    }
                    for index in range(4)
                ],
                "provider_blocks": [{"provider": "medrxiv", "status": "ok", "result_count": 4}],
            }
        )

    def fake_import(project_id, search_id, result_index=0, use_llm=False, **kwargs):
        call_order.append(f"preprint_import_{result_index}")
        import_calls.append({"project_id": project_id, "search_id": search_id, "result_index": result_index, **kwargs})
        return json.dumps(
            {
                "status": "imported",
                "record": {"paper_id": f"paper_preprint_{result_index}"},
            }
        )

    def fake_primary(**kwargs):
        call_order.append("peer_reviewed_search")
        primary_calls.append(kwargs)
        return json.dumps(
            {
                "action": {
                    "imported_records": 4,
                    "search_papers_stratified": {
                        "search_id": "peer_reviewed_search",
                        "total_results": 10,
                        "strata": [{"layer": "L4_regular", "selected": 10}],
                    },
                }
            }
        )

    with (
        patch.object(project_module, "load_project", side_effect=lambda _project_id: project),
        patch.object(project_module, "save_project", side_effect=lambda _project: None),
        patch.object(literature_search, "search_literature", side_effect=fake_preprint),
        patch.object(literature_import, "import_literature_search_result", side_effect=fake_import),
        patch.object(pipeline, "run_zhizhi_literature_analysis", side_effect=fake_primary),
        patch.object(gaps, "build_knowledge_map", return_value={}),
        patch.object(gaps, "detect_knowledge_gaps", return_value=[]),
    ):
        result = json.loads(
            pipeline.run_zhizhi_subhypothesis_analysis(
                "project_preprint_import_order",
                import_top_k_per_hypothesis=8,
                providers=["semantic_scholar", "pubmed", "medrxiv", "biorxiv"],
            )
        )

    retrieval = result["reports"][0]
    assert call_order == ["preprint_search", "preprint_import_0", "preprint_import_1", "preprint_import_2", "peer_reviewed_search"]
    assert len(import_calls) == 3
    assert all(call["search_id"] == "preprint_search" for call in import_calls)
    assert all(call["stratified_layer_override"] == "L3_preprint" for call in import_calls)
    assert all(call["query_branch_override"] == "SH2" for call in import_calls)
    assert primary_calls and set(primary_calls[0]["providers"]) == {"semantic_scholar", "pubmed"}
    assert primary_calls[0]["import_top_k"] == 5
    assert primary_calls[0]["layer_quotas"]["L3_preprint"] == 0
    assert primary_calls[0]["import_layer_minimums"]["L3_preprint"] == 0
    assert retrieval["p0_preprint_imported"] == 3
    assert retrieval["p0_preprint_selected"] == 3
    assert retrieval["peer_reviewed_imported_records"] == 4
    assert retrieval["imported_records"] == 7
    assert retrieval["preprint_evidence"]["status"] == "imported"
test("Sub-hypothesis imports up to three P0 preprints before peer-reviewed retrieval", t_subhypothesis_imports_preprints_before_peer_reviewed_search)

def t_legacy_preprint_status_is_nonblocking_when_primary_evidence_exists():
    import _hypothesis as hypothesis

    gap = {
        "gap_id": "G1",
        "sub_hypothesis_id": "SH1",
        "description": "A causal mechanism needs an intervention-based validation experiment.",
        "mechanism_relevance": {"eligible_for_mechanism_hypothesis": True, "score": 1.0},
    }
    selected = hypothesis.select_gaps_for_hypothesis(
        {
            "sub_hypotheses": [
                {
                    "id": "SH1",
                    "status": "evidence_insufficient_preprint",
                    "retrieval": {"total_results": 4},
                }
            ],
            "knowledge_gaps": [gap],
        },
        None,
    )
    assert selected and selected[0]["gap_id"] == "G1"
    assert gap["preprint_evidence_nonblocking"] is True
    assert "requires_human_review" not in gap
test("Legacy preprint-only status does not block MingLi when primary evidence exists", t_legacy_preprint_status_is_nonblocking_when_primary_evidence_exists)

def t_new_causal_tools_are_exposed():
    import tools
    assert "decompose_research_objective" in tools.TOOL_HANDLERS
    assert "run_zhizhi_subhypothesis_analysis" in tools.TOOL_HANDLERS
test("Decomposition and sub-hypothesis retrieval tools are exposed", t_new_causal_tools_are_exposed)

# --- Tools.py / autogen_collab.py compatibility ---
print("\n-- Backward Compatibility --")

TOOLS_IMPORTS = [
    "create_research_project", "list_research_projects", "get_research_project", "decompose_research_objective", "set_research_brief",
    "list_science_agents", "get_science_agent_prompt", "list_literature_providers",
    "explore_domain_subspaces", "search_literature", "search_literature_stratified",
    "search_papers", "search_papers_stratified", "extract_structured_info",
    "select_literature_result", "expand_literature_graph", "build_literature_relation_graph",
    "create_science_pipeline_tasks", "create_science_delegation_tasks",
    "create_boxue_delegation_tasks", "run_boxue_research_round",
    "build_knowledge_map", "add_literature_evidence", "import_literature_text",
    "import_literature_file", "import_literature_search_result", "domain_review_paper", "reconcile_project_domain_reviews",
    "extract_paper_keynote", "import_papergraph_record", "list_papergraph_records",
    "verify_citation_uniqueness", "assess_novelty", "verify_uniqueness",
    "run_zhizhi_literature_analysis", "run_zhizhi_subhypothesis_analysis", "parse_literature_text", "build_coverage_matrix",
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
    "create_research_project", "load_project", "decompose_research_objective", "search_literature_stratified",
    "select_zhizhi_import_results", "import_literature_search_result",
    "extract_paper_keynote", "detect_knowledge_gaps", "run_tanxi_gap_exploration", "run_zhizhi_subhypothesis_analysis",
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
