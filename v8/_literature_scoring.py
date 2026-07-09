from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any
import ast
import json
import math
import re
import time
import xml.etree.ElementTree as ET

try:
    from .config import (
        SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K,
    )
    from .log import log_event
except ImportError:
    from config import (
        SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K,
    )
    from log import log_event



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

def zhizhi_import_minimum_plan(limit: int) -> dict[str, int]:
    try:
        from ._models import ZHIZHI_IMPORT_LAYER_PRIORITY, ZHIZHI_IMPORT_MIN_PER_LAYER
        from ._utils import clamp_int
    except ImportError:
        from _models import ZHIZHI_IMPORT_LAYER_PRIORITY, ZHIZHI_IMPORT_MIN_PER_LAYER
        from _utils import clamp_int
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
    try:
        from ._literature_search import is_review_like_paper
        from ._utils import numeric_value
    except ImportError:
        from _literature_search import is_review_like_paper
        from _utils import numeric_value
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
    try:
        from ._literature_search import literature_result_unique_key
    except ImportError:
        from _literature_search import literature_result_unique_key
    result_index = item.get("result_index")
    if result_index is not None:
        return f"result_index:{result_index}"
    return literature_result_unique_key(item)

def literature_result_text_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    try:
        from ._literature_search import query_terms
    except ImportError:
        from _literature_search import query_terms
    text_a = " ".join(query_terms(" ".join(str(a.get(key) or "") for key in ("title", "abstract", "query_branch")))[:24])
    text_b = " ".join(query_terms(" ".join(str(b.get(key) or "") for key in ("title", "abstract", "query_branch")))[:24])
    terms_a = set(query_terms(text_a))
    terms_b = set(query_terms(text_b))
    if not terms_a or not terms_b:
        return 0.0
    return len(terms_a & terms_b) / max(1, len(terms_a | terms_b))

def domain_topic_profile(text: str, query: str = "", use_llm: bool = False) -> dict[str, Any]:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    base_text = normalize_space(" ".join([text, query]))
    if use_llm:
        llm_profile = infer_domain_topic_profile_with_llm(base_text, query=query)
        if llm_profile:
            return llm_profile
    return infer_domain_topic_profile_heuristic(base_text, query=query)

def infer_domain_topic_profile_with_llm(text: str, query: str = "") -> dict[str, Any] | None:
    try:
        from ._llm import call_llm_json
    except ImportError:
        from _llm import call_llm_json
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
    try:
        from ._literature_search import query_terms
        from ._utils import normalize_space
    except ImportError:
        from _literature_search import query_terms
        from _utils import normalize_space
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
    try:
        from ._literature_search import query_terms
        from ._utils import clamp_int, scalar, string_list
    except ImportError:
        from _literature_search import query_terms
        from _utils import clamp_int, scalar, string_list
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
    try:
        from ._utils import clamp_int, normalize_space, scalar, string_list
    except ImportError:
        from _utils import clamp_int, normalize_space, scalar, string_list
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
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    value = normalize_space(text).lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")[:80]

def domain_relevance_assessment(result: dict[str, Any], domain: str = "", query: str = "") -> dict[str, Any]:
    try:
        from ._literature_search import is_preprint_literature_result, query_terms
        from ._models import PREPRINT_API_PROVIDERS
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _literature_search import is_preprint_literature_result, query_terms
        from _models import PREPRINT_API_PROVIDERS
        from _utils import normalize_space, unique_preserve_order
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
    is_preprint = is_preprint_literature_result(result) or provider in PREPRINT_API_PROVIDERS or any(name in provider for name in PREPRINT_API_PROVIDERS)
    core_alignment = core_domain_alignment(result, domain=domain, query=query)
    if core_alignment["enabled"]:
        if not core_alignment["passes"]:
            flags.append("core_domain_mismatch")
            score = round(score * 0.45, 4)
        elif core_alignment["specific_hit_count"] >= 2:
            score = round(min(1.0, score + 0.08), 4)
            flags.append("core_domain_hit")
    if is_preprint and score < 0.16:
        flags.append("weak_preprint_domain_relevance")
    preprint_has_signal = bool(query_hits or anchor_hits or topic_hits)
    verdict = "keep"
    if noise_hits:
        verdict = "reject"
    elif core_alignment["enabled"] and not core_alignment["passes"]:
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
        "core_domain_alignment": core_alignment,
        "verdict": verdict,
        "requires_human_review": bool((is_preprint and score < 0.16 and verdict != "reject") or (field_mismatch and verdict != "reject")),
    }

def should_reject_for_domain(result: dict[str, Any], domain: str = "") -> bool:
    try:
        from ._utils import numeric_value
    except ImportError:
        from _utils import numeric_value
    if not domain:
        return False
    assessment = result.get("domain_relevance")
    if not isinstance(assessment, dict):
        assessment = domain_relevance_assessment(result, domain=domain, query="")
        result["domain_relevance"] = assessment
    if assessment.get("verdict") == "reject":
        return True
    # Domain exclusion marker check: reject papers from clearly different disciplines
    exclusion_markers = domain_exclusion_markers(domain)
    if exclusion_markers:
        paper_text = " ".join(
            str(result.get(key) or "")
            for key in ("title", "abstract", "venue")
        ).lower()
        matched_exclusions = [marker for marker in exclusion_markers if marker in paper_text]
        if matched_exclusions:
            log_event(
                "SCIENCE",
                "domain_exclusion_marker_hit",
                domain=domain,
                title=str(result.get("title", ""))[:80],
                markers=matched_exclusions,
            )
            return True
    score = float(assessment.get("score") or 0.0)
    quality = float(result.get("publication_quality_score") or publication_quality_assessment(result)["quality_score"])
    citations = numeric_value(result.get("citation_count"))
    if "field_mismatch" in set(assessment.get("flags") or []) and score < 0.18 and citations <= 5:
        return True
    return score < 0.1 and quality < 0.55 and citations <= 0

def domain_exclusion_markers(domain: str = "") -> set[str]:
    """Return exclusion markers for the given domain.

    These markers identify papers that are clearly from a *different* major
    discipline and should be rejected even if they share some surface-level
    keyword overlap (e.g., 'neural network' in a chemistry project).
    """
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    if not domain:
        return set()
    domain_lower = normalize_space(domain).lower()

    # Build exclusion markers based on domain family
    exclusions: set[str] = set()

    # Physical / materials / chemistry domains should exclude pure clinical / social / finance
    if any(term in domain_lower for term in ("battery", "catalyst", "material", "polymer", "semiconductor", "alloy", "chemistry", "chemical")):
        exclusions.update({
            "clinical trial", "patient cohort", "epidemiological", "public health",
            "stock market", "financial return", "gdp", "macroeconomic",
            "social media", "survey respondents", "questionnaire",
        })

    # Bio / medical domains should exclude pure physics / math / finance
    if any(term in domain_lower for term in ("protein", "cell", "gene", "clinical", "patient", "disease", "organism", "cancer", "biomedical")):
        exclusions.update({
            "dark matter", "gravitational wave", "black hole", "cosmological",
            "stock market", "financial return", "gdp", "macroeconomic",
            "partial differential equation", "algebraic geometry", "number theory",
        })

    # CS / AI domains should exclude pure clinical / materials / ecology
    if any(term in domain_lower for term in ("algorithm", "model", "ai", "simulation", "control", "robot", "grid", "neural", "deep learning")):
        exclusions.update({
            "clinical trial", "patient cohort", "epidemiological",
            "crystal structure", "x-ray diffraction", "catalytic activity",
            "species richness", "community ecology", "biodiversity",
        })

    # Ecology / environmental should exclude pure CS / finance / high-energy physics
    if any(term in domain_lower for term in ("climate", "ecology", "environment", "geology", "agriculture")):
        exclusions.update({
            "stock market", "financial return", "gdp",
            "collider", "quark", "hadron", "lattice gauge",
            "compiler optimization", "operating system",
        })

    # Math / stats should exclude pure experimental sciences
    if any(term in domain_lower for term in ("mathematics", "statistics", "topology", "algebra")):
        exclusions.update({
            "clinical trial", "in vivo", "in vitro",
            "battery performance", "catalytic activity",
            "field experiment", "crop yield",
        })

    return exclusions


def core_domain_alignment(result: dict[str, Any], domain: str = "", query: str = "") -> dict[str, Any]:
    try:
        from ._literature_search import is_preprint_literature_result, is_review_like_paper
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _literature_search import is_preprint_literature_result, is_review_like_paper
        from _utils import normalize_space, unique_preserve_order
    seed_text = normalize_space(f"{domain} {query}")
    core_terms = core_domain_terms(seed_text)
    if len(core_terms) < 3:
        return {
            "enabled": False,
            "passes": True,
            "reason": "not enough specific core terms to enforce strict alignment",
            "core_terms": core_terms,
        }
    text = normalize_space(
        " ".join(
            str(result.get(key) or "")
            for key in ("title", "abstract", "citation", "method", "scenario", "benchmark")
        )
    ).lower()
    hits = [term for term in core_terms if term in text]
    specific_terms = [term for term in core_terms if core_domain_term_is_specific(term)]
    specific_hits = [term for term in specific_terms if term in text]
    preprint = is_preprint_literature_result(result)
    min_hits = 3 if len(core_terms) >= 8 else 2
    min_specific = 2 if len(specific_terms) >= 4 else 1
    if preprint:
        min_hits += 1
    passes = len(hits) >= min_hits and (not specific_terms or len(specific_hits) >= min_specific)
    if is_review_like_paper(result) and len(hits) >= max(2, min_hits - 1):
        passes = True
    return {
        "enabled": True,
        "passes": passes,
        "core_terms": core_terms[:18],
        "core_hit_count": len(hits),
        "core_hits": unique_preserve_order(hits)[:12],
        "specific_terms": specific_terms[:12],
        "specific_hit_count": len(specific_hits),
        "specific_hits": unique_preserve_order(specific_hits)[:10],
        "min_core_hits": min_hits,
        "min_specific_hits": min_specific if specific_terms else 0,
        "is_preprint": preprint,
        "reason": (
            "core topic terms sufficiently covered"
            if passes
            else "title/abstract do not cover enough user-specified core topic terms"
        ),
    }

def core_domain_terms(seed_text: str) -> list[str]:
    try:
        from ._literature_search import query_terms
        from ._utils import unique_preserve_order
    except ImportError:
        from _literature_search import query_terms
        from _utils import unique_preserve_order
    generic = {
        "review",
        "survey",
        "roadmap",
        "progress",
        "perspective",
        "systematic",
        "seminal",
        "foundational",
        "highly",
        "cited",
        "landmark",
        "classic",
        "influential",
        "latest",
        "recent",
        "frontier",
        "breakthrough",
        "method",
        "model",
        "algorithm",
        "mechanism",
        "experiment",
        "framework",
        "application",
        "system",
        "scenario",
        "case",
        "study",
        "deployment",
        "benchmark",
        "dataset",
        "validation",
        "evaluation",
        "metric",
        "measurement",
        "preprint",
        "arxiv",
        "paper",
        "science",
        "research",
        "technology",
    }
    raw_terms = query_terms(seed_text)
    terms = [term for term in raw_terms if term not in generic and len(term) >= 3]
    phrase_terms = extract_core_domain_phrases(seed_text)
    return unique_preserve_order(phrase_terms + terms)[:24]

def extract_core_domain_phrases(seed_text: str) -> list[str]:
    try:
        from ._literature_search import query_terms
    except ImportError:
        from _literature_search import query_terms
    tokens = [term for term in query_terms(seed_text) if len(term) >= 3]
    phrases: list[str] = []
    for size in (3, 2):
        for index in range(0, max(0, len(tokens) - size + 1)):
            window = tokens[index : index + size]
            if any(core_domain_term_is_specific(term) for term in window):
                phrases.append(" ".join(window))
    return phrases[:10]

def core_domain_term_is_specific(term: str) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    value = normalize_space(term).lower()
    if any(char.isdigit() for char in value):
        return True
    if len(value) >= 7:
        return True
    if any(marker in value for marker in ("-", "_", "+", "/")):
        return True
    return False

def literature_domain_coverage_diagnostic(
    search_id: str,
    domain: str = "",
    query: str = "",
    live_validate: bool = False,
    use_llm: bool = False,
    max_live_probes: int = 4,
) -> dict[str, Any]:
    try:
        from ._literature_search import live_probe_literature_branch
        from ._project import load_search
        from ._utils import clamp_int, normalize_space
    except ImportError:
        from _literature_search import live_probe_literature_branch
        from _project import load_search
        from _utils import clamp_int, normalize_space
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

def literature_relevance_score(query: str, result: dict[str, Any]) -> tuple[float, list[str], str, dict[str, Any]]:
    try:
        from ._literature_search import query_terms
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _literature_search import query_terms
        from _utils import normalize_space, unique_preserve_order
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
    try:
        from ._utils import numeric_value
    except ImportError:
        from _utils import numeric_value
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
    try:
        from ._models import PREPRINT_VENUES
        from ._utils import normalize_space, numeric_value
    except ImportError:
        from _models import PREPRINT_VENUES
        from _utils import normalize_space, numeric_value
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
    try:
        from ._models import SUSPICIOUS_VENUES
    except ImportError:
        from _models import SUSPICIOUS_VENUES
    if venue in SUSPICIOUS_VENUES:
        return True
    return any(pattern in venue for pattern in SUSPICIOUS_VENUES)

def has_suspicious_publisher(text: str) -> bool:
    try:
        from ._models import SUSPICIOUS_PUBLISHER_PATTERNS
    except ImportError:
        from _models import SUSPICIOUS_PUBLISHER_PATTERNS
    return any(pattern in text for pattern in SUSPICIOUS_PUBLISHER_PATTERNS)

def is_reputable_venue(venue: str) -> bool:
    try:
        from ._models import REPUTABLE_VENUES, REPUTABLE_VENUE_PATTERNS
    except ImportError:
        from _models import REPUTABLE_VENUES, REPUTABLE_VENUE_PATTERNS
    if venue in REPUTABLE_VENUES:
        return True
    generic_names = {"nature", "science", "cell", "ecology", "oikos"}
    if any(name not in generic_names and name in venue for name in REPUTABLE_VENUES):
        return True
    return any(pattern in venue for pattern in REPUTABLE_VENUE_PATTERNS)

def journal_metric_for_venue(venue: str) -> dict[str, str]:
    try:
        from ._models import JOURNAL_METRICS
        from ._utils import normalize_space
    except ImportError:
        from _models import JOURNAL_METRICS
        from _utils import normalize_space
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
    try:
        from ._models import PREPRINT_VENUES
        from ._utils import normalize_space
    except ImportError:
        from _models import PREPRINT_VENUES
        from _utils import normalize_space
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
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
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
    try:
        from ._literature_search import arxiv_categories
        from ._models import ARXIV_CATEGORY_FIELD_MAP
        from ._utils import normalize_space
    except ImportError:
        from _literature_search import arxiv_categories
        from _models import ARXIV_CATEGORY_FIELD_MAP
        from _utils import normalize_space
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

def strip_markup(text: str) -> str:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_space(text)

