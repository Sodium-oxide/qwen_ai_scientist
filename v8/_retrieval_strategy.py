"""Research-question-aware retrieval planning and paper role assessment."""
from __future__ import annotations

import re
from typing import Any


QUERY_FAMILIES = (
    (
        "landscape",
        "domain map, terminology, and established mechanisms",
        "review OR survey OR overview",
    ),
    (
        "direct_mechanism",
        "direct causal and mechanistic evidence",
        "mechanism OR causal OR perturbation OR mediation OR necessary OR sufficient",
    ),
    (
        "barrier_failure",
        "barriers, limitations, inefficiency, and anomalous observations",
        "barrier OR limitation OR inefficiency OR resistance OR incomplete OR failure",
    ),
    (
        "counter_evidence",
        "boundary conditions, alternative mechanisms, and contradictory evidence",
        "context dependent OR contradictory OR alternative mechanism OR boundary condition",
    ),
    (
        "frontier",
        "recent frontier work and preprints",
        "recent OR latest OR preprint",
    ),
)

_GENERIC_TERMS = {
    "a", "about", "after", "analysis", "an", "and", "approach", "are", "as", "at", "based", "be", "between", "by", "cell", "cells",
    "data", "effect", "effects", "for", "from", "human", "into", "model", "models",
    "of", "on", "or", "paper", "papers", "research", "result", "results", "science", "study", "studies",
    "system", "systems", "that", "the", "their", "this", "to", "using", "via", "with",
}
_CAUSAL_MARKERS = (
    "causal", "mechanism", "mediated", "mediation", "necessary", "sufficient",
    "perturb", "knockdown", "knockout", "overexpression", "inhibit", "ablation",
    "rescue", "intervention", "lineage tracing", "time course",
)
_REVIEW_MARKERS = ("review", "survey", "meta-analysis", "perspective", "overview")
_EXPLICIT_NEGATION_MARKERS = (
    "exclude", "excluding", "without", "not including", "not involve", "排除", "不包括", "不含",
)


def build_research_question_card(
    domain: str,
    objective: str,
    research_brief: str = "",
    query: str = "",
) -> dict[str, Any]:
    """Build a durable retrieval contract before literature search begins."""
    try:
        from ._models import research_domain_subfield_topics
    except ImportError:
        from _models import research_domain_subfield_topics
    source = " ".join(
        value.strip()
        for value in (domain, objective, research_brief, query)
        if str(value or "").strip()
    )
    lowered = source.lower()
    core_terms = _significant_terms(" ".join((query, domain, objective)), limit=18)
    catalog_subfields = research_domain_subfield_topics(source, max_topics=4, terms_per_topic=5)
    normal_cell_fate_scope = bool(
        any(marker in lowered for marker in ("normal", "正常"))
        and any(
            marker in lowered
            for marker in ("cell fate", "cellular", "lineage", "reprogramming", "differentiation", "细胞", "谱系", "重编程", "分化")
        )
    )
    cancer_terms = ["cancer", "tumor", "tumour", "neoplasm", "oncology", "癌症", "肿瘤"]
    explicitly_excluded = [
        term
        for term in cancer_terms
        if term in lowered and any(
            marker in lowered[max(0, lowered.find(term) - 40) : lowered.find(term) + len(term) + 40]
            for marker in _EXPLICIT_NEGATION_MARKERS
        )
    ]
    bridge_terms = ["cancer", "tumor", "tumour", "neoplasm", "oncology"] if normal_cell_fate_scope and not explicitly_excluded else []
    boundary_confirmation_required = normal_cell_fate_scope and not explicitly_excluded
    return {
        "version": "retrieval_strategy_v1",
        "research_question": str(objective or query or domain).strip(),
        "domain": str(domain or "").strip(),
        "core_terms": core_terms,
        "catalog_subfields": catalog_subfields,
        "causal_questions": [
            "What is necessary or sufficient for the proposed effect?",
            "What mediates the relationship, and what alternative paths remain plausible?",
            "Under which model, stage, time, or boundary condition does the relationship fail?",
        ],
        "accepted_evidence": [
            "genetic_or_pharmacological_perturbation",
            "rescue_or_epistasis",
            "time_resolved_measurement",
            "lineage_or_context_matched_validation",
            "orthogonal_multi_modal_readout",
        ],
        "boundary_policy": {
            "normal_cell_fate_scope": normal_cell_fate_scope,
            "bridge_terms": bridge_terms,
            "explicit_exclusion_terms": explicitly_excluded,
            "requires_human_confirmation": boundary_confirmation_required,
            "instruction": (
                "Treat cancer or tumour plasticity papers as bridge background unless the user explicitly includes them."
                if normal_cell_fate_scope and not explicitly_excluded
                else "Use the stated project boundary when assigning a paper role."
            ),
        },
        "paper_role_policy": {
            "CORE": "Directly supports the bounded research question or supplies context-matched mechanism evidence.",
            "BRIDGE": "Supplies transferable mechanism, terminology, or background but cannot alone support the core causal claim.",
            "EXCLUDE": "Conflicts with an explicit project boundary or final domain gate and must not enter core reasoning.",
        },
    }


def with_retrieval_query(card: dict[str, Any] | None, query: str) -> dict[str, Any]:
    """Merge the provider-safe retrieval query into a durable question card."""
    normalized = dict(card or {})
    existing_terms = [str(item) for item in normalized.get("core_terms", []) if str(item).strip()]
    normalized["core_terms"] = _unique(_significant_terms(query, limit=18) + existing_terms)[:24]
    normalized["retrieval_query"] = str(query or "").strip()
    normalized.setdefault("version", "retrieval_strategy_v1")
    normalized.setdefault("boundary_policy", {})
    normalized.setdefault("paper_role_policy", {})
    return normalized


def build_purposeful_query_plan(
    query: str,
    question_card: dict[str, Any] | None = None,
    focus_branches: list[str] | None = None,
    max_branches: int = 5,
) -> list[dict[str, str]]:
    """Create bounded query families with an explicit scientific purpose."""
    base_query = _normalize_space(query)
    if not base_query:
        return []
    plan: list[dict[str, str]] = []
    for branch, purpose, suffix in QUERY_FAMILIES[: max(1, max_branches)]:
        plan.append(
            {
                "branch": branch,
                "query": f"({base_query}) AND ({suffix})",
                "purpose": purpose,
                "query_family": branch,
            }
        )
    for topic in (question_card or {}).get("catalog_subfields", [])[:2]:
        if not isinstance(topic, dict):
            continue
        terms = [str(term) for term in topic.get("terms", []) if str(term).strip()]
        if not terms:
            continue
        plan.append(
            {
                "branch": f"catalog_{topic.get('domain')}_{topic.get('subfield')}",
                "query": f"({base_query}) AND ({' OR '.join(terms[:4])})",
                "purpose": "cover a catalog-matched subfield without broadening the core question to unrelated disciplines",
                "query_family": "catalog_subfield",
            }
        )
    for index, focus in enumerate(focus_branches or []):
        focus_text = _normalize_space(focus)
        if not focus_text:
            continue
        plan.append(
            {
                "branch": f"user_focus_{index + 1}",
                "query": f"({base_query}) AND ({focus_text})",
                "purpose": "user-specified subproblem or missing evidence branch",
                "query_family": "user_focus",
            }
        )
    boundary_policy = (question_card or {}).get("boundary_policy", {})
    bridge_terms = [str(item) for item in boundary_policy.get("bridge_terms", []) if str(item).strip()]
    if bridge_terms:
        plan.append(
            {
                "branch": "bridge_context",
                "query": f"({base_query}) AND ({' OR '.join(bridge_terms[:3])}) AND (mechanism OR pathway)",
                "purpose": "retrieve transferable bridge evidence separately from the core corpus",
                "query_family": "bridge_context",
            }
        )
    return _dedupe_query_plan(plan)


def classify_paper_research_role(
    result: dict[str, Any],
    question_card: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assign a research role without equating topical similarity with causal evidence."""
    card = question_card or {}
    boundary_policy = card.get("boundary_policy", {}) if isinstance(card.get("boundary_policy"), dict) else {}
    text = _paper_text(result)
    domain_gate = result.get("domain_gate", {}) if isinstance(result.get("domain_gate"), dict) else {}
    if str(domain_gate.get("verdict") or "").lower() == "reject":
        return _role("EXCLUDE", 0.0, "The final domain gate rejected this candidate.", "excluded")
    explicit_exclusions = [str(item).lower() for item in boundary_policy.get("explicit_exclusion_terms", []) if str(item).strip()]
    matched_exclusions = [term for term in explicit_exclusions if term in text]
    if matched_exclusions:
        return _role(
            "EXCLUDE",
            0.0,
            f"Candidate matches explicit project exclusion terms: {', '.join(matched_exclusions)}.",
            "excluded",
        )
    core_terms = [str(item).lower() for item in card.get("core_terms", []) if len(str(item).strip()) >= 3]
    core_hits = [term for term in core_terms if term in text]
    causal_hits = [marker for marker in _CAUSAL_MARKERS if marker in text]
    bridge_terms = [str(item).lower() for item in boundary_policy.get("bridge_terms", []) if str(item).strip()]
    bridge_hits = [term for term in bridge_terms if term in text]
    is_review = any(marker in text for marker in _REVIEW_MARKERS)
    if bridge_hits:
        return _role(
            "BRIDGE",
            0.45 + min(0.2, 0.04 * len(core_hits)),
            f"Candidate belongs to a boundary-adjacent context ({', '.join(bridge_hits)}) and is retained only as bridge background.",
            "bridge_background",
            core_hits=core_hits,
            causal_hits=causal_hits,
            boundary_hits=bridge_hits,
        )
    if len(core_hits) >= 2 or (core_hits and causal_hits):
        allowed_use = "landscape_or_vocabulary_only" if is_review else "core_mechanism_evidence_candidate"
        return _role(
            "CORE",
            min(1.0, 0.6 + 0.05 * len(core_hits) + 0.03 * len(causal_hits)),
            "Candidate matches the research boundary and contains core entities or mechanism evidence.",
            allowed_use,
            core_hits=core_hits,
            causal_hits=causal_hits,
        )
    if core_hits or causal_hits:
        return _role(
            "BRIDGE",
            0.35 + min(0.2, 0.04 * (len(core_hits) + len(causal_hits))),
            "Candidate has partial mechanism or topic overlap but lacks sufficient context for core causal use.",
            "bridge_background",
            core_hits=core_hits,
            causal_hits=causal_hits,
        )
    return _role(
        "BRIDGE",
        0.2,
        "Candidate passed the domain gate but lacks enough bounded mechanism context for core use.",
        "background_only",
    )


def prioritize_candidates_for_question_card(
    candidates: list[dict[str, Any]],
    question_card: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Annotate and prioritize CORE evidence while preserving bridge candidates."""
    if not question_card:
        return [dict(candidate) for candidate in candidates if isinstance(candidate, dict)]
    prepared: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        item = dict(candidate)
        assessment = classify_paper_research_role(item, question_card)
        item["research_role"] = assessment["role"]
        item["research_role_assessment"] = assessment
        item["research_role_priority"] = _role_priority(assessment["role"])
        prepared.append(item)
    prepared.sort(
        key=lambda item: (
            -int(item.get("research_role_priority") or 0),
            -float((item.get("research_role_assessment") or {}).get("score") or 0.0),
            -float(item.get("relevance_score") or 0.0),
            str(item.get("title") or ""),
        )
    )
    return prepared


def summarize_retrieval_role_coverage(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {role: 0 for role in ("CORE", "BRIDGE", "EXCLUDE")}
    purpose_counts: dict[str, int] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        role = str(result.get("research_role") or "BRIDGE").upper()
        if role in counts:
            counts[role] += 1
        purpose = str(result.get("query_branch") or result.get("query_family") or "unspecified")
        purpose_counts[purpose] = purpose_counts.get(purpose, 0) + 1
    missing_core_families = [
        branch
        for branch, _, _ in QUERY_FAMILIES
        if purpose_counts.get(branch, 0) == 0
    ]
    return {
        "paper_roles": counts,
        "query_family_result_counts": purpose_counts,
        "missing_query_families": missing_core_families,
        "requires_follow_up": bool(missing_core_families or counts["CORE"] == 0),
    }


def _role(
    role: str,
    score: float,
    reason: str,
    allowed_use: str,
    **signals: list[str],
) -> dict[str, Any]:
    return {
        "role": role,
        "score": round(score, 4),
        "reason": reason,
        "allowed_use": allowed_use,
        **{key: value for key, value in signals.items() if value},
    }


def _role_priority(role: str) -> int:
    return {"CORE": 2, "BRIDGE": 1, "EXCLUDE": 0}.get(str(role).upper(), 1)


def _paper_text(result: dict[str, Any]) -> str:
    payload = result.get("papergraph_input") if isinstance(result.get("papergraph_input"), dict) else {}
    values = []
    for key in ("title", "abstract", "conclusion", "contribution", "limitation", "method", "scenario", "benchmark", "venue"):
        values.append(str(result.get(key) or payload.get(key) or ""))
    return _normalize_space(" ".join(values)).lower()


def _significant_terms(text: str, limit: int) -> list[str]:
    phrases = [match.strip().lower() for match in re.findall(r'"([^\"]{3,80})"', str(text or ""))]
    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(text or ""))
        if word.lower() not in _GENERIC_TERMS
    ]
    return _unique(phrases + words)[:limit]


def _dedupe_query_plan(plan: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in plan:
        query = _normalize_space(str(item.get("query") or ""))
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        copied = dict(item)
        copied["query"] = query
        deduped.append(copied)
    return deduped


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        normalized = _normalize_space(value)
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        unique_values.append(normalized)
    return unique_values


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
