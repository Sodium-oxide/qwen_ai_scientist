from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import ast
import json
import re
import ssl
import time
import xml.etree.ElementTree as ET

try:
    from .config import (
        SCIENCE_ARXIV_CIRCUIT_SECONDS,
        SCIENCE_ARXIV_MIN_INTERVAL_SECONDS,
        SCIENCE_DIR,
        SCIENCE_INSECURE_SSL,
        SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429,
        SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT,
        SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER,
        SEMANTIC_SCHOLAR_API_KEY,
    )
    from .log import log_event
except ImportError:
    from config import (
        SCIENCE_ARXIV_CIRCUIT_SECONDS,
        SCIENCE_ARXIV_MIN_INTERVAL_SECONDS,
        SCIENCE_DIR,
        SCIENCE_INSECURE_SSL,
        SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS,
        SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429,
        SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT,
        SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER,
        SEMANTIC_SCHOLAR_API_KEY,
    )
    from log import log_event



SEMANTIC_SCHOLAR_LAST_REQUEST_AT = 0.0

SEMANTIC_SCHOLAR_COOLDOWN_UNTIL = 0.0

SEMANTIC_SCHOLAR_429_COUNT = 0

SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED = False

SEMANTIC_SCHOLAR_RESPONSE_CACHE: dict[str, tuple[float, str]] = {}

ARXIV_LAST_REQUEST_AT = 0.0

ARXIV_COOLDOWN_UNTIL = 0.0

ARXIV_429_COUNT = 0

def search_papers(
    query: str,
    databases: list[str] | None = None,
    max_results: int = 50,
    years: str = "",
) -> str:
    try:
        from ._project import default_literature_providers
        from ._utils import unique_preserve_order
    except ImportError:
        from _project import default_literature_providers
        from _utils import unique_preserve_order
    providers = [database_to_provider(item) for item in (databases or default_literature_providers(query=query))]
    providers = unique_preserve_order([item for item in providers if item])
    result = json.loads(search_literature(query, providers=providers, max_results=max_results))
    result["zhizhi_action"] = "search_papers"
    result["databases_requested"] = databases or providers
    result["years"] = years
    return json.dumps(result, ensure_ascii=False, indent=2)

def search_papers_stratified(
    query: str,
    databases: list[str] | None = None,
    max_results: int = 50,
    years: str = "",
    domain: str = "",
    focus_branches: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    try:
        from ._project import default_literature_providers
        from ._utils import unique_preserve_order
    except ImportError:
        from _project import default_literature_providers
        from _utils import unique_preserve_order
    providers = [database_to_provider(item) for item in (databases or default_literature_providers(domain=domain, query=query))]
    providers = unique_preserve_order([item for item in providers if item])
    result = json.loads(
        search_literature_stratified(
            query,
            providers=providers,
            max_results=max_results,
            domain=domain,
            focus_branches=focus_branches,
            use_llm=use_llm,
        )
    )
    result["zhizhi_action"] = "search_papers_stratified"
    result["databases_requested"] = databases or providers
    result["years"] = years
    result["domain"] = domain
    result["focus_branches"] = focus_branches or []
    return json.dumps(result, ensure_ascii=False, indent=2)

def database_to_provider(name: str) -> str:
    try:
        from ._models import STABLE_LITERATURE_PROVIDERS
        from ._utils import normalize_key
    except ImportError:
        from _models import STABLE_LITERATURE_PROVIDERS
        from _utils import normalize_key
    key = normalize_key(name)
    mapping = {
        "semantic_scholar": "semantic_scholar",
        "semanticscholar": "semantic_scholar",
        "s2": "semantic_scholar",
        "arxiv": "arxiv",
        "bio_rxiv": "biorxiv",
        "biorxiv": "biorxiv",
        "bioarchive": "biorxiv",
        "med_rxiv": "medrxiv",
        "medrxiv": "medrxiv",
        "chem_rxiv": "chemrxiv",
        "chemrxiv": "chemrxiv",
        "pub_med": "pubmed",
        "pubmed": "pubmed",
        "ncbi": "pubmed",
        "medline": "pubmed",
    }
    provider = mapping.get(key, "")
    return provider if provider in STABLE_LITERATURE_PROVIDERS else ""

def extract_structured_info(
    paper_content: str,
    fields: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    try:
        from ._literature_import import extract_paper_structure
        from ._pipeline import classify_evidence_claims
    except ImportError:
        from _literature_import import extract_paper_structure
        from _pipeline import classify_evidence_claims
    parsed = extract_paper_structure(paper_content, use_llm=use_llm)
    result = {
        "zhizhi_action": "extract_structured_info",
        "requested_fields": fields
        or ["research method", "application scenario", "test benchmark", "core contribution", "limitation"],
        "structured_info": {
            "research_method": parsed.get("method", ""),
            "application_scenario": parsed.get("scenario", ""),
            "test_benchmark": parsed.get("benchmark", ""),
            "core_contribution": parsed.get("contribution", ""),
            "core_conclusion": parsed.get("conclusion", ""),
            "limitation": parsed.get("limitation", ""),
        },
        "evidence_type_annotations": classify_evidence_claims(paper_content, parsed),
        "extractor": parsed.get("extractor", ""),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)

def search_literature_provider_block(provider: str, query: str, max_results: int) -> dict[str, Any]:
    try:
        from ._literature_import import import_literature_text, import_papergraph_record
        from ._models import LITERATURE_PROVIDERS
    except ImportError:
        from _literature_import import import_literature_text, import_papergraph_record
        from _models import LITERATURE_PROVIDERS
    spec = LITERATURE_PROVIDERS.get(provider)
    if spec is None:
        return {
            "provider": provider,
            "query": query,
            "status": "unknown_provider",
            "results": [],
        }
    if provider == "arxiv":
        return search_arxiv(query, max_results=max_results)
    if provider == "semantic_scholar":
        return search_semantic_scholar(query, max_results=max_results)
    if provider == "pubmed":
        return search_pubmed(query, max_results=max_results)
    if provider in {"biorxiv", "medrxiv", "chemrxiv"}:
        return search_preprint_api(provider, query, max_results=max_results)
    return {
        "provider": provider,
        "query": query,
        "status": spec["status"],
        "note": spec["note"],
        "results": [],
        "next_step": "Use a compliant external connector, or import_literature_text/import_papergraph_record manually only if the user provides the paper text.",
    }

def search_literature(
    query: str,
    providers: list[str] | None = None,
    max_results: int = 50,
) -> str:
    try:
        from ._literature_import import import_literature_search_result
        from ._project import default_literature_providers, live_literature_provider_names, save_search
        from ._utils import new_id, unique_preserve_order
    except ImportError:
        from _literature_import import import_literature_search_result
        from _project import default_literature_providers, live_literature_provider_names, save_search
        from _utils import new_id, unique_preserve_order
    search_id = new_id("search")
    selected = [database_to_provider(provider) for provider in (providers or default_literature_providers(query=query))]
    selected = unique_preserve_order([provider for provider in selected if provider in live_literature_provider_names()])
    if not selected:
        selected = default_literature_providers(query=query) or ["semantic_scholar"]
    provider_blocks: list[dict[str, Any]] = []
    if selected:
        indexed_blocks: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(6, len(selected))) as executor:
            future_map = {
                executor.submit(search_literature_provider_block, provider, query, max_results): (index, provider)
                for index, provider in enumerate(selected)
            }
            for future in as_completed(future_map):
                index, provider = future_map[future]
                try:
                    indexed_blocks[index] = future.result()
                except Exception as exc:
                    indexed_blocks[index] = provider_error_result(provider, query, exc)
                    log_event("SCIENCE", "literature_search_failed", provider=provider, error=str(exc))
        provider_blocks = [indexed_blocks[index] for index in sorted(indexed_blocks)]
    flattened = rank_literature_results(query, flatten_literature_results(provider_blocks))
    for index, item in enumerate(flattened):
        item["result_index"] = index
        item["search_id"] = search_id
    search_record = {
        "search_id": search_id,
        "query": query,
        "providers": selected,
        "createdAt": time.time(),
        "total_results": len(flattened),
        "results": flattened,
        "provider_blocks": provider_blocks,
    }
    save_search(search_record)
    response = {
        "search_id": search_id,
        "query": query,
        "providers": selected,
        "total_results": len(flattened),
        "results": summarize_literature_results(flattened),
        "provider_blocks": summarize_provider_blocks(provider_blocks),
        "full_results_cached": True,
        "next_step": (
            "Use select_literature_result(search_id) to inspect the top-ranked paper, then "
            "use import_literature_search_result(project_id, search_id, result_index) to import a real retrieved paper. "
            "If total_results is 0, stop and report retrieval failure; do not invent or substitute papers."
        ),
    }
    log_event("SCIENCE", "literature_search", query=query, providers=",".join(selected), max_results=max_results)
    return json.dumps(response, ensure_ascii=False, indent=2)

def search_literature_stratified(
    query: str,
    providers: list[str] | None = None,
    max_results: int = 50,
    domain: str = "",
    focus_branches: list[str] | None = None,
    use_llm: bool = False,
) -> str:
    try:
        from ._literature_import import import_literature_search_result
        from ._literature_scoring import domain_relevance_assessment, should_reject_for_domain
        from ._project import default_literature_providers, live_literature_provider_names, save_search
        from ._utils import new_id, unique_preserve_order
    except ImportError:
        from _literature_import import import_literature_search_result
        from _literature_scoring import domain_relevance_assessment, should_reject_for_domain
        from _project import default_literature_providers, live_literature_provider_names, save_search
        from _utils import new_id, unique_preserve_order
    search_id = new_id("search")
    selected = [database_to_provider(provider) for provider in (providers or default_literature_providers(domain=domain, query=query))]
    selected = unique_preserve_order([provider for provider in selected if provider in live_literature_provider_names()])
    if not selected:
        selected = default_literature_providers(domain=domain, query=query) or ["semantic_scholar"]
    query_plan = build_domain_query_plan(query, domain=domain, focus_branches=focus_branches, use_llm=use_llm)
    ranking_query = expanded_ranking_query(query, domain, query_plan)
    quotas = stratified_literature_quotas(max_results)
    provider_blocks: list[dict[str, Any]] = []
    selected_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    carry = 0
    strata_reports: list[dict[str, Any]] = []

    for layer in stratified_literature_layers(quotas):
        target = layer["quota"] + carry
        if target <= 0:
            strata_reports.append({**layer, "target": target, "selected": 0, "carried_to_next": 0})
            continue
        blocks = fetch_stratified_layer_blocks(query, selected, layer, query_plan=query_plan)
        provider_blocks.extend(blocks)
        raw_candidates = rank_literature_results(ranking_query, dedupe_literature_results(flatten_literature_results(blocks)))
        candidates = [item for item in raw_candidates if stratified_candidate_matches(layer["layer"], item)]
        recovery_used = ""
        if not candidates and layer["layer"] in {"L1_milestone", "L2_top_latest"}:
            candidates, recovery_used = recover_stratified_layer_candidates(layer["layer"], raw_candidates)
        picked: list[dict[str, Any]] = []
        rejected_for_domain = 0
        for candidate in candidates:
            candidate["domain_relevance"] = domain_relevance_assessment(candidate, domain=domain, query=query)
            if should_reject_for_domain(candidate, domain=domain):
                rejected_for_domain += 1
                continue
            key = literature_result_unique_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            item = dict(candidate)
            item["stratified_layer"] = layer["layer"]
            item["stratified_label"] = layer["label"]
            if recovery_used:
                item["stratified_recovery"] = recovery_used
            item["_why_selected"] = stratified_selection_reason(layer["layer"], item)
            picked.append(item)
            if len(picked) >= target:
                break
        selected_results.extend(picked)
        carry = max(0, target - len(picked))
        strata_reports.append(
            {
                **layer,
                "target": target,
                "candidate_count": len(candidates),
                "raw_candidate_count": len(raw_candidates),
                "selected": len(picked),
                "domain_rejected": rejected_for_domain,
                "recovery_used": recovery_used,
                "carried_to_next": carry,
            }
        )
        if len(selected_results) >= max_results:
            carry = 0
            break

    if len(selected_results) < max_results:
        regular_needed = max_results - len(selected_results)
        blocks = fetch_regular_backfill_blocks(query, selected, regular_needed + carry, query_plan=query_plan)
        provider_blocks.extend(blocks)
        candidates = rank_literature_results(ranking_query, dedupe_literature_results(flatten_literature_results(blocks)))
        picked = []
        rejected_for_domain = 0
        for candidate in candidates:
            candidate["domain_relevance"] = domain_relevance_assessment(candidate, domain=domain, query=query)
            if should_reject_for_domain(candidate, domain=domain):
                rejected_for_domain += 1
                continue
            key = literature_result_unique_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            item = dict(candidate)
            item["stratified_layer"] = "L4_regular"
            item["stratified_label"] = "regular journal / supplemental evidence"
            item["_why_selected"] = stratified_selection_reason("L4_regular", item)
            picked.append(item)
            if len(picked) >= regular_needed:
                break
        selected_results.extend(picked)
        strata_reports.append(
            {
                "layer": "L4_regular_backfill",
                "label": "regular journal / quota backfill",
                "quota": regular_needed,
                "target": regular_needed,
                "candidate_count": len(candidates),
                "selected": len(picked),
                "domain_rejected": rejected_for_domain,
                "carried_to_next": max(0, regular_needed - len(picked)),
            }
        )

    final_results = diverse_rerank_literature_results(selected_results, max_results=max_results)
    for index, item in enumerate(final_results):
        item["result_index"] = index
        item["search_id"] = search_id
    knowledge_pyramid = build_knowledge_pyramid(query, final_results, strata_reports)
    search_record = {
        "search_id": search_id,
        "query": query,
        "domain": domain,
        "focus_branches": focus_branches or [],
        "providers": selected,
        "createdAt": time.time(),
        "strategy": "stratified_cascade",
        "query_plan": query_plan,
        "strata": strata_reports,
        "knowledge_pyramid": knowledge_pyramid,
        "total_results": len(final_results),
        "results": final_results,
        "provider_blocks": provider_blocks,
    }
    save_search(search_record)
    response = {
        "search_id": search_id,
        "query": query,
        "domain": domain,
        "focus_branches": focus_branches or [],
        "providers": selected,
        "strategy": "stratified_cascade",
        "query_plan": query_plan,
        "strata": strata_reports,
        "knowledge_pyramid": knowledge_pyramid,
        "root_result_index": knowledge_pyramid.get("root_result_index"),
        "root_policy": knowledge_pyramid.get("root_policy"),
        "total_results": len(final_results),
        "results": summarize_literature_results(final_results),
        "provider_blocks": summarize_provider_blocks(provider_blocks),
        "full_results_cached": True,
        "next_step": (
            "Import selected stratified results with import_literature_search_result(project_id, search_id, result_index). "
            "Each result has stratified_layer and _why_selected explaining its role in the literature map."
        ),
    }
    log_event(
        "SCIENCE",
        "literature_search_stratified",
        query=query,
        providers=",".join(selected),
        max_results=max_results,
        results=len(final_results),
    )
    return json.dumps(response, ensure_ascii=False, indent=2)

def diverse_rerank_literature_results(results: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    try:
        from ._literature_scoring import literature_result_text_similarity, literature_selection_base_score
        from ._utils import clamp_int
    except ImportError:
        from _literature_scoring import literature_result_text_similarity, literature_selection_base_score
        from _utils import clamp_int
    limit = clamp_int(max_results, 1, 200)
    remaining = [dict(item) for item in results if isinstance(item, dict)]
    if len(remaining) <= limit:
        return remaining[:limit]
    selected: list[dict[str, Any]] = []
    used_branches: set[str] = set()
    used_layers: set[str] = set()
    while remaining and len(selected) < limit:
        best_index = 0
        best_score = -999.0
        for index, item in enumerate(remaining):
            score = literature_selection_base_score(item)
            branch = str(item.get("query_branch") or item.get("stratified_label") or "")
            layer = str(item.get("stratified_layer") or "")
            if branch and branch in used_branches:
                score -= 0.18
            if layer and layer in used_layers and layer in {"L3_preprint", "L4_regular"}:
                score -= 0.08
            similarity = max((literature_result_text_similarity(item, chosen) for chosen in selected), default=0.0)
            score -= 0.28 * similarity
            if score > best_score:
                best_score = score
                best_index = index
        chosen = remaining.pop(best_index)
        chosen["diversity_rank_score"] = round(best_score, 4)
        selected.append(chosen)
        branch = str(chosen.get("query_branch") or chosen.get("stratified_label") or "")
        layer = str(chosen.get("stratified_layer") or "")
        if branch:
            used_branches.add(branch)
        if layer:
            used_layers.add(layer)
    return selected

def stratified_literature_quotas(max_results: int) -> dict[str, int]:
    try:
        from ._utils import clamp_int
    except ImportError:
        from _utils import clamp_int
    total = clamp_int(max_results, 1, 100)
    base = {
        "L0_review": 3,
        "L1_milestone": 4,
        "L2_top_latest": 4,
        "L3_preprint": 3,
    }
    if total <= 1:
        return {"L0_review": total, "L1_milestone": 0, "L2_top_latest": 0, "L3_preprint": 0, "L4_regular": 0}
    assigned = 0
    quotas: dict[str, int] = {}
    for key in ("L0_review", "L1_milestone", "L2_top_latest", "L3_preprint"):
        value = min(base[key], max(0, total - assigned))
        quotas[key] = value
        assigned += value
    quotas["L4_regular"] = max(0, total - assigned)
    return quotas

def stratified_literature_layers(quotas: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "layer": "L0_review",
            "label": "high-impact review / field map",
            "quota": int(quotas.get("L0_review", 0)),
            "query_suffix": "review survey progress perspective tutorial systematic review meta-analysis",
        },
        {
            "layer": "L1_milestone",
            "label": "milestone / highly cited foundation",
            "quota": int(quotas.get("L1_milestone", 0)),
            "query_suffix": "seminal foundational highly cited landmark classic influential",
        },
        {
            "layer": "L2_top_latest",
            "label": "recent top-venue frontier",
            "quota": int(quotas.get("L2_top_latest", 0)),
            "query_suffix": "latest recent top journal high impact breakthrough advance frontier",
        },
        {
            "layer": "L3_preprint",
            "label": "latest arXiv preprint frontier",
            "quota": int(quotas.get("L3_preprint", 0)),
            "query_suffix": "",
        },
        {
            "layer": "L4_regular",
            "label": "regular journal / supplemental evidence",
            "quota": int(quotas.get("L4_regular", 0)),
            "query_suffix": "",
        },
    ]

def build_domain_query_plan(
    query: str,
    domain: str = "",
    max_branches: int = 8,
    focus_branches: list[str] | None = None,
    use_llm: bool = False,
) -> list[dict[str, str]]:
    try:
        from ._literature_scoring import domain_topic_profile, slug_label
        from ._utils import normalize_space
    except ImportError:
        from _literature_scoring import domain_topic_profile, slug_label
        from _utils import normalize_space
    primary = normalize_space(query)
    plan: list[dict[str, str]] = [{"branch": "primary", "query": primary}]
    profile = domain_topic_profile(domain or query, query=query, use_llm=use_llm)
    focus_branches = [normalize_space(item) for item in (focus_branches or []) if normalize_space(item)]
    for focus in focus_branches:
        plan.append({"branch": slug_label(focus), "query": normalize_space(f"{primary} {focus}")})
    topics = list(profile.get("core_topics", [])) + list(profile.get("retrieval_facets", []))
    for topic in topics[: max(0, max_branches)]:
        branch = str(topic.get("branch") or "subfield")
        terms = str(topic.get("query") or "")
        if not terms:
            continue
        branch_query = normalize_space(terms if primary.lower() in terms.lower() else f"{primary} {terms}")
        plan.append({"branch": branch, "query": branch_query, "topic_type": str(topic.get("topic_type") or "subfield")})
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in plan:
        key = normalize_space(item["query"]).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[: max(1, max_branches + 1)]

def expanded_ranking_query(query: str, domain: str, query_plan: list[dict[str, str]]) -> str:
    try:
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _utils import normalize_space, unique_preserve_order
    topic_terms: list[str] = []
    for item in query_plan:
        topic_terms.extend(query_terms(str(item.get("query") or ""))[:4])
    return normalize_space(" ".join([query, domain, " ".join(unique_preserve_order(topic_terms)[:24])]))

def live_probe_literature_branch(query: str, providers: list[str] | None = None) -> dict[str, Any]:
    try:
        from ._project import default_literature_providers, live_literature_provider_names
        from ._utils import trim_text, unique_preserve_order
    except ImportError:
        from _project import default_literature_providers, live_literature_provider_names
        from _utils import trim_text, unique_preserve_order
    if not query:
        return {"query": query, "status": "skipped", "total_results": 0, "reason": "empty query"}
    selected = [database_to_provider(provider) for provider in (providers or default_literature_providers(query=query))]
    selected = unique_preserve_order([item for item in selected if item in live_literature_provider_names()])
    if not selected:
        selected = default_literature_providers(query=query) or ["semantic_scholar"]
    reports: list[dict[str, Any]] = []
    total = 0
    for provider in selected:
        try:
            if provider == "semantic_scholar":
                block = search_semantic_scholar(query, max_results=3)
            elif provider == "pubmed":
                block = search_pubmed(query, max_results=3)
            elif provider in {"biorxiv", "medrxiv", "chemrxiv"}:
                block = search_preprint_api(provider, query, max_results=3)
            else:
                block = search_arxiv(query, max_results=3)
            count = len(block.get("results") or []) if block.get("status") == "ok" else 0
            total += count
            reports.append(
                {
                    "provider": provider,
                    "status": block.get("status"),
                    "result_count": count,
                    "top_titles": [trim_text(str(item.get("title") or ""), 120) for item in (block.get("results") or [])[:3]],
                    "error": block.get("error", ""),
                }
            )
        except Exception as exc:
            reports.append({"provider": provider, "status": "error", "result_count": 0, "error": str(exc)})
    return {
        "query": query,
        "status": "ok" if total > 0 else "empty_or_error",
        "total_results": total,
        "providers": reports,
    }

def build_branch_user_interaction(coverage_diagnostic: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._pipeline import run_zhizhi_literature_analysis
    except ImportError:
        from _pipeline import run_zhizhi_literature_analysis
    blind_spots = coverage_diagnostic.get("blind_spots", [])
    options: list[dict[str, Any]] = []
    for spot in blind_spots[:6]:
        options.append(
            {
                "label": str(spot.get("topic") or "missing branch"),
                "suggested_query": str(spot.get("suggested_query") or ""),
                "live_evidence_count": int((spot.get("live_probe") or {}).get("total_results") or 0)
                if isinstance(spot.get("live_probe"), dict)
                else 0,
                "false_negative_risk": bool(spot.get("false_negative_risk")),
            }
        )
    if not options:
        return {"needed": False}
    return {
        "needed": True,
        "type": "research_branch_confirmation",
        "question": "Some major sub-branches appear missing from the current retrieval. Which should be prioritized for a supplemental search before treating gaps as real?",
        "options": options,
        "default_action": "Run supplemental stratified search for options with false_negative_risk=true, or ask the user to pick 2-3 priority branches.",
        "continue_with": "Pass selected option labels or custom branch keywords as focus_branches to run_zhizhi_literature_analysis/search_papers_stratified.",
    }

def fetch_stratified_layer_blocks(
    query: str,
    providers: list[str],
    layer: dict[str, Any],
    query_plan: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    try:
        from ._utils import clamp_int
    except ImportError:
        from _utils import clamp_int
    layer_name = str(layer.get("layer", ""))
    suffix = str(layer.get("query_suffix", "")).strip()
    fetch_limit = max(12, int(layer.get("quota", 1)) * 8)
    blocks: list[dict[str, Any]] = []
    plans = query_plan or [{"branch": "primary", "query": query}]
    plans = plans[: clamp_int(SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER, 1, 20)]
    per_query_limit = max(4, min(fetch_limit, max(4, fetch_limit // max(1, len(plans)) + 2)))
    for plan in plans:
        branch = str(plan.get("branch") or "primary")
        planned_query = str(plan.get("query") or query)
        layer_query = stratified_layer_retrieval_query(layer_name, planned_query, suffix)
        if layer_name == "L3_preprint":
            if "arxiv" in providers:
                block = arxiv_skip_block(planned_query) or search_arxiv(planned_query, max_results=per_query_limit, sort_by="submittedDate")
                block["query_branch"] = branch
                block["retrieval_strategy"] = "latest_preprint_query"
                blocks.append(block)
            for provider in ("biorxiv", "medrxiv", "chemrxiv"):
                if provider in providers:
                    block = search_preprint_api(provider, planned_query, max_results=min(per_query_limit, 20))
                    block["query_branch"] = branch
                    block["retrieval_strategy"] = "latest_preprint_query"
                    blocks.append(block)
            continue
        if "semantic_scholar" in providers:
            block = search_semantic_scholar(layer_query, max_results=per_query_limit)
            block["query_branch"] = branch
            block["retrieval_strategy"] = stratified_layer_retrieval_strategy(layer_name)
            blocks.append(block)
        if "pubmed" in providers:
            block = search_pubmed(layer_query, max_results=per_query_limit)
            block["query_branch"] = branch
            block["retrieval_strategy"] = stratified_layer_retrieval_strategy(layer_name)
            blocks.append(block)
        if layer_name == "L0_review" and "arxiv" in providers:
            block = arxiv_skip_block(layer_query) or search_arxiv(layer_query, max_results=min(per_query_limit, 20))
            block["query_branch"] = branch
            block["retrieval_strategy"] = "review_query"
            blocks.append(block)
        if layer_name == "L0_review":
            for provider in ("biorxiv", "medrxiv", "chemrxiv"):
                if provider in providers:
                    block = search_preprint_api(provider, layer_query, max_results=min(per_query_limit, 20))
                    block["query_branch"] = branch
                    block["retrieval_strategy"] = "review_query"
                    blocks.append(block)
        if layer_name == "L4_regular" and "arxiv" in providers:
            block = arxiv_skip_block(planned_query) or search_arxiv(planned_query, max_results=min(per_query_limit, 20))
            block["query_branch"] = branch
            block["retrieval_strategy"] = "regular_backfill_query"
            blocks.append(block)
        if layer_name == "L4_regular":
            for provider in ("biorxiv", "medrxiv", "chemrxiv"):
                if provider in providers:
                    block = search_preprint_api(provider, planned_query, max_results=min(per_query_limit, 20))
                    block["query_branch"] = branch
                    block["retrieval_strategy"] = "regular_backfill_query"
                    blocks.append(block)
    return blocks

def stratified_layer_retrieval_query(layer_name: str, planned_query: str, suffix: str) -> str:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    base = normalize_space(planned_query)
    if layer_name in {"L1_milestone", "L2_top_latest"}:
        return base
    return normalize_space(f"{base} {suffix}".strip())

def stratified_layer_retrieval_strategy(layer_name: str) -> str:
    if layer_name == "L1_milestone":
        return "broad_recall_then_citation_rerank"
    if layer_name == "L2_top_latest":
        return "broad_recall_then_recent_top_venue_rerank"
    if layer_name == "L0_review":
        return "review_query"
    if layer_name == "L4_regular":
        return "regular_backfill_query"
    return "layer_query"

def fetch_regular_backfill_blocks(
    query: str,
    providers: list[str],
    needed: int,
    query_plan: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    layer = {"layer": "L4_regular", "quota": max(needed, 1), "query_suffix": ""}
    return fetch_stratified_layer_blocks(query, providers, layer, query_plan=query_plan)

def build_knowledge_pyramid(
    query: str,
    results: list[dict[str, Any]],
    strata_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    root = choose_pyramid_review_root(results)
    layer_nodes: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        layer = str(item.get("stratified_layer") or "unlayered")
        layer_nodes.setdefault(layer, []).append(summarize_literature_result(item))

    edges: list[dict[str, Any]] = []
    root_index = root.get("result_index") if root else None
    if root_index is not None:
        for item in results:
            child_index = item.get("result_index")
            if child_index == root_index:
                continue
            edges.append(
                {
                    "source": root_index,
                    "target": child_index,
                    "relation": pyramid_relation_for_layer(str(item.get("stratified_layer") or "")),
                    "evidence": "stratified retrieval layer",
                    "confidence": 0.65,
                }
            )

    return {
        "query": query,
        "root_result_index": root_index,
        "root_node": summarize_literature_result(root) if root else None,
        "root_policy": (
            "Prefer a high-impact review as the knowledge-map root. Only a clearly superior "
            "Nature/Science/Cell/PNAS-level paper should override it as the seed."
        ),
        "layers": {
            "L0_review": layer_nodes.get("L0_review", []),
            "L1A_milestone": layer_nodes.get("L1_milestone", []),
            "L1B_top_latest": layer_nodes.get("L2_top_latest", []),
            "L1C_preprint": layer_nodes.get("L3_preprint", []),
            "L2_regular": layer_nodes.get("L4_regular", []),
        },
        "edges": edges,
        "strata": strata_reports,
    }

def choose_pyramid_review_root(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    reviews = [
        item
        for item in results
        if str(item.get("stratified_layer") or "") == "L0_review" or is_review_like_paper(item)
    ]
    candidates = reviews or results
    if not candidates:
        return None
    return max(candidates, key=pyramid_root_score)

def pyramid_root_score(item: dict[str, Any]) -> float:
    try:
        from ._literature_scoring import literature_impact_score
    except ImportError:
        from _literature_scoring import literature_impact_score
    score = float(item.get("relevance_score") or 0.0)
    score += 0.35 if is_review_like_paper(item) else 0.0
    score += 0.2 * float(item.get("publication_quality_score") or 0.0)
    score += 0.15 * literature_impact_score(item)
    if is_top_venue_result(item):
        score += 0.08
    return round(score, 4)

def pyramid_relation_for_layer(layer: str) -> str:
    return {
        "L1_milestone": "field foundation / canonical evidence",
        "L2_top_latest": "frontier extension from field map",
        "L3_preprint": "emerging preprint signal",
        "L4_regular": "supplemental validation detail",
    }.get(layer, "pyramid child")

def stratified_candidate_matches(layer: str, item: dict[str, Any]) -> bool:
    try:
        from ._literature_scoring import is_recent_paper
        from ._utils import numeric_value
    except ImportError:
        from _literature_scoring import is_recent_paper
        from _utils import numeric_value
    if layer == "L0_review":
        return is_review_like_paper(item) and not is_low_quality_literature_result(item)
    if layer == "L1_milestone":
        return numeric_value(item.get("citation_count")) >= milestone_citation_threshold(item) and not is_low_quality_literature_result(item)
    if layer == "L2_top_latest":
        return is_recent_paper(item, max_age=3) and is_top_venue_result(item) and not is_low_quality_literature_result(item)
    if layer == "L3_preprint":
        return is_preprint_literature_result(item) and not has_suspicious_literature_flags(item)
    if layer == "L4_regular":
        return not is_preprint_literature_result(item) and not is_low_quality_literature_result(item)
    return True

def is_preprint_literature_result(item: dict[str, Any]) -> bool:
    try:
        from ._models import PREPRINT_API_PROVIDERS
        from ._utils import normalize_space
    except ImportError:
        from _models import PREPRINT_API_PROVIDERS
        from _utils import normalize_space
    provider = normalize_space(str(item.get("provider") or "")).lower()
    venue = normalize_space(str(item.get("venue") or "")).lower()
    payload = item.get("papergraph_input") if isinstance(item.get("papergraph_input"), dict) else {}
    payload_provider = normalize_space(str(payload.get("provider") or "")).lower()
    payload_venue = normalize_space(str(payload.get("venue") or "")).lower()
    candidates = {provider, venue, payload_provider, payload_venue}
    return bool(candidates & PREPRINT_API_PROVIDERS) or any(
        marker in " ".join(candidates)
        for marker in ("preprint", "arxiv", "biorxiv", "medrxiv", "chemrxiv")
    )

def has_suspicious_literature_flags(item: dict[str, Any]) -> bool:
    flags = set(item.get("quality_flags") or [])
    return "suspicious_venue_or_publisher" in flags or "journal_quartile_suspicious" in flags

def recover_stratified_layer_candidates(layer: str, raw_candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    try:
        from ._literature_scoring import is_recent_paper, literature_recency_score, publication_channel_is_strong
        from ._utils import numeric_value
    except ImportError:
        from _literature_scoring import is_recent_paper, literature_recency_score, publication_channel_is_strong
        from _utils import numeric_value
    usable = [item for item in raw_candidates if not is_low_quality_literature_result(item)]
    if not usable:
        return [], ""
    if layer == "L1_milestone":
        ranked = sorted(
            usable,
            key=lambda item: (
                -numeric_value(item.get("citation_count")),
                -numeric_value(item.get("influential_citation_count")),
                -float(item.get("publication_quality_score") or 0.0),
                -float(item.get("relevance_score") or 0.0),
            ),
        )
        recovered = [
            item
            for item in ranked
            if numeric_value(item.get("citation_count")) > 0
            or numeric_value(item.get("influential_citation_count")) > 0
            or publication_channel_is_strong(item)
        ][:20]
        return recovered or ranked[:10], "relaxed_milestone_highest_available_citation"
    if layer == "L2_top_latest":
        recent = [item for item in usable if is_recent_paper(item, max_age=5)]
        topish = [item for item in usable if is_top_venue_result(item)]
        pool = [item for item in recent if is_top_venue_result(item)] or recent or topish or usable
        ranked = sorted(
            pool,
            key=lambda item: (
                -literature_recency_score(item),
                -float(item.get("publication_quality_score") or 0.0),
                -float(item.get("relevance_score") or 0.0),
                -numeric_value(item.get("citation_count")),
            ),
        )
        return ranked[:20], "relaxed_top_latest_recent_or_high_quality_available"
    return [], ""

def is_review_like_paper(item: dict[str, Any]) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    text = " ".join(
        normalize_space(str(item.get(key) or ""))
        for key in ("title", "abstract", "citation", "venue")
    ).lower()
    markers = (
        "review",
        "survey",
        "systematic review",
        "meta-analysis",
        "meta analysis",
        "progress in",
        "recent advances",
        "perspective",
        "tutorial",
        "state of the art",
        "roadmap",
    )
    return any(marker in text for marker in markers)

def milestone_citation_threshold(item: dict[str, Any]) -> float:
    try:
        from ._literature_scoring import field_citation_baseline, infer_research_field
    except ImportError:
        from _literature_scoring import field_citation_baseline, infer_research_field
    field = infer_research_field(item)
    return max(30.0, field_citation_baseline(field) * 0.15)

def is_top_venue_result(item: dict[str, Any]) -> bool:
    quartile = str(item.get("journal_quartile") or "").upper()
    flags = set(item.get("quality_flags") or [])
    venue_quality = str(item.get("venue_quality") or "")
    return quartile == "Q1" or "reputable_venue" in flags or venue_quality == "reputable"

def is_low_quality_literature_result(item: dict[str, Any]) -> bool:
    flags = set(item.get("quality_flags") or [])
    if "suspicious_venue_or_publisher" in flags or "journal_quartile_suspicious" in flags:
        return True
    return float(item.get("publication_quality_score") or 0.0) < 0.45

def stratified_selection_reason(layer: str, item: dict[str, Any]) -> str:
    try:
        from ._utils import numeric_value, trim_text
    except ImportError:
        from _utils import numeric_value, trim_text
    title = trim_text(str(item.get("title") or ""), 120)
    citations = int(numeric_value(item.get("citation_count")))
    year = str(item.get("year") or "")
    venue = str(item.get("venue") or item.get("provider") or "")
    quality = item.get("publication_quality_score")
    relevance = item.get("relevance_score")
    if layer == "L0_review":
        return f"Selected as field-map review/survey candidate: {title}; venue={venue}; citations={citations}; quality={quality}; relevance={relevance}."
    if layer == "L1_milestone":
        return f"Selected as milestone/high-impact paper: {title}; citations={citations}; year={year}; venue={venue}; quality={quality}."
    if layer == "L2_top_latest":
        return f"Selected as recent top-venue frontier paper: {title}; year={year}; venue={venue}; quality={quality}; relevance={relevance}."
    if layer == "L3_preprint":
        return f"Selected as latest preprint/frontier signal: {title}; year={year}; provider={item.get('provider')}; relevance={relevance}."
    return f"Selected as regular supplemental paper: {title}; year={year}; venue={venue}; quality={quality}; relevance={relevance}."

def flatten_literature_results(provider_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for block in provider_blocks:
        provider = str(block.get("provider", ""))
        if block.get("status") != "ok":
            continue
        for result in block.get("results", []):
            if not isinstance(result, dict):
                continue
            item = dict(result)
            item["provider"] = provider
            if block.get("query_branch"):
                item["query_branch"] = block.get("query_branch")
            if block.get("query"):
                item["retrieval_query"] = block.get("query")
            flattened.append(item)
    return flattened

def rank_literature_results(query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from ._literature_scoring import literature_relevance_score, publication_quality_assessment
    except ImportError:
        from _literature_scoring import literature_relevance_score, publication_quality_assessment
    scored: list[dict[str, Any]] = []
    for original_index, item in enumerate(results):
        ranked = dict(item)
        score, matched, reason, components = literature_relevance_score(query, ranked)
        ranked["relevance_score"] = score
        ranked["relevance_components"] = components
        quality = publication_quality_assessment(ranked)
        ranked["publication_quality_score"] = quality["quality_score"]
        ranked["venue_quality"] = quality["venue_quality"]
        ranked["journal_quartile"] = quality["journal_quartile"]
        ranked["journal_metric_source"] = quality["journal_metric_source"]
        ranked["inferred_field"] = quality["inferred_field"]
        ranked["quality_flags"] = quality["flags"]
        ranked["quality_criteria"] = quality["criteria"]
        ranked["suspicion_type"] = quality["suspicion_type"]
        ranked["quality_reason"] = quality["reason"]
        ranked["matched_query_terms"] = matched
        ranked["relevance_reason"] = reason
        ranked["_original_index"] = original_index
        scored.append(ranked)
    scored.sort(key=lambda item: (-float(item.get("relevance_score", 0.0)), int(item.get("_original_index", 0))))
    for item in scored:
        item.pop("_original_index", None)
    return scored

def select_literature_result(search_id: str, query: str = "", top_k: int = 5, use_llm: bool = False) -> str:
    try:
        from ._literature_import import import_literature_search_result
        from ._project import load_search, save_search
        from ._utils import clamp_int, find_by_id
    except ImportError:
        from _literature_import import import_literature_search_result
        from _project import load_search, save_search
        from _utils import clamp_int, find_by_id
    search_record = load_search(search_id)
    results = search_record.get("results", [])
    if query:
        results = rank_literature_results(query, [result for result in results if isinstance(result, dict)])
        for index, item in enumerate(results):
            item["result_index"] = index
            item["search_id"] = search_id
        search_record["query"] = query
        search_record["results"] = results
        search_record["total_results"] = len(results)
        save_search(search_record)
    ranked = [result for result in results if isinstance(result, dict)]
    if not ranked:
        return json.dumps(
            {
                "search_id": search_id,
                "selected": None,
                "top_results": [],
                "next_step": "No retrieved papers are available. Stop and report retrieval failure.",
            },
            ensure_ascii=False,
            indent=2,
        )
    limit = clamp_int(top_k, 1, 20)
    selected, root_selection_policy = choose_seed_with_review_root_policy(search_record, ranked)
    llm_judgement: dict[str, Any] | None = None
    if use_llm:
        llm_judgement = judge_literature_candidates_with_llm(
            query or str(search_record.get("query", "")),
            ranked[:limit],
        )
        chosen_index = llm_judgement.get("selected_result_index")
        chosen = find_by_id(ranked, "result_index", chosen_index) if chosen_index is not None else None
        if chosen is not None:
            root_candidate = pyramid_root_from_search_record(search_record, ranked)
            if root_candidate is None or chosen_is_allowed_seed_override(chosen, root_candidate):
                selected = chosen
                root_selection_policy = "LLM selected a candidate allowed by the review-root override policy."
            else:
                root_selection_policy = (
                    "LLM selected a non-review candidate, but the review-root policy kept the high-impact "
                    "review as seed because the candidate was not a clearly superior flagship override."
                )
    summary = {
        "search_id": search_id,
        "selected": summarize_literature_result(selected),
        "root_selection_policy": root_selection_policy,
        "knowledge_pyramid": search_record.get("knowledge_pyramid"),
        "top_results": [summarize_literature_result(result) for result in ranked[:limit]],
        "llm_judgement": llm_judgement,
        "next_step": "Import selected.result_index with import_literature_search_result, or choose another top_results item.",
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)

def choose_seed_with_review_root_policy(
    search_record: dict[str, Any],
    ranked: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    root = pyramid_root_from_search_record(search_record, ranked)
    if root is None:
        return ranked[0], "No review root was available; selected the rule-ranked top result."
    challenger = ranked[0]
    if result_identity(challenger) != result_identity(root) and chosen_is_allowed_seed_override(challenger, root):
        return (
            challenger,
            "Selected the rule-ranked top result because it clearly overrides the review root "
            "under the Nature/Science/Cell/PNAS flagship-impact exception.",
        )
    return (
        root,
        "Selected the high-impact review as the seed/root for knowledge-graph expansion.",
    )

def pyramid_root_from_search_record(
    search_record: dict[str, Any],
    ranked: list[dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        from ._utils import find_by_id
    except ImportError:
        from _utils import find_by_id
    pyramid = search_record.get("knowledge_pyramid") if isinstance(search_record, dict) else None
    root_index = pyramid.get("root_result_index") if isinstance(pyramid, dict) else None
    root = find_by_id(ranked, "result_index", root_index) if root_index is not None else None
    if root is not None:
        return root
    return choose_pyramid_review_root(ranked)

def chosen_is_allowed_seed_override(chosen: dict[str, Any], review_root: dict[str, Any]) -> bool:
    try:
        from ._literature_scoring import literature_impact_score
        from ._utils import numeric_value
    except ImportError:
        from _literature_scoring import literature_impact_score
        from _utils import numeric_value
    if result_identity(chosen) == result_identity(review_root):
        return True
    if is_review_like_paper(chosen):
        return pyramid_root_score(chosen) >= pyramid_root_score(review_root)
    if not is_flagship_root_override_candidate(chosen):
        return False
    chosen_impact = literature_impact_score(chosen)
    root_impact = literature_impact_score(review_root)
    chosen_quality = float(chosen.get("publication_quality_score") or 0.0)
    root_quality = float(review_root.get("publication_quality_score") or 0.0)
    chosen_citations = numeric_value(chosen.get("citation_count"))
    root_citations = numeric_value(review_root.get("citation_count"))
    return (
        chosen_quality >= root_quality + 0.08
        and chosen_impact >= max(0.85, root_impact + 0.18)
        and chosen_citations >= max(100.0, root_citations * 1.5)
    )

def is_flagship_root_override_candidate(item: dict[str, Any]) -> bool:
    try:
        from ._models import FLAGSHIP_ROOT_OVERRIDE_VENUES
        from ._utils import normalize_space
    except ImportError:
        from _models import FLAGSHIP_ROOT_OVERRIDE_VENUES
        from _utils import normalize_space
    venue = normalize_space(item.get("venue", "")).lower()
    if venue in FLAGSHIP_ROOT_OVERRIDE_VENUES:
        return True
    return any(venue.startswith(f"{name} ") for name in FLAGSHIP_ROOT_OVERRIDE_VENUES)

def result_identity(item: dict[str, Any]) -> Any:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    return (
        item.get("result_index"),
        normalize_space(item.get("doi", "")).lower(),
        normalize_space(item.get("arxiv_id", "")).lower(),
        normalize_space(item.get("title", "")).lower(),
    )

def summarize_literature_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [summarize_literature_result(result) for result in results if isinstance(result, dict)]

def summarize_provider_blocks(provider_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for block in provider_blocks:
        results = block.get("results", [])
        summaries.append(
            {
                "provider": block.get("provider"),
                "query": block.get("query"),
                "status": block.get("status"),
                "note": block.get("note"),
                "error": block.get("error"),
                "result_count": len(results) if isinstance(results, list) else 0,
            }
        )
    return summaries

def summarize_literature_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_index": result.get("result_index"),
        "stratified_layer": result.get("stratified_layer", ""),
        "stratified_label": result.get("stratified_label", ""),
        "query_branch": result.get("query_branch", ""),
        "retrieval_query": result.get("retrieval_query", ""),
        "_why_selected": result.get("_why_selected", ""),
        "domain_relevance": result.get("domain_relevance", {}),
        "relevance_score": result.get("relevance_score"),
        "relevance_components": result.get("relevance_components", {}),
        "publication_quality_score": result.get("publication_quality_score"),
        "venue_quality": result.get("venue_quality"),
        "journal_quartile": result.get("journal_quartile", ""),
        "journal_metric_source": result.get("journal_metric_source", ""),
        "inferred_field": result.get("inferred_field", ""),
        "quality_flags": result.get("quality_flags", []),
        "quality_criteria": result.get("quality_criteria", []),
        "suspicion_type": result.get("suspicion_type", ""),
        "is_review_like": is_review_like_paper(result),
        "pyramid_root_score": pyramid_root_score(result),
        "matched_query_terms": result.get("matched_query_terms", []),
        "title": result.get("title"),
        "citation": result.get("citation"),
        "provider": result.get("provider"),
        "year": result.get("year"),
        "citation_count": result.get("citation_count"),
        "influential_citation_count": result.get("influential_citation_count"),
        "doi": result.get("doi"),
        "arxiv_id": result.get("arxiv_id"),
        "url": result.get("url"),
        "relevance_reason": result.get("relevance_reason"),
        "quality_reason": result.get("quality_reason"),
    }

def judge_literature_candidates_with_llm(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from ._llm import call_llm_json
        from ._utils import scalar
    except ImportError:
        from _llm import call_llm_json
        from _utils import scalar
    if not candidates:
        return {"status": "empty", "selected_result_index": None, "reason": "No candidates."}
    try:
        raw = call_llm_json(
            system="You are a strict scientific literature selection judge. Select only from the provided result_index values.",
            prompt=(
                "Choose the best paper for the research query. Prefer direct topical fit, peer-reviewed/reputable venue, "
                "non-suspicious publication channel, citation impact, and recentness. Penalize tangential keyword matches.\n"
                "Return JSON only with: selected_result_index, reason, rejected_indices, quality_warnings.\n\n"
                f"Query: {query}\n\nCandidates:\n"
                + json.dumps([summarize_literature_result(item) for item in candidates], ensure_ascii=False, indent=2)
            ),
            max_tokens=1200,
        )
    except Exception as exc:
        return {
            "status": "fallback",
            "selected_result_index": candidates[0].get("result_index"),
            "reason": f"LLM judge failed: {exc}; used rule-ranked top result.",
            "quality_warnings": [],
        }
    allowed = {item.get("result_index") for item in candidates}
    selected = raw.get("selected_result_index")
    if selected not in allowed:
        selected = candidates[0].get("result_index")
        raw["reason"] = f"Invalid LLM selection; used rule-ranked top result. Original reason: {raw.get('reason', '')}"
    return {
        "status": "ok",
        "selected_result_index": selected,
        "reason": scalar(raw.get("reason")),
        "rejected_indices": raw.get("rejected_indices", []),
        "quality_warnings": raw.get("quality_warnings", []),
    }

def query_terms(query: str) -> list[str]:
    try:
        from ._utils import unique_preserve_order
    except ImportError:
        from _utils import unique_preserve_order
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", query)]
    return unique_preserve_order([term for term in terms if term not in stopwords])

def search_arxiv(query: str, max_results: int = 10, sort_by: str = "relevance") -> dict[str, Any]:
    try:
        from ._literature_import import import_literature_text, import_papergraph_record
        from ._utils import clamp_int
    except ImportError:
        from _literature_import import import_literature_text, import_papergraph_record
        from _utils import clamp_int
    skipped = arxiv_skip_block(query)
    if skipped:
        return skipped
    selected_sort = sort_by if sort_by in {"relevance", "lastUpdatedDate", "submittedDate"} else "relevance"
    params = urlencode(
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": clamp_int(max_results, 1, 50),
            "sortBy": selected_sort,
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{params}"
    try:
        raw = arxiv_get_text(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"})
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        papers = [arxiv_entry_to_result(entry, ns) for entry in root.findall("atom:entry", ns)]
        return {
            "provider": "arxiv",
            "query": query,
            "status": "ok",
            "results": papers,
            "next_step": "Pass a result's papergraph_input fields into import_papergraph_record, or paste abstract into import_literature_text.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="arxiv", error=str(exc))
        return provider_error_result("arxiv", query, exc)

def search_semantic_scholar(query: str, max_results: int = 10) -> dict[str, Any]:
    try:
        from ._literature_import import import_literature_text, import_papergraph_record
        from ._utils import clamp_int
    except ImportError:
        from _literature_import import import_literature_text, import_papergraph_record
        from _utils import clamp_int
    fields = ",".join(
        [
            "title",
            "abstract",
            "year",
            "authors",
            "venue",
            "url",
            "externalIds",
            "citationCount",
            "influentialCitationCount",
            "referenceCount",
            "isOpenAccess",
            "openAccessPdf",
        ]
    )
    params = urlencode({"query": query, "limit": clamp_int(max_results, 1, 100), "fields": fields})
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    headers = {"User-Agent": "qwen-zhikan-papergraph/0.1"}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    try:
        payload = semantic_scholar_get_json(url, headers=headers)
        papers = [semantic_scholar_item_to_result(item) for item in (payload.get("data") or []) if isinstance(item, dict)]
        return {
            "provider": "semantic_scholar",
            "query": query,
            "status": "ok",
            "total": payload.get("total"),
            "results": papers,
            "next_step": "Pass a result's papergraph_input fields into import_papergraph_record, or use import_literature_text with use_llm=true.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="semantic_scholar", error=str(exc))
        return provider_error_result("semantic_scholar", query, exc)

def search_pubmed(query: str, max_results: int = 10) -> dict[str, Any]:
    try:
        from ._literature_import import import_literature_search_result, import_papergraph_record
        from ._utils import clamp_int
    except ImportError:
        from _literature_import import import_literature_search_result, import_papergraph_record
        from _utils import clamp_int
    retmax = clamp_int(max_results, 1, 50)
    search_params = urlencode(
        {
            "db": "pubmed",
            "term": query,
            "retmax": retmax,
            "retmode": "json",
            "sort": "relevance",
            "tool": "qwen_zhikan",
        }
    )
    search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{search_params}"
    try:
        search_payload = http_get_json(search_url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        id_list = (
            search_payload.get("esearchresult", {}).get("idlist", [])
            if isinstance(search_payload.get("esearchresult"), dict)
            else []
        )
        ids = [str(item).strip() for item in id_list if str(item).strip()]
        if not ids:
            return {
                "provider": "pubmed",
                "query": query,
                "status": "ok",
                "total": int((search_payload.get("esearchresult") or {}).get("count") or 0)
                if isinstance(search_payload.get("esearchresult"), dict)
                else 0,
                "results": [],
                "next_step": "No PubMed records matched; try broader biomedical terms or Semantic Scholar.",
            }
        fetch_params = urlencode(
            {
                "db": "pubmed",
                "id": ",".join(ids),
                "retmode": "xml",
                "tool": "qwen_zhikan",
            }
        )
        fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{fetch_params}"
        raw = http_get_text(fetch_url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        root = ET.fromstring(raw)
        papers = [pubmed_article_to_result(article) for article in root.findall(".//PubmedArticle")]
        papers = [paper for paper in papers if paper.get("title")]
        return {
            "provider": "pubmed",
            "query": query,
            "status": "ok",
            "total": int((search_payload.get("esearchresult") or {}).get("count") or len(papers))
            if isinstance(search_payload.get("esearchresult"), dict)
            else len(papers),
            "results": papers,
            "next_step": "Pass a result's papergraph_input fields into import_papergraph_record, or use import_literature_search_result.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="pubmed", error=str(exc))
        return provider_error_result("pubmed", query, exc)

def search_preprint_api(provider: str, query: str, max_results: int = 10) -> dict[str, Any]:
    selected = database_to_provider(provider)
    if selected in {"biorxiv", "medrxiv"}:
        return search_biorxiv_or_medrxiv(selected, query, max_results=max_results)
    if selected == "chemrxiv":
        return search_chemrxiv(query, max_results=max_results)
    return {
        "provider": selected,
        "query": query,
        "status": "unknown_provider",
        "results": [],
    }

def search_biorxiv_or_medrxiv(server: str, query: str, max_results: int = 10, days_back: int = 365) -> dict[str, Any]:
    try:
        from ._literature_import import import_literature_search_result
        from ._utils import clamp_int, normalize_space
    except ImportError:
        from _literature_import import import_literature_search_result
        from _utils import clamp_int, normalize_space
    today = date.today()
    start = today - timedelta(days=clamp_int(days_back, 30, 1825))
    params = f"{server}/{start.isoformat()}/{today.isoformat()}/0"
    url = f"https://api.biorxiv.org/details/{params}"
    try:
        payload = http_get_json(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        items = payload.get("collection") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []
        candidates = [biorxiv_item_to_result(item, server) for item in items if isinstance(item, dict)]
        ranked = rank_literature_results(query, candidates)
        query_words = set(query_terms(query))
        filtered = [
            item
            for item in ranked
            if not query_words
            or any(term in normalize_space(f"{item.get('title', '')} {item.get('abstract', '')}").lower() for term in query_words)
        ]
        papers = filtered[: clamp_int(max_results, 1, 50)]
        return {
            "provider": server,
            "query": query,
            "status": "ok",
            "api": f"api.biorxiv.org/details/{server}",
            "date_window": {"from": start.isoformat(), "to": today.isoformat()},
            "results": papers,
            "next_step": "Import a result with import_literature_search_result; these are preprint metadata records filtered locally by query.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider=server, error=str(exc))
        return provider_error_result(server, query, exc)

def search_chemrxiv(query: str, max_results: int = 10) -> dict[str, Any]:
    try:
        from ._literature_import import import_literature_search_result
        from ._utils import clamp_int
    except ImportError:
        from _literature_import import import_literature_search_result
        from _utils import clamp_int
    params = urlencode(
        {
            "query.bibliographic": query,
            "filter": "prefix:10.26434,type:posted-content",
            "rows": clamp_int(max_results, 1, 50),
        }
    )
    url = f"https://api.crossref.org/works?{params}"
    try:
        payload = http_get_json(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"}, timeout=30.0)
        message = payload.get("message") if isinstance(payload, dict) else {}
        items = message.get("items") if isinstance(message, dict) else []
        if not isinstance(items, list):
            items = []
        papers = [crossref_chemrxiv_item_to_result(item) for item in items if isinstance(item, dict)]
        papers = rank_literature_results(query, papers)[: clamp_int(max_results, 1, 50)]
        return {
            "provider": "chemrxiv",
            "query": query,
            "status": "ok",
            "api": "api.crossref.org/works?filter=prefix:10.26434,type:posted-content",
            "results": papers,
            "next_step": "Import a result with import_literature_search_result; ChemRxiv metadata is retrieved via Crossref posted-content records.",
        }
    except Exception as exc:
        log_event("SCIENCE", "literature_search_failed", provider="chemrxiv", error=str(exc))
        return provider_error_result("chemrxiv", query, exc)

def dedupe_literature_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for result in results:
        key = literature_result_unique_key(result)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped

def literature_result_unique_key(result: dict[str, Any]) -> str:
    try:
        from ._literature_import import paper_unique_key
    except ImportError:
        from _literature_import import paper_unique_key
    payload = result.get("papergraph_input") if isinstance(result.get("papergraph_input"), dict) else {}
    return paper_unique_key(
        title=str(result.get("title") or payload.get("title") or ""),
        citation=str(result.get("citation") or payload.get("citation") or ""),
        doi=str(result.get("doi") or payload.get("doi") or ""),
        arxiv_id=str(result.get("arxiv_id") or payload.get("arxiv_id") or ""),
        semantic_scholar_id=str(result.get("semantic_scholar_id") or payload.get("semantic_scholar_id") or ""),
        url=str(result.get("url") or payload.get("url") or ""),
    )

def arxiv_entry_to_result(entry: ET.Element, ns: dict[str, str]) -> dict[str, Any]:
    try:
        from ._literature_import import build_citation, normalize_doi
        from ._utils import normalize_space, xml_text
    except ImportError:
        from _literature_import import build_citation, normalize_doi
        from _utils import normalize_space, xml_text
    title = normalize_space(xml_text(entry, "atom:title", ns))
    abstract = normalize_space(xml_text(entry, "atom:summary", ns))
    published = xml_text(entry, "atom:published", ns)
    year_match = re.search(r"\b(19|20)\d{2}\b", published)
    year = year_match.group(0) if year_match else ""
    authors = [normalize_space(author.findtext("atom:name", default="", namespaces=ns)) for author in entry.findall("atom:author", ns)]
    authors = [author for author in authors if author]
    url = xml_text(entry, "atom:id", ns)
    arxiv_id = url.rstrip("/").split("/")[-1] if url else ""
    doi = normalize_doi(xml_text(entry, "arxiv:doi", ns))
    categories = arxiv_categories(entry, ns)
    pdf_url = ""
    for link in entry.findall("atom:link", ns):
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            pdf_url = link.attrib.get("href", "")
            break
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id=arxiv_id)
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": "arXiv",
        "provider": "arxiv",
        "source_type": "api",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "arxiv_categories": categories,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "arxiv_categories": categories,
        "url": url,
        "pdf_url": pdf_url,
        "abstract": abstract,
        "papergraph_input": input_payload,
    }

def arxiv_categories(entry: ET.Element, ns: dict[str, str]) -> list[str]:
    try:
        from ._utils import normalize_space, unique_preserve_order
    except ImportError:
        from _utils import normalize_space, unique_preserve_order
    categories: list[str] = []
    for category in entry.findall("atom:category", ns) + entry.findall("category"):
        term = normalize_space(category.attrib.get("term", ""))
        if term:
            categories.append(term)
    return unique_preserve_order(categories)

def semantic_scholar_item_to_result(item: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._literature_import import build_citation, normalize_doi
        from ._utils import normalize_space
    except ImportError:
        from _literature_import import build_citation, normalize_doi
        from _utils import normalize_space
    external = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    title = normalize_space(item.get("title", ""))
    abstract = normalize_space(item.get("abstract", ""))
    authors = [normalize_space(author.get("name", "")) for author in (item.get("authors") or []) if isinstance(author, dict)]
    authors = [author for author in authors if author]
    year = str(item.get("year") or "")
    doi = normalize_doi(str(external.get("DOI") or ""))
    arxiv_id = str(external.get("ArXiv") or "")
    semantic_scholar_id = str(item.get("paperId") or external.get("CorpusId") or "")
    url = str(item.get("url") or "")
    pdf = item.get("openAccessPdf") if isinstance(item.get("openAccessPdf"), dict) else {}
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id=arxiv_id)
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": str(item.get("venue") or ""),
        "provider": "semantic_scholar",
        "source_type": "api",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "semantic_scholar_id": semantic_scholar_id,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": item.get("venue"),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "semantic_scholar_id": semantic_scholar_id,
        "url": url,
        "open_access_pdf": pdf.get("url", ""),
        "citation_count": item.get("citationCount"),
        "influential_citation_count": item.get("influentialCitationCount"),
        "reference_count": item.get("referenceCount"),
        "is_open_access": item.get("isOpenAccess"),
        "abstract": abstract,
        "papergraph_input": input_payload,
    }

def pubmed_article_to_result(article: ET.Element) -> dict[str, Any]:
    try:
        from ._literature_import import build_citation, normalize_doi
        from ._literature_scoring import strip_markup
        from ._utils import normalize_space
    except ImportError:
        from _literature_import import build_citation, normalize_doi
        from _literature_scoring import strip_markup
        from _utils import normalize_space
    medline = article.find("MedlineCitation")
    pubmed_data = article.find("PubmedData")
    article_node = medline.find("Article") if medline is not None else None
    title = strip_markup(normalize_space(article_node.findtext("ArticleTitle", default="") if article_node is not None else ""))
    abstract_parts: list[str] = []
    if article_node is not None:
        for abstract_text in article_node.findall(".//Abstract/AbstractText"):
            label = normalize_space(str(abstract_text.attrib.get("Label") or ""))
            text = strip_markup(normalize_space("".join(abstract_text.itertext())))
            if not text:
                continue
            abstract_parts.append(f"{label}: {text}" if label else text)
    abstract = normalize_space("\n".join(abstract_parts))
    authors: list[str] = []
    if article_node is not None:
        for author in article_node.findall(".//AuthorList/Author"):
            collective = normalize_space(author.findtext("CollectiveName", default=""))
            if collective:
                authors.append(collective)
                continue
            given = normalize_space(author.findtext("ForeName", default=""))
            last = normalize_space(author.findtext("LastName", default=""))
            name = normalize_space(f"{given} {last}".strip())
            if name:
                authors.append(name)
    authors = authors[:30]
    journal = article_node.find("Journal") if article_node is not None else None
    venue = normalize_space(journal.findtext("Title", default="") if journal is not None else "")
    pub_date = journal.find(".//PubDate") if journal is not None else None
    year = ""
    if pub_date is not None:
        year = normalize_space(pub_date.findtext("Year", default="")) or first_year(pub_date.findtext("MedlineDate", default=""))
    pmid = normalize_space(medline.findtext("PMID", default="") if medline is not None else "")
    doi = ""
    if pubmed_data is not None:
        for article_id in pubmed_data.findall(".//ArticleIdList/ArticleId"):
            if str(article_id.attrib.get("IdType") or "").lower() == "doi":
                doi = normalize_doi("".join(article_id.itertext()))
                break
    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id="")
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "provider": "pubmed",
        "source_type": "pubmed_eutils",
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "pmid": pmid,
        "url": url,
        "abstract": abstract,
        "papergraph_input": input_payload,
    }

def biorxiv_item_to_result(item: dict[str, Any], server: str) -> dict[str, Any]:
    try:
        from ._literature_import import build_citation, normalize_doi
        from ._utils import normalize_space
    except ImportError:
        from _literature_import import build_citation, normalize_doi
        from _utils import normalize_space
    title = normalize_space(str(item.get("title") or ""))
    abstract = normalize_space(str(item.get("abstract") or ""))
    authors = split_author_string(str(item.get("authors") or ""))
    year = first_year(str(item.get("date") or item.get("published") or item.get("version") or ""))
    doi = normalize_doi(str(item.get("doi") or ""))
    category = normalize_space(str(item.get("category") or ""))
    url = f"https://www.{server}.org/content/{doi}" if doi else str(item.get("url") or "")
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id="")
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": server,
        "provider": server,
        "source_type": "api",
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": server,
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "category": category,
        "papergraph_input": input_payload,
    }

def crossref_chemrxiv_item_to_result(item: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._literature_import import build_citation, normalize_doi
        from ._literature_scoring import strip_markup
        from ._utils import normalize_space
    except ImportError:
        from _literature_import import build_citation, normalize_doi
        from _literature_scoring import strip_markup
        from _utils import normalize_space
    title = normalize_space(" ".join(str(part) for part in (item.get("title") or []) if part))
    abstract = strip_markup(normalize_space(str(item.get("abstract") or "")))
    authors = [
        normalize_space(" ".join(str(author.get(key) or "") for key in ("given", "family")).strip())
        for author in (item.get("author") or [])
        if isinstance(author, dict)
    ]
    authors = [author for author in authors if author]
    year = crossref_year(item)
    doi = normalize_doi(str(item.get("DOI") or ""))
    containers = item.get("container-title") if isinstance(item.get("container-title"), list) else []
    venue = normalize_space(str(containers[0] if containers else "ChemRxiv")) or "ChemRxiv"
    url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id="")
    input_payload = {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": "ChemRxiv",
        "provider": "chemrxiv",
        "source_type": "crossref_api",
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "conclusion": "",
    }
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue or "ChemRxiv",
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "papergraph_input": input_payload,
    }

def split_author_string(text: str) -> list[str]:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    parts = re.split(r"\s*;\s*|\s*,\s+(?=[A-Z][A-Za-z.-]+(?:\s|$))", normalize_space(text))
    return [part.strip() for part in parts if part.strip()][:30]

def first_year(text: str) -> str:
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else ""

def crossref_year(item: dict[str, Any]) -> str:
    for key in ("published-print", "published-online", "published", "created"):
        value = item.get(key)
        if not isinstance(value, dict):
            continue
        date_parts = value.get("date-parts")
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            year = str(date_parts[0][0])
            if re.fullmatch(r"(19|20)\d{2}", year):
                return year
    return ""

def provider_error_result(provider: str, query: str, exc: Exception) -> dict[str, Any]:
    try:
        from ._literature_import import import_literature_text
    except ImportError:
        from _literature_import import import_literature_text
    return {
        "provider": provider,
        "query": query,
        "status": "error",
        "error": str(exc),
        "results": [],
        "next_step": "Network/API failed. Retry later, configure API keys, or use manual import_literature_text.",
    }

def enrich_papergraph_payload(payload: dict[str, Any], result: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    try:
        from ._literature_import import extraction_quality_report, normalize_doi
        from ._utils import unique_preserve_order
    except ImportError:
        from _literature_import import extraction_quality_report, normalize_doi
        from _utils import unique_preserve_order
    """Best-effort metadata enrichment before structured extraction.

    This intentionally avoids hard PDF parsing dependencies. It first asks
    Semantic Scholar for a single-paper detail record, then tries arXiv when an
    arXiv id is available. Callers can still attach a PDF parser later.
    """
    enriched = dict(payload)
    result = result or {}
    sources: list[str] = []
    errors: list[str] = []
    initial_quality = extraction_quality_report(enriched)
    if not initial_quality.get("needs_enrichment"):
        return enriched, sources

    semantic_id = str(
        enriched.get("semantic_scholar_id")
        or result.get("semantic_scholar_id")
        or ""
    ).strip()
    doi = normalize_doi(str(enriched.get("doi") or result.get("doi") or ""))
    s2_identifier = semantic_id or (f"DOI:{doi}" if doi else "")
    if s2_identifier:
        try:
            detail = fetch_semantic_scholar_paper_detail(s2_identifier)
            before_len = len(str(enriched.get("abstract") or ""))
            enriched = merge_semantic_scholar_detail(enriched, detail)
            after_len = len(str(enriched.get("abstract") or ""))
            if after_len > before_len:
                sources.append("semantic_scholar_detail")
        except Exception as exc:
            error = str(exc)
            errors.append(f"semantic_scholar: {error}")
            log_event("SCIENCE", "metadata_enrichment_failed", provider="semantic_scholar", error=error)

    arxiv_id = str(enriched.get("arxiv_id") or result.get("arxiv_id") or "").strip()
    if arxiv_id and extraction_quality_report(enriched).get("needs_enrichment"):
        try:
            arxiv_payload = fetch_arxiv_by_id(arxiv_id)
            before_len = len(str(enriched.get("abstract") or ""))
            enriched = merge_nonempty(enriched, arxiv_payload)
            after_len = len(str(enriched.get("abstract") or ""))
            if after_len > before_len:
                sources.append("arxiv_detail")
        except Exception as exc:
            error = str(exc)
            errors.append(f"arxiv: {error}")
            log_event("SCIENCE", "metadata_enrichment_failed", provider="arxiv", error=error)

    pdf_url = str(result.get("open_access_pdf") or enriched.get("open_access_pdf") or "").strip()
    if pdf_url:
        enriched["open_access_pdf"] = pdf_url
        sources.append("open_access_pdf_available")
        if extraction_quality_report(enriched).get("needs_enrichment"):
            try:
                excerpt = fetch_pdf_text_excerpt(pdf_url)
                if excerpt:
                    enriched["full_text_excerpt"] = excerpt
                    sources.append("open_access_pdf_text")
            except Exception as exc:
                error = str(exc)
                errors.append(f"open_access_pdf: {error}")
                log_event("SCIENCE", "metadata_enrichment_failed", provider="open_access_pdf", error=error)
    if errors:
        enriched["_enrichment_errors"] = errors
    return enriched, unique_preserve_order(sources)

def fetch_semantic_scholar_paper_detail(identifier: str) -> dict[str, Any]:
    fields = ",".join(
        [
            "title",
            "abstract",
            "year",
            "authors",
            "venue",
            "url",
            "externalIds",
            "citationCount",
            "influentialCitationCount",
            "referenceCount",
            "isOpenAccess",
            "openAccessPdf",
            "tldr",
        ]
    )
    url = f"https://api.semanticscholar.org/graph/v1/paper/{quote(identifier, safe=':')}?{urlencode({'fields': fields})}"
    headers = {"User-Agent": "qwen-zhikan-papergraph/0.1"}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    return semantic_scholar_get_json(url, headers=headers)

def merge_semantic_scholar_detail(payload: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    result = semantic_scholar_item_to_result(detail)
    detail_payload = result.get("papergraph_input") if isinstance(result.get("papergraph_input"), dict) else {}
    tldr = detail.get("tldr") if isinstance(detail.get("tldr"), dict) else {}
    if not detail_payload.get("abstract") and tldr.get("text"):
        detail_payload["abstract"] = normalize_space(str(tldr.get("text") or ""))
    merged = merge_nonempty(payload, detail_payload)
    if result.get("open_access_pdf"):
        merged["open_access_pdf"] = result.get("open_access_pdf")
    return merged

def fetch_arxiv_by_id(arxiv_id: str) -> dict[str, Any]:
    clean_id = arxiv_id.strip()
    if not clean_id:
        return {}
    url = f"https://export.arxiv.org/api/query?{urlencode({'id_list': clean_id})}"
    raw = arxiv_get_text(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"})
    root = ET.fromstring(raw)
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        return {}
    result = arxiv_entry_to_result(entry, ns)
    return result.get("papergraph_input", {}) if isinstance(result.get("papergraph_input"), dict) else {}

def fetch_pdf_text_excerpt(url: str, max_bytes: int = 8_000_000, max_pages: int = 4) -> str:
    try:
        from ._utils import normalize_space, trim_text
    except ImportError:
        from _utils import normalize_space, trim_text
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is not installed; install pypdf to enable PDF full-text fallback") from exc
    request = Request(url, headers={"User-Agent": "qwen-zhikan-papergraph/0.1"})
    context = ssl_context()
    try:
        with urlopen(request, timeout=30.0, context=context) as response:
            data = response.read(max_bytes + 1)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: PDF fetch failed") from exc
    except URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc
    if len(data) > max_bytes:
        raise RuntimeError(f"PDF exceeds {max_bytes} byte safety limit")
    reader = PdfReader(BytesIO(data))
    chunks: list[str] = []
    for page in reader.pages[: max(1, max_pages)]:
        try:
            text = normalize_space(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            chunks.append(text)
    return trim_text("\n\n".join(chunks), 8000)

def merge_nonempty(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, list):
            if value and not merged.get(key):
                merged[key] = value
            continue
        text = normalize_space(str(value or ""))
        if not text:
            continue
        existing = normalize_space(str(merged.get(key) or ""))
        if not existing or (key in {"abstract", "conclusion"} and len(text) > len(existing)):
            merged[key] = value
    return merged

def http_get_text(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> str:
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
    request = Request(url, headers=headers or {})
    context = ssl_context()
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            encoding = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(encoding, errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        retry_after = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else ""
        retry_hint = f" retry_after={retry_after}" if retry_after else ""
        raise RuntimeError(f"HTTP {exc.code}:{retry_hint} {trim_text(body, 500)}") from exc
    except URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc

def http_get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    text = http_get_text(url, headers=headers, timeout=timeout)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON parse failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("JSON response is not an object")
    return payload

def semantic_scholar_get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    cached = semantic_scholar_cache_get(url)
    if cached is not None:
        log_event("SCIENCE", "semantic_scholar_cache_hit")
        return json.loads(cached)
    # Session-level kill switch: if too many 429s total, stop hitting SS entirely
    if SEMANTIC_SCHOLAR_429_COUNT >= 25:
        raise RuntimeError(
            f"Semantic Scholar session rate limit exceeded: {SEMANTIC_SCHOLAR_429_COUNT} total 429s. "
            "All further SS API calls are skipped for this session to avoid wasting time."
        )
    wait_for_semantic_scholar_circuit_if_needed("pre_request")
    retry_limit = max(20, int(SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT))
    last_error: RuntimeError | None = None
    for attempt in range(retry_limit + 1):
        # Check session kill switch inside retry loop too
        if SEMANTIC_SCHOLAR_429_COUNT >= 25:
            raise RuntimeError(
                f"Semantic Scholar session rate limit exceeded during retry: {SEMANTIC_SCHOLAR_429_COUNT} total 429s. "
                "Stopping retries to avoid wasting time."
            )
        try:
            wait_for_semantic_scholar_circuit_if_needed("retry" if attempt else "request")
            text = semantic_scholar_get_text(url, headers=headers)
            semantic_scholar_cache_put(url, text)
            return json.loads(text)
        except RuntimeError as exc:
            if "HTTP 429" not in str(exc):
                raise
            last_error = exc
            delay = semantic_scholar_backoff_seconds(attempt, str(exc))
            register_semantic_scholar_429(delay)
            log_event(
                "SCIENCE",
                "semantic_scholar_429_fail_fast"
                if SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429
                else "semantic_scholar_429_backoff",
                attempt=attempt + 1,
                max_attempts=retry_limit + 1,
                delay_seconds=round(delay, 2),
                fail_fast=bool(SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429),
            )
            if SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429 or attempt >= retry_limit:
                raise RuntimeError(
                    f"Semantic Scholar rate limited after {retry_limit + 1} attempts with backoff: {exc}"
                ) from exc
            wait_seconds = semantic_scholar_retry_wait_seconds(delay)
            log_event(
                "SCIENCE",
                "semantic_scholar_retry_wait",
                attempt=attempt + 1,
                wait_seconds=round(wait_seconds, 2),
            )
            time.sleep(wait_seconds)
    raise RuntimeError(
        f"Semantic Scholar rate limit persisted after {retry_limit + 1} attempts: {last_error}"
    )

def wait_for_semantic_scholar_circuit_if_needed(reason: str = "request") -> None:
    circuit_open, retry_after = semantic_scholar_circuit_open()
    if not circuit_open:
        return
    wait_seconds = min(retry_after, 120.0)
    log_event(
        "SCIENCE",
        "semantic_scholar_circuit_wait",
        reason=reason,
        wait_seconds=round(wait_seconds, 2),
    )
    time.sleep(wait_seconds)

def semantic_scholar_retry_wait_seconds(delay: float) -> float:
    """Return actual wait time for retry, respecting the computed delay.

    Floor: strict_interval (1.5s) to avoid hammering.
    Cap: 60s to avoid excessive waits.
    """
    floor = semantic_scholar_strict_interval_seconds()
    return max(floor, min(float(delay), 60.0))

def semantic_scholar_strict_interval_seconds() -> float:
    return 1.5

def semantic_scholar_retry_buffer_seconds() -> float:
    return 0.0

def semantic_scholar_circuit_open() -> tuple[bool, float]:
    try:
        from ._models import SEMANTIC_SCHOLAR_CIRCUIT_LOCK
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_CIRCUIT_LOCK
    with SEMANTIC_SCHOLAR_CIRCUIT_LOCK:
        remaining = SEMANTIC_SCHOLAR_COOLDOWN_UNTIL - time.monotonic()
    return remaining > 0, max(0.0, remaining)

def semantic_scholar_circuit_seconds(delay: float) -> float:
    configured = max(0.0, float(SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS))
    if configured <= 0:
        return 0.0
    floor = semantic_scholar_strict_interval_seconds()
    return min(max(configured, floor), 180.0)

def register_semantic_scholar_429(delay: float) -> None:
    try:
        from ._models import SEMANTIC_SCHOLAR_CIRCUIT_LOCK
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_CIRCUIT_LOCK
    global SEMANTIC_SCHOLAR_429_COUNT, SEMANTIC_SCHOLAR_COOLDOWN_UNTIL
    cooldown = semantic_scholar_circuit_seconds(delay)
    with SEMANTIC_SCHOLAR_CIRCUIT_LOCK:
        SEMANTIC_SCHOLAR_429_COUNT += 1
        if cooldown > 0:
            SEMANTIC_SCHOLAR_COOLDOWN_UNTIL = max(
                SEMANTIC_SCHOLAR_COOLDOWN_UNTIL,
                time.monotonic() + cooldown,
            )
    log_event(
        "SCIENCE",
        "semantic_scholar_429_registered",
        cooldown_seconds=round(cooldown, 2),
        count=SEMANTIC_SCHOLAR_429_COUNT,
    )

def semantic_scholar_skip_block(query: str, provider: str = "semantic_scholar") -> dict[str, Any] | None:
    circuit_open, retry_after = semantic_scholar_circuit_open()
    if not circuit_open:
        return None
    return {
        "provider": provider,
        "query": query,
        "status": "rate_limited_skipped",
        "error": f"Semantic Scholar circuit open; retry_after_seconds={retry_after:.1f}",
        "rate_limited": True,
        "results": [],
    }

def arxiv_circuit_open() -> tuple[bool, float]:
    try:
        from ._models import ARXIV_CIRCUIT_LOCK
    except ImportError:
        from _models import ARXIV_CIRCUIT_LOCK
    with ARXIV_CIRCUIT_LOCK:
        remaining = ARXIV_COOLDOWN_UNTIL - time.monotonic()
    return remaining > 0, max(0.0, remaining)

def arxiv_circuit_seconds() -> float:
    configured = max(0.0, float(SCIENCE_ARXIV_CIRCUIT_SECONDS))
    floor = max(15.0, float(SCIENCE_ARXIV_MIN_INTERVAL_SECONDS) * 4)
    return min(max(configured, floor), 300.0)

def register_arxiv_429(error: str = "") -> None:
    try:
        from ._models import ARXIV_CIRCUIT_LOCK
        from ._utils import trim_text
    except ImportError:
        from _models import ARXIV_CIRCUIT_LOCK
        from _utils import trim_text
    global ARXIV_429_COUNT, ARXIV_COOLDOWN_UNTIL
    cooldown = arxiv_circuit_seconds()
    with ARXIV_CIRCUIT_LOCK:
        ARXIV_429_COUNT += 1
        ARXIV_COOLDOWN_UNTIL = max(ARXIV_COOLDOWN_UNTIL, time.monotonic() + cooldown)
    log_event(
        "SCIENCE",
        "arxiv_circuit_open",
        cooldown_seconds=round(cooldown, 2),
        count=ARXIV_429_COUNT,
        error=trim_text(error, 180),
    )

def arxiv_skip_block(query: str) -> dict[str, Any] | None:
    circuit_open, retry_after = arxiv_circuit_open()
    if not circuit_open:
        return None
    return {
        "provider": "arxiv",
        "query": query,
        "status": "rate_limited_skipped",
        "error": f"arXiv circuit open; retry_after_seconds={retry_after:.1f}",
        "rate_limited": True,
        "results": [],
    }

def semantic_scholar_backoff_seconds(attempt: int, error: str = "") -> float:
    """Compute backoff delay for a 429 retry.

    Priority:
    1. Retry-After header value parsed from the error string (capped at 5s).
    2. Exponential backoff: strict_interval * 2^attempt, capped at 5s.
    3. Configured SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS as floor.
    """
    floor = semantic_scholar_strict_interval_seconds()
    configured = max(floor, float(SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS))
    # 1) Try server-provided Retry-After (cap at 5s — SS values of 8-30s are too conservative)
    retry_after = semantic_scholar_retry_after_seconds(error) if error else None
    if retry_after is not None and retry_after > 0:
        return max(floor, min(retry_after, 5.0))
    # 2) Exponential backoff as fallback (cap at 5s)
    exp_delay = floor * (2 ** min(attempt, 3))
    return max(configured, min(exp_delay, 5.0))

def semantic_scholar_get_text(url: str, headers: dict[str, str] | None = None) -> str:
    log_semantic_scholar_key_status()
    wait_for_semantic_scholar_rate_limit()
    return http_get_text(url, headers=headers)

def arxiv_get_text(url: str, headers: dict[str, str] | None = None) -> str:
    wait_for_arxiv_rate_limit()
    try:
        return http_get_text(url, headers=headers)
    except RuntimeError as exc:
        if is_rate_limit_error(str(exc)):
            register_arxiv_429(str(exc))
        raise

def semantic_scholar_retry_after_seconds(error: str) -> float | None:
    match = re.search(r"retry_after=([0-9]+(?:\.[0-9]+)?)", error, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None

def semantic_scholar_cache_get(url: str) -> str | None:
    try:
        from ._models import SEMANTIC_SCHOLAR_CACHE_LOCK
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_CACHE_LOCK
    ttl = max(0.0, float(SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS))
    if ttl <= 0:
        return None
    with SEMANTIC_SCHOLAR_CACHE_LOCK:
        cached = SEMANTIC_SCHOLAR_RESPONSE_CACHE.get(url)
        if not cached:
            return None
        created_at, text = cached
        if time.time() - created_at > ttl:
            SEMANTIC_SCHOLAR_RESPONSE_CACHE.pop(url, None)
            return None
        return text

def semantic_scholar_cache_put(url: str, text: str) -> None:
    try:
        from ._models import SEMANTIC_SCHOLAR_CACHE_LOCK
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_CACHE_LOCK
    ttl = max(0.0, float(SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS))
    if ttl <= 0:
        return
    with SEMANTIC_SCHOLAR_CACHE_LOCK:
        if len(SEMANTIC_SCHOLAR_RESPONSE_CACHE) > 512:
            oldest = sorted(SEMANTIC_SCHOLAR_RESPONSE_CACHE.items(), key=lambda item: item[1][0])[:64]
            for key, _ in oldest:
                SEMANTIC_SCHOLAR_RESPONSE_CACHE.pop(key, None)
        SEMANTIC_SCHOLAR_RESPONSE_CACHE[url] = (time.time(), text)

def log_semantic_scholar_key_status() -> None:
    global SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED
    if SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED:
        return
    SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED = True
    log_event(
        "SCIENCE",
        "semantic_scholar_key_status",
        configured=bool(SEMANTIC_SCHOLAR_API_KEY),
        min_interval_seconds=semantic_scholar_strict_interval_seconds(),
    )

def is_semantic_scholar_rate_limit_error(error: str) -> bool:
    return is_rate_limit_error(error)

def is_rate_limit_error(error: str) -> bool:
    text = str(error).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text

def is_semantic_scholar_not_found_error(error: str) -> bool:
    text = str(error).lower()
    return "http 404" in text or "paper with id" in text and "not found" in text

def wait_for_semantic_scholar_rate_limit() -> None:
    try:
        from ._models import SEMANTIC_SCHOLAR_RATE_LOCK
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_RATE_LOCK
    global SEMANTIC_SCHOLAR_LAST_REQUEST_AT
    interval = semantic_scholar_strict_interval_seconds()
    if interval <= 0:
        return
    with SEMANTIC_SCHOLAR_RATE_LOCK:
        release = acquire_semantic_scholar_process_lock()
        try:
            now_wall = time.time()
            persisted_at = read_semantic_scholar_rate_timestamp()
            if persisted_at > now_wall + interval:
                log_event(
                    "SCIENCE",
                    "semantic_scholar_rate_state_future_ignored",
                    future_seconds=round(persisted_at - now_wall, 2),
                    strict_interval_seconds=round(interval, 2),
                )
                persisted_at = 0.0
            last_wall = max(persisted_at, wall_time_from_monotonic(SEMANTIC_SCHOLAR_LAST_REQUEST_AT))
            wait_seconds = last_wall + interval - now_wall
            if wait_seconds > 0:
                log_event(
                    "SCIENCE",
                    "semantic_scholar_rate_limit",
                    wait_ms=int(wait_seconds * 1000),
                    scope="process_file",
                )
                time.sleep(wait_seconds)
            current_wall = time.time()
            SEMANTIC_SCHOLAR_LAST_REQUEST_AT = time.monotonic()
            write_semantic_scholar_rate_timestamp(current_wall)
        finally:
            release()

def wait_for_arxiv_rate_limit() -> None:
    try:
        from ._models import ARXIV_PROCESS_LOCK_DIR, ARXIV_RATE_LOCK, ARXIV_RATE_STATE_FILE
    except ImportError:
        from _models import ARXIV_PROCESS_LOCK_DIR, ARXIV_RATE_LOCK, ARXIV_RATE_STATE_FILE
    global ARXIV_LAST_REQUEST_AT
    interval = max(0.0, float(SCIENCE_ARXIV_MIN_INTERVAL_SECONDS))
    if interval <= 0:
        return
    with ARXIV_RATE_LOCK:
        release = acquire_provider_process_lock(ARXIV_PROCESS_LOCK_DIR, interval)
        try:
            now_wall = time.time()
            persisted_at = read_provider_rate_timestamp(ARXIV_RATE_STATE_FILE)
            last_wall = max(persisted_at, wall_time_from_monotonic(ARXIV_LAST_REQUEST_AT))
            wait_seconds = last_wall + interval - now_wall
            if wait_seconds > 0:
                log_event(
                    "SCIENCE",
                    "arxiv_rate_limit",
                    wait_ms=int(wait_seconds * 1000),
                    scope="process_file",
                )
                time.sleep(wait_seconds)
            current_wall = time.time()
            ARXIV_LAST_REQUEST_AT = time.monotonic()
            write_provider_rate_timestamp(
                ARXIV_RATE_STATE_FILE,
                current_wall,
                min_interval_seconds=SCIENCE_ARXIV_MIN_INTERVAL_SECONDS,
            )
        finally:
            release()

def wall_time_from_monotonic(monotonic_timestamp: float) -> float:
    if monotonic_timestamp <= 0:
        return 0.0
    return time.time() - max(0.0, time.monotonic() - monotonic_timestamp)

def read_semantic_scholar_rate_timestamp() -> float:
    try:
        from ._models import SEMANTIC_SCHOLAR_RATE_STATE_FILE
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_RATE_STATE_FILE
    return read_provider_rate_timestamp(SEMANTIC_SCHOLAR_RATE_STATE_FILE)

def write_semantic_scholar_rate_timestamp(timestamp: float) -> None:
    try:
        from ._models import SEMANTIC_SCHOLAR_RATE_STATE_FILE
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_RATE_STATE_FILE
    write_provider_rate_timestamp(
        SEMANTIC_SCHOLAR_RATE_STATE_FILE,
        timestamp,
        min_interval_seconds=semantic_scholar_strict_interval_seconds(),
    )

def read_provider_rate_timestamp(path: Path) -> float:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return float(raw.get("last_request_wall_time") or 0.0)
    except Exception:
        return 0.0

def write_provider_rate_timestamp(path: Path, timestamp: float, min_interval_seconds: float) -> None:
    try:
        SCIENCE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "last_request_wall_time": timestamp,
                    "min_interval_seconds": min_interval_seconds,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp)),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        log_event("SCIENCE", "provider_rate_state_write_failed", path=str(path), error=str(exc))

def acquire_semantic_scholar_process_lock():
    try:
        from ._models import SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR
    return acquire_provider_process_lock(
        SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR,
        semantic_scholar_strict_interval_seconds(),
    )

def acquire_provider_process_lock(lock_dir: Path, min_interval_seconds: float):
    SCIENCE_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    stale_after = max(60.0, float(min_interval_seconds) * 20)
    while True:
        try:
            lock_dir.mkdir()
            return lambda: release_provider_process_lock(lock_dir)
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
                if age > stale_after:
                    lock_dir.rmdir()
                    log_event("SCIENCE", "provider_rate_lock_stale_removed", path=str(lock_dir), age_seconds=round(age, 2))
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                pass
            if time.monotonic() - started > 30.0:
                log_event("SCIENCE", "provider_rate_lock_timeout", path=str(lock_dir))
                return lambda: None
            time.sleep(0.05)

def release_semantic_scholar_process_lock() -> None:
    try:
        from ._models import SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR
    except ImportError:
        from _models import SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR
    release_provider_process_lock(SEMANTIC_SCHOLAR_PROCESS_LOCK_DIR)

def release_provider_process_lock(lock_dir: Path) -> None:
    try:
        lock_dir.rmdir()
    except FileNotFoundError:
        return
    except OSError as exc:
        log_event("SCIENCE", "provider_rate_lock_release_failed", path=str(lock_dir), error=str(exc))

def ssl_context() -> ssl.SSLContext:
    if SCIENCE_INSECURE_SSL:
        return ssl._create_unverified_context()
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

