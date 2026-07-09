from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any
import ast
import json
import re
import time

try:
    from .log import log_event
except ImportError:
    from log import log_event



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
    try:
        from ._models import PaperEvidence
        from ._project import load_project, save_project
        from ._utils import new_id
    except ImportError:
        from _models import PaperEvidence
        from _project import load_project, save_project
        from _utils import new_id
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
    try:
        from ._pipeline import classify_record_evidence, project_records_for_mapping
        from ._project import load_project, save_project
        from ._utils import normalize_label, record_context_text, repair_project_extraction_quality, repair_unknown_field
    except ImportError:
        from _pipeline import classify_record_evidence, project_records_for_mapping
        from _project import load_project, save_project
        from _utils import normalize_label, record_context_text, repair_project_extraction_quality, repair_unknown_field
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
    try:
        from ._project import load_project, save_project
        from ._utils import normalize_label
    except ImportError:
        from _project import load_project, save_project
        from _utils import normalize_label
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
    try:
        from ._pipeline import project_records_for_mapping
    except ImportError:
        from _pipeline import project_records_for_mapping
    gaps: list[dict[str, Any]] = []
    records = [record for record in project_records_for_mapping(project) if isinstance(record, dict)]
    gaps.extend(detect_contradiction_gaps(project, records, limit=max(1, limit // 2)))
    if len(gaps) < limit:
        gaps.extend(detect_anomaly_gaps(project, records, limit=limit - len(gaps)))
    return dedupe_knowledge_gaps(gaps)[:limit]

def detect_contradiction_gaps(project: dict[str, Any], records: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    try:
        from ._utils import trim_text, unique_preserve_order
    except ImportError:
        from _utils import trim_text, unique_preserve_order
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
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
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
    try:
        from ._utils import normalize_label, trim_text
    except ImportError:
        from _utils import normalize_label, trim_text
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
    try:
        from ._utils import normalize_space, scalar
    except ImportError:
        from _utils import normalize_space, scalar
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
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
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
    try:
        from ._utils import split_sentences, trim_text
    except ImportError:
        from _utils import split_sentences, trim_text
    for sentence in split_sentences(text):
        lowered = sentence.lower()
        if any(term in lowered for term in terms):
            return trim_text(sentence, 260)
    return ""

def detect_knowledge_gaps(project_id: str, max_gaps: int = 10) -> str:
    try:
        from ._pipeline import supporting_references_for_method_or_scenario
        from ._project import load_project, save_project
        from ._utils import trim_text
    except ImportError:
        from _pipeline import supporting_references_for_method_or_scenario
        from _project import load_project, save_project
        from _utils import trim_text
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
    semantic_rejected_gaps: list[dict[str, Any]] = []
    per_type_quota = max(1, max_gaps // 5)

    if len(gaps) < max_gaps:
        gaps.extend(detect_reasoning_gaps(project, min(per_type_quota + 1, max_gaps - len(gaps))))

    if len(gaps) < max_gaps:
        gaps.extend(detect_mechanism_issue_gaps(project, max_gaps - len(gaps)))

    if len(gaps) < max_gaps:
        gaps.extend(detect_gap_signal_gaps(project, max_gaps - len(gaps)))

    if len(gaps) < max_gaps:
        gaps.extend(detect_problem_gaps(project, min(per_type_quota + 1, max_gaps - len(gaps))))

    # TABI abductive reasoning: implicit gaps from evidence pair analysis
    if len(gaps) < max_gaps:
        tabi_candidates = tabi_abductive_gap_detection(project, max_gaps=max_gaps - len(gaps))
        gaps.extend(tabi_candidates)

    for method in methods:
        for scenario in scenarios:
            if scenario in matrix.get(method, {}):
                continue
            references = supporting_references_for_method_or_scenario(project, method, scenario)
            ingredients = extract_hypothesis_ingredients(project, method, scenario, references)
            cf_leaves = generate_counterfactual_leaves(method, scenario, references)
            gap = make_gap(
                gap_type="combinatorial",
                description=f"Method '{method}' has no recorded validation in scenario '{scenario}' in the current PaperGraph map.",
                supporting_references=references,
                suggested_research_path="Run a targeted validation study with explicit benchmarks, baselines, and failure-mode analysis.",
                value_argument="The combination may expose method-scenario boundary conditions rather than simply adding another benchmark.",
                hypothesis_ingredients=ingredients,
                counterfactual_leaves=cf_leaves,
            )
            gate = semantic_plausibility_for_pair(project, method, scenario, gap)
            gap["semantic_plausibility"] = gate
            if gate.get("verdict") == "REJECT":
                semantic_rejected_gaps.append(
                    {
                        "gap_id": gap.get("gap_id"),
                        "gap_type": gap.get("gap_type"),
                        "method": method,
                        "scenario": scenario,
                        "description": trim_text(str(gap.get("description", "")), 220),
                        "reason": gate.get("reason"),
                        "score": gate.get("score"),
                    }
                )
                continue
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
                gate = semantic_plausibility_for_pair(project, method, scenario, gap)
                gap["semantic_plausibility"] = gate
                if gate.get("verdict") == "REJECT":
                    semantic_rejected_gaps.append(
                        {
                            "gap_id": gap.get("gap_id"),
                            "gap_type": gap.get("gap_type"),
                            "method": method,
                            "scenario": scenario,
                            "description": trim_text(str(gap.get("description", "")), 220),
                            "reason": gate.get("reason"),
                            "score": gate.get("score"),
                        }
                    )
                    continue
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
        "semantic_rejected_count": len(semantic_rejected_gaps),
        "semantic_rejected": semantic_rejected_gaps[:10],
    }
    project["semantic_rejected_knowledge_gaps"] = semantic_rejected_gaps
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
        rejected_semantic=len(semantic_rejected_gaps),
    )
    return json.dumps(project["knowledge_gaps"], ensure_ascii=False, indent=2)

def run_tanxi_gap_exploration(
    project_id: str,
    target_domain: str = "",
    strategic_domains: list[str] | None = None,
    max_gaps: int = 10,
) -> str:
    try:
        from ._project import load_project, save_project
    except ImportError:
        from _project import load_project, save_project
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
    source_signal_candidates = detect_gap_signal_gaps(project, limit=max(4, max_gaps))
    mechanism_issue_candidates = detect_mechanism_issue_gaps(project, limit=max(4, max_gaps))
    density_gap_candidates = gaps_from_density_holes(project, coverage_analysis.get("density_holes", []))
    pair_gap_candidates = gaps_from_unconnected_pairs(project, unconnected_pairs)
    suspended_gap_candidates = gaps_from_suspended_problems(project, suspended)
    tabi_gap_candidates = tabi_abductive_gap_detection(project, max_gaps=max(4, max_gaps))
    candidates = dedupe_knowledge_gaps(
        mechanism_issue_candidates
        + source_signal_candidates
        + reasoning_gap_candidates
        + list(raw_gaps)
        + density_gap_candidates
        + pair_gap_candidates
        + suspended_gap_candidates
        + tabi_gap_candidates
    )
    ranked = prioritize_gaps(project, candidates, coverage_analysis, strategic_domains, max_gaps=max_gaps)
    ranked = counterfactual_gap_analysis(project, ranked, limit=min(max_gaps, 10))
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
            "detect_source_gap_signals": {"types": ["limitations", "future_work", "open_problem", "missing_evidence"]},
            "detect_mechanism_issue_gaps": {"priority": "higher_than_matrix_holes"},
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
    try:
        from ._pipeline import supporting_references_for_method_or_scenario
    except ImportError:
        from _pipeline import supporting_references_for_method_or_scenario
    matrix: dict[str, dict[str, list[str]]] = project.get("coverage_matrix", {})
    knowledge_map: dict[str, Any] = project.get("knowledge_map", {})
    methods = sorted(knowledge_map.get("main_methods") or matrix.keys())
    scenarios = sorted(knowledge_map.get("main_scenarios") or {scenario for coverage in matrix.values() for scenario in coverage})
    benchmarks = sorted(knowledge_map.get("main_benchmarks") or [])
    dense_areas: list[dict[str, Any]] = []
    density_holes: list[dict[str, Any]] = []
    rejected_density_holes: list[dict[str, Any]] = []
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
                    gate = semantic_plausibility_for_pair(project, method, scenario)
                    hole = {
                        "topic": f"{method} + {scenario} benchmark coverage",
                        "method": method,
                        "scenario": scenario,
                        "importance_score": importance,
                        "current_evidence_level": "medium",
                        "missing_benchmarks": [item for item in benchmarks if item not in covered_benchmarks][:5],
                        "why_important": "The method-scenario pair has evidence, but benchmark coverage is sparse, so robustness and generalization remain uncertain.",
                        "supporting_references": refs[:5],
                        "semantic_plausibility": gate,
                    }
                    if gate.get("verdict") == "REJECT":
                        rejected_density_holes.append(hole)
                    else:
                        density_holes.append(hole)
            elif importance >= 5:
                refs_for_context = supporting_references_for_method_or_scenario(project, method, scenario)
                gate = semantic_plausibility_for_pair(project, method, scenario)
                hole = {
                    "topic": f"{method} + {scenario}",
                    "method": method,
                    "scenario": scenario,
                    "importance_score": importance,
                    "current_evidence_level": "none",
                    "missing_benchmarks": benchmarks[:5],
                    "why_important": "Both the method and scenario are visible in the field map, but this intersection has no recorded validation.",
                    "supporting_references": refs_for_context[:5],
                    "semantic_plausibility": gate,
                }
                if gate.get("verdict") == "REJECT":
                    rejected_density_holes.append(hole)
                else:
                    density_holes.append(hole)

    dense_areas.sort(key=lambda item: (-int(item["evidence_count"]), -int(item["benchmark_count"]), item["topic"]))
    density_holes.sort(key=lambda item: (-int(item["importance_score"]), item["current_evidence_level"], item["topic"]))
    return {
        "target_domain": target_domain,
        "method_count": len(methods),
        "scenario_count": len(scenarios),
        "benchmark_count": len(benchmarks),
        "dense_areas": dense_areas[:10],
        "density_holes": density_holes[:20],
        "rejected_density_holes": rejected_density_holes[:20],
    }

def find_unconnected_pairs(project: dict[str, Any], target_domain: str = "") -> list[dict[str, Any]]:
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import is_unknown_value, normalize_label, unique_preserve_order
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import is_unknown_value, normalize_label, unique_preserve_order
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
                    if concepts_are_connected(project, item_a["concept"], item_b["concept"]) or concept_bridge_exists(project, item_a["concept"], item_b["concept"]):
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
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import extract_year, numeric_value, trim_text
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import extract_year, numeric_value, trim_text
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
        gate = semantic_plausibility_for_pair(project, str(pair.get("concept_a") or ""), str(pair.get("concept_b") or ""))
        if gate.get("verdict") == "REJECT":
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
        gaps[-1]["semantic_plausibility"] = gate
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
    try:
        from ._literature_search import query_terms
    except ImportError:
        from _literature_search import query_terms
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
    try:
        from ._literature_scoring import infer_research_field
    except ImportError:
        from _literature_scoring import infer_research_field
    field_name = str(record.get("field") or "").strip()
    if field_name:
        return field_name
    return infer_research_field(record)

def concepts_are_connected(project: dict[str, Any], left: str, right: str) -> bool:
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_label
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import normalize_label
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

def concept_bridge_exists(project: dict[str, Any], left: str, right: str) -> bool:
    try:
        from ._literature_search import query_terms
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_label, record_context_text
    except ImportError:
        from _literature_search import query_terms
        from _pipeline import project_records_for_mapping
        from _utils import normalize_label, record_context_text
    left_terms = set(query_terms(left))
    right_terms = set(query_terms(right))
    if not left_terms or not right_terms:
        return False
    for record in project_records_for_mapping(project):
        terms = set(query_terms(record_context_text(record)))
        left_hit = bool(left_terms & terms) or normalize_label(left).lower() in record_context_text(record).lower()
        right_hit = bool(right_terms & terms) or normalize_label(right).lower() in record_context_text(record).lower()
        if left_hit and right_hit:
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
    try:
        from ._literature_search import query_terms
        from ._utils import normalize_space
    except ImportError:
        from _literature_search import query_terms
        from _utils import normalize_space
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
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
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
    try:
        from ._literature_search import query_terms
    except ImportError:
        from _literature_search import query_terms
    novelty = int(gap.get("novelty_score") or 5)
    refs = len([ref for ref in gap.get("supporting_references", []) if ref])
    feasibility = str(gap.get("feasibility", "medium"))
    application = str(gap.get("application_value", "medium"))
    gap_type = str(gap.get("gap_type", ""))
    score = novelty
    score += min(2, refs)
    score += {"high": 2, "medium": 1, "low": -1}.get(application, 0)
    score += {"high": 2, "medium": 1, "low": -2}.get(feasibility, 0)
    if gap_type in {"migration", "problem", "mechanism_problem", "contradiction", "anomaly", "structural"}:
        score += 1
    if gap_type in {"mechanism_problem", "contradiction", "anomaly"}:
        score += 1
    if gap.get("mechanism_issue_signal") or gap.get("gap_signal"):
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
    try:
        from ._literature_scoring import slug_label
        from ._pipeline import project_records_for_mapping
        from ._project import load_project, load_subspace_map, save_project, save_subspace_map
        from ._utils import clamp_int, new_id
    except ImportError:
        from _literature_scoring import slug_label
        from _pipeline import project_records_for_mapping
        from _project import load_project, load_subspace_map, save_project, save_subspace_map
        from _utils import clamp_int, new_id
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
    try:
        from ._literature_search import query_terms
        from ._pipeline import project_records_for_mapping
        from ._project import normalize_domain_subspace
        from ._utils import is_unknown_value, normalize_label
    except ImportError:
        from _literature_search import query_terms
        from _pipeline import project_records_for_mapping
        from _project import normalize_domain_subspace
        from _utils import is_unknown_value, normalize_label
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
    try:
        from ._literature_search import query_terms
        from ._utils import string_list, unique_preserve_order
    except ImportError:
        from _literature_search import query_terms
        from _utils import string_list, unique_preserve_order
    raw: list[str] = []
    raw.extend(query_terms(str(subspace.get("name") or "")))
    raw.extend(query_terms(" ".join(string_list(subspace.get("aliases")))))
    raw.extend(query_terms(" ".join(string_list(subspace.get("keywords")))))
    return unique_preserve_order([term.lower() for term in raw if len(term) >= 3])[:24]

def record_search_text(record: dict[str, Any]) -> str:
    try:
        from ._utils import normalize_space, scalar
    except ImportError:
        from _utils import normalize_space, scalar
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
    try:
        from ._utils import extract_year, is_unknown_value, normalize_label, numeric_value
    except ImportError:
        from _utils import extract_year, is_unknown_value, normalize_label, numeric_value
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
    try:
        from ._literature_search import query_terms
    except ImportError:
        from _literature_search import query_terms
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
    try:
        from ._utils import first_nonempty
    except ImportError:
        from _utils import first_nonempty
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
    try:
        from ._pipeline import project_records_for_mapping
        from ._project import load_project, save_project
        from ._utils import extract_year, is_unknown_value, new_id, normalize_label, numeric_value
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _project import load_project, save_project
        from _utils import extract_year, is_unknown_value, new_id, normalize_label, numeric_value
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
    try:
        from ._project import load_project, save_project
    except ImportError:
        from _project import load_project, save_project
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
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import is_unknown_value, normalize_label
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import is_unknown_value, normalize_label
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
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import is_unknown_value, normalize_label
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import is_unknown_value, normalize_label
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
    try:
        from ._utils import unique_preserve_order
    except ImportError:
        from _utils import unique_preserve_order
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
    try:
        from ._pipeline import project_records_for_mapping
        from ._project import load_project, save_project
        from ._utils import clamp_int, is_unknown_value, new_id, normalize_label, unique_preserve_order
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _project import load_project, save_project
        from _utils import clamp_int, is_unknown_value, new_id, normalize_label, unique_preserve_order
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
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
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
    try:
        from ._utils import is_unknown_value, normalize_label
    except ImportError:
        from _utils import is_unknown_value, normalize_label
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

def make_gap(
    gap_type: str,
    description: str,
    supporting_references: list[str],
    suggested_research_path: str,
    value_argument: str,
    hypothesis_ingredients: dict[str, Any] | None = None,
    counterfactual_leaves: list[str] | None = None,
) -> dict[str, Any]:
    try:
        from ._utils import new_id, unique_preserve_order
    except ImportError:
        from _utils import new_id, unique_preserve_order
    default_ingredients = {
        "methods": [],
        "scenarios": [],
        "benchmarks": [],
        "numerical_bounds": [],
        "operating_conditions": [],
        "measurable_metrics": [],
    }
    if hypothesis_ingredients:
        for k, v in hypothesis_ingredients.items():
            if isinstance(v, list):
                default_ingredients[k] = v
            else:
                default_ingredients[k] = [v] if v else []
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
        "hypothesis_ingredients": default_ingredients,
        "counterfactual_leaves": counterfactual_leaves or [],
    }

def semantic_plausibility_for_pair(
    project: dict[str, Any],
    method: str,
    scenario: str,
    gap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from ._literature_scoring import fields_are_incompatible, infer_research_field
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_space, record_context_text
    except ImportError:
        from _literature_scoring import fields_are_incompatible, infer_research_field
        from _pipeline import project_records_for_mapping
        from _utils import normalize_space, record_context_text
    method_text = normalize_space(method).lower()
    scenario_text = normalize_space(scenario).lower()
    project_text = normalize_space(
        " ".join(
            [
                str(project.get("domain", "")),
                str(project.get("objective", "")),
                str((gap or {}).get("description", "")),
                " ".join(record_context_text(record) for record in project_records_for_mapping(project)[:20]),
            ]
        )
    ).lower()
    requirements = method_input_requirements(method_text)
    affordances = scenario_data_affordances(f"{scenario_text} {project_text}")
    bridge = semantic_bridge_terms(method_text, scenario_text, project_text)
    score = 0.45
    score_breakdown: list[dict[str, Any]] = [{"factor": "base_prior", "delta": 0.45, "reason": "default prior before evidence checks"}]
    reasons: list[str] = []

    if concepts_are_connected(project, method, scenario):
        score += 0.35
        score_breakdown.append({"factor": "papergraph_cooccurrence", "delta": 0.35, "reason": "method and scenario co-occur in PaperGraph"})
        reasons.append("method and scenario already co-occur in at least one PaperGraph record")
    if bridge:
        delta = min(0.3, 0.08 * len(bridge))
        score += delta
        score_breakdown.append({"factor": "bridge_terms", "delta": round(delta, 3), "reason": f"{len(bridge)} bridge concept(s) detected"})
        reasons.append(f"bridge concepts detected: {', '.join(bridge[:6])}")
    if requirements:
        missing = sorted(requirements - affordances)
        if missing:
            delta = -min(0.5, 0.18 * len(missing))
            score += delta
            score_breakdown.append({"factor": "missing_method_affordances", "delta": round(delta, 3), "reason": ", ".join(missing)})
            reasons.append(f"method input requirements not visible in scenario/context: {', '.join(missing)}")
        else:
            score += 0.2
            score_breakdown.append({"factor": "matched_method_affordances", "delta": 0.2, "reason": "scenario/context exposes required data affordances"})
            reasons.append("scenario/context exposes the required data affordances")

    method_field = infer_research_field({"title": method, "abstract": method})
    scenario_field = infer_research_field({"title": scenario, "abstract": f"{scenario} {project.get('domain', '')}"})
    if fields_are_incompatible(method_field, scenario_field) and not bridge and not concepts_are_connected(project, method, scenario):
        delta = -0.35 if not project_context_mentions_pair(project_text, method_text, scenario_text) else -0.25
        score += delta
        score_breakdown.append({"factor": "field_mismatch_without_bridge", "delta": round(delta, 3), "reason": f"{method_field} -> {scenario_field}"})
        reasons.append(f"field mismatch without bridge evidence: {method_field} -> {scenario_field}")

    if ambiguous_short_method_label(method) and not bridge and not concepts_are_connected(project, method, scenario):
        score -= 0.2
        score_breakdown.append({"factor": "ambiguous_short_label_without_bridge", "delta": -0.2, "reason": "short acronym-like method label has no explicit bridge in context"})
        reasons.append("short acronym-like method label may be ambiguous across disciplines and lacks bridge evidence")

    if migration_noise_risk(project_text, method_text, scenario_text, bridge):
        score -= 0.2
        score_breakdown.append({"factor": "migration_noise_risk", "delta": -0.2, "reason": "pair appears to be driven by disconnected source domains rather than a shared mechanism"})
        reasons.append("cross-domain transfer risk: no shared mechanism terms, project context, or PaperGraph bridge")

    if method_looks_like_narrow_tool(method_text) and not ({"spatial_coordinates", "spatial_context"} & affordances):
        score -= 0.3
        score_breakdown.append({"factor": "narrow_tool_modality_mismatch", "delta": -0.3, "reason": "tool implies a data modality absent from scenario/context"})
        reasons.append("narrow tool/software method appears without matching data modality in the scenario")

    score = round(max(0.0, min(1.0, score)), 3)
    if score < 0.32:
        verdict = "REJECT"
    elif score < 0.55:
        verdict = "HUMAN_REVIEW"
    else:
        verdict = "PASS"
    return {
        "verdict": verdict,
        "score": score,
        "requirements": sorted(requirements),
        "scenario_affordances": sorted(affordances),
        "bridge_terms": bridge[:10],
        "score_breakdown": score_breakdown,
        "reason": "; ".join(reasons) if reasons else "no obvious semantic incompatibility detected",
    }

def method_input_requirements(method_text: str) -> set[str]:
    rules: list[tuple[tuple[str, ...], set[str]]] = [
        (("kernel density", "kde", "arcgis", "gis", "geospatial", "spatial interpolation", "hotspot analysis"), {"spatial_coordinates"}),
        (("cnn", "convolution", "vision transformer", "image segmentation", "microscopy"), {"image"}),
        (("lstm", "rnn", "recurrent", "sequence model", "time series", "temporal"), {"sequence"}),
        (("graph neural", "gnn", "message passing", "network embedding", "knowledge graph"), {"graph"}),
        (("single-cell", "scrna", "transcriptomic", "omics", "proteomic", "multi-omics"), {"omics"}),
        (("causal", "counterfactual", "instrumental variable", "difference-in-differences"), {"intervention"}),
        (("molecular docking", "density functional", "dft", "quantum", "molecular dynamics"), {"molecular"}),
    ]
    reqs: set[str] = set()
    for terms, required in rules:
        if any(term in method_text for term in terms):
            reqs.update(required)
    return reqs

def scenario_data_affordances(text: str) -> set[str]:
    rules: list[tuple[tuple[str, ...], str]] = [
        (("spatial transcriptomics", "spatial proteomics", "coordinate", "coordinates", "geospatial", "location", "neighborhood map"), "spatial_coordinates"),
        (("spatial", "atlas", "map", "mapping", "histology", "microenvironment", "neighborhood", "local context"), "spatial_context"),
        (("image", "imaging", "microscopy", "histology", "radiology", "pathology slide", "scan"), "image"),
        (("time", "temporal", "longitudinal", "trajectory", "dynamic", "persistence", "survival", "progression"), "sequence"),
        (("interaction", "network", "pathway", "graph", "cell-cell", "protein-protein", "ppi", "signaling"), "graph"),
        (("omics", "transcript", "rna-seq", "single-cell", "scrna", "proteomic", "genomic", "expression", "atlas"), "omics"),
        (("intervention", "trial", "randomized", "knockout", "perturbation", "dose", "treatment", "causal"), "intervention"),
        (("molecule", "protein", "ligand", "binding", "structure", "receptor", "site", "motif"), "molecular"),
    ]
    affordances: set[str] = set()
    for terms, affordance in rules:
        if any(term in text for term in terms):
            affordances.add(affordance)
    return affordances

def semantic_bridge_terms(method_text: str, scenario_text: str, project_text: str) -> list[str]:
    bridges = [
        "spatially resolved measurement",
        "reference atlas",
        "single-cell atlas",
        "context map",
        "interaction network",
        "heterogeneity profile",
        "target specificity",
        "adverse-effect profile",
        "multi-omics",
        "multi-modal measurement",
        "mechanistic model",
        "causal pathway",
        "benchmark dataset",
        "simulation",
        "domain adaptation",
        "boundary condition",
        "stress test",
    ]
    text = f"{method_text} {scenario_text} {project_text}"
    return [term for term in bridges if term in text]

def method_looks_like_narrow_tool(method_text: str) -> bool:
    return any(term in method_text for term in ("arcgis", "qgis", "gis", "kernel density", "kde", "excel", "tableau"))

def ambiguous_short_method_label(method: str) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    raw = normalize_space(str(method or ""))
    compact = re.sub(r"[^A-Za-z0-9]", "", raw)
    if 2 <= len(compact) <= 5 and compact.upper() == compact and any(ch.isalpha() for ch in compact):
        return True
    words = raw.split()
    if len(words) > 1:
        return False
    return 2 <= len(raw) <= 5 and raw.lower() in {"ai", "ml", "rl", "md", "sem", "mas", "pde", "ode", "gcn", "vae"}

def project_context_mentions_pair(project_text: str, method_text: str, scenario_text: str) -> bool:
    try:
        from ._literature_search import query_terms
        from ._utils import science_term_in_text
    except ImportError:
        from _literature_search import query_terms
        from _utils import science_term_in_text
    method_terms = set(query_terms(method_text))
    scenario_terms = set(query_terms(scenario_text))
    if not method_terms or not scenario_terms:
        return False
    method_hit = any(science_term_in_text(term, project_text) for term in method_terms)
    scenario_hit = any(science_term_in_text(term, project_text) for term in scenario_terms)
    return method_hit and scenario_hit

def migration_noise_risk(project_text: str, method_text: str, scenario_text: str, bridge: list[str]) -> bool:
    try:
        from ._literature_search import query_terms
    except ImportError:
        from _literature_search import query_terms
    if bridge:
        return False
    method_terms = set(query_terms(method_text))
    scenario_terms = set(query_terms(scenario_text))
    project_terms = set(query_terms(project_text))
    if not method_terms or not scenario_terms:
        return True
    shared = method_terms & scenario_terms
    method_overlap = method_terms & project_terms
    scenario_overlap = scenario_terms & project_terms
    return not shared and (not method_overlap or not scenario_overlap)

def count_gap_type(gaps: list[dict[str, Any]], gap_type: str) -> int:
    return sum(1 for gap in gaps if gap.get("gap_type") == gap_type)

def dedupe_knowledge_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from ._utils import unique_preserve_order
    except ImportError:
        from _utils import unique_preserve_order
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
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
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
    try:
        from ._literature_search import query_terms
    except ImportError:
        from _literature_search import query_terms
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
    if gap_type in {"migration", "problem", "mechanism_problem", "contradiction", "anomaly", "structural"}:
        novelty += 1
    if not refs:
        novelty -= 1
    semantic_gate = assessed.get("semantic_plausibility") if isinstance(assessed.get("semantic_plausibility"), dict) else {}
    if semantic_gate.get("verdict") == "HUMAN_REVIEW":
        novelty -= 2
    elif semantic_gate.get("verdict") == "REJECT":
        novelty -= 4
    novelty = max(1, min(10, novelty))
    feasibility = "high" if refs and gap_type in {"improvement", "mechanism_problem", "combinatorial", "contradiction", "anomaly"} else "medium"
    if semantic_gate.get("verdict") == "HUMAN_REVIEW":
        feasibility = "low"
    elif semantic_gate.get("verdict") == "REJECT":
        feasibility = "low"
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
                f"coverage={round(coverage, 3)}, feasibility={feasibility}, application_value={application_value}, "
                f"semantic_plausibility={semantic_gate.get('verdict', 'not_run')}"
            ),
            "requires_human_review": strongest_overlap >= 0.65 or not refs or semantic_gate.get("verdict") in {"HUMAN_REVIEW", "REJECT"},
        }
    )
    return assessed

def detect_migration_gaps(project: dict[str, Any], methods: list[str], scenarios: list[str], limit: int) -> list[dict[str, Any]]:
    try:
        from ._pipeline import supporting_references_for_method_or_scenario
    except ImportError:
        from _pipeline import supporting_references_for_method_or_scenario
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
        gate = semantic_plausibility_for_pair(project, method, missing[0], gap)
        gap["semantic_plausibility"] = gate
        if gate.get("verdict") == "REJECT":
            continue
        gaps.append(assess_gap_dict(project, gap))
        if len(gaps) >= limit:
            break
    return gaps

def detect_gap_signal_gaps(project: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import is_unknown_value, normalize_label, unique_preserve_order
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import is_unknown_value, normalize_label, unique_preserve_order
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

def detect_mechanism_issue_gaps(project: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_label, normalize_space, unique_preserve_order
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import normalize_label, normalize_space, unique_preserve_order
    gaps: list[dict[str, Any]] = []
    for record in project_records_for_mapping(project):
        if len(gaps) >= limit:
            break
        citation = record_reference(record)
        method = normalize_label(record.get("method", ""))
        scenario = normalize_label(record.get("scenario", ""))
        benchmark = normalize_label(record.get("benchmark", ""))
        text = normalize_space(
            " ".join(
                str(record.get(key, ""))
                for key in ("limitation", "conclusion", "abstract", "full_text_excerpt", "contribution")
                if record.get(key)
            )
        )
        candidate_signals = list(record.get("gap_signals", []) if isinstance(record.get("gap_signals"), list) else [])
        candidate_signals.extend(extract_mechanism_issue_signals(text, citation=citation))
        for signal in normalize_gap_signals(candidate_signals, citation=citation, limit=8):
            if len(gaps) >= limit:
                break
            signal_text = str(signal.get("text") or "")
            issue_axis = mechanism_issue_axis(signal_text)
            if not issue_axis:
                continue
            gap = make_gap(
                gap_type="mechanism_problem",
                description=mechanism_gap_description(issue_axis, signal_text, method, scenario, benchmark),
                supporting_references=unique_preserve_order([str(signal.get("supporting_reference") or ""), citation]),
                suggested_research_path=mechanism_gap_research_path(issue_axis, method, scenario, benchmark),
                value_argument=(
                    "This gap is grounded in a source-level mechanism/limitation/challenge statement, "
                    "so it should outrank bare method-scenario matrix holes."
                ),
            )
            assessed = assess_gap_dict(project, gap)
            assessed["mechanism_issue_signal"] = {
                "axis": issue_axis,
                "source_text": signal_text,
                "signal_type": signal.get("signal_type"),
                "confidence": signal.get("confidence"),
            }
            gaps.append(assessed)
    return dedupe_knowledge_gaps(gaps)[:limit]

def extract_mechanism_issue_signals(text: str, *, citation: str = "", limit: int = 12) -> list[dict[str, Any]]:
    try:
        from ._utils import new_id, split_sentences, trim_text
    except ImportError:
        from _utils import new_id, split_sentences, trim_text
    signals: list[dict[str, Any]] = []
    for sentence in split_sentences(text):
        axis = mechanism_issue_axis(sentence)
        if not axis:
            continue
        if len(sentence.split()) < 6:
            continue
        signals.append(
            {
                "signal_id": new_id("sig"),
                "signal_type": "mechanism_issue",
                "issue_axis": axis,
                "text": trim_text(sentence, 420),
                "evidence_type": "mechanism_problem_statement",
                "supporting_reference": citation,
                "confidence": mechanism_issue_confidence(axis, sentence),
            }
        )
    signals.sort(key=lambda item: (-float(item.get("confidence", 0.0)), item.get("issue_axis", ""), item.get("text", "")))
    return signals[:limit]

def mechanism_issue_axis(text: str) -> str:
    lowered = text.lower()
    axis_rules = [
        ("adverse_effect_or_safety", ("toxicity", "toxic", "safety", "adverse", "side effect", "risk", "hazard", "failure mode")),
        ("heterogeneity_or_subgroup", ("heterogeneity", "heterogeneous", "subgroup", "escape", "variation", "variability", "stratification", "combination", "combinatorial")),
        ("persistence_or_context_stress", ("persistence", "fatigue", "exhaustion", "stress", "environment", "microenvironment", "context", "adaptation", "infiltration")),
        ("interface_or_boundary_degradation", ("interface", "interfacial", "boundary", "surface", "degradation", "side reaction", "leakage", "drift", "decay", "aging")),
        ("operating_regime_stability", ("voltage", "temperature", "pressure", "frequency", "load", "scale", "resolution", "stability", "cycling", "retention", "capacity fading")),
        ("mechanism_uncertainty", ("mechanism", "remain unclear", "remains unclear", "unclear", "not understood", "unknown", "debate")),
        ("data_measurement_gap", ("lack of", "limited data", "insufficient", "scarce", "underexplored", "not measured", "no dataset")),
        ("generalization_robustness", ("generalization", "robustness", "failure mode", "distribution shift", "scale", "scalable", "reproducibility")),
    ]
    for axis, terms in axis_rules:
        if any(term in lowered for term in terms):
            return axis
    return ""

def mechanism_issue_confidence(axis: str, sentence: str) -> float:
    confidence = 0.78
    lowered = sentence.lower()
    if any(term in lowered for term in ("remain unclear", "remains unclear", "challenge", "limitation", "failure", "degradation", "adverse", "risk")):
        confidence += 0.08
    if axis in {"adverse_effect_or_safety", "interface_or_boundary_degradation", "operating_regime_stability"}:
        confidence += 0.04
    if any(term in lowered for term in ("may", "could", "might")):
        confidence -= 0.04
    return round(max(0.1, min(0.98, confidence)), 3)

def mechanism_gap_description(axis: str, signal_text: str, method: str, scenario: str, benchmark: str) -> str:
    try:
        from ._utils import is_unknown_value, trim_text
    except ImportError:
        from _utils import is_unknown_value, trim_text
    context = []
    if method and not is_unknown_value(method):
        context.append(f"method={method}")
    if scenario and not is_unknown_value(scenario):
        context.append(f"scenario={scenario}")
    if benchmark and not is_unknown_value(benchmark):
        context.append(f"benchmark={benchmark}")
    prefix = f"Source-grounded mechanism gap ({axis.replace('_', ' ')})"
    if context:
        prefix += f" for {', '.join(context)}"
    return f"{prefix}: {trim_text(signal_text, 360)}"

def mechanism_gap_research_path(axis: str, method: str, scenario: str, benchmark: str) -> str:
    if axis == "adverse_effect_or_safety":
        return "Map intended effects against adverse effects across relevant contexts, then test whether the proposed intervention improves benefit-risk without hiding failure modes."
    if axis == "heterogeneity_or_subgroup":
        return "Quantify heterogeneity, identify subgroup-specific failure modes, and test single versus combined strategies under explicit stratified benchmarks."
    if axis == "persistence_or_context_stress":
        return "Measure persistence under contextual stress and compare against interventions that change the suspected stress pathway."
    if axis == "interface_or_boundary_degradation":
        return "Isolate boundary or interface degradation pathways with matched diagnostics and test protective modifications under accelerated stress."
    if axis == "operating_regime_stability":
        return "Run operating-regime stress tests with mechanism-specific readouts to separate headline performance from mechanism fidelity."
    if axis == "mechanism_uncertainty":
        return "Convert the unclear mechanism into competing causal explanations and design an experiment or simulation that distinguishes them."
    if axis == "data_measurement_gap":
        return "Collect or retrieve the missing measurement layer, then evaluate whether the original claim survives the added data modality."
    return "Define the failure mode, perturb the suspected mechanism, and test whether the benchmark changes in the predicted direction."

def research_path_for_gap_signal(signal_type: str, method: str, scenario: str) -> str:
    try:
        from ._utils import is_unknown_value
    except ImportError:
        from _utils import is_unknown_value
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
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import trim_text
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import trim_text
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
    try:
        from ._literature_search import query_terms
        from ._pipeline import project_records_for_mapping
    except ImportError:
        from _literature_search import query_terms
        from _pipeline import project_records_for_mapping
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
    try:
        from ._literature_search import query_terms
        from ._pipeline import project_records_for_mapping
        from ._utils import record_context_text
    except ImportError:
        from _literature_search import query_terms
        from _pipeline import project_records_for_mapping
        from _utils import record_context_text
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

def extract_gap_signals_from_text(text: str, *, citation: str = "", limit: int = 12) -> list[dict[str, Any]]:
    try:
        from ._utils import new_id, normalize_space, split_sentences, trim_text
    except ImportError:
        from _utils import new_id, normalize_space, split_sentences, trim_text
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
    try:
        from ._utils import extract_section, trim_text, unique_preserve_order
    except ImportError:
        from _utils import extract_section, trim_text, unique_preserve_order
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
    if mechanism_issue_axis(sentence):
        return "mechanism_issue"
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
        "mechanism_issue": 0.84,
    }.get(signal_type, 0.6)
    lowered = sentence.lower()
    if any(term in lowered for term in ("we", "our", "this study", "the present study")):
        base += 0.05
    if any(term in lowered for term in ("may", "could", "might")):
        base -= 0.05
    return round(max(0.1, min(0.98, base)), 3)

def normalize_gap_signals(signals: list[dict[str, Any]], *, citation: str = "", limit: int = 16) -> list[dict[str, Any]]:
    try:
        from ._utils import new_id, trim_text
    except ImportError:
        from _utils import new_id, trim_text
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


# ---------------------------------------------------------------------------
# TABI: Toulmin-Abductive Bucketed Inference
# ---------------------------------------------------------------------------

def extract_evidence_pairs_from_records(project, limit=30):
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_space, trim_text
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import normalize_space, trim_text
    records = project_records_for_mapping(project)
    pairs = []
    method_records = defaultdict(list)
    for rec in records:
        method = str(rec.get("method") or "").strip()
        if method and method.lower() not in ("unknown", "unspecified", ""):
            method_records[method.lower()].append(rec)
    for method_key, recs in method_records.items():
        if len(recs) < 2:
            continue
        for i, left in enumerate(recs):
            for right in recs[i+1:]:
                ls = str(left.get("scenario", "")).lower().strip()
                rs = str(right.get("scenario", "")).lower().strip()
                if ls and rs and ls == rs:
                    lc = normalize_space(str(left.get("contribution") or ""))
                    rc = normalize_space(str(right.get("contribution") or ""))
                    if lc and rc and lc != rc:
                        pairs.append({
                            "pair_type": "contradiction",
                            "grounds_a": {
                                "text": trim_text(f"{left.get('method','')}: {lc}", 300),
                                "reference": record_reference(left),
                                "scenario": str(left.get("scenario", "")),
                            },
                            "grounds_b": {
                                "text": trim_text(f"{right.get('method','')}: {rc}", 300),
                                "reference": record_reference(right),
                                "scenario": str(right.get("scenario", "")),
                            },
                            "shared_context": f"method={left.get('method','')}, scenario={left.get('scenario','')}",
                        })
    causal_markers = [
        "leads to", "causes", "results in", "improves", "reduces",
        "increases", "decreases", "affects", "enables",
    ]
    chain_claims = []
    for rec in records:
        for fn in ("contribution", "limitation", "conclusion"):
            text = normalize_space(str(rec.get(fn) or ""))
            if not text:
                continue
            for m in causal_markers:
                if m in text.lower():
                    chain_claims.append({
                        "text": trim_text(text, 300),
                        "marker": m,
                        "reference": record_reference(rec),
                        "method": str(rec.get("method", "")),
                        "scenario": str(rec.get("scenario", "")),
                    })
                    break
    for i, ca in enumerate(chain_claims):
        for cb in chain_claims[i+1:]:
            aw = set(re.findall(r"\w{4,}", ca["text"].lower()))
            bw = set(re.findall(r"\w{4,}", cb["text"].lower()))
            overlap = aw & bw
            if len(overlap) >= 2 and ca["reference"] != cb["reference"]:
                pairs.append({
                    "pair_type": "causal_chain_gap",
                    "grounds_a": {
                        "text": ca["text"],
                        "reference": ca["reference"],
                        "scenario": ca.get("scenario", ""),
                    },
                    "grounds_b": {
                        "text": cb["text"],
                        "reference": cb["reference"],
                        "scenario": cb.get("scenario", ""),
                    },
                    "shared_context": f"shared_terms={','.join(list(overlap)[:5])}",
                })
    condition_markers = [
        ("under", "condition"), ("at", "level"), ("in", "environment"),
        ("for", "case"), ("when", "scenario"), ("within", "range"),
        ("above", "threshold"), ("below", "threshold"),
    ]
    for rec in records:
        lim = normalize_space(str(rec.get("limitation") or ""))
        if not lim or len(lim) < 20:
            continue
        for marker, kind in condition_markers:
            if f" {marker} " in lim.lower():
                pairs.append({
                    "pair_type": "extrapolation_limit",
                    "grounds_a": {
                        "text": trim_text(lim, 300),
                        "reference": record_reference(rec),
                        "scenario": str(rec.get("scenario", "")),
                    },
                    "grounds_b": {
                        "text": f"Validity claimed {marker} specific {kind}; generalization to other {kind}s is unverified",
                        "reference": record_reference(rec),
                        "scenario": str(rec.get("scenario", "")),
                    },
                    "shared_context": f"extrapolation from {kind} '{marker}'",
                })
                break
    return pairs[:limit]


def tabi_abductive_gap_detection(project, max_gaps=8):
    try:
        from ._utils import new_id, normalize_space, trim_text, unique_preserve_order
    except ImportError:
        from _utils import new_id, normalize_space, trim_text, unique_preserve_order
    evidence_pairs = extract_evidence_pairs_from_records(project, limit=30)
    if not evidence_pairs:
        return []
    gaps, seen_claims = [], set()
    for pair in evidence_pairs:
        pt = pair.get("pair_type", "")
        ga, gb = pair.get("grounds_a", {}), pair.get("grounds_b", {})
        sc = pair.get("shared_context", "")
        ta = normalize_space(str(ga.get("text", "")))
        tb = normalize_space(str(gb.get("text", "")))
        if not ta or not tb:
            continue
        warrant = tabi_warrant_for_pair(pt, ta, tb, sc)
        claim = tabi_abductive_claim(pt, ta, tb, warrant, sc)
        if not claim or len(claim) < 15:
            continue
        ck = gap_signature(claim)
        if ck in seen_claims:
            continue
        seen_claims.add(ck)
        bucket = tabi_bucket_confidence(pt, ta, tb, warrant)
        refs = unique_preserve_order([str(ga.get("reference", "")), str(gb.get("reference", ""))])
        gap = make_gap(
            gap_type="implicit_tabi" if pt != "extrapolation_limit" else "migration",
            description=trim_text(claim, 500),
            supporting_references=[r for r in refs if r],
            suggested_research_path=tabi_research_path(pt, claim, sc),
            value_argument=f"TABI abductive inference from {pt} evidence pair.",
        )
        gap["tabi_chain"] = {
            "grounds_a": trim_text(ta, 300),
            "grounds_b": trim_text(tb, 300),
            "warrant": trim_text(warrant, 300),
            "claim": trim_text(claim, 300),
            "pair_type": pt,
            "shared_context": sc,
        }
        gap["tabi_warrant"] = trim_text(warrant, 300)
        gap["tabi_claim"] = trim_text(claim, 300)
        gap["gap_discovery_method"] = "implicit_tabi"
        gap["confidence_bucket"] = bucket
        gap["tabi_evidence_type"] = pt
        gaps.append(gap)
        if len(gaps) >= max_gaps:
            break
    gaps.sort(key=lambda g: (0 if g.get("confidence_bucket") == "more_probable" else 1, -len(str(g.get("description", "")))))
    log_event("SCIENCE", "tabi_abductive_gaps_detected", count=len(gaps), pairs_evaluated=len(evidence_pairs))
    return gaps


def tabi_warrant_for_pair(pt, ta, tb, sc):
    if pt == "contradiction":
        return (
            f"Two studies report conflicting findings about {sc}. "
            "When evidence contradicts, the underlying mechanism or boundary condition is likely unresolved."
        )
    if pt == "causal_chain_gap":
        return (
            f"Evidence establishes separate causal links that share intermediate terms ({sc}). "
            "If A→B and B→C are independently supported but A→C has not been directly validated, "
            "the transitive causal claim remains a knowledge gap."
        )
    if pt == "extrapolation_limit":
        return (
            f"Validity is claimed {sc}. "
            "Generalization beyond the stated condition boundary is not supported by the available evidence."
        )
    return "Evidence premises suggest an unresolved inferential gap."


def tabi_abductive_claim(pt, ta, tb, warrant, sc):
    if pt == "contradiction":
        return (
            f"The mechanism underlying the contradiction between "
            f"'{ta[:120].rstrip('.,;')}' and '{tb[:120].rstrip('.,;')}' remains unresolved. "
            f"A systematic study controlling for {sc} is needed."
        )
    if pt == "causal_chain_gap":
        return (
            f"Although individual causal links are supported "
            f"({ta[:80].rstrip('.,;')} and {tb[:80].rstrip('.,;')}), "
            "the transitive relationship has not been directly validated."
        )
    if pt == "extrapolation_limit":
        return (
            f"The evidence supports validity {ta[:100].rstrip('.,;')}, "
            "but generalization to untested conditions remains an open question."
        )
    return ""


def tabi_bucket_confidence(pt, ta, tb, warrant):
    if pt == "contradiction":
        return "more_probable"
    if pt == "causal_chain_gap":
        aw = set(re.findall(r"\w{4,}", ta.lower()))
        bw = set(re.findall(r"\w{4,}", tb.lower()))
        return "more_probable" if len(aw & bw) >= 3 else "least_probable"
    if pt == "extrapolation_limit":
        return "more_probable" if len(ta) > 40 else "least_probable"
    return "least_probable"


def tabi_research_path(pt, claim, sc):
    if pt == "contradiction":
        return "Design a controlled experiment that systematically varies the disputed parameters while holding confounders constant."
    if pt == "causal_chain_gap":
        return "Conduct an end-to-end study that directly tests the transitive causal relationship with intermediate variable monitoring."
    if pt == "extrapolation_limit":
        return "Perform a regime-shift experiment varying the boundary condition to map the validity frontier."
    return "Investigate the identified gap with targeted experiments."


# ---------------------------------------------------------------------------
# Counterfactual Gap Analysis (CG)
# ---------------------------------------------------------------------------

def counterfactual_gap_analysis(project, gaps, limit=10):
    try:
        from ._pipeline import project_records_for_mapping
    except ImportError:
        from _pipeline import project_records_for_mapping
    records = project_records_for_mapping(project)
    if not records:
        return gaps
    enriched = []
    for gap in gaps[:limit]:
        tree = build_counterfactual_tree(gap, records)
        gap["counterfactual_tree"] = tree
        gap["gap_resolution_type"] = classify_gap_counterfactual_type(tree)
        gap["leaf_conditions"] = tree.get("leaf_conditions", [])
        gap["resolution_complexity"] = tree.get("resolution_complexity", "unknown")
        enriched.append(gap)
    log_event(
        "SCIENCE", "counterfactual_gap_analysis",
        gaps_analyzed=len(enriched),
        complement=sum(1 for g in enriched if g.get("gap_resolution_type") == "complement_gap"),
        novel=sum(1 for g in enriched if g.get("gap_resolution_type") == "novel_concept_gap"),
    )
    return enriched


def build_counterfactual_tree(gap, records):
    try:
        from ._utils import normalize_space, trim_text
    except ImportError:
        from _utils import normalize_space, trim_text
    desc = normalize_space(str(gap.get("description", "")))
    gt = str(gap.get("gap_type", ""))
    gm, gs = infer_method_scenario_from_gap(gap, records)
    related = find_related_records(gm, gs, records)
    missing = find_missing_evidence(gm, gs, records)
    branches = []
    if related:
        covered = {normalize_space(str(r.get("scenario", ""))).lower() for r in related}
        target = normalize_space(gs).lower()
        if target and target not in covered:
            branches.append({
                "condition": f"'{gm}' validated in other scenarios",
                "missing": f"No validation in '{gs}'",
                "counterfactual": f"If '{gm}' were validated in '{gs}', gap resolved",
                "leaf": True,
            })
        for rec in related:
            lim = normalize_space(str(rec.get("limitation", "")))
            if lim and len(lim) > 15:
                branches.append({
                    "condition": f"Study: {trim_text(str(rec.get('title', '')), 80)}",
                    "missing": f"Limitation: {trim_text(lim, 150)}",
                    "counterfactual": "If limitation addressed, evidence base strengthens",
                    "leaf": False,
                })
    else:
        # Fallback: synthesize counterfactual branches from gap_type and description
        if gt == "contradiction":
            branches.append({
                "condition": f"Conflicting claims about '{trim_text(desc, 80)}'",
                "missing": "No controlled experiment resolving the contradiction",
                "counterfactual": f"If a controlled experiment varied the disputed parameter in '{trim_text(gs or desc, 60)}', the contradiction would be resolved",
                "leaf": True,
            })
        elif gt in ("combinatorial", "density_hole"):
            branches.append({
                "condition": f"Method-scenario pair untested: '{trim_text(gm or desc, 60)}' in '{trim_text(gs or desc, 60)}'",
                "missing": "No validation study for this combination",
                "counterfactual": f"If '{trim_text(gm or 'the method', 40)}' were tested in '{trim_text(gs or 'the target scenario', 40)}', this density hole would be filled",
                "leaf": True,
            })
        elif gt == "migration":
            branches.append({
                "condition": f"Cross-domain transfer unvalidated",
                "missing": f"No study bridging the source and target domains in '{trim_text(desc, 80)}'",
                "counterfactual": f"If a transfer experiment validated the method across domains, this migration gap would be resolved",
                "leaf": True,
            })
        elif gt in ("improvement", "mechanism_problem"):
            branches.append({
                "condition": f"Mechanism unclear for '{trim_text(gm or desc, 60)}'",
                "missing": "No ablation or mechanistic study",
                "counterfactual": f"If an ablation study isolated the causal mechanism, this gap would be resolved",
                "leaf": True,
            })
        elif gt == "implicit_tabi":
            branches.append({
                "condition": f"TABI inference chain incomplete",
                "missing": "Warrant not empirically validated",
                "counterfactual": f"If the warrant linking the evidence pairs were tested, the implicit gap would be confirmed or refuted",
                "leaf": True,
            })
        # Always add a generic fallback branch for any gap type
        if not branches:
            branches.append({
                "condition": f"Gap: '{trim_text(desc, 100)}'",
                "missing": "No directly related evidence",
                "counterfactual": f"If a study addressed '{trim_text(desc, 60)}' directly, this gap would not exist",
                "leaf": True,
            })
    # Ingredients-based branches: concrete conditions from hypothesis_ingredients
    ingredients = gap.get("hypothesis_ingredients", {})
    for bound in (ingredients.get("numerical_bounds") or [])[:3]:
        branches.append({
            "condition": f"Test condition: {bound}",
            "missing": f"Not validated under {bound}",
            "counterfactual": f"If validated under {bound} conditions, this gap would be resolved",
            "leaf": True,
        })
    for metric in (ingredients.get("measurable_metrics") or [])[:3]:
        branches.append({
            "condition": f"Measurable metric: {metric}",
            "missing": f"{metric} not measured in current evidence",
            "counterfactual": f"If {metric} were measured and met threshold, this gap would be resolved",
            "leaf": True,
        })
    for cond in (ingredients.get("operating_conditions") or [])[:2]:
        branches.append({
            "condition": f"Operating condition: {cond}",
            "missing": f"Not tested under {cond}",
            "counterfactual": f"If tested under {cond} condition, this gap would be resolved",
            "leaf": True,
        })
    # Pre-built counterfactual_leaves from gap (if any)
    prebuilt_leaves = gap.get("counterfactual_leaves") or []
    for leaf_text in prebuilt_leaves[:3]:
        branches.append({
            "condition": trim_text(str(leaf_text), 120),
            "missing": "Pre-built counterfactual",
            "counterfactual": str(leaf_text),
            "leaf": True,
        })
    if gt in ("contradiction", "implicit_tabi"):
        tc = gap.get("tabi_chain", {})
        if tc:
            branches.append({
                "condition": f"Conflict: {trim_text(str(tc.get('shared_context', '')), 120)}",
                "missing": f"Warrant: {trim_text(str(tc.get('warrant', '')), 150)}",
                "counterfactual": "If controlled experiment resolved conflict, gap disappears",
                "leaf": True,
            })
    benchmarks = {normalize_space(str(r.get("benchmark", ""))).lower() for r in related if r.get("benchmark")}
    if benchmarks and gm:
        branches.append({
            "condition": f"Benchmarks: {', '.join(list(benchmarks)[:4])}",
            "missing": "No standardized benchmark",
            "counterfactual": "If standard benchmark existed, gap could be quantitatively assessed",
            "leaf": True,
        })
    leaves = [trim_text(b.get("counterfactual", ""), 200) for b in branches if b.get("leaf")]
    if not branches:
        root, cx = "No related evidence; gap may require entirely new research", "high"
    elif len(leaves) <= 1:
        root, cx = f"Single validation missing: {leaves[0] if leaves else desc[:100]}", "low"
    elif len(leaves) <= 3:
        root, cx = f"{len(leaves)} evidence conditions unmet", "medium"
    else:
        root, cx = f"{len(leaves)} evidence conditions unmet across dimensions", "high"
    return {
        "root": trim_text(root, 300),
        "branches": branches[:6],
        "leaf_conditions": leaves[:5],
        "resolution_complexity": cx,
        "related_evidence_count": len(related),
        "missing_evidence_count": len(missing),
        "gap_method": gm,
        "gap_scenario": gs,
    }


def classify_gap_counterfactual_type(tree):
    if tree.get("related_evidence_count", 0) == 0:
        return "novel_concept_gap"
    return "complement_gap"


# ---------------------------------------------------------------------------
# Hypothesis Ingredients Extraction
# ---------------------------------------------------------------------------

def extract_hypothesis_ingredients(project, method, scenario, refs):
    """Extract domain-specific 'hypothesis raw materials' from PaperGraph records.

    Returns a dict with methods, scenarios, benchmarks, numerical_bounds,
    operating_conditions, and measurable_metrics — concrete parameters that
    MingLi can use to build non-template hypotheses.
    """
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import normalize_space, unique_preserve_order

    ingredients = {
        "methods": [method] if method else [],
        "scenarios": [scenario] if scenario else [],
        "benchmarks": [],
        "numerical_bounds": [],
        "operating_conditions": [],
        "measurable_metrics": [],
    }

    records = project_records_for_mapping(project)
    ml = normalize_space(method).lower() if method else ""
    sl = normalize_space(scenario).lower() if scenario else ""

    # Collect benchmarks from knowledge_map
    km = project.get("knowledge_map", {})
    msb = km.get("method_scenario_benchmark", {})
    for m_key, scenarios_map in msb.items():
        if ml and normalize_space(m_key).lower() == ml:
            for s_key, bench_map in scenarios_map.items():
                if isinstance(bench_map, dict):
                    ingredients["benchmarks"].extend(bench_map.keys())
                elif isinstance(bench_map, list):
                    ingredients["benchmarks"].extend(bench_map)

    # Extract numerical bounds, operating conditions, and metrics from related records
    numerical_re = re.compile(r"(\d+\.?\d*)\s*(kV|V|MW|GW|km|m|°C|℃|%|kPa|W/m2|MPa|GPa|kA|A|Hz|μs|ns|pC|dB)")
    condition_keywords = [
        "high-altitude", "extreme", "low-pressure", "high-temperature", "overload",
        "rated", "no-load", "short-circuit", "transient", "steady-state",
        "cold-start", "hot-spot", "partial-discharge", "full-load", "lightning",
    ]
    metric_keywords = [
        "flashover voltage", "electric field distortion", "partial discharge",
        "insulation resistance", "breakdown voltage", "corona loss",
        "efficiency", "stability", "temperature rise", "power factor",
        "dissipation factor", "withstand voltage", "impedance",
    ]

    related_records = []
    for r in records:
        rm = normalize_space(str(r.get("method", ""))).lower()
        rs = normalize_space(str(r.get("scenario", ""))).lower()
        if (ml and ml == rm) or (sl and sl == rs):
            related_records.append(r)
    # Fallback: if no exact match, use records whose method/scenario share tokens
    if not related_records:
        desc_tokens = set(re.findall(r"\w{4,}", f"{ml} {sl}"))
        for r in records:
            rec_tokens = set(re.findall(r"\w{4,}", f"{r.get('method', '')} {r.get('scenario', '')}".lower()))
            if desc_tokens & rec_tokens:
                related_records.append(r)

    for rec in related_records[:10]:
        text = " ".join([
            str(rec.get("abstract", "")),
            str(rec.get("conclusion", "")),
            str(rec.get("limitation", "")),
            str(rec.get("title", "")),
        ])
        # Numerical bounds
        for match in numerical_re.finditer(text):
            val, unit = match.group(1), match.group(2)
            ingredients["numerical_bounds"].append(f"{val}{unit}")
        # Operating conditions
        text_lower = text.lower()
        for cond in condition_keywords:
            if cond in text_lower:
                ingredients["operating_conditions"].append(cond)
        # Measurable metrics
        for metric in metric_keywords:
            if metric in text_lower:
                ingredients["measurable_metrics"].append(metric)

    # Deduplicate and cap
    for key in ingredients:
        if isinstance(ingredients[key], list):
            ingredients[key] = unique_preserve_order(ingredients[key])[:5]

    return ingredients


def generate_counterfactual_leaves(method, scenario, refs):
    """Generate 'if X holds, gap disappears' leaf conditions."""
    try:
        from ._utils import unique_preserve_order
    except ImportError:
        from _utils import unique_preserve_order
    leaves = []
    m = str(method or "").strip()
    s = str(scenario or "").strip()
    if m and s:
        leaves.append(f"If '{m}' were validated in '{s}', this gap would not exist")
        leaves.append(f"If '{s}' had a standardized test benchmark, this gap could be directly assessed")
        leaves.append(f"If a published study confirmed '{m}' effectiveness in '{s}', the gap is resolved")
    if refs and isinstance(refs, list) and refs:
        leaves.append(f"If the method from '{trim_text(str(refs[0]), 80)}' were replicated in '{s}', this gap would be filled")
    if not leaves:
        leaves.append("If sufficient evidence were available, this gap would not exist")
    return unique_preserve_order(leaves)


# ---------------------------------------------------------------------------
# Multi-Gap Combination Selector
# ---------------------------------------------------------------------------

def select_gap_combination_for_hypothesis(project, ranked_gaps, strategy="auto"):
    """Select multiple gaps for aggregated hypothesis generation.

    Strategies:
    - 'auto': score by hypothesis_ingredients richness, pick top-3 with type diversity
    - 'top_k': pick top-3 by existing rank
    - 'complementary': pick one gap per distinct type
    """
    if not ranked_gaps:
        return []
    if len(ranked_gaps) <= 3:
        return list(ranked_gaps)

    if strategy == "top_k":
        return list(ranked_gaps[:3])

    if strategy == "complementary":
        selected, seen_types = [], set()
        for gap in ranked_gaps:
            gt = str(gap.get("gap_type", ""))
            if gt and gt not in seen_types:
                selected.append(gap)
                seen_types.add(gt)
                if len(selected) >= 3:
                    break
        # Fill remaining slots with top-ranked gaps
        for gap in ranked_gaps:
            if len(selected) >= 3:
                break
            if gap not in selected:
                selected.append(gap)
        return selected

    # 'auto': score by ingredient richness
    scored = []
    for gap in ranked_gaps:
        ingredients = gap.get("hypothesis_ingredients", {})
        score = 0
        score += len(ingredients.get("methods", [])) * 2
        score += len(ingredients.get("scenarios", [])) * 2
        score += len(ingredients.get("benchmarks", [])) * 1
        score += len(ingredients.get("numerical_bounds", [])) * 3
        score += len(ingredients.get("measurable_metrics", [])) * 2
        score += len(ingredients.get("operating_conditions", [])) * 2
        # Bonus for having supporting references
        score += len(gap.get("supporting_references", [])) * 1
        scored.append((score, gap))
    scored.sort(key=lambda x: -x[0])

    # Pick top-3 ensuring at least 2 different gap_types
    selected, types_seen = [], set()
    for _, gap in scored:
        if len(selected) >= 3:
            break
        selected.append(gap)
        types_seen.add(gap.get("gap_type", ""))
    # If all same type, swap last with first different type from remaining
    if len(types_seen) == 1 and len(scored) > 3:
        for _, gap in scored[3:]:
            if gap.get("gap_type", "") not in types_seen:
                selected[-1] = gap
                break
    return selected


# ---------------------------------------------------------------------------
# GRADE Pre-screening for Gap Combinations
# ---------------------------------------------------------------------------

def prefilter_gap_combination(project, gaps):
    """GRADE-style pre-screening: check if gap combination has enough literature support.

    Returns (sufficient: bool, reason: str, coverage: float).
    - coverage >= 0.6 → sufficient
    - 0.3 <= coverage < 0.6 → partially sufficient (proceed with warning)
    - coverage < 0.3 → insufficient (recommend supplement first)
    """
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space

    all_refs = []
    all_descriptions = []
    for gap in gaps:
        refs = gap.get("supporting_references", [])
        if isinstance(refs, list):
            all_refs.extend(refs)
        desc = str(gap.get("description", ""))
        if desc:
            all_descriptions.append(desc)

    if not all_refs and not all_descriptions:
        return False, "Gap combination has no references and no descriptions", 0.0

    # Build corpus from PaperGraph records
    papergraph = project.get("papergraph", [])
    if not papergraph:
        return False, "PaperGraph is empty; need literature first", 0.0

    corpus_parts = []
    for record in papergraph:
        if isinstance(record, dict):
            corpus_parts.append(str(record.get("title", "")))
            corpus_parts.append(str(record.get("abstract", "")))
    corpus = " ".join(corpus_parts).lower()

    if not corpus.strip():
        return False, "PaperGraph records have no text content", 0.0

    # Check reference coverage
    covered = 0
    total = len(all_refs) if all_refs else 1
    for ref in all_refs:
        ref_key = normalize_space(str(ref)).lower()[:80]
        if ref_key and ref_key in corpus:
            covered += 1
    ref_coverage = covered / total if total > 0 else 0.0

    # Check description term coverage (GRADE-style)
    desc_terms = set(re.findall(r"\w{4,}", " ".join(all_descriptions).lower()))
    if desc_terms:
        term_hits = sum(1 for t in desc_terms if t in corpus)
        term_coverage = term_hits / len(desc_terms)
    else:
        term_coverage = 0.0

    # Combined coverage
    coverage = 0.5 * ref_coverage + 0.5 * term_coverage

    if coverage >= 0.6:
        return True, f"Coverage sufficient ({coverage:.0%})", coverage
    elif coverage >= 0.3:
        return True, f"Coverage partial ({coverage:.0%}), recommend supplement", coverage
    else:
        return False, f"Coverage insufficient ({coverage:.0%}), need literature supplement first", coverage


def infer_method_scenario_from_gap(gap, records):
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    dl = normalize_space(str(gap.get("description", ""))).lower()
    tabi = gap.get("tabi_chain", {})
    if tabi:
        sc = str(tabi.get("shared_context", ""))
        if "method=" in sc:
            m = sc.split("method=")[-1].split(",")[0].strip()
            s = sc.split("scenario=")[-1].split(",")[0].strip() if "scenario=" in sc else ""
            return m, s
    km = {
        normalize_space(str(r.get("method", ""))).lower()
        for r in records
        if r.get("method") and str(r.get("method", "")).lower() not in ("unknown", "unspecified")
    }
    ks = {
        normalize_space(str(r.get("scenario", ""))).lower()
        for r in records
        if r.get("scenario") and str(r.get("scenario", "")).lower() not in ("unknown", "unspecified")
    }
    mm = ms = ""
    for m in km:
        if m and m in dl:
            mm = m
            break
    for s in ks:
        if s and s in dl:
            ms = s
            break
    # Token-based fallback: if exact substring matching failed, score by shared significant words
    if not mm and km:
        desc_tokens = {t for t in re.findall(r"\w{4,}", dl)}
        best_score, best_method = 0, ""
        for m in km:
            m_tokens = {t for t in re.findall(r"\w{4,}", m)}
            overlap = len(desc_tokens & m_tokens)
            if overlap > best_score:
                best_score, best_method = overlap, m
        if best_score >= 1 and best_method:
            mm = best_method
    if not ms and ks:
        desc_tokens = {t for t in re.findall(r"\w{4,}", dl)}
        best_score, best_scenario = 0, ""
        for s in ks:
            s_tokens = {t for t in re.findall(r"\w{4,}", s)}
            overlap = len(desc_tokens & s_tokens)
            if overlap > best_score:
                best_score, best_scenario = overlap, s
        if best_score >= 1 and best_scenario:
            ms = best_scenario
    return mm, ms


def find_related_records(method, scenario, records):
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    ml = normalize_space(method).lower() if method else ""
    sl = normalize_space(scenario).lower() if scenario else ""
    return [
        r for r in records
        if (ml and normalize_space(str(r.get("method", ""))).lower() == ml)
        or (sl and normalize_space(str(r.get("scenario", ""))).lower() == sl)
    ][:8]


def find_missing_evidence(method, scenario, records):
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    ml = normalize_space(method).lower() if method else ""
    sl = normalize_space(scenario).lower() if scenario else ""
    missing = []
    pair_exists = any(
        normalize_space(str(r.get("method", ""))).lower() == ml
        and normalize_space(str(r.get("scenario", ""))).lower() == sl
        for r in records
    )
    if not pair_exists and ml and sl:
        missing.append(f"No record validates '{method}' in scenario '{scenario}'")
    return missing


# ---------------------------------------------------------------------------
# GRADE Knowledge Sufficiency
# ---------------------------------------------------------------------------

def grade_knowledge_sufficiency(hypothesis_text, project):
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_space
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import normalize_space
    records = project_records_for_mapping(project)
    if not records:
        return {
            "rank_ratio": 1.0,
            "verdict": "knowledge_insufficient",
            "knowledge_boundary": "outside",
            "covered_terms": [],
            "uncovered_terms": [],
            "suggested_action": "No records; import literature first",
        }
    corpus = " ".join(
        normalize_space(
            " ".join(str(r.get(k, "")) for k in ("title", "abstract", "method", "scenario", "contribution", "conclusion"))
        ).lower()
        for r in records
    )
    key_terms = extract_grade_key_terms(normalize_space(hypothesis_text).lower())
    if not key_terms:
        return {
            "rank_ratio": 0.0,
            "verdict": "knowledge_sufficient",
            "knowledge_boundary": "within",
            "covered_terms": [],
            "uncovered_terms": [],
            "suggested_action": "Proceed to verification",
        }
    covered = [t for t in key_terms if t in corpus]
    uncovered = [t for t in key_terms if t not in corpus]
    rr = len(uncovered) / max(1, len(key_terms))
    if rr < 0.3:
        v, b, a = "knowledge_sufficient", "within", "PaperGraph covers hypothesis well"
    elif rr < 0.6:
        v, b, a = "knowledge_partial", "boundary", f"Partial coverage; supplement: {', '.join(uncovered[:5])}"
    else:
        v, b, a = "knowledge_insufficient", "outside", f"Lacks coverage: {', '.join(uncovered[:5])}"
    return {
        "rank_ratio": round(rr, 3),
        "verdict": v,
        "knowledge_boundary": b,
        "covered_terms": covered[:10],
        "uncovered_terms": uncovered[:10],
        "total_key_terms": len(key_terms),
        "suggested_action": a,
    }


def extract_grade_key_terms(text):
    stopwords = {
        "the", "and", "for", "that", "this", "with", "from", "have", "been",
        "will", "are", "was", "were", "not", "but", "can", "may", "should",
        "when", "then", "than", "also", "more", "less", "such", "each",
        "which", "their", "there", "would", "could", "does", "into", "over",
        "under", "between", "through", "during", "before", "after", "above",
        "below", "because", "while", "where", "both", "either", "neither",
        "hypothesis", "study", "experiment", "method", "results", "show",
        "using", "based", "propose", "approach", "analysis", "paper",
    }
    words = re.findall(r"[a-z][a-z0-9-]{2,}", text.lower())
    filtered = [w for w in words if w not in stopwords and len(w) >= 4]
    bigrams = []
    for i in range(len(filtered) - 1):
        bg = f"{filtered[i]} {filtered[i+1]}"
        if len(bg) > 8:
            bigrams.append(bg)
    return list(dict.fromkeys(filtered + bigrams))[:20]

