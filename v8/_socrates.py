"""Socrates: evidence-bounded mechanism enrichment for research gaps.

Socrates sits between TanXi and MingLi. It does not invent a mechanism. It
turns an incomplete mechanism draft into small, auditable ZhiZhi retrieval
passes, then stores source excerpts for each mechanism field. A missing field
remains explicitly unsupported when the literature does not resolve it.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

try:
    from .log import log_event
except ImportError:
    from log import log_event


SOCRATES_MAX_ITERATIONS = 3
SOCRATES_MAX_FIELDS_PER_ITERATION = 2
SOCRATES_MAX_IMPORTS_PER_QUERY = 2
MECHANISM_FIELDS = (
    "identity",
    "location_or_scope",
    "dynamics",
    "reversibility",
    "observability",
    "intervention",
    "counterfactual",
)
FIELD_ALIASES = {"location": "location_or_scope", "scope": "location_or_scope"}

FIELD_QUERY_TEMPLATES: dict[str, tuple[str, ...]] = {
    "identity": (
        "{context} mechanism physical chemical biological origin",
        "{context} defect species phase pathway characterization",
    ),
    "location_or_scope": (
        "{context} interface surface region site spatial localization",
        "{context} boundary layer local distribution mapping",
    ),
    "dynamics": (
        "{context} kinetics rate time evolution accumulation cycle dependence",
        "{context} growth decay threshold temporal evolution model",
    ),
    "reversibility": (
        "{context} reversible irreversible recovery relaxation annealing",
        "{context} restoration hysteresis transient permanent degradation",
    ),
    "observability": (
        "{context} in situ operando measurement characterization detection",
        "{context} spectroscopy microscopy imaging assay observable signal",
    ),
    "intervention": (
        "{context} control manipulation suppression enhancement ablation",
        "{context} intervention blocking perturbation causal experiment",
    ),
    "counterfactual": (
        "{context} control experiment absence without baseline comparison",
        "{context} causal validation negative control mediation test",
    ),
}

FIELD_MARKERS: dict[str, tuple[str, ...]] = {
    "identity": ("mechanism", "pathway", "formation", "origin", "species", "phase", "defect", "reaction"),
    "location_or_scope": ("interface", "surface", "region", "site", "layer", "boundary", "within", "localized"),
    "dynamics": ("kinetic", "rate", "time", "cycle", "accumulation", "growth", "decay", "evolution", "threshold"),
    "reversibility": ("reversible", "irreversible", "recovery", "relaxation", "anneal", "restoration", "hysteresis"),
    "observability": ("measured", "detected", "observed", "characterized", "spectroscopy", "microscopy", "imaging", "assay"),
    "intervention": ("controlled", "suppressed", "enhanced", "inhibited", "ablation", "varied", "manipulated", "perturb"),
    "counterfactual": ("without", "absence", "control", "baseline", "compared", "negative control", "mediation"),
}

_STOPWORDS = {
    "about", "after", "before", "between", "from", "into", "that", "their", "there", "these", "this",
    "with", "when", "where", "which", "while", "using", "used", "study", "studies", "research",
    "method", "methods", "mechanism", "effect", "effects", "system", "systems", "analysis", "approach",
}


def canonical_mechanism_field(field: str) -> str:
    return FIELD_ALIASES.get(str(field or "").strip().lower(), str(field or "").strip().lower())


def mechanism_draft_from_gap(gap: dict[str, Any], domain: str = "") -> dict[str, Any]:
    """Create a deliberately incomplete draft without asserting a mechanism."""
    supplied = gap.get("mechanism_draft", {}) if isinstance(gap.get("mechanism_draft"), dict) else {}
    ingredients = gap.get("hypothesis_ingredients", {}) if isinstance(gap.get("hypothesis_ingredients"), dict) else {}
    method = _first_text(ingredients.get("methods"))
    scenario = _first_text(ingredients.get("scenarios"))
    benchmark = _first_text(ingredients.get("benchmarks"))
    description = _clean_text(gap.get("description"))
    context = _clean_text(" ".join(part for part in (method, scenario, domain, description) if part))
    tabi = gap.get("tabi_checks") if isinstance(gap.get("tabi_checks"), dict) else {}
    if tabi and not tabi.get("substantive"):
        # This is a retrieval instruction, not an asserted contradiction. It
        # steers the next Socrates/ZhiZhi passes toward the two evidence types
        # a real TABI audit needs instead of merely filling a matrix hole.
        context = _clean_text(f"{context} theory prediction experimental observation matched conditions")
    draft = {
        "gap_id": str(gap.get("gap_id") or ""),
        "input": _clean_text(supplied.get("input")) or _clean_text(" ".join(part for part in (method, scenario) if part)) or description,
        "proposed_mediator": _clean_text(supplied.get("proposed_mediator") or gap.get("proposed_mediator") or gap.get("mechanism_hint")),
        "output": _clean_text(supplied.get("output")) or benchmark,
        "context": context,
        "evidence": {},
        "tanxi_mechanism_draft": supplied,
        "tabi_required_retrieval": tabi.get("required_directed_retrieval", []) if tabi else [],
    }
    for field in MECHANISM_FIELDS:
        draft[field] = "unresolved"
    return draft


def unresolved_mechanism_fields(contract: dict[str, Any]) -> list[str]:
    """Return only fields that lack a source-cited evidence record."""
    evidence = contract.get("evidence", {}) if isinstance(contract.get("evidence"), dict) else {}
    specification = contract.get("mechanism_specification", {}) if isinstance(contract.get("mechanism_specification"), dict) else {}
    unresolved: list[str] = []
    for field in MECHANISM_FIELDS:
        entries = evidence.get(field, [])
        if _has_cited_evidence(entries):
            continue
        value = contract.get(field, specification.get(field))
        if isinstance(value, dict) and _has_cited_evidence(value.get("evidence", [])):
            continue
        unresolved.append(field)
    return unresolved


def check_mechanism_contract_completeness(contract: dict[str, Any]) -> list[str]:
    """Backward-compatible public name for the unresolved-field audit."""
    return unresolved_mechanism_fields(contract if isinstance(contract, dict) else {})


def translate_unresolved_to_queries(
    unresolved_fields: list[str],
    domain: str,
    method: str = "",
    scenario: str = "",
    mediator: str = "",
    context: str = "",
) -> dict[str, list[str]]:
    """Turn an evidence question into neutral, domain-general query variants."""
    query_context = _clean_text(" ".join(part for part in (domain, method, scenario, mediator, context) if part))
    if not query_context:
        query_context = "scientific mechanism"
    queries: dict[str, list[str]] = {}
    for raw_field in unresolved_fields:
        field = canonical_mechanism_field(raw_field)
        templates = FIELD_QUERY_TEMPLATES.get(field, ())
        variants: list[str] = []
        for template in templates:
            query = _clean_text(template.format(context=query_context))
            if query and query not in variants:
                variants.append(query[:240])
        if variants:
            queries[field] = variants
    return queries


def socrates_field_question(field: str, draft: dict[str, Any]) -> str:
    mediator = _clean_text(draft.get("proposed_mediator")) or "the proposed mediator"
    output = _clean_text(draft.get("output")) or "the stated outcome"
    questions = {
        "identity": f"What concrete physical, chemical, biological, mathematical, or engineering state does '{mediator}' denote, and what source sentence defines it?",
        "location_or_scope": f"Where, in the relevant system or regime, is '{mediator}' reported to act?",
        "dynamics": f"What time, dose, cycle, scale, or parameter dependence links '{mediator}' to {output}?",
        "reversibility": f"Under what recovery, relaxation, reversal, or boundary conditions is '{mediator}' reversible or irreversible?",
        "observability": f"Which measurement or observation directly detects '{mediator}' rather than only {output}?",
        "intervention": f"Which controllable intervention changes '{mediator}' while keeping the comparison interpretable?",
        "counterfactual": f"What control or absence-of-mediator comparison would weaken the claimed link to {output}?",
    }
    return questions.get(field, f"What source evidence resolves the {field} of '{mediator}'?")


def extract_mechanism_evidence(
    project: dict[str, Any],
    target_fields: list[str],
    *,
    domain: str = "",
    method: str = "",
    scenario: str = "",
    mediator: str = "",
    paper_ids: list[str] | None = None,
    max_per_field: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """Return only quoted PaperGraph excerpts that match a field and context.

    The returned excerpts are evidence records, not synthesized mechanisms. This
    is the guardrail that keeps Socrates from filling a contract with inference.
    """
    wanted = [canonical_mechanism_field(field) for field in target_fields]
    evidence: dict[str, list[dict[str, Any]]] = {field: [] for field in wanted}
    allowed_ids = {str(item) for item in (paper_ids or []) if str(item)}
    anchors = _context_terms(domain, method, scenario, mediator)
    for paper in project.get("papergraph", []):
        if not isinstance(paper, dict):
            continue
        if paper.get("active", True) is False:
            continue
        if str(paper.get("retrieval_phase") or "") == "boundary_extension":
            continue
        if str(paper.get("domain_review_verdict") or "keep") in {"review", "reject"}:
            continue
        paper_id = str(paper.get("paper_id") or "")
        if allowed_ids and paper_id not in allowed_ids:
            continue
        source_text = " ".join(
            str(paper.get(key) or "")
            for key in ("title", "abstract", "conclusion", "limitation", "full_text_excerpt")
        )
        alignment = socrates_paper_alignment(project, paper, anchors)
        if not alignment["passes"]:
            continue
        for sentence in _sentences(source_text):
            lowered = sentence.lower()
            anchor_hits = sum(1 for term in anchors if term in lowered)
            for field in wanted:
                marker_hits = sum(1 for marker in FIELD_MARKERS[field] if marker in lowered)
                if marker_hits == 0 or (anchors and anchor_hits == 0):
                    continue
                record = {
                    "paper_id": paper_id,
                    "citation": str(paper.get("citation") or paper.get("title") or ""),
                    "title": str(paper.get("title") or ""),
                    "field": field,
                    "excerpt": sentence,
                    "score": round(marker_hits * 2 + min(anchor_hits, 3), 2),
                    "alignment": alignment,
                }
                evidence[field].append(record)
    for field, entries in evidence.items():
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in entries:
            unique[(entry["citation"], entry["excerpt"])] = entry
        evidence[field] = sorted(unique.values(), key=lambda item: -float(item["score"]))[:max_per_field]
    return evidence


def socrates_paper_alignment(project: dict[str, Any], paper: dict[str, Any], anchors: set[str]) -> dict[str, Any]:
    """Require evidence papers to share the project-local mechanism vocabulary.

    This catches the tempting but invalid move of filling an ``intervention``
    field with a paper from another application merely because it uses the word
    "control". The vocabulary is learned from the core PaperGraph, not a
    field-specific denylist.
    """
    try:
        from ._gap_detection import mechanism_entity_profile
    except ImportError:
        from _gap_detection import mechanism_entity_profile
    profile = mechanism_entity_profile(project)
    text_terms = _context_terms(" ".join(str(paper.get(field) or "") for field in (
        "title", "abstract", "method", "scenario", "benchmark", "contribution", "limitation",
    )))
    core_hits = sorted(text_terms & set(profile.get("entities", [])))
    anchor_hits = sorted(text_terms & anchors)
    # Two core terms, or one core plus one query/gap anchor, keeps a short but
    # genuinely on-topic paper usable without admitting a broad boundary case.
    passes = len(core_hits) >= 2 or (len(core_hits) >= 1 and len(anchor_hits) >= 1)
    return {"passes": passes, "core_hits": core_hits[:10], "anchor_hits": anchor_hits[:10]}


def run_socrates_mechanism_enrichment(
    project_id: str,
    gap: dict[str, Any] | str = "",
    gap_id: str = "",
    mechanism_contract: dict[str, Any] | None = None,
    domain: str = "",
    providers: list[str] | None = None,
    max_iterations: int = SOCRATES_MAX_ITERATIONS,
    max_fields_per_iteration: int = SOCRATES_MAX_FIELDS_PER_ITERATION,
    max_results_per_query: int = 12,
    imports_per_query: int = SOCRATES_MAX_IMPORTS_PER_QUERY,
    use_llm: bool = False,
) -> str:
    """Run bounded Socrates -> ZhiZhi retrieval/enrichment iterations.

    At most ``max_fields_per_iteration`` searches are made per iteration. New
    papers are imported through the normal stratified search store before being
    cited, so every completed field remains traceable to PaperGraph evidence.
    """
    try:
        from ._project import default_literature_providers, load_project, save_project
    except ImportError:
        from _project import default_literature_providers, load_project, save_project

    project = load_project(project_id)
    selected_gap = _resolve_gap(project, gap=gap, gap_id=gap_id)
    actual_domain = _clean_text(domain or project.get("domain"))
    contract = dict(mechanism_contract) if isinstance(mechanism_contract, dict) else mechanism_draft_from_gap(selected_gap, actual_domain)
    contract.setdefault("gap_id", str(selected_gap.get("gap_id") or ""))
    contract.setdefault("evidence", {})
    contract.setdefault("context", _clean_text(selected_gap.get("description")))
    for field in MECHANISM_FIELDS:
        contract.setdefault(field, "unresolved")

    ingredients = selected_gap.get("hypothesis_ingredients", {}) if isinstance(selected_gap.get("hypothesis_ingredients"), dict) else {}
    method = _first_text(ingredients.get("methods"))
    scenario = _first_text(ingredients.get("scenarios"))
    mediator = _clean_text(contract.get("proposed_mediator") or contract.get("mediator"))
    selected_providers = providers or default_literature_providers(domain=actual_domain, query=contract.get("context", ""))
    selected_providers = [str(provider) for provider in selected_providers if str(provider)]
    max_iterations = max(1, min(int(max_iterations or SOCRATES_MAX_ITERATIONS), 5))
    max_fields_per_iteration = max(1, min(int(max_fields_per_iteration or SOCRATES_MAX_FIELDS_PER_ITERATION), 3))
    max_results_per_query = max(5, min(int(max_results_per_query or 12), 30))
    imports_per_query = max(1, min(int(imports_per_query or SOCRATES_MAX_IMPORTS_PER_QUERY), 3))

    iterations: list[dict[str, Any]] = []
    searches = 0
    imports = 0
    attempted_queries: set[tuple[str, str]] = set()
    for iteration in range(1, max_iterations + 1):
        project = load_project(project_id)
        validate_mechanism_contract_evidence(project, contract)
        unresolved = unresolved_mechanism_fields(contract)
        if not unresolved:
            break

        # First, mine what ZhiZhi has already imported before spending a query.
        existing = extract_mechanism_evidence(
            project, unresolved, domain=actual_domain, method=method, scenario=scenario, mediator=mediator,
        )
        updated_from_existing = _apply_evidence(contract, existing)
        validate_mechanism_contract_evidence(project, contract)
        unresolved = unresolved_mechanism_fields(contract)
        query_plan = translate_unresolved_to_queries(
            unresolved, actual_domain, method, scenario, mediator, str(contract.get("context") or ""),
        )
        selected_queries = select_untried_socrates_queries(
            unresolved, query_plan, attempted_queries, max_fields_per_iteration,
        )
        if not selected_queries:
            break
        search_reports: list[dict[str, Any]] = []
        updated_from_new = 0

        for field, query in selected_queries:
            attempted_queries.add((field, query))
            question = socrates_field_question(field, contract)
            report = socrates_call_zhizhi_targeted_search(
                project_id=project_id,
                query=query,
                domain=actual_domain,
                field=field,
                question=question,
                providers=selected_providers,
                max_results=max_results_per_query,
                imports_per_query=imports_per_query,
                use_llm=use_llm,
            )
            searches += int(report.get("searches", 0))
            imports += int(report.get("imports", 0))
            search_reports.append(report)
            project = load_project(project_id)
            new_evidence = extract_mechanism_evidence(
                project, [field], domain=actual_domain, method=method, scenario=scenario,
                mediator=mediator, paper_ids=report.get("paper_ids", []),
            )
            updated_from_new += _apply_evidence(contract, new_evidence)
            validate_mechanism_contract_evidence(project, contract)

        validate_mechanism_contract_evidence(project, contract)
        remaining = unresolved_mechanism_fields(contract)
        iteration_report = {
            "iteration": iteration,
            "unresolved_at_start": unresolved,
            "questions": {field: socrates_field_question(field, contract) for field, _ in selected_queries},
            "search_reports": search_reports,
            "fields_resolved_from_existing_papers": updated_from_existing,
            "fields_resolved_from_new_papers": updated_from_new,
            "remaining_unresolved": remaining,
        }
        iterations.append(iteration_report)
        log_event(
            "SCIENCE", "socrates_iteration_complete", project_id=project_id, iteration=iteration,
            searches=sum(item.get("searches", 0) for item in search_reports), imports=sum(item.get("imports", 0) for item in search_reports),
            resolved=updated_from_existing + updated_from_new, remaining=len(remaining),
        )
        queries_remain = any(
            any((field, query) not in attempted_queries for query in query_plan.get(field, []))
            for field in remaining
        )
        if updated_from_existing + updated_from_new == 0 and not queries_remain:
            break

    project = load_project(project_id)
    validate_mechanism_contract_evidence(project, contract)
    remaining = unresolved_mechanism_fields(contract)
    verdict = "COMPLETE" if not remaining else "INSUFFICIENT_EVIDENCE"
    causal_inference_plan = build_causal_inference_plan(selected_gap, contract)
    report = {
        "project_id": project_id,
        "gap_id": str(selected_gap.get("gap_id") or ""),
        "mechanism_contract": contract,
        "verdict": verdict,
        "iterations": iterations,
        "searches": searches,
        "imports": imports,
        "remaining_unresolved": remaining,
        "reading_focus": _reading_focus(remaining),
        "causal_inference_plan": causal_inference_plan,
        "next_step": (
            "Pass this evidence-cited mechanism contract to MingLi."
            if verdict == "COMPLETE"
            else "Do not assert the unresolved mechanism. Narrow the question, provide additional sources, or retain it as an explicit unsupported assumption."
        ),
    }
    contract["socrates_enrichment"] = {
        "verdict": verdict,
        "iterations_run": len(iterations),
        "searches": searches,
        "imports": imports,
        "remaining_unresolved": remaining,
    }
    contract["causal_inference_plan"] = causal_inference_plan
    project = load_project(project_id)
    project.setdefault("socrates_mechanism_contracts", {})[str(selected_gap.get("gap_id") or "unassigned")] = contract
    project.setdefault("socrates_reports", []).append(report)
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "socrates_enrichment_finished", project_id=project_id, verdict=verdict, searches=searches, imports=imports, remaining=len(remaining))
    return json.dumps(report, ensure_ascii=False, indent=2)


def build_causal_inference_plan(gap: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    input_variable = _clean_text(contract.get("input")) or _clean_text(gap.get("description"))
    mediator = _clean_text(contract.get("proposed_mediator")) or "the proposed mediator"
    outcome = _clean_text(contract.get("output")) or "the outcome"
    alternatives = [
        _clean_text(item)
        for item in gap.get("alternative_mechanisms", [])
        if _clean_text(item)
    ] if isinstance(gap.get("alternative_mechanisms"), list) else []
    return {
        "counterfactual_experiments": [
            {
                "question": f"If {input_variable} is changed while the proposed mediator is suppressed or absent, does {outcome} still change?",
                "design": f"Use matched control, intervention, and mediator-suppression or absence conditions; measure both {mediator} and {outcome}.",
                "prediction_if_mechanism_true": f"Changing {input_variable} changes {mediator}, and the {outcome} effect weakens when {mediator} is blocked or absent.",
                "prediction_if_mechanism_false": f"{outcome} changes independently of {mediator}, or does not respond reproducibly to the intervention.",
                "observability_requirement": "Use a direct measurement of the mediator plus an independent outcome measurement; proxies alone are insufficient.",
            }
        ],
        "first_principles_derivation": [
            {
                "step": f"State the conservation law, thermodynamic potential, kinetic rate law, or governing equation that could connect {input_variable} to {mediator}.",
                "status": "requires domain-specific source or calculation",
            },
            {
                "step": f"Derive a directional prediction for {mediator} -> {outcome} under the intervention and a matched null condition.",
                "status": "requires parameter assumptions and uncertainty bounds",
            },
        ],
        "mechanism_competition": {
            "primary": f"{input_variable} -> {mediator} -> {outcome}",
            "alternatives": alternatives,
            "discriminator": f"Measure the temporal or conditional ordering of {mediator} and {outcome} under independently chosen controls; retain competing mechanisms when the data do not separate them.",
        },
        "evidence_boundary": "This is an experimental and derivational plan, not evidence that the causal claim is already true.",
    }


def select_untried_socrates_queries(
    unresolved_fields: list[str],
    query_plan: dict[str, list[str]],
    attempted_queries: set[tuple[str, str]],
    limit: int,
) -> list[tuple[str, str]]:
    """Choose untried field-query pairs in deterministic, bounded order."""
    selected: list[tuple[str, str]] = []
    for field in unresolved_fields:
        canonical = canonical_mechanism_field(field)
        query = next(
            (item for item in query_plan.get(canonical, []) if (canonical, item) not in attempted_queries),
            "",
        )
        if query:
            selected.append((canonical, query))
        if len(selected) >= limit:
            break
    return selected


def socrates_call_zhizhi_targeted_search(
    *,
    project_id: str,
    query: str,
    domain: str,
    field: str,
    question: str,
    providers: list[str],
    max_results: int,
    imports_per_query: int,
    use_llm: bool,
) -> dict[str, Any]:
    """Run one small, persisted ZhiZhi retrieval pass for one evidence question."""
    try:
        from ._literature_import import domain_review_paper, extract_paper_keynote, import_literature_search_result
        from ._literature_search import search_literature_stratified
    except ImportError:
        from _literature_import import domain_review_paper, extract_paper_keynote, import_literature_search_result
        from _literature_search import search_literature_stratified
    output = {"field": field, "question": question, "query": query, "searches": 0, "imports": 0, "paper_ids": [], "errors": []}
    try:
        search = json.loads(
            search_literature_stratified(
                query=query,
                providers=providers,
                max_results=max_results,
                domain=domain,
                focus_branches=[f"Socrates evidence for {field}"],
                use_llm=use_llm,
            )
        )
        output["searches"] = 1
        output["search_id"] = str(search.get("search_id") or "")
        output["result_count"] = int(search.get("total_results") or 0)
        for result_index in range(min(output["result_count"], imports_per_query)):
            try:
                imported = json.loads(import_literature_search_result(project_id, output["search_id"], result_index, use_llm=use_llm))
                if str(imported.get("status") or "") == "duplicate":
                    continue
                record = imported.get("record") or {}
                paper_id = str(record.get("paper_id") or "") if isinstance(record, dict) else ""
                if paper_id:
                    review = domain_review_paper(project_id, paper_id, target_domain_profile=domain, min_confidence=0.6)
                    if str(review.get("verdict") or "") != "keep":
                        output["errors"].append(f"domain_review[{result_index}]:{review.get('verdict')}")
                        continue
                    output["paper_ids"].append(paper_id)
                    output["imports"] += 1
                    try:
                        extract_paper_keynote(project_id, paper_id=paper_id, use_llm=use_llm)
                    except Exception as exc:
                        output["errors"].append(f"keynote:{exc}")
            except Exception as exc:
                output["errors"].append(f"import[{result_index}]:{exc}")
    except Exception as exc:
        output["errors"].append(f"search:{exc}")
    return output


def validate_mechanism_contract_evidence(project: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Remove cited entries that do not belong to the same mechanism context.

    A citation is not accepted merely because it contains a generic field
    marker such as ``control`` or ``intervention``. Invalid evidence is kept in
    an audit trail and the field becomes unresolved, which makes the normal
    Socrates query loop retrieve a targeted replacement.
    """
    evidence = contract.get("evidence") if isinstance(contract.get("evidence"), dict) else {}
    if not evidence:
        return {"valid_fields": [], "rejected": []}
    by_id = {
        str(record.get("paper_id") or ""): record
        for record in project.get("papergraph", [])
        if isinstance(record, dict)
    }
    anchors = _context_terms(
        str(contract.get("context") or ""), str(contract.get("input") or ""),
        str(contract.get("proposed_mediator") or ""), str(contract.get("output") or ""),
    )
    rejected: list[dict[str, Any]] = []
    valid_fields: list[str] = []
    for field in MECHANISM_FIELDS:
        entries = evidence.get(field, []) if isinstance(evidence.get(field), list) else []
        valid: list[dict[str, Any]] = []
        for entry in entries:
            paper = by_id.get(str(entry.get("paper_id") or ""))
            alignment = socrates_paper_alignment(project, paper or {}, anchors) if paper else {"passes": False}
            if paper and alignment.get("passes"):
                item = dict(entry)
                item["alignment"] = alignment
                valid.append(item)
            else:
                rejected.append({
                    "field": field,
                    "citation": str(entry.get("citation") or ""),
                    "reason": "evidence paper does not share the core mechanism context",
                })
        if valid:
            evidence[field] = valid
            valid_fields.append(field)
        elif entries:
            evidence[field] = []
            contract[field] = "unresolved"
    if rejected:
        contract.setdefault("rejected_evidence", []).extend(rejected)
    contract["evidence_alignment_audit"] = {
        "valid_fields": valid_fields,
        "rejected_count": len(rejected),
        "status": "pass" if not rejected else "replacement_retrieval_required",
    }
    return {"valid_fields": valid_fields, "rejected": rejected}


def _apply_evidence(contract: dict[str, Any], evidence: dict[str, list[dict[str, Any]]]) -> int:
    store = contract.setdefault("evidence", {})
    updated = 0
    for field, entries in evidence.items():
        if not entries or _has_cited_evidence(store.get(field, [])):
            continue
        store[field] = entries
        contract[field] = {
            "status": "evidence_based",
            "claim": str(entries[0].get("excerpt") or ""),
            "citation": str(entries[0].get("citation") or ""),
            "evidence": entries,
        }
        updated += 1
    return updated


def _resolve_gap(project: dict[str, Any], *, gap: dict[str, Any] | str, gap_id: str) -> dict[str, Any]:
    if isinstance(gap, dict) and gap:
        return dict(gap)
    wanted = str(gap_id or gap or "").strip()
    tanxi = project.get("tanxi_gap_analysis", {}) if isinstance(project.get("tanxi_gap_analysis"), dict) else {}
    # Ranked TanXi gaps preserve mechanism relevance, TABI and ingredients;
    # prefer them over the older canonical list when both share an id.
    candidates = [item for item in tanxi.get("ranked_gaps", []) if isinstance(item, dict)]
    candidates.extend(item for item in project.get("knowledge_gaps", []) if isinstance(item, dict))
    if wanted:
        for item in candidates:
            if str(item.get("gap_id") or "") == wanted:
                return dict(item)
    if candidates:
        return dict(candidates[0])
    raise ValueError("Socrates requires a TanXi gap or a project with ranked knowledge gaps.")


def _has_cited_evidence(value: Any) -> bool:
    entries = value if isinstance(value, list) else []
    return any(isinstance(entry, dict) and str(entry.get("citation") or "").strip() and str(entry.get("excerpt") or "").strip() for entry in entries)


def _context_terms(*parts: str) -> set[str]:
    terms: set[str] = set()
    for part in parts:
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9+\-]{3,}", str(part or "").lower()):
            if token not in _STOPWORDS:
                terms.add(token)
    return terms


def _sentences(text: str) -> list[str]:
    sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")):
        clean = _clean_text(sentence)
        if 30 <= len(clean) <= 600:
            sentences.append(clean)
    return sentences


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        return _clean_text(value[0]) if value else ""
    return _clean_text(value)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _reading_focus(fields: list[str]) -> dict[str, str]:
    return {
        field: {
            "identity": "definition of the mediator and its causal link to the claimed outcome",
            "location_or_scope": "where the mediator is reported to act and the stated validity regime",
            "dynamics": "time, dose, cycle, scale, or parameter dependence rather than an endpoint-only result",
            "reversibility": "recovery, relaxation, annealing, hysteresis, or explicit irreversibility evidence",
            "observability": "direct measurement signal and instrument, not a proxy endpoint alone",
            "intervention": "a controllable manipulation that changes the mediator",
            "counterfactual": "negative controls, absence-of-mediator comparisons, or mediation tests",
        }.get(field, "source text that directly operationalizes the unresolved mechanism field")
        for field in fields
    }
