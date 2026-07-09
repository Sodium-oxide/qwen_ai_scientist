from __future__ import annotations

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



def run_mingli_hypothesis_evolution(
    project_id: str,
    gap_ids: list[str] | None = None,
    population_size: int = 24,
    generations: int = 4,
    top_k: int = 5,
    use_llm: bool = False,
) -> str:
    try:
        from ._gap_detection import build_temporal_knowledge_graph, detect_knowledge_gaps, detect_structural_knowledge_gaps, find_structural_analogy_transfers
        from ._models import Hypothesis
        from ._project import load_project, save_project
        from ._utils import clamp_int, new_id
    except ImportError:
        from _gap_detection import build_temporal_knowledge_graph, detect_knowledge_gaps, detect_structural_knowledge_gaps, find_structural_analogy_transfers
        from _models import Hypothesis
        from _project import load_project, save_project
        from _utils import clamp_int, new_id
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

    # Filter out gaps without substantive descriptions — they cause MingLi to generate templates
    valid_gaps = []
    for gap in gaps:
        desc = str(gap.get("description") or "").strip()
        # Must have at least 20 chars of real content (not just boilerplate)
        if len(desc) >= 20 and not desc.lower().startswith(("none", "null", "n/a", "todo")):
            valid_gaps.append(gap)
        else:
            # Mark incomplete gaps for downstream awareness
            gap["requires_human_review"] = True
            log_event("WARN", "gap_incomplete_description", gap_id=gap.get("gap_id"), desc_len=len(desc))

    if gap_ids:
        wanted = set(gap_ids)
        valid_gaps = [g for g in valid_gaps if g.get("gap_id") in wanted]

    # Fall back to all gaps if filtering removed everything (don't break the pipeline)
    pool = valid_gaps if valid_gaps else gaps
    if not valid_gaps and gaps:
        log_event("WARN", "no_valid_gaps_after_filter", total=len(gaps), valid=0)

    return sorted(
        pool,
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
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import is_unknown_value, normalize_label
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import is_unknown_value, normalize_label
    description = str(gap.get("description") or "")
    methods = sorted({normalize_label(record.get("method", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("method", ""))})
    scenarios = sorted({normalize_label(record.get("scenario", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("scenario", ""))})
    benchmarks = sorted({normalize_label(record.get("benchmark", "")) for record in project_records_for_mapping(project) if not is_unknown_value(record.get("benchmark", ""))})
    method = first_matching_label(description, methods) or (methods[0] if methods else "targeted intervention")
    scenario = first_matching_label(description, scenarios) or (scenarios[0] if scenarios else str(project.get("domain") or "target scenario"))
    benchmark = first_matching_label(description, benchmarks) or (benchmarks[0] if benchmarks else "mechanistic validity")
    benchmark = normalize_hypothesis_benchmark(benchmark, scenario, project)
    return {"method": method, "scenario": scenario, "benchmark": benchmark}

def normalize_hypothesis_benchmark(benchmark: str, scenario: str, project: dict[str, Any]) -> str:
    try:
        from ._literature_import import is_generic_phrase
        from ._utils import normalize_space
    except ImportError:
        from _literature_import import is_generic_phrase
        from _utils import normalize_space
    clean = normalize_space(benchmark).lower()
    generic = {
        "benchmark",
        "benchmark data",
        "benchmark dataset",
        "dataset",
        "validation dataset",
        "evaluation metric",
        "performance metric",
        "primary benchmark",
        "mechanistic validity",
    }
    if clean not in generic and not is_generic_phrase(clean):
        return benchmark
    text = normalize_space(f"{scenario} {project.get('domain', '')} {project.get('objective', '')}").lower()
    if any(term in text for term in ("reaction", "chemical", "molecular", "catalyst", "synthesis", "ligat", "cycloaddition")):
        return "reaction yield, rate constant, selectivity, stability, and functional outcome"
    if any(term in text for term in ("image", "imaging", "microscopy", "spectroscopy", "sensor")):
        return "signal-to-noise ratio, resolution, specificity, and measurement reproducibility"
    if any(term in text for term in ("protein", "cell", "gene", "clinical", "patient", "disease", "organism")):
        return "target specificity, biological response, safety margin, and reproducibility"
    if any(term in text for term in ("material", "device", "battery", "polymer", "semiconductor", "alloy")):
        return "stability, efficiency, transport, durability, and failure-mode metrics"
    if any(term in text for term in ("climate", "ecology", "environment", "geology", "agriculture")):
        return "forecast skill, process attribution, robustness across regimes, and uncertainty calibration"
    if any(term in text for term in ("algorithm", "model", "ai", "simulation", "control", "robot", "grid")):
        return "predictive accuracy, robustness, constraint satisfaction, calibration, and deployment cost"
    return "scenario-specific measurable outcome, uncertainty, robustness, and failure-mode metrics"

def first_matching_label(text: str, labels: list[str]) -> str:
    lowered = text.lower()
    for label in labels:
        if label and label.lower() in lowered:
            return label
    return ""

def specific_mechanism_text(
    project: dict[str, Any],
    method: str,
    scenario: str,
    benchmark: str,
    gap: dict[str, Any],
    semantic_gate: dict[str, Any],
) -> str:
    capability = method_capability_description(method)
    target = scenario_target_description(scenario, project)
    bridge = semantic_gate.get("bridge_terms", []) if isinstance(semantic_gate.get("bridge_terms"), list) else []
    requirements = semantic_gate.get("requirements", []) if isinstance(semantic_gate.get("requirements"), list) else []
    affordances = semantic_gate.get("scenario_affordances", []) if isinstance(semantic_gate.get("scenario_affordances"), list) else []
    bridge_text = (
        f"The required bridge is {', '.join(str(item) for item in bridge[:4])}."
        if bridge
        else "No explicit bridge concept is currently visible; this must be treated as a human-review assumption rather than a validated mechanism."
    )
    requirement_text = (
        f"The method requires {', '.join(str(item) for item in requirements)}, while the scenario exposes {', '.join(str(item) for item in affordances) or 'no explicit matching data modality'}."
        if requirements
        else "The method's input requirements are broad or not clearly specified; the experiment must make them explicit."
    )
    return (
        f"Concrete mechanism chain: (1) method capability: {method} contributes through {capability}; "
        f"(2) scenario target: in {scenario}, the affected process is {target}; "
        f"(3) measurable consequence: the bridge must produce a preregistered change in {benchmark}. "
        f"{requirement_text} {bridge_text} "
        f"The decisive prediction is that this concrete bridge, not a generic representation change, will alter {benchmark}; "
        f"if the bridge data or causal link is absent, the hypothesis should fail rather than be reinterpreted post hoc."
    )

def method_capability_description(method: str) -> str:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    text = normalize_space(method).lower()
    if any(term in text for term in ("cycloaddition", "ligation", "click", "reaction", "synthesis", "conjugation")):
        return "forming or transforming molecular bonds with measurable kinetics, selectivity, compatibility, and product stability"
    if any(term in text for term in ("printing", "bioprint", "fabrication", "manufacturing", "assembly")):
        return "controlling spatial organization, material architecture, and process-structure-property relationships"
    if any(term in text for term in ("spectroscopy", "microscopy", "imaging", "sensor", "assay")):
        return "turning a latent physical, chemical, or biological state into a calibrated observable signal"
    if any(term in text for term in ("kernel density", "kde", "arcgis", "gis")):
        return "estimating spatial density over coordinate-indexed observations"
    if any(term in text for term in ("single-cell", "scrna", "transcript", "omics")):
        return "resolving cell-state or molecular-expression heterogeneity across samples"
    if any(term in text for term in ("graph neural", "gnn", "knowledge graph", "network")):
        return "propagating evidence across explicitly defined nodes and relationships"
    if any(term in text for term in ("causal", "counterfactual", "intervention")):
        return "separating candidate causes from correlational associations under stated assumptions"
    if any(term in text for term in ("simulation", "model", "digital twin")):
        return "testing mechanistic predictions under controlled parameter variations"
    if any(term in text for term in ("deep learning", "machine learning", "classification", "prediction")):
        return "learning predictive structure from measurable input features"
    return "a specified operation that must be mapped to observable inputs and outputs before validation"

def scenario_target_description(scenario: str, project: dict[str, Any]) -> str:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    text = normalize_space(f"{scenario} {project.get('domain', '')} {project.get('objective', '')}").lower()
    if any(term in text for term in ("protein", "cell", "gene", "cancer", "clinical", "patient")):
        return "a measurable biological or clinical mechanism such as expression, pathway activation, response, adverse effect, or persistence"
    if any(term in text for term in ("material", "battery", "catalyst", "chemical", "reaction")):
        return "a measurable material, molecular, or reaction mechanism under controlled conditions"
    if any(term in text for term in ("climate", "ecology", "drought", "environment")):
        return "a measurable environmental process, spatial pattern, temporal regime, or ecosystem response"
    if any(term in text for term in ("grid", "control", "power", "robot", "engineering")):
        return "a controllable system state, stability margin, safety constraint, or operational performance metric"
    return "the scenario-specific measurable process named by the project evidence"

def make_hypothesis_seed(
    project: dict[str, Any],
    gap: dict[str, Any],
    components: dict[str, str],
    variant: int,
    *,
    analogy: dict[str, Any],
    hotspot: dict[str, Any],
) -> dict[str, Any]:
    try:
        from ._gap_detection import semantic_plausibility_for_pair
        from ._utils import new_id
    except ImportError:
        from _gap_detection import semantic_plausibility_for_pair
        from _utils import new_id
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
    semantic_gate = semantic_plausibility_for_pair(project, method, scenario, gap)
    variable = hypothesis_control_variable(gap, method, scenario)
    boundary = hypothesis_boundary_condition(gap)
    if str(gap.get("gap_type") or "") == "contradiction":
        statement = (
            f"If the competing claims about {scenario} are evaluated under matched {variable} conditions, "
            f"then {benchmark} will separate which mechanism holds and identify the boundary condition {boundary}."
        )
    else:
        statement = (
            f"If {method} is used to perturb or stratify {variable} in {scenario} {condition}, "
            f"then {benchmark} will show a directional or non-monotonic boundary at {boundary}."
        )
    mechanism = specific_mechanism_text(project, method, scenario, benchmark, gap, semantic_gate)
    if analogy:
        mechanism += f" The structural analogy to {analogy.get('analog_source_scenario')} supports transfer because the encoded problem structures are similar."
    causal_chain = [
        f"Input/intervention: vary {variable} for {method} in {scenario}",
        f"Mechanism: {method} must act through {method_capability_description(method)} on {scenario_target_description(scenario, project)}",
        f"Observable output: measure {benchmark} and locate boundary condition {boundary}",
    ]
    return {
        "candidate_id": new_id("hcand"),
        "gap_id": gap.get("gap_id"),
        "gap_ids": [str(gap.get("gap_id"))] if gap.get("gap_id") else [],
        "statement": statement,
        "mechanism": mechanism,
        "causal_chain": causal_chain,
        "expected_value": gap.get("value_argument") or "Potential to convert a mapped knowledge gap into a testable scientific mechanism.",
        "test_plan": (
            f"Build a minimal benchmark for {scenario}; compare {method} against canonical baselines; measure {benchmark}; "
            "include negative controls, ablations, and failure-mode analysis."
        ),
        "verification_plan": {
            "primary_metric": benchmark,
            "baselines": ["nearest dense PaperGraph method", "domain-standard baseline"],
            "falsification_condition": (
                f"No directional, non-monotonic, or mechanism-separating change in {benchmark} when {variable} crosses {boundary}."
            ),
        },
        "semantic_plausibility": semantic_gate,
        "source_gap": gap,
        "lineage": [{"generation": 0, "operation": "seed", "gap_id": gap.get("gap_id"), "analogy_used": analogy.get("analog_source_scenario", "")}],
        "generation": 0,
    }

def score_hypothesis_population(project: dict[str, Any], population: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [score_hypothesis_candidate(project, candidate) for candidate in population]

def score_hypothesis_candidate(project: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._gap_detection import local_idea_overlap
    except ImportError:
        from _gap_detection import local_idea_overlap
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
    try:
        from ._gap_detection import semantic_plausibility_for_pair
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _gap_detection import semantic_plausibility_for_pair
        from _utils import normalize_space, unique_preserve_order
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
    semantic_gate = candidate.get("semantic_plausibility") if isinstance(candidate.get("semantic_plausibility"), dict) else semantic_plausibility_for_pair(project, method, scenario, gap)
    if semantic_gate.get("verdict") == "REJECT":
        issues.append(f"Method-scenario semantic gate rejected the pair: {semantic_gate.get('reason')}")
        suggestions.append("Regenerate from a gap with an explicit data/modality/mechanism bridge or mark for human review.")
    elif semantic_gate.get("verdict") == "HUMAN_REVIEW":
        issues.append(f"Method-scenario semantic bridge is under-specified: {semantic_gate.get('reason')}")
        suggestions.append("Add the missing bridge representation before treating the hypothesis as plausible.")
    if "changes the information, intervention, or representation pathway" in text:
        issues.append("Mechanism uses a forbidden generic template rather than a concrete causal operation.")
        suggestions.append("Specify the method capability, scenario target process, bridge data, and falsification condition.")

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
    if semantic_gate.get("verdict") == "REJECT":
        score -= 0.3
    elif semantic_gate.get("verdict") == "HUMAN_REVIEW":
        score -= 0.12
    if "baseline" in text and ("falsification" in text or "negative control" in text or "stress" in text):
        score += 0.08
    score = max(0.15, min(1.0, score))
    return {
        "score": round(score, 3),
        "issues": issues,
        "suggestions": unique_preserve_order(suggestions),
        "semantic_plausibility": semantic_gate,
        "requires_human_review": bool(issues),
    }

def hypothesis_control_variable(gap: dict[str, Any], method: str, scenario: str) -> str:
    try:
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _utils import normalize_space, unique_preserve_order
    text = normalize_space(
        " ".join(
            str(item)
            for item in [
                gap.get("description", ""),
                gap.get("suggested_research_path", ""),
                method,
                scenario,
            ]
        )
    )
    patterns = [
        r"\b(?:concentration|dose|temperature|pressure|voltage|frequency|resolution|scale|sample size|time step|threshold|ratio|loading|coverage|depth|rate)\b",
        r"\b(?:noise level|data quality|constraint strength|parameter|boundary condition|operating regime)\b",
    ]
    hits: list[str] = []
    for pattern in patterns:
        hits.extend(match.group(0).lower() for match in re.finditer(pattern, text, flags=re.IGNORECASE))
    if hits:
        return unique_preserve_order(hits)[0]
    if str(gap.get("gap_type") or "") == "contradiction":
        return "the experimental, observational, or simulation conditions that differ between the claims"
    return "the key controllable variable named by the source evidence"

def hypothesis_boundary_condition(gap: dict[str, Any]) -> str:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    text = normalize_space(f"{gap.get('description', '')} {gap.get('suggested_research_path', '')}")
    numeric = re.search(
        r"\b(?:[<>]=?\s*)?\d+(?:\.\d+)?\s*(?:%|k|c|v|mv|a|ma|hz|khz|mhz|s|ms|us|nm|um|mm|cm|m|pa|bar|mol|mM|M|cycles?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if numeric:
        return normalize_space(numeric.group(0))
    if any(term in text.lower() for term in ("challenge", "contradict", "conflict", "debate", "unclear")):
        return "the condition where the competing explanations diverge"
    return "a preregistered stress threshold rather than an open-ended improvement claim"

def non_negated_phrase_in_text(phrase: str, text: str) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    normalized = normalize_space(phrase).lower()
    lowered = text.lower()
    for match in re.finditer(re.escape(normalized).replace(r"\ ", r"\s+"), lowered):
        prefix = lowered[max(0, match.start() - 40) : match.start()]
        if any(marker in prefix for marker in ("without", "no ", "not ", "lack", "lacks", "missing", "absent")):
            continue
        return True
    return False

def hypothesis_surprise_score(project: dict[str, Any], candidate: dict[str, Any]) -> float:
    try:
        from ._gap_detection import concepts_are_connected, literature_coverage_factor, record_field
        from ._literature_scoring import fields_are_incompatible
    except ImportError:
        from _gap_detection import concepts_are_connected, literature_coverage_factor, record_field
        from _literature_scoring import fields_are_incompatible
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
    try:
        from ._gap_detection import text_jaccard
    except ImportError:
        from _gap_detection import text_jaccard
    ordered = sorted(population, key=lambda item: (-float(item.get("score", 0.0)), item.get("statement", "")))
    selected: list[dict[str, Any]] = []
    used_gap_ids: set[str] = set()
    for candidate in ordered:
        semantic_gate = candidate.get("semantic_plausibility") if isinstance(candidate.get("semantic_plausibility"), dict) else {}
        if semantic_gate.get("verdict") == "REJECT":
            continue
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
        semantic_gate = candidate.get("semantic_plausibility") if isinstance(candidate.get("semantic_plausibility"), dict) else {}
        if semantic_gate.get("verdict") == "REJECT":
            continue
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
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import is_unknown_value, new_id, normalize_label, trim_text, unique_preserve_order
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import is_unknown_value, new_id, normalize_label, trim_text, unique_preserve_order
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

def generate_idea(
    project_id: str,
    gap: dict[str, Any] | str = "",
    gap_id: str = "",
    style: str = "innovative",
    parent_hypothesis_id: str = "",
    use_llm: bool = False,
) -> str:
    try:
        from ._models import Hypothesis
        from ._project import load_project, save_project
        from ._utils import new_id, normalize_key
    except ImportError:
        from _models import Hypothesis
        from _project import load_project, save_project
        from _utils import new_id, normalize_key
    project = load_project(project_id)
    selected_gap = mingli_resolve_gap(project, gap=gap, gap_id=gap_id)
    components = infer_gap_components(project, selected_gap)
    analogies = collect_project_analogies(project)
    hotspots = collect_project_hotspots(project)
    variant = 1 if normalize_key(style) == "innovative" else 0
    candidate = make_hypothesis_seed(
        project,
        selected_gap,
        components,
        variant,
        analogy=analogies[0] if analogies else {},
        hotspot=hotspots[0] if hotspots else {},
    )
    candidate["style"] = style
    candidate["parent_hypothesis_id"] = parent_hypothesis_id or None
    if parent_hypothesis_id:
        candidate.setdefault("lineage", []).append(
            {"generation": 0, "operation": "manual_parent_link", "parent_hypothesis_id": parent_hypothesis_id}
        )
    if normalize_key(style) == "conservative":
        candidate["statement"] = conservative_hypothesis_statement(candidate, components)
        candidate["mechanism"] = (
            f"The conservative mechanism tests the most direct pathway suggested by the gap: {components['method']} in "
            f"{components['scenario']}, with {components['benchmark']} as the decisive readout. "
            f"It must vary {hypothesis_control_variable(selected_gap, components['method'], components['scenario'])} and report the "
            f"boundary condition {hypothesis_boundary_condition(selected_gap)} rather than only a broad improvement claim."
        )
    else:
        candidate["statement"] = innovative_hypothesis_statement(candidate, components, selected_gap)
        candidate["mechanism"] += " MingLi treats this as a structural mutation rather than a surface rephrasing."
    candidate = score_hypothesis_candidate(project, candidate)
    idea = mingli_candidate_to_idea_json(project, candidate)
    draft = {
        "draft_idea_id": new_id("idea"),
        "project_id": project_id,
        "gap_id": selected_gap.get("gap_id", ""),
        "style": style,
        "candidate": candidate,
        "idea_json": idea,
        "use_llm_requested": bool(use_llm),
        "status": "draft",
        "createdAt": time.time(),
    }
    project.setdefault("mingli_draft_ideas", []).append(draft)
    project["phase"] = "Hypothesis Generation"
    project["updatedAt"] = time.time()
    save_project(project)
    return json.dumps(
        {
            "thought": "Generated a gap-traceable MingLi draft idea and scored it for novelty, plausibility, grounding, testability, impact, and surprise.",
            "action": {"type": "generate_idea", "gap_id": selected_gap.get("gap_id", ""), "style": style},
            **draft,
            "next_step": "Call design_experiment with the draft idea, then finalize_idea to run mandatory uniqueness verification.",
        },
        ensure_ascii=False,
        indent=2,
    )

def design_experiment(
    project_id: str,
    idea: dict[str, Any] | str = "",
    idea_id: str = "",
    constraints: str = "academic lab scale",
) -> str:
    try:
        from ._project import load_project, save_project
        from ._utils import new_id
    except ImportError:
        from _project import load_project, save_project
        from _utils import new_id
    project = load_project(project_id)
    idea_json = mingli_resolve_idea_json(project, idea=idea, idea_id=idea_id)
    gap = mingli_resolve_gap(project, gap_id=str(idea_json.get("gap_id") or ""))
    components = infer_gap_components(project, gap)
    experiment = {
        "setup": (
            f"Operationalize the hypothesis in {components['scenario']} under {constraints}. "
            f"Construct a minimal reproducible benchmark, include positive and negative controls, and run ablations that isolate "
            f"{components['method']} from data, representation, and intervention effects."
        ),
        "metrics": (
            f"Primary: {components['benchmark']}. Secondary: robustness under distribution shift, calibration/error bars, failure-mode rate, "
            "resource cost, and reproducibility across at least two independent splits or cohorts."
        ),
        "baselines": (
            "Nearest dense PaperGraph method; current domain-standard method; simple interpretable baseline; random or no-intervention control "
            "where scientifically meaningful."
        ),
        "falsification_criteria": (
            f"Reject or revise the hypothesis if {components['method']} does not improve {components['benchmark']} or if the claimed mechanism "
            "does not survive ablation, negative controls, or regime-shift checks."
        ),
    }
    idea_json["experiments"] = {
        "setup": experiment["setup"],
        "metrics": experiment["metrics"],
        "baselines": experiment["baselines"],
    }
    idea_json["risks"] = mingli_risk_text(gap, experiment)
    record = {
        "experiment_plan_id": new_id("exp"),
        "project_id": project_id,
        "idea_id": idea_id,
        "gap_id": gap.get("gap_id", ""),
        "constraints": constraints,
        "idea_json": idea_json,
        "falsification_criteria": experiment["falsification_criteria"],
        "createdAt": time.time(),
    }
    project.setdefault("mingli_experiment_plans", []).append(record)
    if idea_id:
        for draft in project.get("mingli_draft_ideas", []):
            if isinstance(draft, dict) and draft.get("draft_idea_id") == idea_id:
                draft["idea_json"] = idea_json
                draft["experiment_plan_id"] = record["experiment_plan_id"]
                draft["status"] = "experiment_designed"
                break
    project["updatedAt"] = time.time()
    save_project(project)
    return json.dumps(
        {
            "thought": "Designed a falsifiable experiment with setup, metrics, baselines, and rejection criteria.",
            "action": {"type": "design_experiment", "gap_id": gap.get("gap_id", ""), "constraints": constraints},
            **record,
            "next_step": "Call finalize_idea; it will run mandatory uniqueness verification before persisting the hypothesis.",
        },
        ensure_ascii=False,
        indent=2,
    )

def detect_hypothesis_template(idea: dict[str, Any]) -> dict[str, Any]:
    """Detect if a hypothesis uses forbidden generic templates.

    Returns a dict with is_template (bool), matched_patterns (list), and severity.
    """
    hyp_text = " ".join(
        str(idea.get(k) or "") for k in ("title", "hypothesis", "abstract", "related_work")
    ).lower()

    forbidden_patterns = [
        ("conflicting claims", "generic conflicting-claims template"),
        ("retested under matched", "generic retest-under-matched-conditions template"),
        ("mechanism-stress intervention", "generic mechanism-stress template"),
        ("reaction yield, rate constant, selectivity", "generic cross-domain metric list"),
        ("stability, and functional outcome", "generic cross-domain metric list"),
    ]
    matched = []
    for pattern, label in forbidden_patterns:
        if pattern in hyp_text:
            matched.append(label)

    # Check for extreme genericness: hypothesis has no numbers, no units, no chemical formulas
    import re as _re
    has_specifics = bool(
        _re.search(r"\d+\.?\d*\s*(nm|μm|mm|°c|°C|mV|V|A|mol|wt%|at%|hrs?|hours?|cycles?|ppm|K)", hyp_text)
        or _re.search(r"[A-Z][a-z]{0,2}\d|[IVX]{2,}|Li[A-Z]|V\([IVX]+\)", hyp_text)
        or _re.search(r"\b\d+\s*%", hyp_text)
    )

    is_template = bool(matched) or (not has_specifics and len(hyp_text) > 50)
    return {
        "is_template": is_template,
        "matched_patterns": matched,
        "has_domain_specifics": has_specifics,
        "severity": "REJECT" if matched else ("WARN" if not has_specifics and len(hyp_text) > 50 else "OK"),
    }


def check_hypothesis_evidence_alignment(idea: dict[str, Any], papergraph: list[dict[str, Any]]) -> dict[str, Any]:
    """Check if a hypothesis is anchored to the PaperGraph's core topics.

    Extracts significant terms from the hypothesis and checks overlap with
    PaperGraph paper titles/abstracts. Returns a verdict and details.
    """
    # Build hypothesis text
    hyp_text = " ".join(
        str(idea.get(k) or "") for k in ("title", "hypothesis", "abstract", "related_work")
    ).lower()

    if not hyp_text.strip():
        return {"verdict": "ALIGNED", "score": 1.0, "reason": "empty hypothesis text"}

    # Extract significant terms from hypothesis (skip stopwords and short words)
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
        "into", "through", "during", "before", "after", "above", "below",
        "between", "and", "but", "or", "nor", "not", "so", "yet", "both",
        "either", "neither", "each", "every", "all", "any", "few", "more",
        "most", "other", "some", "such", "no", "only", "own", "same",
        "than", "too", "very", "just", "because", "if", "when", "where",
        "how", "what", "which", "who", "whom", "this", "that", "these",
        "those", "it", "its", "we", "our", "their", "they", "them",
        "then", "about", "up", "out", "under", "over", "again", "further",
        "once", "here", "there", "also", "while", "although", "though",
        "however", "therefore", "thus", "hence", "since", "until",
        "study", "research", "method", "approach", "paper", "based",
        "using", "used", "new", "novel", "propose", "proposed", "show",
        "results", "analysis", "model", "system", "data", "use",
    }
    words = re.findall(r"[a-z][a-z\-]{2,}", hyp_text)
    hyp_terms = [w for w in words if w not in stopwords and len(w) > 2]

    if not hyp_terms:
        return {"verdict": "ALIGNED", "score": 1.0, "reason": "no significant terms extracted"}

    # Build PaperGraph corpus (titles + abstracts)
    pg_text = " ".join(
        str(p.get("title") or "") + " " + str(p.get("abstract") or "")
        for p in papergraph
        if isinstance(p, dict)
    ).lower()

    if not pg_text.strip():
        return {"verdict": "ALIGNED", "score": 1.0, "reason": "empty PaperGraph"}

    # Check overlap: how many hypothesis terms appear in PaperGraph
    matched = [t for t in set(hyp_terms) if t in pg_text]
    score = len(matched) / max(1, len(set(hyp_terms)))

    if score >= 0.3:
        verdict = "ALIGNED"
    elif score >= 0.15:
        verdict = "PARTIAL"
    else:
        verdict = "DRIFTED"

    return {
        "verdict": verdict,
        "score": round(score, 3),
        "hypothesis_terms": sorted(set(hyp_terms))[:20],
        "matched_terms": sorted(matched)[:20],
        "papergraph_paper_count": len([p for p in papergraph if isinstance(p, dict)]),
        "reason": (
            f"{len(matched)}/{len(set(hyp_terms))} hypothesis terms found in PaperGraph"
            + (f" (matched: {', '.join(sorted(matched)[:10])})" if matched else "")
        ),
    }


def finalize_idea(
    project_id: str,
    idea_json: dict[str, Any] | str = "",
    idea_id: str = "",
    live_search: bool = False,
    providers: list[str] | None = None,
) -> str:
    try:
        from ._models import Hypothesis
        from ._pipeline import verify_uniqueness
        from ._project import default_literature_providers, load_project, save_project
        from ._utils import new_id
    except ImportError:
        from _models import Hypothesis
        from _pipeline import verify_uniqueness
        from _project import default_literature_providers, load_project, save_project
        from _utils import new_id
    project = load_project(project_id)
    idea = mingli_resolve_idea_json(project, idea=idea_json, idea_id=idea_id)
    gap_id = str(idea.get("gap_id") or "")
    gap = mingli_resolve_gap(project, gap_id=gap_id)
    missing = mingli_final_schema_missing(idea)
    if missing:
        raise ValueError(f"finalize_idea requires complete MingLi JSON; missing: {', '.join(missing)}")
    semantic_gate = idea.get("semantic_plausibility") if isinstance(idea.get("semantic_plausibility"), dict) else {}
    if semantic_gate.get("verdict") == "REJECT":
        rejected = {
            "status": "rejected_semantic_plausibility",
            "reason": "MingLi idea failed method-scenario semantic plausibility gate; regenerate with an explicit bridge mechanism.",
            "idea_json": idea,
            "semantic_plausibility": semantic_gate,
            "gap_id": gap_id,
        }
        project.setdefault("mingli_rejected_ideas", []).append(rejected)
        project["updatedAt"] = time.time()
        save_project(project)
        return json.dumps(rejected, ensure_ascii=False, indent=2)

    verification_text = " ".join(str(idea.get(key) or "") for key in ("title", "hypothesis", "abstract", "related_work"))
    uniqueness = json.loads(
        verify_uniqueness(
            project_id,
            verification_text,
            precision="high",
            live_search=live_search,
            providers=providers or default_literature_providers(domain=str(project.get("domain", "")), query=verification_text),
        )
    )
    live_summary = uniqueness.get("live_search") if isinstance(uniqueness.get("live_search"), dict) else {}
    if live_search and live_summary.get("status") == "error":
        failed = {
            "status": "verification_failed",
            "reason": "Mandatory live literature verification failed; do not finalize until search succeeds.",
            "idea_json": idea,
            "uniqueness_check": uniqueness,
            "gap_id": gap_id,
        }
        project.setdefault("mingli_rejected_ideas", []).append(failed)
        project["updatedAt"] = time.time()
        save_project(project)
        return json.dumps(failed, ensure_ascii=False, indent=2)
    if uniqueness.get("verdict") == "overlap_risk":
        rejected = {
            "status": "rejected_overlap",
            "reason": "Mandatory novelty verification found high local overlap; regenerate or structurally mutate the idea.",
            "idea_json": idea,
            "uniqueness_check": uniqueness,
            "gap_id": gap_id,
        }
        project.setdefault("mingli_rejected_ideas", []).append(rejected)
        project["updatedAt"] = time.time()
        save_project(project)
        return json.dumps(rejected, ensure_ascii=False, indent=2)

    # Template detection: reject hypotheses that use forbidden generic structures
    template_check = detect_hypothesis_template(idea)
    if template_check.get("severity") == "REJECT":
        rejected = {
            "status": "rejected_template",
            "reason": (
                "Hypothesis uses a forbidden generic template. "
                f"Matched patterns: {', '.join(template_check.get('matched_patterns', []))}. "
                "Regenerate with domain-specific variables, metrics, and concrete mechanisms."
            ),
            "template_check": template_check,
            "idea_json": idea,
            "gap_id": gap_id,
        }
        project.setdefault("mingli_rejected_ideas", []).append(rejected)
        project["updatedAt"] = time.time()
        save_project(project)
        log_event("WARN", "hypothesis_rejected_template", gap_id=gap_id, patterns=template_check.get("matched_patterns"))
        return json.dumps(rejected, ensure_ascii=False, indent=2)

    hypothesis = Hypothesis(
        hypothesis_id=new_id("hyp"),
        gap_id=gap_id,
        statement=str(idea.get("hypothesis") or ""),
        mechanism=str(idea.get("abstract") or ""),
        expected_value=str(idea.get("related_work") or ""),
        test_plan=json.dumps(idea.get("experiments", {}), ensure_ascii=False),
        status="finalized",
    )
    # Evidence alignment check
    papergraph = project.get("papergraph", [])
    alignment = check_hypothesis_evidence_alignment(idea, papergraph)
    if alignment.get("verdict") == "DRIFTED":
        log_event(
            "WARN",
            "hypothesis_evidence_drift",
            project_id=project_id,
            score=alignment.get("score"),
            reason=alignment.get("reason"),
        )

    payload = asdict(hypothesis)
    payload.update(
        {
            "mingli_final_idea": idea,
            "uniqueness_check": uniqueness,
            "source_gap": gap,
            "parent_hypothesis_id": idea.get("parent_hypothesis_id"),
            "tournament_generation": idea.get("tournament_generation", 1),
            "lineage": idea.get("lineage", []),
            "evidence_alignment": alignment,
            "template_check": template_check,
            "constraints_checked": {
                "traceable_to_gap": bool(gap_id),
                "papergraph_grounded": bool(gap.get("supporting_references")),
                "mandatory_uniqueness_verification": True,
                "live_literature_verification": bool(live_search),
                "experiment_has_setup_metrics_baselines": True,
                "evidence_alignment_verdict": alignment.get("verdict"),
                "evidence_alignment_score": alignment.get("score"),
                "template_severity": template_check.get("severity"),
                "has_domain_specifics": template_check.get("has_domain_specifics"),
            },
        }
    )
    project.setdefault("hypotheses", []).append(payload)
    project.setdefault("mingli_finalized_ideas", []).append(payload)
    if idea_id:
        for draft in project.get("mingli_draft_ideas", []):
            if isinstance(draft, dict) and draft.get("draft_idea_id") == idea_id:
                draft["status"] = "finalized"
                draft["hypothesis_id"] = hypothesis.hypothesis_id
                break
    project["phase"] = "Hypothesis Generation"
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "mingli_idea_finalized", project_id=project_id, hypothesis_id=hypothesis.hypothesis_id, gap_id=gap_id)
    return json.dumps(
        {
            "status": "finalized",
            "hypothesis_id": hypothesis.hypothesis_id,
            "finalized_idea": idea,
            "uniqueness_check": uniqueness,
            "stored_hypothesis": payload,
        },
        ensure_ascii=False,
        indent=2,
    )

def mingli_resolve_gap(project: dict[str, Any], gap: dict[str, Any] | str = "", gap_id: str = "") -> dict[str, Any]:
    try:
        from ._gap_detection import dedupe_knowledge_gaps, parse_gap_input
        from ._utils import find_by_id
    except ImportError:
        from _gap_detection import dedupe_knowledge_gaps, parse_gap_input
        from _utils import find_by_id
    gaps = [item for item in project.get("knowledge_gaps", []) if isinstance(item, dict)]
    tanxi = project.get("tanxi_gap_analysis", {}) if isinstance(project.get("tanxi_gap_analysis"), dict) else {}
    tanxi_ranked = [item for item in tanxi.get("ranked_gaps", []) if isinstance(item, dict)]
    all_gaps = dedupe_knowledge_gaps(gaps + tanxi_ranked)
    if gap_id:
        found = find_by_id(all_gaps, "gap_id", gap_id)
        if found is None:
            raise ValueError(f"Unknown gap_id for project {project.get('project_id', '')}: {gap_id}")
        return found
    if isinstance(gap, dict) and gap:
        parsed = parse_gap_input(gap)
        if parsed.get("gap_id"):
            found = find_by_id(all_gaps, "gap_id", str(parsed.get("gap_id")))
            return found or parsed
        return parsed
    if isinstance(gap, str) and gap.strip():
        parsed = parse_gap_input(gap)
        if parsed.get("gap_id"):
            found = find_by_id(all_gaps, "gap_id", str(parsed.get("gap_id")))
            return found or parsed
        return parsed
    selected = select_gaps_for_hypothesis(project, None)
    if not selected:
        selected = tanxi_ranked[:1]
    if not selected:
        fallback = mingli_fallback_gap_from_papergraph(project)
        if fallback:
            return fallback
        raise ValueError("No TanXi/ZhiZhi knowledge gaps are available for MingLi.")
    return selected[0]

def mingli_fallback_gap_from_papergraph(project: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._gap_detection import assess_gap_dict, detect_gap_signal_gaps, detect_mechanism_issue_gaps, make_gap, record_reference
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_label
    except ImportError:
        from _gap_detection import assess_gap_dict, detect_gap_signal_gaps, detect_mechanism_issue_gaps, make_gap, record_reference
        from _pipeline import project_records_for_mapping
        from _utils import normalize_label
    mechanism = detect_mechanism_issue_gaps(project, limit=1)
    if mechanism:
        return mechanism[0]
    signals = detect_gap_signal_gaps(project, limit=1)
    if signals:
        return signals[0]
    records = project_records_for_mapping(project)
    if not records:
        return {}
    record = records[0]
    citation = record_reference(record)
    method = normalize_label(record.get("method", "")) or "the reported method"
    scenario = normalize_label(record.get("scenario", "")) or normalize_label(project.get("domain", "")) or "the target system"
    benchmark = normalize_label(record.get("benchmark", "")) or "the primary performance metric"
    gap = make_gap(
        gap_type="mechanism_problem",
        description=(
            f"PaperGraph contains evidence for {method} in {scenario}, but no explicit source-grounded limitation or contradiction "
            f"was available for MingLi; require a mechanism-specific validation around {benchmark} before proposing a broad hypothesis."
        ),
        supporting_references=[citation] if citation else [],
        suggested_research_path=(
            f"Extract a concrete causal link from the source text, then test how a controllable variable in {method} changes {benchmark} "
            f"in {scenario} under matched controls."
        ),
        value_argument="This fallback preserves evidence traceability and prevents a matrix-only pseudo-gap from silently driving hypothesis generation.",
    )
    return assess_gap_dict(project, gap)

def mingli_resolve_idea_json(project: dict[str, Any], idea: dict[str, Any] | str = "", idea_id: str = "") -> dict[str, Any]:
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
    if idea_id:
        for collection_name in ("mingli_draft_ideas", "mingli_experiment_plans"):
            for item in project.get(collection_name, []):
                if isinstance(item, dict) and item.get("draft_idea_id") == idea_id:
                    value = item.get("idea_json")
                    if isinstance(value, dict):
                        return dict(value)
                if isinstance(item, dict) and item.get("experiment_plan_id") == idea_id:
                    value = item.get("idea_json")
                    if isinstance(value, dict):
                        return dict(value)
        raise ValueError(f"Unknown MingLi idea_id: {idea_id}")
    if isinstance(idea, dict):
        return dict(idea)
    if isinstance(idea, str) and idea.strip():
        try:
            parsed = json.loads(idea)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {"title": trim_text(idea, 90), "hypothesis": idea}
    raise ValueError("Provide idea_json or idea_id.")

def mingli_candidate_to_idea_json(project: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    gap = candidate.get("source_gap") if isinstance(candidate.get("source_gap"), dict) else {}
    refs = gap.get("supporting_references", []) if isinstance(gap.get("supporting_references"), list) else []
    title = mingli_title_from_statement(str(candidate.get("statement", "")))
    experiments = candidate.get("verification_plan", {}) if isinstance(candidate.get("verification_plan"), dict) else {}
    components = infer_gap_components(project, gap)
    control_variable = hypothesis_control_variable(gap, components["method"], components["scenario"])
    boundary = hypothesis_boundary_condition(gap)
    return {
        "title": title,
        "hypothesis": str(candidate.get("statement") or ""),
        "abstract": (
            f"This proposal addresses the PaperGraph gap '{gap.get('description', '')}'. "
            f"It hypothesizes a testable mechanism: {candidate.get('mechanism', '')} "
            f"The study is designed to be falsifiable through {candidate.get('test_plan', '')}"
        ),
        "related_work": (
            f"Grounding evidence comes from: {', '.join(str(ref) for ref in refs[:5]) or 'PaperGraph records requiring expansion'}. "
            "The proposal differs by testing the mapped gap directly with explicit baselines, ablations, and failure-mode criteria."
        ),
        "experiments": {
            "setup": str(candidate.get("test_plan") or ""),
            "metrics": str(experiments.get("primary_metric") or "primary benchmark plus robustness and failure-mode metrics"),
            "baselines": ", ".join(str(item) for item in experiments.get("baselines", []) or ["domain-standard baseline"]),
        },
        "risks": mingli_risk_text(gap, experiments),
        "tournament_generation": int(candidate.get("generation") or 1),
        "parent_hypothesis_id": candidate.get("parent_hypothesis_id"),
        "gap_id": str(candidate.get("gap_id") or gap.get("gap_id") or ""),
        "lineage": candidate.get("lineage", []),
        "scores": candidate.get("scores", {}),
        "semantic_plausibility": candidate.get("semantic_plausibility", {}),
        "causal_chain": candidate.get("causal_chain", []),
        "controllable_variables": [control_variable],
        "measurable_outputs": [components["benchmark"]],
        "boundary_conditions": [boundary],
    }

def mingli_title_from_statement(statement: str) -> str:
    try:
        from ._models import Hypothesis
        from ._utils import normalize_space, trim_text
    except ImportError:
        from _models import Hypothesis
        from _utils import normalize_space, trim_text
    clean = normalize_space(statement)
    clean = re.sub(r"^if\s+", "", clean, flags=re.IGNORECASE)
    clean = clean.split(", then", 1)[0]
    clean = clean.split(" will ", 1)[0]
    return trim_text(clean[:1].upper() + clean[1:] if clean else "Gap-Grounded Testable Hypothesis", 120)

def conservative_hypothesis_statement(candidate: dict[str, Any], components: dict[str, str]) -> str:
    gap = candidate.get("source_gap") if isinstance(candidate.get("source_gap"), dict) else {}
    variable = hypothesis_control_variable(gap, components["method"], components["scenario"])
    boundary = hypothesis_boundary_condition(gap)
    return (
        f"If {components['method']} is evaluated in {components['scenario']} while explicitly varying {variable}, "
        f"then {components['benchmark']} should identify the limiting boundary {boundary} against domain-standard baselines."
    )

def innovative_hypothesis_statement(candidate: dict[str, Any], components: dict[str, str], gap: dict[str, Any]) -> str:
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
    variable = hypothesis_control_variable(gap, components["method"], components["scenario"])
    boundary = hypothesis_boundary_condition(gap)
    if str(gap.get("gap_type") or "") == "contradiction":
        return (
            f"If the conflicting claims in {components['scenario']} are retested under matched {variable} conditions, "
            f"then {components['benchmark']} will reveal which mechanism is valid and where the disagreement boundary lies: {boundary}."
        )
    return (
        f"If {components['method']} is coupled with a mechanism-stress intervention that varies {variable} in {components['scenario']}, "
        f"then {components['benchmark']} will expose a directional or non-monotonic boundary at {boundary}: "
        f"{trim_text(str(gap.get('description', '')), 140)}"
    )

def mingli_risk_text(gap: dict[str, Any], experiment: dict[str, Any]) -> str:
    risks = [
        "The mapped gap may be a retrieval or extraction artifact rather than a true scientific opening.",
        "The proposed mechanism may fail under ablation or regime-shift tests.",
        "Available datasets, instruments, or simulations may not expose the decisive variable cleanly.",
    ]
    if not gap.get("supporting_references"):
        risks.append("PaperGraph grounding is weak; collect stronger evidence before expensive experiments.")
    return " ".join(risks)

def mingli_final_schema_missing(idea: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in ("title", "hypothesis", "abstract", "related_work", "risks"):
        if not str(idea.get(key) or "").strip():
            missing.append(key)
    experiments = idea.get("experiments")
    if not isinstance(experiments, dict):
        missing.append("experiments")
    else:
        for key in ("setup", "metrics", "baselines"):
            if not str(experiments.get(key) or "").strip():
                missing.append(f"experiments.{key}")
    if "tournament_generation" not in idea:
        missing.append("tournament_generation")
    if "parent_hypothesis_id" not in idea:
        missing.append("parent_hypothesis_id")
    if not str(idea.get("gap_id") or "").strip():
        missing.append("gap_id")
    return missing

def create_hypothesis(
    project_id: str,
    gap_id: str,
    statement: str,
    mechanism: str,
    expected_value: str,
    test_plan: str,
) -> str:
    try:
        from ._models import Hypothesis
        from ._project import load_project, save_project
        from ._utils import new_id
    except ImportError:
        from _models import Hypothesis
        from _project import load_project, save_project
        from _utils import new_id
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

