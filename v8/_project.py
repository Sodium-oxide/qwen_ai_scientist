from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
import ast
import json
import math
import re
import time
import xml.etree.ElementTree as ET

try:
    from .config import (
        SCIENCE_DIR,
        SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS,
        SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER,
    )
    from .log import log_event
except ImportError:
    from config import (
        SCIENCE_DIR,
        SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS,
        SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER,
    )
    from log import log_event



def create_research_project(
    title: str,
    domain: str,
    objective: str,
    strategic_need: str = "",
) -> str:
    try:
        from ._models import PHASES
        from ._utils import new_id
    except ImportError:
        from _models import PHASES
        from _utils import new_id
    project = {
        "project_id": new_id("sci"),
        "title": title,
        "domain": domain,
        "objective": objective,
        "strategic_need": strategic_need,
        "phase": PHASES[0],
        "createdAt": time.time(),
        "updatedAt": time.time(),
        "papergraph": [],
        "evidence": [],
        "coverage_matrix": {},
        "knowledge_gaps": [],
        "hypotheses": [],
        "keynotes": [],
        "mechanism_reports": [],
        "pipeline_tasks": [],
    }
    save_project(project)
    log_event("SCIENCE", "project_created", project_id=project["project_id"], domain=domain)
    return json.dumps(project, ensure_ascii=False, indent=2)

def list_literature_providers() -> str:
    try:
        from ._models import LITERATURE_PROVIDERS
    except ImportError:
        from _models import LITERATURE_PROVIDERS
    return json.dumps(LITERATURE_PROVIDERS, ensure_ascii=False, indent=2)

def live_literature_provider_names() -> set[str]:
    try:
        from ._models import LITERATURE_PROVIDERS
    except ImportError:
        from _models import LITERATURE_PROVIDERS
    return {name for name, spec in LITERATURE_PROVIDERS.items() if spec.get("status") == "live"}

def default_literature_providers(domain: str = "", query: str = "") -> list[str]:
    try:
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _utils import normalize_space, unique_preserve_order
    text = normalize_space(f"{domain} {query}").lower()
    biomedical_terms = (
        "cancer",
        "carcinoma",
        "tumor",
        "tumour",
        "clinical",
        "medicine",
        "disease",
        "genomic",
        "genomics",
        "cell",
        "immunology",
        "oncology",
        "hepatocellular",
        "hcc",
    )
    chemistry_terms = (
        "chemistry",
        "catalysis",
        "catalyst",
        "organic",
        "inorganic",
        "organometallic",
        "polymer",
        "materials chemistry",
    )
    arxiv_terms = (
        "physics",
        "astrophysics",
        "mathematics",
        "computer science",
        "machine learning",
        "artificial intelligence",
        "quantum",
        "control",
        "robotics",
        "statistics",
        "electrical engineering",
        "power",
        "grid",
        "transmission",
        "energy",
        "engineering",
        "signal processing",
        "optimization",
        "automation",
    )
    cs_terms = (
        "computer science",
        "machine learning",
        "artificial intelligence",
        "deep learning",
        "neural",
        "algorithm",
        "software",
        "systems",
        "database",
        "programming",
        "nlp",
        "computer vision",
        "reinforcement learning",
    )
    providers = ["semantic_scholar"]
    if any(term in text for term in biomedical_terms):
        providers.extend(["pubmed", "biorxiv", "medrxiv"])
    if any(term in text for term in chemistry_terms):
        providers.append("chemrxiv")
    if any(term in text for term in arxiv_terms):
        providers.append("arxiv")
    return unique_preserve_order([provider for provider in providers if provider in live_literature_provider_names()])

def explore_domain_subspaces(
    domain: str,
    max_subspaces: int = 12,
    probe_depth: int = 5,
    use_llm: bool = True,
    providers: list[str] | None = None,
    user_hints: list[str] | None = None,
) -> str:
    try:
        from ._literature_search import database_to_provider
        from ._pipeline import run_zhizhi_literature_analysis
        from ._utils import clamp_int, new_id, normalize_space, unique_preserve_order
    except ImportError:
        from _literature_search import database_to_provider
        from _pipeline import run_zhizhi_literature_analysis
        from _utils import clamp_int, new_id, normalize_space, unique_preserve_order
    domain_text = normalize_space(domain)
    if not domain_text:
        raise ValueError("domain is required")
    selected_providers = [database_to_provider(item) for item in (providers or default_literature_providers(domain=domain_text))]
    selected_providers = unique_preserve_order([item for item in selected_providers if item in live_literature_provider_names()])
    if not selected_providers:
        selected_providers = default_literature_providers(domain=domain_text) or ["semantic_scholar"]
    subspaces = generate_domain_subspaces(domain_text, max_subspaces=max_subspaces, use_llm=use_llm, user_hints=user_hints)
    probe_reports: list[dict[str, Any]] = []
    enriched: list[dict[str, Any]] = []
    probe_budget = build_subspace_probe_budget(selected_providers)
    for subspace in subspaces[: clamp_int(max_subspaces, 1, 30)]:
        report = probe_domain_subspace(
            subspace,
            providers=selected_providers,
            probe_depth=probe_depth,
            provider_budget=probe_budget,
        )
        probe_reports.append(report)
        enriched.append(enrich_subspace_with_probe(subspace, report))
    generated_sources = {str(item.get("generated_by") or "") for item in enriched}
    generated_by = "llm" if generated_sources == {"llm"} else "hybrid" if "llm" in generated_sources else "heuristic"
    subspace_map = {
        "subspace_map_id": new_id("subspace"),
        "domain": domain_text,
        "generated_by": generated_by,
        "confidence": domain_subspace_map_confidence(enriched, use_llm=generated_by in {"llm", "hybrid"}),
        "createdAt": time.time(),
        "providers": selected_providers,
        "user_hints": user_hints or [],
        "subspaces": enriched,
        "probe_results": probe_reports,
    }
    subspace_map["coverage_plan"] = build_subspace_coverage_plan(subspace_map)
    subspace_map["query_plan"] = query_plan_from_subspace_map(subspace_map)
    subspace_map["user_interaction"] = build_subspace_selection_interaction(subspace_map)
    save_subspace_map(subspace_map)
    log_event(
        "SCIENCE",
        "domain_subspaces_explored",
        subspace_map_id=subspace_map["subspace_map_id"],
        domain=domain_text,
        subspaces=len(enriched),
    )
    response = dict(subspace_map)
    response["next_step"] = (
        "Ask the user to choose subspaces from user_interaction.options, then pass "
        "subspace_map_id and selected_subfields/focus_branches into run_zhizhi_literature_analysis."
    )
    return json.dumps(response, ensure_ascii=False, indent=2)

def generate_domain_subspaces(
    domain: str,
    max_subspaces: int,
    use_llm: bool,
    user_hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    try:
        from ._literature_scoring import domain_topic_profile
        from ._literature_search import query_terms
        from ._utils import clamp_int, string_list
    except ImportError:
        from _literature_scoring import domain_topic_profile
        from _literature_search import query_terms
        from _utils import clamp_int, string_list
    if use_llm:
        llm_subspaces = generate_domain_subspaces_with_llm(domain, max_subspaces=max_subspaces, user_hints=user_hints)
        if llm_subspaces:
            return llm_subspaces
    profile = domain_topic_profile(domain, query=domain, use_llm=use_llm)
    subspaces: list[dict[str, Any]] = []
    for topic in profile.get("core_topics", []):
        keywords = string_list(topic.get("expected_terms")) or query_terms(str(topic.get("query") or ""))[:8]
        subspaces.append(
            normalize_domain_subspace(
                {
                    "name": str(topic.get("branch") or "subspace"),
                    "aliases": [],
                    "description": str(topic.get("rationale") or ""),
                    "keywords": keywords,
                    "seed_papers": [],
                    "maturity": "unknown",
                    "strategic_importance": int(topic.get("min_hits") or 5),
                    "search_strategy": "must_include",
                    "generated_by": "profile",
                },
                domain=domain,
            )
        )
    if not subspaces:
        for hint in user_hints or []:
            subspaces.append(normalize_domain_subspace({"name": hint, "keywords": query_terms(hint)}, domain=domain))
    if not subspaces:
        subspaces.append(
            normalize_domain_subspace(
                {
                    "name": "Field map and major subfields",
                    "keywords": query_terms(domain) + ["review", "survey", "roadmap"],
                    "description": "Fallback subspace for building an initial field map when no validated ontology is available.",
                    "maturity": "unknown",
                    "strategic_importance": 7,
                    "search_strategy": "must_include",
                    "generated_by": "heuristic",
                },
                domain=domain,
            )
        )
    return subspaces[: clamp_int(max_subspaces, 1, 30)]

def generate_domain_subspaces_with_llm(
    domain: str,
    max_subspaces: int,
    user_hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    try:
        from ._llm import call_llm_json
        from ._utils import clamp_int, trim_text
    except ImportError:
        from _llm import call_llm_json
        from _utils import clamp_int, trim_text
    max_items = clamp_int(max_subspaces, 1, 30)
    compact_domain = compact_domain_label(domain)
    try:
        payload = call_llm_json(
            system=(
                "You are a domain-agnostic research cartographer. You map a broad scientific domain "
                "into substantive research subspaces before literature review. Work across all sciences, "
                "engineering, medicine, agriculture, AI, mathematics, social-science-adjacent empirical fields, "
                "and interdisciplinary topics. Return JSON only."
            ),
            prompt=(
                "Decompose the domain into major substantive subspaces. Do not output generic facets such as "
                "'methods', 'applications', or 'benchmarks' unless they are real named subfields in this domain.\n"
                "Return strict JSON with key subspaces. Each subspace must contain:\n"
                "- name: English concise name\n"
                "- aliases: aliases in English/Chinese/acronyms if useful\n"
                "- description: 1-2 sentence scope\n"
                "- parent: optional parent category\n"
                "- keywords: 5-10 retrieval keywords/phrases\n"
                "- seed_papers: 0-3 representative reviews or seed papers if you know them; leave empty if unsure\n"
                "- maturity: emerging | growing | mature | saturated | unknown\n"
                "- strategic_importance: integer 1-10\n"
                "- search_strategy: must_include | nice_to_have | exploratory\n\n"
                f"Domain label: {compact_domain}\n"
                f"Full user domain: {trim_text(domain, 500)}\n"
                f"User hints: {', '.join(user_hints or [])}\n"
                f"Maximum subspaces: {max_items}\n"
                "Keep descriptions concise. Prefer 8-12 high-signal subspaces over verbose prose.\n"
            ),
            max_tokens=max(4200, min(8000, 700 + max_items * 520)),
            fallback_list_key="subspaces",
        )
    except Exception as exc:
        log_event("WARN", "domain_subspace_llm_failed", error=str(exc))
        return []
    raw = payload.get("subspaces") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    subspaces = [normalize_domain_subspace(item, domain=domain) for item in raw if isinstance(item, dict)]
    for item in subspaces:
        item["generated_by"] = "llm"
    return [item for item in subspaces if item.get("name") and item.get("keywords")]

def compact_domain_label(domain: str) -> str:
    try:
        from ._utils import normalize_space, trim_text, unique_preserve_order
    except ImportError:
        from _utils import normalize_space, trim_text, unique_preserve_order
    clean = normalize_space(domain)
    if len(clean) <= 180:
        return clean
    phrases = re.split(r"\s*(?:/|,|;| and | with | for | of )\s*", clean, flags=re.IGNORECASE)
    useful = [phrase.strip() for phrase in phrases if len(phrase.strip()) >= 4]
    compact = "; ".join(unique_preserve_order(useful)[:6])
    return trim_text(compact or clean, 180)

def normalize_domain_subspace(raw: dict[str, Any], domain: str) -> dict[str, Any]:
    try:
        from ._literature_scoring import slug_label
        from ._literature_search import query_terms
        from ._utils import clamp_int, new_id, normalize_key, normalize_space, scalar, string_list, unique_preserve_order
    except ImportError:
        from _literature_scoring import slug_label
        from _literature_search import query_terms
        from _utils import clamp_int, new_id, normalize_key, normalize_space, scalar, string_list, unique_preserve_order
    name = scalar(raw.get("name")) or scalar(raw.get("name_en")) or "Unnamed subspace"
    keywords = string_list(raw.get("keywords")) or query_terms(" ".join([name, domain]))[:8]
    aliases = string_list(raw.get("aliases"))
    seed_papers = string_list(raw.get("seed_papers")) or string_list(raw.get("representative_reviews"))
    maturity = normalize_space(str(raw.get("maturity") or raw.get("estimated_density") or "unknown")).lower()
    if maturity not in {"emerging", "growing", "mature", "saturated", "unknown"}:
        maturity = "unknown"
    importance = clamp_int(raw.get("strategic_importance", raw.get("hotness", 5)), 1, 10)
    strategy = normalize_key(str(raw.get("search_strategy") or "must_include"))
    if strategy not in {"must_include", "nice_to_have", "exploratory"}:
        strategy = "must_include" if importance >= 7 else "nice_to_have"
    return {
        "subspace_id": slug_label(name) or new_id("subspace_item"),
        "name": name,
        "aliases": aliases[:8],
        "description": scalar(raw.get("description")),
        "parent": scalar(raw.get("parent")),
        "keywords": unique_preserve_order(keywords)[:12],
        "seed_papers": seed_papers[:5],
        "maturity": maturity,
        "estimated_density": "unknown",
        "strategic_importance": importance,
        "search_strategy": strategy,
        "generated_by": str(raw.get("generated_by") or "heuristic"),
    }

def probe_domain_subspace(
    subspace: dict[str, Any],
    providers: list[str],
    probe_depth: int = 5,
    provider_budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    try:
        from ._literature_scoring import is_recent_paper
        from ._literature_search import arxiv_skip_block, dedupe_literature_results, flatten_literature_results, milestone_citation_threshold, rank_literature_results, search_arxiv, search_preprint_api, search_pubmed, search_semantic_scholar, summarize_literature_result, summarize_provider_blocks
        from ._utils import clamp_int, normalize_space, numeric_value, string_list, unique_preserve_order
    except ImportError:
        from _literature_scoring import is_recent_paper
        from _literature_search import arxiv_skip_block, dedupe_literature_results, flatten_literature_results, milestone_citation_threshold, rank_literature_results, search_arxiv, search_preprint_api, search_pubmed, search_semantic_scholar, summarize_literature_result, summarize_provider_blocks
        from _utils import clamp_int, normalize_space, numeric_value, string_list, unique_preserve_order
    keywords = string_list(subspace.get("keywords"))
    name = str(subspace.get("name") or "")
    query = normalize_space(" ".join(keywords[:6]) or name)
    probe_queries = unique_preserve_order(
        [
            normalize_space(f"{name} {' '.join(keywords[:4])}"),
            query,
            normalize_space(f"{name} {' '.join(keywords[:3])} review survey"),
        ]
    )
    probe_queries = probe_queries[: clamp_int(SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS, 1, 3)]
    blocks: list[dict[str, Any]] = []
    per_query_depth = max(1, min(clamp_int(probe_depth, 1, 20), 3))
    for probe_query in probe_queries:
        if not probe_query:
            continue
        for provider in providers:
            try:
                if provider_budget is not None and provider_budget.get(provider, 0) <= 0:
                    blocks.append(
                        {
                            "provider": provider,
                            "query": probe_query,
                            "status": "probe_budget_exhausted",
                            "results": [],
                        }
                    )
                    continue
                if provider == "semantic_scholar":
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_semantic_scholar(probe_query, max_results=per_query_depth)
                elif provider == "arxiv":
                    skipped = arxiv_skip_block(probe_query)
                    if skipped:
                        blocks.append(skipped)
                        continue
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_arxiv(probe_query, max_results=per_query_depth)
                elif provider == "pubmed":
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_pubmed(probe_query, max_results=per_query_depth)
                elif provider in {"biorxiv", "medrxiv", "chemrxiv"}:
                    if provider_budget is not None:
                        provider_budget[provider] = provider_budget.get(provider, 0) - 1
                    block = search_preprint_api(provider, probe_query, max_results=per_query_depth)
                else:
                    continue
                block["probe_query_variant"] = probe_query
                blocks.append(block)
            except Exception as exc:
                blocks.append({"provider": provider, "query": probe_query, "status": "error", "error": str(exc), "results": []})
    ranked = rank_literature_results(query, dedupe_literature_results(flatten_literature_results(blocks)))
    recent_count = sum(1 for item in ranked if is_recent_paper(item, max_age=3))
    high_impact_count = sum(1 for item in ranked if numeric_value(item.get("citation_count")) >= milestone_citation_threshold(item))
    return {
        "subspace_id": subspace.get("subspace_id"),
        "name": subspace.get("name"),
        "query": query,
        "probe_queries": probe_queries,
        "provider_blocks": summarize_provider_blocks(blocks),
        "hit_count": len(ranked),
        "recent_count": recent_count,
        "high_impact_count": high_impact_count,
        "top_seed_papers": [summarize_literature_result(item) for item in ranked[: clamp_int(probe_depth, 1, 10)]],
    }

def enrich_subspace_with_probe(subspace: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(subspace)
    hit_count = int(probe.get("hit_count") or 0)
    recent_count = int(probe.get("recent_count") or 0)
    high_impact_count = int(probe.get("high_impact_count") or 0)
    enriched["probe_query"] = probe.get("query", "")
    enriched["probe_hit_count"] = hit_count
    enriched["recent_hit_count"] = recent_count
    enriched["high_impact_hit_count"] = high_impact_count
    enriched["estimated_density"] = estimate_subspace_density(hit_count, recent_count, high_impact_count)
    if not enriched.get("seed_papers"):
        enriched["seed_papers"] = [
            str(item.get("title") or item.get("citation") or "")
            for item in probe.get("top_seed_papers", [])[:3]
            if str(item.get("title") or item.get("citation") or "")
        ]
    enriched["suggested_quota"] = suggested_subspace_quota(enriched)
    enriched["coverage_status"] = "uncovered" if hit_count <= 0 else "probe_covered"
    return enriched

def build_subspace_probe_budget(providers: list[str]) -> dict[str, int]:
    max_calls = max(0, int(SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER))
    return {provider: max_calls for provider in providers}

def estimate_subspace_density(hit_count: int, recent_count: int, high_impact_count: int) -> str:
    if hit_count >= 5 and (recent_count >= 2 or high_impact_count >= 1):
        return "high"
    if hit_count >= 3:
        return "medium"
    if hit_count >= 1:
        return "low"
    return "unknown"

def suggested_subspace_quota(subspace: dict[str, Any]) -> int:
    importance = int(subspace.get("strategic_importance") or 5)
    density = str(subspace.get("estimated_density") or "unknown")
    strategy = str(subspace.get("search_strategy") or "")
    if strategy == "must_include" or importance >= 8:
        return 3 if density in {"high", "medium"} else 2
    if strategy == "exploratory" or density == "low":
        return 1
    return 2

def domain_subspace_map_confidence(subspaces: list[dict[str, Any]], use_llm: bool) -> float:
    if not subspaces:
        return 0.0
    with_keywords = sum(1 for item in subspaces if item.get("keywords"))
    with_probe = sum(1 for item in subspaces if int(item.get("probe_hit_count") or 0) > 0)
    base = 0.35 + (0.2 if use_llm else 0.0)
    score = base + 0.25 * (with_keywords / len(subspaces)) + 0.2 * (with_probe / len(subspaces))
    return round(max(0.0, min(1.0, score)), 3)

def build_subspace_coverage_plan(subspace_map: dict[str, Any]) -> dict[str, Any]:
    subspaces = [item for item in subspace_map.get("subspaces", []) if isinstance(item, dict)]
    total = len(subspaces)
    covered = [item for item in subspaces if int(item.get("probe_hit_count") or 0) > 0]
    missing = [item for item in subspaces if int(item.get("probe_hit_count") or 0) <= 0]
    insufficient = [
        item
        for item in subspaces
        if int(item.get("probe_hit_count") or 0) > 0 and int(item.get("probe_hit_count") or 0) < int(item.get("suggested_quota") or 1)
    ]
    return {
        "total_subspaces": total,
        "covered": len(covered),
        "missing": len(missing),
        "insufficient": len(insufficient),
        "coverage_rate": round(len(covered) / max(1, total), 3),
        "missing_details": [
            {
                "name": item.get("name"),
                "keywords": item.get("keywords", [])[:6],
                "suggested_action": "supplemental_search" if int(item.get("strategic_importance") or 0) >= 6 else "lower_priority_or_confirm",
            }
            for item in missing
        ],
        "recommendation": "Confirm priority subspaces with the user before running ZhiZhi, then search selected subspaces independently.",
    }

def query_plan_from_subspace_map(subspace_map: dict[str, Any], selected_subfields: list[str] | None = None) -> list[dict[str, Any]]:
    try:
        from ._literature_scoring import slug_label
        from ._utils import normalize_key, normalize_space, string_list
    except ImportError:
        from _literature_scoring import slug_label
        from _utils import normalize_key, normalize_space, string_list
    selected = {normalize_key(item) for item in (selected_subfields or []) if normalize_space(item)}
    plan: list[dict[str, Any]] = []
    matched_selected: set[str] = set()
    for subspace in subspace_map.get("subspaces", []):
        if not isinstance(subspace, dict):
            continue
        name = str(subspace.get("name") or "")
        subspace_id = str(subspace.get("subspace_id") or "")
        if selected and normalize_key(name) not in selected and normalize_key(subspace_id) not in selected:
            continue
        if normalize_key(name) in selected:
            matched_selected.add(normalize_key(name))
        if normalize_key(subspace_id) in selected:
            matched_selected.add(normalize_key(subspace_id))
        keywords = string_list(subspace.get("keywords"))
        if not keywords:
            continue
        maturity = str(subspace.get("maturity") or "")
        suffix = "review survey" if maturity in {"mature", "saturated"} else "latest recent" if maturity in {"emerging", "growing"} else ""
        plan.append(
            {
                "branch": subspace_id or slug_label(name),
                "name": name,
                "query": normalize_space(" ".join(keywords[:8] + ([suffix] if suffix else []))),
                "quota": int(subspace.get("suggested_quota") or 1),
                "estimated_density": subspace.get("estimated_density"),
                "strategic_importance": subspace.get("strategic_importance"),
                "search_strategy": subspace.get("search_strategy"),
            }
        )
    for raw in selected:
        if raw in matched_selected:
            continue
        label = normalize_space(raw.replace("_", " "))
        if not label:
            continue
        plan.append(
            {
                "branch": slug_label(label),
                "name": label,
                "query": label,
                "quota": 2,
                "estimated_density": "unknown",
                "strategic_importance": 7,
                "search_strategy": "custom_user_subspace",
                "custom": True,
            }
        )
    return plan

def build_subspace_selection_interaction(subspace_map: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._pipeline import run_zhizhi_literature_analysis
    except ImportError:
        from _pipeline import run_zhizhi_literature_analysis
    options: list[dict[str, Any]] = []
    for item in subspace_map.get("subspaces", [])[:12]:
        if not isinstance(item, dict):
            continue
        options.append(
            {
                "label": str(item.get("name") or item.get("subspace_id")),
                "subspace_id": str(item.get("subspace_id") or ""),
                "description": str(item.get("description") or ""),
                "keywords": item.get("keywords", [])[:8],
                "probe_hit_count": int(item.get("probe_hit_count") or 0),
                "estimated_density": item.get("estimated_density", "unknown"),
                "strategic_importance": item.get("strategic_importance", 5),
                "recommended": item.get("search_strategy") == "must_include" or int(item.get("strategic_importance") or 0) >= 7,
            }
        )
    return {
        "needed": True,
        "type": "pre_retrieval_subspace_selection",
        "question": "Select the subspaces to prioritize before ZhiZhi imports papers. You can also add custom subspaces.",
        "options": options,
        "custom_subspace_input": {
            "enabled": True,
            "placeholder": "e.g. Demand Response; EV Charging Coordination; Building Energy Management",
            "instructions": "If your target subfield is not listed, provide one subspace per line or semicolon-separated. These will be converted into custom retrieval branches.",
        },
        "continue_with": "Pass subspace_map_id plus selected_subfields, or pass option labels as focus_branches to run_zhizhi_literature_analysis.",
    }

def post_retrieval_subspace_coverage(
    subspace_map: dict[str, Any],
    selected_subfields: list[str] | None,
    imported_records: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        from ._literature_search import query_terms
        from ._utils import clamp_int
    except ImportError:
        from _literature_search import query_terms
        from _utils import clamp_int
    plan = query_plan_from_subspace_map(subspace_map, selected_subfields=selected_subfields)
    records = []
    for item in imported_records:
        if not isinstance(item, dict):
            continue
        record = item.get("record") or item.get("existing_record") or {}
        if isinstance(record, dict):
            records.append(record)
    coverage: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []
    for branch in plan:
        terms = query_terms(" ".join([str(branch.get("name") or ""), str(branch.get("query") or "")]))[:16]
        target = clamp_int(branch.get("quota", 2), 1, 10)
        matches = [
            summarize_imported_record_for_subspace(record)
            for record in records
            if record_matches_terms(record, terms)
        ]
        status = "sufficient" if len(matches) >= target else "missing" if len(matches) == 0 else "insufficient"
        entry = {
            "subspace": branch.get("name") or branch.get("branch"),
            "branch": branch.get("branch"),
            "target": target,
            "actual": len(matches),
            "status": status,
            "terms": terms,
            "matched_papers": matches[:5],
            "suggested_query": branch.get("query"),
            "custom": bool(branch.get("custom")),
        }
        coverage.append(entry)
        if status != "sufficient":
            insufficient.append(entry)
    return {
        "total_selected_subspaces": len(plan),
        "sufficient": len([item for item in coverage if item["status"] == "sufficient"]),
        "insufficient": len(insufficient),
        "coverage": coverage,
        "needs_second_alignment": bool(insufficient),
        "user_interaction": build_post_retrieval_alignment_interaction(insufficient),
    }

def record_matches_terms(record: dict[str, Any], terms: list[str]) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    if not terms:
        return False
    text = normalize_space(
        " ".join(
            str(record.get(key) or "")
            for key in ("title", "citation", "abstract", "method", "scenario", "benchmark", "contribution", "limitation")
        )
    ).lower()
    hits = [term for term in terms if term in text]
    return len(hits) >= max(1, min(2, len(terms)))

def summarize_imported_record_for_subspace(record: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
    return {
        "paper_id": record.get("paper_id"),
        "title": trim_text(str(record.get("title") or ""), 140),
        "citation": trim_text(str(record.get("citation") or ""), 120),
        "method": record.get("method"),
        "scenario": record.get("scenario"),
    }

def build_post_retrieval_alignment_interaction(insufficient: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from ._pipeline import run_zhizhi_literature_analysis
    except ImportError:
        from _pipeline import run_zhizhi_literature_analysis
    if not insufficient:
        return {"needed": False}
    return {
        "needed": True,
        "type": "post_retrieval_subspace_alignment",
        "question": "Some selected subspaces are missing or under-covered after import. Should ZhiZhi run supplemental searches before TanXi treats gaps as real?",
        "options": [
            {
                "label": str(item.get("subspace")),
                "status": item.get("status"),
                "target": item.get("target"),
                "actual": item.get("actual"),
                "suggested_query": item.get("suggested_query"),
            }
            for item in insufficient[:8]
        ],
        "actions": [
            "supplemental_search_selected_subspaces",
            "adjust_query_terms",
            "continue_without_supplement",
        ],
        "continue_with": "Rerun run_zhizhi_literature_analysis with focus_branches set to the suggested_query values for missing subspaces.",
    }

def list_research_projects() -> str:
    projects = [load_project(path.stem) for path in sorted(projects_dir().glob("sci_*.json"))]
    if not projects:
        return "(no science projects)"
    return "\n".join(
        f"{project['project_id']} [{project.get('phase', '')}] {project.get('domain', '')} - {project.get('title', '')}"
        for project in projects
    )

def get_research_project(project_id: str) -> str:
    return json.dumps(load_project(project_id), ensure_ascii=False, indent=2)

def list_science_agents() -> str:
    try:
        from ._models import SCIENCE_AGENTS
    except ImportError:
        from _models import SCIENCE_AGENTS
    return json.dumps(SCIENCE_AGENTS, ensure_ascii=False, indent=2)

def get_science_agent_prompt(agent: str) -> str:
    try:
        from ._debate import run_socratic_hypothesis_debate
        from ._gap_detection import build_knowledge_map, detect_knowledge_gaps, run_tanxi_gap_exploration
        from ._hypothesis import design_experiment, finalize_idea, generate_idea, run_mingli_hypothesis_evolution
        from ._literature_search import extract_structured_info, search_literature, search_papers, search_papers_stratified
        from ._models import BIANLUN_FULL_PROMPT, BOXUE_FULL_PROMPT, DUZHI_FULL_PROMPT, Hypothesis, MINGLI_FULL_PROMPT, SCIENCE_AGENTS, TANXI_FULL_PROMPT, YANZHEN_FULL_PROMPT, ZHIZHI_FULL_PROMPT
        from ._pipeline import assess_novelty, create_boxue_delegation_tasks, create_science_delegation_tasks, create_science_pipeline_tasks, run_zhizhi_literature_analysis, verify_uniqueness
        from ._utils import normalize_key
        from ._verification import ask_critical_questions, ask_socratic_questions, causal_chain_audit, check_data_consistency, check_internal_consistency, detect_selective_citation, extract_emergent_method, find_counterexamples, moderate_round, regime_shift_test, run_yanzhen_mechanism_verification, stress_test_assumptions, summarize_positions
    except ImportError:
        from _debate import run_socratic_hypothesis_debate
        from _gap_detection import build_knowledge_map, detect_knowledge_gaps, run_tanxi_gap_exploration
        from _hypothesis import design_experiment, finalize_idea, generate_idea, run_mingli_hypothesis_evolution
        from _literature_search import extract_structured_info, search_literature, search_papers, search_papers_stratified
        from _models import BIANLUN_FULL_PROMPT, BOXUE_FULL_PROMPT, DUZHI_FULL_PROMPT, Hypothesis, MINGLI_FULL_PROMPT, SCIENCE_AGENTS, TANXI_FULL_PROMPT, YANZHEN_FULL_PROMPT, ZHIZHI_FULL_PROMPT
        from _pipeline import assess_novelty, create_boxue_delegation_tasks, create_science_delegation_tasks, create_science_pipeline_tasks, run_zhizhi_literature_analysis, verify_uniqueness
        from _utils import normalize_key
        from _verification import ask_critical_questions, ask_socratic_questions, causal_chain_audit, check_data_consistency, check_internal_consistency, detect_selective_citation, extract_emergent_method, find_counterexamples, moderate_round, regime_shift_test, run_yanzhen_mechanism_verification, stress_test_assumptions, summarize_positions
    key = normalize_key(agent)
    spec = SCIENCE_AGENTS.get(key)
    if spec is None:
        raise ValueError(f"Unknown science agent: {agent}")
    if key == "boxue":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": BOXUE_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Assess project state, dependencies, output quality, gap lifecycle, and delegation risk.",
                "action_tools": [
                    "create_autogen_groupchat",
                    "run_autogen_research_flow",
                    "create_boxue_delegation_tasks",
                    "create_science_delegation_tasks",
                    "create_science_pipeline_tasks",
                    "create_task",
                    
                    "check_inbox",
                    "review_plan",
                ],
                "observation": "Track specialist deliverables, gate shared project writes, synthesize conclusions, and decide advance/revise/finalize.",
            },
            "output_schema": {
                "thought": "string",
                "action": {"type": "assign_task | review_output | synthesize | adjust_plan | finalize", "params": {}},
                "progress": {
                    "current_phase": "Gap Discovery | Hypothesis Generation | Socratic Debate | Mechanism Verification | Experimental Design | Implementation | Manuscript Writing | Review & Iteration",
                    "completed_tasks": ["task_id"],
                    "ongoing_tasks": ["task_id"],
                },
                "remaining_steps": "integer",
            },
            "global_constraints": [
                "Boxue coordinates; specialist agents execute domain work.",
                "Every task needs explicit deliverable standards and acceptance criteria.",
                "Use delegation DAGs for broad or long-running workflows instead of one brittle agent run.",
                
                "Do not treat unsupported or unreviewed evidence as a validated knowledge gap.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    if key == "zhizhi":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": ZHIZHI_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Analyze search strategy, source quality, evidence coverage, blind spots, migration opportunities, and pseudo-gap risk.",
                "action_tools": [
                    "search_papers_stratified",
                    "search_papers",
                    "extract_structured_info",
                    "build_knowledge_map",
                    "detect_knowledge_gaps",
                    "assess_novelty",
                    "verify_uniqueness",
                    "run_zhizhi_literature_analysis",
                ],
                "observation": "Update PaperGraph, benchmark-aware knowledge map, novelty checks, and valid innovation flags.",
            },
            "output_schema": zhizhi_output_schema(),
            "global_constraints": [
                "Never invent or substitute papers when retrieval fails.",
                "Every methodological claim must be grounded in a retrieved/imported source or marked as unsupported.",
                "Classify evidence as empirical_result, theoretical_claim, methodological_description, or author_opinion.",
                "Return structured JSON matching the ZhiZhi output schema.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    if key == "tanxi":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": TANXI_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Scan PaperGraph density, unresolved problems, unconnected cross-field pairs, strategic value, and pseudo-gap risk.",
                "action_tools": [
                    "run_tanxi_gap_exploration",
                    "detect_knowledge_gaps",
                    "check_semantic_plausibility",
                    "assess_novelty",
                    "verify_uniqueness",
                ],
                "observation": "Return coverage_analysis, cross_disciplinary_unconnected_pairs, suspended_problems, and ranked_gaps.",
            },
            "output_schema": {
                "thought": "string",
                "action": {},
                "coverage_analysis": {"dense_areas": [], "density_holes": []},
                "cross_disciplinary_unconnected_pairs": [],
                "suspended_problems": [],
                "ranked_gaps": [],
            },
            "global_constraints": [
                "Every gap must be backed by at least one PaperGraph reference.",
                "Rank no more than 10 gaps per scan.",
                "Avoid trivial gaps and already-saturated areas.",
                "Prioritize scientific significance, tractability, strategic value, and downstream impact.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    if key == "mingli":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": MINGLI_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Evaluate whether a hypothesis is gap-traceable, PaperGraph-grounded, novel, feasible, and structurally distinct from prior candidates.",
                "action_tools": [
                    "generate_idea",
                    "design_experiment",
                    "check_semantic_plausibility",
                    "verify_uniqueness",
                    "search_literature",
                    "finalize_idea",
                    "run_mingli_hypothesis_evolution",
                ],
                "observation": "Inspect uniqueness evidence, overlap risk, experiment feasibility, lineage, and final JSON completeness before finalization.",
            },
            "output_schema": mingli_output_schema(),
            "global_constraints": [
                "Every finalized idea must reference a real project gap_id.",
                "At least one uniqueness or literature verification check is mandatory before finalize_idea succeeds.",
                "Every experiment must include setup, metrics, and baselines.",
                "Tournament mutations must introduce structural changes and preserve parent lineage.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    if key == "duzhi":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": DUZHI_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Extract key claims, implicit assumptions, measurement gaps, causal gaps, and possible counterexamples.",
                "action_tools": [
                    "ask_socratic_questions",
                    "ask_critical_questions",
                    "find_counterexamples",
                    "stress_test_assumptions",
                    "check_internal_consistency",
                    "regime_shift_test",
                ],
                "observation": "Return categorized questions, required revisions, severity, and whether the hypothesis must be revised.",
            },
            "output_schema": duzhi_output_schema(),
            "global_constraints": [
                "Ask questions that can change the hypothesis, not generic objections.",
                "Every critique must target a concrete claim, missing measurement, missing causal link, or missing boundary condition.",
                "Use domain-general scientific constraints and avoid field-specific hardcoding.",
                "If evidence is missing, mark it as missing instead of inventing a refutation.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    if key == "bianlun":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": BIANLUN_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Check safety gates, compare MingLi claim, DuZhi objections, YanZhen reports, and PaperGraph evidence.",
                "action_tools": [
                    "run_socratic_hypothesis_debate",
                    "moderate_round",
                    "summarize_positions",
                    "extract_emergent_method",
                    "run_yanzhen_mechanism_verification",
                ],
                "observation": "Return round-by-round verdicts, adopted revisions, unresolved disputes, and final decision.",
            },
            "output_schema": bianlun_output_schema(),
            "global_constraints": [
                "Do not accept unsupported hypothesis revisions.",
                "Enforce role-prompt independence as an auditable safety gate.",
                "If YanZhen reports CAWM_DETECTED, the debate cannot accept the hypothesis without revision.",
                "If two rounds produce no substantive revision, terminate with best current hypothesis plus unresolved issues.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    if key == "yanzhen":
        prompt = {
            "agent": key,
            **spec,
            "full_system_prompt": YANZHEN_FULL_PROMPT,
            "tao_workflow": {
                "thought": "Extract mechanism, causal chain, cited evidence, hidden assumptions, and regime-shift stress cases.",
                "action_tools": [
                    "check_internal_consistency",
                    "check_data_consistency",
                    "regime_shift_test",
                    "detect_selective_citation",
                    "causal_chain_audit",
                    "run_yanzhen_mechanism_verification",
                ],
                "observation": "Return layer verdicts, detailed reasoning, CAWM risk, selective citation risk, and human-review flags.",
            },
            "output_schema": yanzhen_output_schema(),
            "global_constraints": [
                "All three layers must be executed.",
                "Regime shift testing must include at least two shifted conditions.",
                "Do not pass hypotheses with missing evidence, unstated assumptions, or brittle mechanisms.",
                "The audit must be domain-general and avoid field-specific hardcoding.",
            ],
        }
        return json.dumps(prompt, ensure_ascii=False, indent=2)
    prompt = {
        "agent": key,
        **spec,
        "operating_protocol": "Use a TAO loop: Thought -> Action -> Observation. Return structured JSON only.",
        "global_constraints": [
            "Every claim must be backed by evidence or marked as a hypothesis.",
            "Every deliverable needs explicit acceptance criteria.",
            "Knowledge gaps must be scientifically meaningful, not merely untried combinations.",
            "Mechanism claims require internal consistency, data consistency, and regime-shift checks.",
        ],
    }
    return json.dumps(prompt, ensure_ascii=False, indent=2)

def zhizhi_output_schema() -> dict[str, Any]:
    return {
        "thought": "string",
        "action": "object",
        "knowledge_map_summary": {
            "main_methods": ["string"],
            "method_scenario_coverage": {"method": ["scenario"]},
            "method_scenario_benchmark_triples": [
                {"method": "string", "scenario": "string", "benchmark": "string", "references": ["string"]}
            ],
        },
        "knowledge_gaps": [
            {
                "gap_id": "string",
                "gap_type": "combinatorial | improvement | migration | problem",
                "description": "string",
                "supporting_references": ["string"],
                "novelty_score": "integer 1-10",
                "application_value": "high | medium | low",
                "feasibility": "high | medium | low",
                "suggested_research_path": "string",
            }
        ],
    }

def mingli_output_schema() -> dict[str, Any]:
    try:
        from ._models import Hypothesis
    except ImportError:
        from _models import Hypothesis
    return {
        "title": "Research Title",
        "hypothesis": "Core Hypothesis",
        "abstract": "Abstract",
        "related_work": "Comparison with Related Work",
        "experiments": {
            "setup": "Experimental Setup",
            "metrics": "Evaluation Metrics",
            "baselines": "Baseline Methods",
        },
        "risks": "Risk Factors and Limitations",
        "tournament_generation": 1,
        "parent_hypothesis_id": "string | null",
    }

def duzhi_output_schema() -> dict[str, Any]:
    try:
        from ._verification import ask_socratic_questions
    except ImportError:
        from _verification import ask_socratic_questions
    return {
        "thought": "Socratic critique reasoning",
        "action": {"type": "ask_socratic_questions", "params": {}},
        "questions": [
            {
                "question_type": "conceptual_clarification | constraint_check | causal_probe | counterexample_challenge",
                "question": "string",
                "target_claim": "string",
                "why_it_matters": "string",
                "required_revision": "string",
                "severity": "low | medium | high | fatal",
            }
        ],
        "overall_severity": "low | medium | high | fatal",
        "must_revise": True,
    }

def bianlun_output_schema() -> dict[str, Any]:
    try:
        from ._debate import run_socratic_hypothesis_debate
    except ImportError:
        from _debate import run_socratic_hypothesis_debate
    return {
        "thought": "Structured debate moderation reasoning",
        "action": {"type": "run_socratic_hypothesis_debate", "params": {}},
        "debate_report": {
            "rounds": [],
            "safety_gates": {},
            "refined_hypothesis": {},
            "unresolved_issues": [],
            "final_decision": "accept_for_experiment | revise | human_review | reject",
        },
    }

def yanzhen_output_schema() -> dict[str, Any]:
    return {
        "thought": "Mechanism verification reasoning process",
        "action": {},
        "mechanism_fidelity_report": {
            "hypothesis_id": "string",
            "layer_1_internal_consistency": {
                "logical_chain_intact": True,
                "formula_application_correct": True,
                "issues_found": [],
                "verdict": "PASS | FAIL",
            },
            "layer_2_data_consistency": {
                "mechanism_matches_data": True,
                "selective_citation_detected": False,
                "original_text_alignment": "high | medium | low",
                "verdict": "PASS | FAIL",
            },
            "layer_3_regime_shift_test": {
                "shifted_conditions_tested": ["condition1", "condition2"],
                "mechanism_stability": "stable | degrades_gracefully | collapses_unexpectedly",
                "cawm_risk_level": "LOW | MEDIUM | HIGH",
                "verdict": "PASS | FAIL",
            },
            "overall_verdict": "MECHANISM_VERIFIED | CAWM_DETECTED | REQUIRES_HUMAN_REVIEW",
            "detailed_reasoning": "string",
        },
    }

def load_project(project_id: str) -> dict[str, Any]:
    path = project_path(project_id)
    if not path.exists():
        raise ValueError(f"Science project not found: {project_id}")
    return json.loads(path.read_text(encoding="utf-8"))

def save_project(project: dict[str, Any]) -> None:
    projects_dir().mkdir(parents=True, exist_ok=True)
    project["updatedAt"] = time.time()
    project_path(str(project["project_id"])).write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

def load_search(search_id: str) -> dict[str, Any]:
    path = search_path(search_id)
    if not path.exists():
        raise ValueError(f"Literature search not found: {search_id}")
    return json.loads(path.read_text(encoding="utf-8"))

def save_search(search: dict[str, Any]) -> None:
    searches_dir().mkdir(parents=True, exist_ok=True)
    search_path(str(search["search_id"])).write_text(json.dumps(search, ensure_ascii=False, indent=2), encoding="utf-8")

def load_subspace_map(subspace_map_id: str) -> dict[str, Any]:
    path = subspace_map_path(subspace_map_id)
    if not path.exists():
        raise ValueError(f"Domain subspace map not found: {subspace_map_id}")
    return json.loads(path.read_text(encoding="utf-8"))

def save_subspace_map(subspace_map: dict[str, Any]) -> None:
    subspaces_dir().mkdir(parents=True, exist_ok=True)
    subspace_map_path(str(subspace_map["subspace_map_id"])).write_text(
        json.dumps(subspace_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def search_path(search_id: str) -> Path:
    try:
        from ._utils import normalize_key
    except ImportError:
        from _utils import normalize_key
    safe = normalize_key(search_id)
    return searches_dir() / f"{safe}.json"

def searches_dir() -> Path:
    return SCIENCE_DIR / "searches"

def subspace_map_path(subspace_map_id: str) -> Path:
    try:
        from ._utils import normalize_key
    except ImportError:
        from _utils import normalize_key
    safe = normalize_key(subspace_map_id)
    return subspaces_dir() / f"{safe}.json"

def subspaces_dir() -> Path:
    return SCIENCE_DIR / "subspaces"

def project_path(project_id: str) -> Path:
    try:
        from ._utils import normalize_key
    except ImportError:
        from _utils import normalize_key
    safe = normalize_key(project_id)
    return projects_dir() / f"{safe}.json"

def projects_dir() -> Path:
    return SCIENCE_DIR / "projects"

