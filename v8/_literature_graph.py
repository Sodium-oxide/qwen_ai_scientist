from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote, urlencode
import ast
import json
import math
import re
import time

try:
    from .config import (
        SCIENCE_BRIDGE_SEARCH_ENABLED,
        SCIENCE_BRIDGE_SEARCH_MAX_RESULTS,
        SCIENCE_BRIDGE_SEARCH_QUERY_LIMIT,
        SCIENCE_COMMUNITY_AWARE_SEED_SELECTION,
        SCIENCE_CROSS_COMMUNITY_EDGE_BONUS,
        SCIENCE_LOUVAIN_BRIDGE_THRESHOLD,
        SCIENCE_LOUVAIN_ENABLED,
        SCIENCE_LOUVAIN_INCLUDE_ARTIFICIAL_EDGES,
        SCIENCE_LOUVAIN_MAX_NODES,
        SCIENCE_LOUVAIN_MIN_COMMUNITY_RECORDS,
        SCIENCE_LOUVAIN_RESOLUTION,
        SCIENCE_MIN_CROSS_COMMUNITY_SEEDS,
        SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT,
        SCIENCE_SPARSE_GRAPH_THRESHOLD,
        SEMANTIC_SCHOLAR_API_KEY,
    )
    from .log import log_event
except ImportError:
    from config import (
        SCIENCE_BRIDGE_SEARCH_ENABLED,
        SCIENCE_BRIDGE_SEARCH_MAX_RESULTS,
        SCIENCE_BRIDGE_SEARCH_QUERY_LIMIT,
        SCIENCE_COMMUNITY_AWARE_SEED_SELECTION,
        SCIENCE_CROSS_COMMUNITY_EDGE_BONUS,
        SCIENCE_LOUVAIN_BRIDGE_THRESHOLD,
        SCIENCE_LOUVAIN_ENABLED,
        SCIENCE_LOUVAIN_INCLUDE_ARTIFICIAL_EDGES,
        SCIENCE_LOUVAIN_MAX_NODES,
        SCIENCE_LOUVAIN_MIN_COMMUNITY_RECORDS,
        SCIENCE_LOUVAIN_RESOLUTION,
        SCIENCE_MIN_CROSS_COMMUNITY_SEEDS,
        SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT,
        SCIENCE_SPARSE_GRAPH_THRESHOLD,
        SEMANTIC_SCHOLAR_API_KEY,
    )
    from log import log_event

try:
    import networkx as nx
except ImportError:
    nx = None

LOUVAIN_AVAILABLE = bool(nx is not None and hasattr(nx.algorithms.community, "louvain_communities"))



def expand_literature_graph(
    search_id: str,
    result_index: int = 0,
    query: str = "",
    direction: str = "both",
    max_results: int = 40,
    use_llm: bool = False,
    depth: int = 1,
    second_layer_top_k: int = 3,
    allow_fallback: bool = True,
) -> str:
    try:
        from ._literature_import import import_literature_search_result
        from ._literature_search import dedupe_literature_results, flatten_literature_results, is_semantic_scholar_not_found_error, is_semantic_scholar_rate_limit_error, judge_literature_candidates_with_llm, literature_result_unique_key, rank_literature_results, search_semantic_scholar, select_literature_result, summarize_literature_result
        from ._project import load_search, save_search
        from ._utils import clamp_int, find_by_id, new_id, normalize_key
    except ImportError:
        from _literature_import import import_literature_search_result
        from _literature_search import dedupe_literature_results, flatten_literature_results, is_semantic_scholar_not_found_error, is_semantic_scholar_rate_limit_error, judge_literature_candidates_with_llm, literature_result_unique_key, rank_literature_results, search_semantic_scholar, select_literature_result, summarize_literature_result
        from _project import load_search, save_search
        from _utils import clamp_int, find_by_id, new_id, normalize_key
    seed_search = load_search(search_id)
    results = seed_search.get("results", [])
    if not results:
        raise ValueError(f"Search {search_id} has no seed results to expand.")
    try:
        seed = results[int(result_index)]
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid seed result_index {result_index} for search {search_id}") from exc
    if not isinstance(seed, dict):
        raise ValueError(f"Seed result is not a paper object: {search_id}:{result_index}")

    lookup_ids = semantic_scholar_lookup_ids(seed)
    lookup_id = lookup_ids[0] if lookup_ids else ""
    if not lookup_ids:
        raise ValueError("Seed paper has no Semantic Scholar id, DOI, or arXiv id for graph expansion.")

    selected_direction = normalize_key(direction)
    edge_kinds = ["references", "citations"] if selected_direction == "both" else [selected_direction]
    raw_edges: list[dict[str, Any]] = []
    per_edge_limit = min(
        max(1, int(SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT)),
        max(1, clamp_int(max_results, 1, 200) // max(1, len(edge_kinds))),
    )
    errors: list[dict[str, str]] = []
    seed_not_indexed = False
    rate_limited_ids: list[str] = []
    for edge_kind in edge_kinds:
        if edge_kind not in {"references", "citations"}:
            errors.append({"edge": edge_kind, "error": "unknown direction"})
            continue
        edge_loaded = False
        not_found_errors: list[str] = []
        for candidate_lookup_id in lookup_ids:
            try:
                edges = fetch_semantic_scholar_edges(candidate_lookup_id, edge_kind, limit=per_edge_limit)
                raw_edges.extend(edges)
                log_event("SCIENCE", "graph_expand_edges_loaded",
                          edge=edge_kind, count=len(edges), lookup_id=candidate_lookup_id[:60])
                lookup_id = candidate_lookup_id
                edge_loaded = True
                if candidate_lookup_id != lookup_ids[0]:
                    log_event(
                        "SCIENCE",
                        "graph_expand_lookup_alias_used",
                        original=lookup_ids[0],
                        used=candidate_lookup_id,
                    )
                break
            except Exception as exc:
                error_text = str(exc)
                if is_semantic_scholar_not_found_error(error_text):
                    not_found_errors.append(error_text)
                    continue
                errors.append(
                    {
                        "edge": edge_kind,
                        "lookup_id": candidate_lookup_id,
                        "error": error_text,
                        "rate_limited": is_semantic_scholar_rate_limit_error(error_text),
                    }
                )
                if is_semantic_scholar_rate_limit_error(error_text):
                    log_event("SCIENCE", "graph_expand_rate_limited", search_id=search_id, edge=edge_kind)
                    rate_limited_ids.append(f"{candidate_lookup_id}:{edge_kind}")
                    continue
                else:
                    log_event("SCIENCE", "graph_expand_failed", search_id=search_id, edge=edge_kind, error=error_text)
                break
        if edge_loaded:
            continue
        if not_found_errors:
            seed_not_indexed = True
            error_text = not_found_errors[-1]
            errors.append(
                {
                    "edge": edge_kind,
                    "lookup_ids": lookup_ids,
                    "error": error_text,
                    "seed_not_indexed": True,
                }
            )
            log_event(
                "SCIENCE",
                "graph_expand_seed_not_indexed",
                search_id=search_id,
                edge=edge_kind,
                lookup_ids=",".join(lookup_ids),
            )
            break

    # Delayed retry for rate-limited edge fetches (429)
    if rate_limited_ids and not raw_edges:
        import time as _time
        _time.sleep(2)
        for rate_limited_entry in rate_limited_ids:
            rl_lookup_id, rl_edge_kind = rate_limited_entry.rsplit(":", 1)
            try:
                edges = fetch_semantic_scholar_edges(rl_lookup_id, rl_edge_kind, limit=per_edge_limit)
                raw_edges.extend(edges)
                log_event("SCIENCE", "graph_expand_rate_limit_retry_success", search_id=search_id, edge=rl_edge_kind)
            except Exception as retry_exc:
                errors.append(
                    {
                        "edge": rl_edge_kind,
                        "lookup_id": rl_lookup_id,
                        "error": str(retry_exc),
                        "rate_limited_retry": True,
                    }
                )
                log_event("SCIENCE", "graph_expand_rate_limit_retry_failed", search_id=search_id, edge=rl_edge_kind, error=str(retry_exc))

    graph_results = dedupe_literature_results(
        [
            semantic_scholar_edge_to_result(edge)
            for edge in raw_edges
            if isinstance(edge, dict)
        ]
    )
    graph_query = query or str(seed_search.get("query", ""))
    ranked = rank_literature_results(graph_query, graph_results)[: clamp_int(max_results, 1, 200)]
    selected_depth = clamp_int(depth, 1, 2)
    second_layer_count = 0
    bridge_activation: dict[str, Any] = {"activated": False, "reason": "not_needed", "count": 0}
    if selected_depth >= 2 and ranked:
        second_layer_results = (
            expand_second_layer_graph_results_with_community_awareness(
                ranked,
                graph_query,
                edge_kinds,
                max_results=max_results,
                top_k=second_layer_top_k,
                errors=errors,
            )
            if SCIENCE_COMMUNITY_AWARE_SEED_SELECTION
            else expand_second_layer_graph_results(
                ranked,
                graph_query,
                edge_kinds,
                max_results=max_results,
                top_k=second_layer_top_k,
                errors=errors,
            )
        )
        second_layer_count = len(second_layer_results)
        if second_layer_results:
            graph_results = dedupe_literature_results(graph_results + second_layer_results)
            ranked = rank_literature_results(graph_query, graph_results)[: clamp_int(max_results, 1, 200)]
        if SCIENCE_BRIDGE_SEARCH_ENABLED and graph_needs_cross_community_bridge(ranked, max_results):
            before_summary = graph_community_summary(ranked)
            try:
                bridge_payload = json.loads(
                    search_cross_community_bridges(
                        search_id,
                        target_communities=before_summary["communities"],
                        max_results=max(1, min(int(SCIENCE_BRIDGE_SEARCH_MAX_RESULTS), clamp_int(max_results, 1, 200) // 2)),
                    )
                )
                bridge_search_id = str(bridge_payload.get("bridge_search_id") or "")
                bridge_record = load_search(bridge_search_id) if bridge_search_id else {}
                bridge_results = bridge_record.get("results", []) if isinstance(bridge_record.get("results"), list) else []
                seed_key = literature_result_unique_key(seed)
                seed_community = infer_literature_community(seed)
                prepared_bridges: list[dict[str, Any]] = []
                for item in bridge_results:
                    if not isinstance(item, dict):
                        continue
                    candidate = dict(item)
                    candidate["graph_relation"] = "cross_community_search"
                    candidate["graph_parent_key"] = seed_key
                    candidate["graph_parent_title"] = seed.get("title", "")
                    candidate["graph_parent_community"] = seed_community
                    candidate["graph_community"] = infer_literature_community(candidate)
                    candidate["graph_cross_community_bridge"] = True
                    candidate["graph_bridge_communities"] = f"{seed_community}->{candidate['graph_community']}"
                    candidate["expanded_depth"] = 2
                    prepared_bridges.append(candidate)
                if prepared_bridges:
                    graph_results = dedupe_literature_results(graph_results + prepared_bridges)
                    ranked = retain_community_bridge_candidates(
                        rank_literature_results(graph_query, graph_results),
                        prepared_bridges,
                        max_results=clamp_int(max_results, 1, 200),
                    )
                bridge_activation = {
                    "activated": True,
                    "reason": "sparse_graph",
                    "bridge_search_id": bridge_search_id,
                    "count": len(prepared_bridges),
                    "before": before_summary,
                    "after": graph_community_summary(ranked),
                }
                errors.append(
                    {
                        "edge": "cross_community_bridge_search",
                        "count": len(prepared_bridges),
                        "community_coverage_before": before_summary["coverage"],
                    }
                )
                log_event(
                    "SCIENCE",
                    "bridge_search_activated",
                    search_id=search_id,
                    bridge_search_id=bridge_search_id,
                    bridge_count=len(prepared_bridges),
                )
            except Exception as exc:
                bridge_activation = {"activated": True, "reason": "bridge_search_failed", "count": 0, "error": str(exc)[:240]}
                errors.append({"edge": "cross_community_bridge_search", "error": str(exc)[:240]})
                log_event("SCIENCE", "bridge_search_failed", source_search_id=search_id, error=str(exc)[:160])

    # --- Seed rotation: try alternative seeds if primary produced empty graph ---
    seed_rotation_used = False
    if not ranked and allow_fallback and len(results) > 1:
        max_alt_attempts = min(2, len(results) - 1)
        for alt_offset in range(1, max_alt_attempts + 1):
            alt_index = (result_index + alt_offset) % len(results)
            alt_seed = results[alt_index]
            if not isinstance(alt_seed, dict):
                continue
            alt_lookup_ids = semantic_scholar_lookup_ids(alt_seed)
            if not alt_lookup_ids:
                continue
            log_event("SCIENCE", "graph_expand_seed_rotation", search_id=search_id,
                      original_index=result_index, alt_index=alt_index,
                      alt_title=str(alt_seed.get("title", ""))[:120], attempt=alt_offset)
            alt_raw_edges: list[dict[str, Any]] = []
            alt_rate_limited: list[str] = []
            alt_not_indexed = False
            for alt_edge_kind in edge_kinds:
                for alt_candidate_id in alt_lookup_ids:
                    try:
                        alt_edges = fetch_semantic_scholar_edges(alt_candidate_id, alt_edge_kind, limit=per_edge_limit)
                        log_event("SCIENCE", "graph_expand_alt_edges_loaded",
                                  edge=alt_edge_kind, count=len(alt_edges), alt_index=alt_index)
                        alt_raw_edges.extend(alt_edges)
                        break
                    except Exception as alt_exc:
                        alt_err = str(alt_exc)
                        if is_semantic_scholar_not_found_error(alt_err):
                            alt_not_indexed = True
                            continue
                        if is_semantic_scholar_rate_limit_error(alt_err):
                            alt_rate_limited.append(f"{alt_candidate_id}:{alt_edge_kind}")
                            continue
                        break
            # Delayed retry for rate-limited alt fetches
            if alt_rate_limited and not alt_raw_edges:
                import time as _alt_time
                _alt_time.sleep(2)
                for rl_entry in alt_rate_limited:
                    rl_id, rl_kind = rl_entry.rsplit(":", 1)
                    try:
                        alt_edges = fetch_semantic_scholar_edges(rl_id, rl_kind, limit=per_edge_limit)
                        alt_raw_edges.extend(alt_edges)
                    except Exception:
                        pass
            alt_graph_results = dedupe_literature_results(
                [semantic_scholar_edge_to_result(e) for e in alt_raw_edges if isinstance(e, dict)]
            )
            alt_ranked = rank_literature_results(graph_query, alt_graph_results)[:clamp_int(max_results, 1, 200)]
            if not alt_ranked:
                log_event("SCIENCE", "graph_expand_alt_empty", alt_index=alt_index, attempt=alt_offset)
                continue
            # Alternative seed succeeded — use its results
            seed_rotation_used = True
            ranked = alt_ranked
            graph_results = alt_graph_results
            seed = alt_seed
            lookup_id = alt_lookup_ids[0]
            lookup_ids = alt_lookup_ids
            result_index = alt_index
            seed_not_indexed = alt_not_indexed
            rate_limited_ids = alt_rate_limited
            errors.append({"edge": "seed_rotation", "alt_index": alt_index, "edges_found": len(alt_raw_edges)})
            log_event("SCIENCE", "graph_expand_seed_rotation_success",
                      search_id=search_id, alt_index=alt_index,
                      edges_found=len(alt_raw_edges), count=len(alt_ranked))
            break

    fallback_used = False
    if not ranked and allow_fallback:
        fallback_used = True
        fallback_max = min(max_results, 30)
        fallback_block = search_semantic_scholar(graph_query, max_results=fallback_max)
        fallback_results = flatten_literature_results([fallback_block])
        seed_key = literature_result_unique_key(seed)
        fallback_results = [item for item in fallback_results if literature_result_unique_key(item) != seed_key]
        for item in fallback_results:
            item["graph_relation"] = "keyword_fallback"
            item["expanded_from_search_id"] = search_id
            item["expanded_from_result_index"] = result_index
            item["seed_title"] = seed.get("title", "")
        ranked = rank_literature_results(graph_query, dedupe_literature_results(fallback_results))[: clamp_int(max_results, 1, 200)]
        errors.append(
            {
                "edge": "fallback_keyword_expansion",
                "error": "citation graph returned no usable neighbors; fell back to Semantic Scholar keyword search",
                "seed_not_indexed": seed_not_indexed,
                "rate_limited_empty": bool(rate_limited_ids),
            }
        )
        log_event(
            "SCIENCE",
            "graph_expand_fallback",
            seed_search_id=search_id,
            reason="rate_limited_empty" if rate_limited_ids else ("seed_not_indexed" if seed_not_indexed else "empty_graph"),
            count=len(ranked),
        )
    louvain_expansion_analysis = (
        annotate_expansion_results_with_louvain(seed, ranked, graph_query)
        if ranked
        else {"status": "not_run", "reason": "no ranked graph results", "community_map": {}, "bridge_nodes": []}
    )
    graph_search_id = new_id("graph")
    for index, item in enumerate(ranked):
        item.setdefault("graph_community", infer_literature_community(item))
        item["result_index"] = index
        item["search_id"] = graph_search_id
        item["expanded_from_search_id"] = search_id
        item["expanded_from_result_index"] = result_index
        item["seed_title"] = seed.get("title", "")

    louvain_expansion_seeds = [
        {
            "result_index": item.get("result_index"),
            "title": item.get("title", ""),
            "semantic_scholar_id": item.get("semantic_scholar_id"),
            "louvain_community": item.get("louvain_community"),
            "connected_communities": item.get("louvain_connected_communities", []),
            "bridge_score": item.get("louvain_bridge_score", 0.0),
            "publication_quality_score": item.get("publication_quality_score", 0.0),
            "relevance_score": item.get("relevance_score", 0.0),
        }
        for item in ranked
        if isinstance(item, dict) and float(item.get("louvain_bridge_score") or 0.0) > 0.0
    ]
    louvain_expansion_seeds.sort(
        key=lambda item: (
            -float(item.get("bridge_score") or 0.0),
            -float(item.get("publication_quality_score") or 0.0),
            -float(item.get("relevance_score") or 0.0),
            int(item.get("result_index") or 0),
        )
    )

    record = {
        "search_id": graph_search_id,
        "kind": "citation_graph_expansion",
        "query": graph_query,
        "seed_search_id": search_id,
        "seed_result_index": result_index,
        "seed_title": seed.get("title", ""),
        "seed_lookup_id": lookup_id,
        "seed_lookup_ids": lookup_ids,
        "direction": selected_direction,
        "depth": selected_depth,
        "second_layer_count": second_layer_count,
        "community_summary": graph_community_summary(ranked),
        "louvain_analysis": louvain_expansion_analysis,
        "louvain_expansion_seeds": louvain_expansion_seeds[:10],
        "bridge_activation": bridge_activation,
        "createdAt": time.time(),
        "total_results": len(ranked),
        "results": ranked,
        "errors": errors,
        "fallback_used": fallback_used,
        "seed_not_indexed": seed_not_indexed,
        "provider_blocks": [
            {
                "provider": "semantic_scholar_graph",
                "status": "ok" if ranked else "empty_or_error",
                "results": ranked,
                "errors": errors,
            }
        ],
    }
    save_search(record)
    selected = None
    llm_judgement = None
    if ranked:
        if use_llm:
            llm_judgement = judge_literature_candidates_with_llm(graph_query, ranked[: min(10, len(ranked))])
            chosen = find_by_id(ranked, "result_index", llm_judgement.get("selected_result_index"))
            selected = chosen or ranked[0]
        else:
            selected = ranked[0]
    response = {
        "graph_search_id": graph_search_id,
        "seed": summarize_literature_result(seed),
        "direction": selected_direction,
        "depth": selected_depth,
        "second_layer_count": second_layer_count,
        "community_summary": record["community_summary"],
        "louvain_analysis": {
            "status": louvain_expansion_analysis.get("status", "not_run"),
            "reason": louvain_expansion_analysis.get("reason", ""),
            "num_communities": louvain_expansion_analysis.get("num_communities", 0),
            "modularity": louvain_expansion_analysis.get("modularity"),
            "structural_node_count": louvain_expansion_analysis.get("structural_node_count", 0),
            "ignored_artificial_edge_count": louvain_expansion_analysis.get("ignored_artificial_edge_count", 0),
            "outlier_communities": [
                {
                    "community_id": item.get("community_id"),
                    "size": item.get("size"),
                    "disposition": item.get("disposition"),
                    "priority": item.get("priority"),
                }
                for item in louvain_expansion_analysis.get("outlier_communities", [])
                if isinstance(item, dict)
            ],
            "bridge_nodes": louvain_expansion_seeds[:5],
        },
        "bridge_activation": bridge_activation,
        "total_results": len(ranked),
        "selected": summarize_literature_result(selected) if selected else None,
        "top_results": [summarize_literature_result(item) for item in ranked[:10]],
        "llm_judgement": llm_judgement,
        "errors": errors,
        "fallback_used": fallback_used,
        "seed_not_indexed": seed_not_indexed,
        "next_step": "Use louvain_analysis.bridge_nodes as cross-community seeds when present; otherwise use select_literature_result(graph_search_id) or import_literature_search_result(project_id, graph_search_id, result_index).",
    }
    log_event("SCIENCE", "graph_expanded", seed_search_id=search_id, graph_search_id=graph_search_id, count=len(ranked))
    return json.dumps(response, ensure_ascii=False, indent=2)

def expand_second_layer_graph_results(
    first_layer_ranked: list[dict[str, Any]],
    query: str,
    edge_kinds: list[str],
    max_results: int,
    top_k: int,
    errors: list[dict[str, Any]],
    community_aware: bool = False,
) -> list[dict[str, Any]]:
    try:
        from ._literature_search import dedupe_literature_results, is_semantic_scholar_not_found_error, is_semantic_scholar_rate_limit_error, literature_result_unique_key, rank_literature_results
        from ._utils import clamp_int, normalize_key
    except ImportError:
        from _literature_search import dedupe_literature_results, is_semantic_scholar_not_found_error, is_semantic_scholar_rate_limit_error, literature_result_unique_key, rank_literature_results
        from _utils import clamp_int, normalize_key
    seeds = (
        select_second_layer_seeds_with_community_awareness(first_layer_ranked, top_k=top_k)
        if community_aware
        else select_second_layer_seeds(first_layer_ranked, top_k=top_k)
    )
    if not seeds:
        return []
    per_edge_limit = min(
        max(1, int(SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT)),
        max(1, clamp_int(max_results, 1, 200) // max(1, len(seeds) * max(1, len(edge_kinds)))),
    )
    expanded: list[dict[str, Any]] = []
    for parent in seeds:
        parent_community = infer_literature_community(parent)
        lookup_ids = semantic_scholar_lookup_ids(parent)
        if not lookup_ids:
            continue
        parent_key = literature_result_unique_key(parent)
        for edge_kind in edge_kinds:
            edges: list[dict[str, Any]] = []
            last_not_found = ""
            for lookup_id in lookup_ids:
                try:
                    edges = fetch_semantic_scholar_edges(lookup_id, edge_kind, limit=per_edge_limit)
                    break
                except Exception as exc:
                    error_text = str(exc)
                    if is_semantic_scholar_not_found_error(error_text):
                        last_not_found = error_text
                        continue
                    errors.append(
                        {
                            "edge": f"second_layer_{edge_kind}",
                            "parent_title": str(parent.get("title") or ""),
                            "lookup_id": lookup_id,
                            "error": error_text,
                            "rate_limited": is_semantic_scholar_rate_limit_error(error_text),
                        }
                    )
                    if is_semantic_scholar_rate_limit_error(error_text):
                        log_event("SCIENCE", "graph_expand_rate_limited", search_id="second_layer", edge=edge_kind)
                    else:
                        log_event("SCIENCE", "graph_expand_failed", search_id="second_layer", edge=edge_kind, error=error_text)
                    break
            if not edges and last_not_found:
                errors.append(
                    {
                        "edge": f"second_layer_{edge_kind}",
                        "parent_title": str(parent.get("title") or ""),
                        "lookup_ids": lookup_ids,
                        "error": last_not_found,
                        "seed_not_indexed": True,
                    }
                )
                continue
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                result = semantic_scholar_edge_to_result(edge)
                base_relation = str(result.get("graph_relation") or normalize_key(edge_kind))
                child_community = infer_literature_community(result)
                is_bridge = community_aware and communities_are_distinct(parent_community, child_community)
                result["graph_relation"] = (
                    f"cross_community_bridge_{base_relation}"
                    if is_bridge
                    else f"second_layer_{base_relation}"
                )
                result["graph_parent_key"] = parent_key
                result["graph_parent_title"] = parent.get("title", "")
                result["graph_parent_result_index"] = parent.get("result_index")
                result["graph_parent_community"] = parent_community
                result["graph_community"] = child_community
                result["graph_cross_community_bridge"] = is_bridge
                result["graph_bridge_communities"] = f"{parent_community}->{child_community}" if is_bridge else ""
                result["expanded_depth"] = 2
                expanded.append(result)
    seed_keys = {literature_result_unique_key(item) for item in first_layer_ranked}
    expanded = [item for item in expanded if literature_result_unique_key(item) not in seed_keys]
    ranked = rank_literature_results(query, dedupe_literature_results(expanded))
    return ranked


def expand_second_layer_graph_results_with_community_awareness(
    first_layer_ranked: list[dict[str, Any]],
    query: str,
    edge_kinds: list[str],
    max_results: int,
    top_k: int,
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return expand_second_layer_graph_results(
        first_layer_ranked,
        query,
        edge_kinds,
        max_results=max_results,
        top_k=top_k,
        errors=errors,
        community_aware=True,
    )


def graph_community_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    profiles = [literature_community_profile(item) for item in results if isinstance(item, dict)]
    communities = [str(profile.get("community") or "mixed") for profile in profiles]
    counts = Counter(communities)
    coverage_set: set[str] = set()
    for profile in profiles:
        coverage_set.update(str(community) for community in profile.get("active_communities", []) if community != "general")
    if not coverage_set:
        for community in counts:
            if community == "translational":
                coverage_set.update({"medicine", "biology"})
            elif community != "mixed":
                coverage_set.add(community)
    return {
        "counts": dict(sorted(counts.items())),
        "coverage": len(coverage_set),
        "communities": sorted(coverage_set),
    }


def graph_needs_cross_community_bridge(results: list[dict[str, Any]], max_results: int) -> bool:
    target = max(1, int(max_results or 1))
    threshold = max(0.05, min(1.0, float(SCIENCE_SPARSE_GRAPH_THRESHOLD)))
    sparse_by_count = len(results) < max(2, math.ceil(target * threshold))
    summary = graph_community_summary(results)
    required_coverage = max(1, int(SCIENCE_MIN_CROSS_COMMUNITY_SEEDS))
    sparse_by_coverage = bool(results) and int(summary["coverage"]) < required_coverage
    return sparse_by_count or sparse_by_coverage


def retain_community_bridge_candidates(
    ranked_results: list[dict[str, Any]],
    bridge_candidates: list[dict[str, Any]],
    max_results: int,
) -> list[dict[str, Any]]:
    try:
        from ._literature_search import literature_result_unique_key
        from ._utils import clamp_int
    except ImportError:
        from _literature_search import literature_result_unique_key
        from _utils import clamp_int
    limit = clamp_int(max_results, 1, 200)
    selected = [item for item in ranked_results[:limit] if isinstance(item, dict)]
    if any(item.get("graph_cross_community_bridge") for item in selected):
        return selected
    bridge_keys = {literature_result_unique_key(item) for item in bridge_candidates if isinstance(item, dict)}
    bridge = next(
        (item for item in ranked_results if isinstance(item, dict) and literature_result_unique_key(item) in bridge_keys),
        None,
    )
    if not bridge:
        return selected
    selected = [item for item in selected if literature_result_unique_key(item) != literature_result_unique_key(bridge)]
    if len(selected) >= limit:
        selected = selected[: limit - 1]
    selected.append(bridge)
    return selected


def bridge_query_plan(
    query: str,
    limit: int | None = None,
    target_communities: list[str] | None = None,
) -> list[str]:
    try:
        from ._models import infer_research_domain
    except ImportError:
        from _models import infer_research_domain
    core_terms = re.findall(r"[A-Za-z0-9-]{3,}", str(query or "").lower())
    generic = {"study", "analysis", "research", "effect", "using", "with", "from", "into", "between"}
    core = " ".join(term for term in core_terms if term not in generic)[:120].strip()
    if not core:
        core = "scientific research"
    inferred_domain = infer_research_domain(query)
    requested_domains = [str(item) for item in (target_communities or []) if str(item)]
    domain = requested_domains[0] if requested_domains else inferred_domain
    bridge_facets = {
        "physics": ("mathematical modeling", "experimental instrumentation", "data analysis"),
        "mathematics": ("physical modeling", "statistical inference", "computational application"),
        "computer_science": ("statistical learning", "scientific computing", "real-world systems"),
        "quantitative_biology": ("molecular mechanism", "statistical modeling", "clinical translation"),
        "quantitative_finance": ("econometric modeling", "statistical learning", "market mechanism"),
        "statistics": ("scientific application", "computational method", "causal inference"),
        "electrical_engineering": ("signal processing", "control system", "physical instrumentation"),
        "economics": ("econometric evidence", "statistical inference", "policy mechanism"),
        "medicine": ("molecular mechanism", "biomarker stratification", "clinical outcome"),
        "biology": ("molecular mechanism", "quantitative modeling", "translational outcome"),
        "chemistry": ("computational chemistry", "materials engineering", "reaction mechanism"),
    }
    facets = bridge_facets.get(domain, ("theoretical mechanism", "computational analysis", "experimental validation"))
    queries = [f"{core} {facet}" for facet in facets]
    configured_limit = max(1, int(limit if limit is not None else SCIENCE_BRIDGE_SEARCH_QUERY_LIMIT))
    return list(dict.fromkeys(queries))[:configured_limit]


def is_cross_community_candidate(result: dict[str, Any]) -> bool:
    profile = literature_community_profile(result)
    active_communities = [str(item) for item in profile.get("active_communities", []) if str(item) != "general"]
    return str(profile["community"]) == "translational" or len(set(active_communities)) >= 2


def search_cross_community_bridges(
    search_id: str,
    target_communities: list[str] | None = None,
    max_results: int | None = None,
) -> str:
    try:
        from ._literature_search import dedupe_literature_results, flatten_literature_results, rank_literature_results, search_semantic_scholar
        from ._project import load_search, save_search
        from ._utils import clamp_int, new_id
    except ImportError:
        from _literature_search import dedupe_literature_results, flatten_literature_results, rank_literature_results, search_semantic_scholar
        from _project import load_search, save_search
        from _utils import clamp_int, new_id
    source_search = load_search(search_id)
    source_query = str(source_search.get("query") or "")
    requested = SCIENCE_BRIDGE_SEARCH_MAX_RESULTS if max_results is None else max_results
    limit = clamp_int(requested, 1, min(40, max(1, int(SCIENCE_BRIDGE_SEARCH_MAX_RESULTS))))
    queries = bridge_query_plan(source_query, target_communities=target_communities)
    collected: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    per_query = max(1, math.ceil(limit / max(1, len(queries))))
    for bridge_query in queries:
        try:
            block = search_semantic_scholar(bridge_query, max_results=per_query)
            for item in flatten_literature_results([block]):
                if not isinstance(item, dict):
                    continue
                candidate = dict(item)
                candidate["bridge_query"] = bridge_query
                candidate["graph_relation"] = "cross_community_search"
                candidate["graph_cross_community_bridge"] = True
                candidate["graph_community"] = infer_literature_community(candidate)
                collected.append(candidate)
        except Exception as exc:
            errors.append({"query": bridge_query, "error": str(exc)[:240]})
            log_event("SCIENCE", "bridge_search_failed", source_search_id=search_id, query=bridge_query, error=str(exc)[:160])
    deduped = dedupe_literature_results(collected)
    filtered = [item for item in deduped if is_cross_community_candidate(item)]
    ranked = rank_literature_results(source_query, filtered)[:limit]
    bridge_search_id = new_id("bridge")
    record = {
        "search_id": bridge_search_id,
        "kind": "cross_community_bridge_search",
        "source_search_id": search_id,
        "query": source_query,
        "target_communities": list(target_communities or []),
        "bridge_queries": queries,
        "createdAt": time.time(),
        "total_results": len(ranked),
        "results": ranked,
        "errors": errors,
        "community_summary": graph_community_summary(ranked),
    }
    save_search(record)
    log_event(
        "SCIENCE",
        "bridge_search_completed",
        source_search_id=search_id,
        bridge_search_id=bridge_search_id,
        results=len(ranked),
        queries=len(queries),
    )
    return json.dumps(
        {
            "bridge_search_id": bridge_search_id,
            "source_search_id": search_id,
            "total_results": len(ranked),
            "bridge_queries_used": queries,
            "community_summary": record["community_summary"],
            "errors": errors,
            "next_step": "Review or import bridge candidates explicitly; bridge search never imports papers automatically.",
        },
        ensure_ascii=False,
        indent=2,
    )

def select_second_layer_seeds(results: list[dict[str, Any]], top_k: int = 3) -> list[dict[str, Any]]:
    try:
        from ._utils import clamp_int
    except ImportError:
        from _utils import clamp_int
    limit = clamp_int(top_k, 0, 10)
    if limit <= 0:
        return []
    candidates = [item for item in results if semantic_scholar_lookup_id(item)]
    candidates.sort(key=second_layer_seed_score, reverse=True)
    return candidates[:limit]


def infer_literature_community(result: dict[str, Any]) -> str:
    profile = literature_community_profile(result)
    return str(profile.get("community") or "mixed")


def literature_community_profile(result: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._models import research_domain_profile
    except ImportError:
        from _models import research_domain_profile
    text = " ".join(
        str(result.get(key) or "")
        for key in ("title", "abstract", "venue", "fields_of_study", "publication_types")
    ).lower()
    catalog_profile = research_domain_profile(text)
    counts = {
        str(community): int(score)
        for community, score in dict(catalog_profile.get("scores") or {}).items()
        if int(score or 0) > 0
    }
    total = sum(counts.values())
    proportions = {
        community: round(count / total, 4) if total else 0.0
        for community, count in counts.items()
    }
    return {
        "counts": counts,
        "proportions": proportions,
        "community": infer_literature_community_from_counts(counts),
        "active_communities": list(catalog_profile.get("active_domains") or []),
        "matched_keywords": catalog_profile.get("matched_keywords") or {},
        "matched_subfields": catalog_profile.get("matched_subfields") or {},
    }


def infer_literature_community_from_counts(counts: dict[str, int]) -> str:
    medicine = int(counts.get("medicine") or 0)
    biology = int(counts.get("biology") or 0)
    total = max(1, sum(int(value or 0) for value in counts.values()))
    if medicine and biology and min(medicine, biology) / total >= 0.2:
        return "translational"
    ranked = sorted(
        ((str(community), int(score or 0)) for community, score in counts.items() if int(score or 0) > 0),
        key=lambda item: (-item[1], item[0]),
    )
    return ranked[0][0] if ranked else "mixed"


def communities_are_distinct(parent: str, child: str) -> bool:
    left = str(parent or "mixed")
    right = str(child or "mixed")
    if left == right or "mixed" in {left, right}:
        return False
    return True


def community_diversity_score(result: dict[str, Any]) -> float:
    proportions = literature_community_profile(result)["proportions"]
    active = [float(value) for value in proportions.values() if float(value) > 0]
    if len(active) <= 1:
        return 0.0
    entropy = -sum(value * math.log(value) for value in active)
    return round(min(1.0, entropy / math.log(len(active))), 4)


def select_second_layer_seeds_with_community_awareness(
    results: list[dict[str, Any]],
    top_k: int = 3,
    min_bridge_attempts: int | None = None,
) -> list[dict[str, Any]]:
    try:
        from ._utils import clamp_int
    except ImportError:
        from _utils import clamp_int
    limit = clamp_int(top_k, 0, 10)
    if limit <= 0:
        return []
    candidates = [dict(item) for item in results if isinstance(item, dict) and semantic_scholar_lookup_id(item)]
    if not candidates:
        return []
    def selection_community(item: dict[str, Any]) -> str:
        structural = item.get("louvain_community")
        if structural is not None and str(structural) != "":
            return f"louvain:{structural}"
        return infer_literature_community(item)

    community_counts = Counter(selection_community(item) for item in candidates)
    scored: list[tuple[float, float, str, dict[str, Any]]] = []
    for item in candidates:
        community = selection_community(item)
        base_score = second_layer_seed_score(item)
        rarity_bonus = 0.24 / max(1, community_counts[community])
        diversity_bonus = 0.14 * community_diversity_score(item)
        bridge_bonus = 0.16 * max(0.0, min(1.0, float(item.get("louvain_bridge_score") or 0.0)))
        drift_penalty = 0.12 if str(item.get("louvain_priority") or "") == "low" else 0.0
        score = base_score + rarity_bonus + diversity_bonus + bridge_bonus - drift_penalty
        item["graph_community"] = community
        item["community_seed_score"] = round(score, 5)
        scored.append((score, base_score, community, item))
    scored.sort(key=lambda entry: (-entry[0], -entry[1], str(entry[3].get("title") or "")))
    required_communities = min(
        limit,
        max(1, int(min_bridge_attempts if min_bridge_attempts is not None else SCIENCE_MIN_CROSS_COMMUNITY_SEEDS)),
        len(community_counts),
    )
    selected: list[dict[str, Any]] = []
    selected_communities: set[str] = set()
    for _, _, community, item in scored:
        if community in selected_communities:
            continue
        selected.append(item)
        selected_communities.add(community)
        if len(selected) >= required_communities:
            break
    for _, _, _, item in scored:
        if len(selected) >= limit:
            break
        if item not in selected:
            selected.append(item)
    return selected[:limit]

def second_layer_seed_score(result: dict[str, Any]) -> float:
    try:
        from ._literature_scoring import literature_impact_score, publication_quality_assessment
    except ImportError:
        from _literature_scoring import literature_impact_score, publication_quality_assessment
    quality = float(result.get("publication_quality_score") or publication_quality_assessment(result)["quality_score"])
    relevance = float(result.get("relevance_score") or 0.0)
    components = result.get("relevance_components") if isinstance(result.get("relevance_components"), dict) else {}
    impact = float(components.get("impact_score") or literature_impact_score(result))
    edge_bonus = 0.08 if result.get("graph_relation") in {"reference", "citation"} else 0.0
    return 0.42 * quality + 0.35 * relevance + 0.15 * impact + edge_bonus

def fetch_semantic_scholar_edges(lookup_id: str, edge_kind: str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        from ._literature_search import semantic_scholar_get_json
        from ._utils import clamp_int
    except ImportError:
        from _literature_search import semantic_scholar_get_json
        from _utils import clamp_int
    fields = ",".join(
        [
            "contexts",
            "intents",
            "isInfluential",
            "paperId",
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
    params = urlencode({"limit": clamp_int(limit, 1, 100), "fields": fields})
    url = f"https://api.semanticscholar.org/graph/v1/paper/{quote(lookup_id, safe='')}/{edge_kind}?{params}"
    headers = {"User-Agent": "qwen-zhikan-papergraph/0.1"}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    payload = semantic_scholar_get_json(url, headers=headers)
    data = payload.get("data") or []
    return [item for item in data if isinstance(item, dict)]

def semantic_scholar_edge_to_result(edge: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._literature_search import semantic_scholar_item_to_result
    except ImportError:
        from _literature_search import semantic_scholar_item_to_result
    relation = "reference" if "citedPaper" in edge else "citation"
    paper = edge.get("citedPaper") if relation == "reference" else edge.get("citingPaper")
    if not isinstance(paper, dict):
        paper = {key: value for key, value in edge.items() if key not in {"contexts", "intents", "isInfluential"}}
    result = semantic_scholar_item_to_result(paper)
    result["graph_relation"] = relation
    result["citation_contexts"] = edge.get("contexts") or []
    result["citation_intents"] = edge.get("intents") or []
    result["edge_is_influential"] = edge.get("isInfluential")
    result["provider"] = "semantic_scholar_graph"
    result["papergraph_input"]["provider"] = "semantic_scholar_graph"
    return result

def semantic_scholar_lookup_id(result: dict[str, Any]) -> str:
    ids = semantic_scholar_lookup_ids(result)
    return ids[0] if ids else ""

def semantic_scholar_lookup_ids(result: dict[str, Any]) -> list[str]:
    try:
        from ._utils import unique_preserve_order
    except ImportError:
        from _utils import unique_preserve_order
    candidates: list[str] = []
    semantic_id = str(result.get("semantic_scholar_id") or "").strip()
    if semantic_id:
        candidates.append(semantic_id)
    payload = result.get("papergraph_input") if isinstance(result.get("papergraph_input"), dict) else {}
    semantic_id = str(payload.get("semantic_scholar_id") or "").strip()
    if semantic_id:
        candidates.append(semantic_id)
    doi = str(result.get("doi") or payload.get("doi") or "").strip()
    if doi:
        candidates.append(f"DOI:{doi}")
    arxiv_id = str(result.get("arxiv_id") or payload.get("arxiv_id") or "").strip()
    if arxiv_id:
        candidates.append(f"ARXIV:{arxiv_id}")
        unversioned = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
        if unversioned and unversioned != arxiv_id:
            candidates.append(f"ARXIV:{unversioned}")
    return unique_preserve_order([candidate for candidate in candidates if candidate])


def louvain_dependency_status() -> dict[str, Any]:
    if not SCIENCE_LOUVAIN_ENABLED:
        return {"available": False, "status": "disabled", "reason": "SCIENCE_LOUVAIN_ENABLED is disabled"}
    if not LOUVAIN_AVAILABLE:
        return {"available": False, "status": "unavailable", "reason": "networkx with louvain_communities is not installed"}
    return {"available": True, "status": "available", "reason": "networkx louvain_communities is available"}


def build_louvain_network(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    include_artificial_edges: bool | None = None,
) -> tuple[Any, dict[str, dict[str, Any]], dict[str, Any]]:
    if not LOUVAIN_AVAILABLE:
        return None, {}, {"reason": "networkx is unavailable", "eligible_node_count": 0, "structural_edge_count": 0}
    include_artificial = (
        SCIENCE_LOUVAIN_INCLUDE_ARTIFICIAL_EDGES
        if include_artificial_edges is None
        else bool(include_artificial_edges)
    )
    max_nodes = max(3, int(SCIENCE_LOUVAIN_MAX_NODES))
    selected_nodes = [item for item in nodes if isinstance(item, dict) and str(item.get("node_id") or "")]
    selected_nodes.sort(key=lambda item: str(item.get("node_id") or ""))
    selected_nodes = selected_nodes[:max_nodes]
    node_attrs = {str(item["node_id"]): item for item in selected_nodes}
    graph = nx.Graph()
    structural_edge_count = 0
    ignored_artificial_edges = 0
    ignored_missing_node_edges = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("edge_type") == "artificial" and not include_artificial:
            ignored_artificial_edges += 1
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target or source == target or source not in node_attrs or target not in node_attrs:
            ignored_missing_node_edges += 1
            continue
        try:
            weight = float(edge.get("weight") or 0.0)
        except (TypeError, ValueError):
            weight = 0.0
        if weight <= 0:
            continue
        if graph.has_edge(source, target):
            graph[source][target]["weight"] += weight
            graph[source][target]["edge_count"] += 1
        else:
            graph.add_edge(source, target, weight=weight, edge_count=1)
        structural_edge_count += 1
    participating_nodes = set(graph.nodes())
    return graph, node_attrs, {
        "eligible_node_count": len(node_attrs),
        "structural_node_count": len(participating_nodes),
        "excluded_isolate_count": max(0, len(node_attrs) - len(participating_nodes)),
        "structural_edge_count": structural_edge_count,
        "unique_edge_count": graph.number_of_edges(),
        "ignored_artificial_edge_count": ignored_artificial_edges,
        "ignored_missing_node_edge_count": ignored_missing_node_edges,
        "include_artificial_edges": include_artificial,
    }


def identify_louvain_bridge_nodes(
    graph: Any,
    community_map: dict[str, int],
    threshold: float | None = None,
) -> list[dict[str, Any]]:
    if graph is None or not community_map:
        return []
    bridge_threshold = max(0.0, min(1.0, float(
        SCIENCE_LOUVAIN_BRIDGE_THRESHOLD if threshold is None else threshold
    )))
    try:
        betweenness = nx.betweenness_centrality(graph, normalized=True, weight=None)
    except Exception:
        betweenness = {node_id: 0.0 for node_id in graph.nodes()}
    bridges: list[dict[str, Any]] = []
    for node_id in graph.nodes():
        community = community_map.get(str(node_id))
        if community is None:
            continue
        total_weight = 0.0
        cross_weight = 0.0
        connected_communities: set[int] = set()
        for neighbor_id, attrs in graph[node_id].items():
            weight = max(0.0, float(attrs.get("weight") or 0.0))
            total_weight += weight
            neighbor_community = community_map.get(str(neighbor_id))
            if neighbor_community is not None and neighbor_community != community:
                cross_weight += weight
                connected_communities.add(neighbor_community)
        if total_weight <= 0 or not connected_communities:
            continue
        cross_ratio = cross_weight / total_weight
        diversity = len(connected_communities)
        bridge_score = min(
            1.0,
            0.65 * cross_ratio
            + 0.2 * min(1.0, diversity / 2.0)
            + 0.15 * float(betweenness.get(node_id) or 0.0),
        )
        if bridge_score < bridge_threshold:
            continue
        bridges.append(
            {
                "node_id": str(node_id),
                "community": community,
                "cross_edge_weight": round(cross_weight, 4),
                "total_edge_weight": round(total_weight, 4),
                "cross_ratio": round(cross_ratio, 4),
                "connected_communities": sorted(connected_communities),
                "community_diversity": diversity,
                "betweenness_centrality": round(float(betweenness.get(node_id) or 0.0), 4),
                "bridge_score": round(bridge_score, 4),
            }
        )
    bridges.sort(
        key=lambda item: (
            -float(item["bridge_score"]),
            -float(item["cross_edge_weight"]),
            str(item["node_id"]),
        )
    )
    return bridges


def summarize_louvain_communities(
    graph: Any,
    community_map: dict[str, int],
    node_attrs: dict[str, dict[str, Any]],
    bridge_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[int, list[str]] = defaultdict(list)
    for node_id, community_id in community_map.items():
        grouped[int(community_id)].append(str(node_id))
    bridge_by_community: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for bridge in bridge_nodes:
        bridge_by_community[int(bridge["community"])].append(bridge)
    summaries: list[dict[str, Any]] = []
    for community_id, member_ids in grouped.items():
        fields = Counter(
            str(node_attrs.get(node_id, {}).get("field") or "unknown")
            for node_id in member_ids
        )
        internal_weight = 0.0
        external_weight = 0.0
        for source, target, attrs in graph.edges(member_ids, data=True):
            weight = max(0.0, float(attrs.get("weight") or 0.0))
            if community_map.get(str(source)) == community_map.get(str(target)) == community_id:
                internal_weight += weight
            elif community_map.get(str(source)) == community_id or community_map.get(str(target)) == community_id:
                external_weight += weight
        ranked_members = sorted(
            (node_attrs.get(node_id, {}) for node_id in member_ids),
            key=lambda item: (
                -float(item.get("publication_quality_score") or 0.0),
                -float(item.get("relevance_score") or 0.0),
                str(item.get("title") or ""),
            ),
        )
        summaries.append(
            {
                "community_id": community_id,
                "size": len(member_ids),
                "primary_field": fields.most_common(1)[0][0] if fields else "unknown",
                "field_distribution": dict(fields),
                "internal_edge_weight": round(internal_weight, 4),
                "external_edge_weight": round(external_weight, 4),
                "bridge_count": len(bridge_by_community.get(community_id, [])),
                "bridge_nodes": bridge_by_community.get(community_id, [])[:5],
                "top_nodes": [
                    {
                        "node_id": str(item.get("node_id") or ""),
                        "title": str(item.get("title") or ""),
                        "quality_score": round(float(item.get("publication_quality_score") or 0.0), 4),
                    }
                    for item in ranked_members[:5]
                ],
            }
        )
    summaries.sort(key=lambda item: (-int(item["size"]), int(item["community_id"])))
    return summaries


def assess_louvain_topic_drift(
    graph: Any,
    community_map: dict[str, int],
    node_attrs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if graph is None or not community_map:
        return []
    seed_node_id = next(
        (
            str(node_id)
            for node_id, attrs in node_attrs.items()
            if str(attrs.get("role") or "") == "seed" and str(node_id) in graph
        ),
        "",
    )
    seed_community = community_map.get(seed_node_id) if seed_node_id else None
    grouped: dict[int, list[str]] = defaultdict(list)
    for node_id, community_id in community_map.items():
        grouped[int(community_id)].append(str(node_id))
    assessments: list[dict[str, Any]] = []
    for community_id, member_ids in grouped.items():
        relevance_values = [
            float(node_attrs.get(node_id, {}).get("relevance_score") or 0.0)
            for node_id in member_ids
        ]
        quality_values = [
            float(node_attrs.get(node_id, {}).get("publication_quality_score") or 0.0)
            for node_id in member_ids
        ]
        has_seed = community_id == seed_community
        connected_to_seed = bool(
            seed_node_id
            and any(nx.has_path(graph, seed_node_id, node_id) for node_id in member_ids)
        )
        external_weight = 0.0
        total_weight = 0.0
        for node_id in member_ids:
            for neighbor_id, edge_attrs in graph[node_id].items():
                weight = max(0.0, float(edge_attrs.get("weight") or 0.0))
                total_weight += weight
                if community_map.get(str(neighbor_id)) != community_id:
                    external_weight += weight
        external_ratio = external_weight / total_weight if total_weight else 0.0
        average_relevance = sum(relevance_values) / max(1, len(relevance_values))
        average_quality = sum(quality_values) / max(1, len(quality_values))
        if has_seed:
            disposition = "core"
            priority = "high"
        elif not connected_to_seed:
            disposition = "disconnected_review"
            priority = "review"
        elif len(member_ids) <= 2 and external_ratio <= 0.18 and average_relevance < 0.4:
            disposition = "weakly_attached_review"
            priority = "low"
        else:
            disposition = "connected"
            priority = "normal"
        assessments.append(
            {
                "community_id": community_id,
                "size": len(member_ids),
                "contains_seed": has_seed,
                "connected_to_seed": connected_to_seed,
                "external_edge_ratio": round(external_ratio, 4),
                "average_relevance": round(average_relevance, 4),
                "average_quality": round(average_quality, 4),
                "disposition": disposition,
                "priority": priority,
                "node_ids": sorted(member_ids),
            }
        )
    assessments.sort(key=lambda item: (0 if item["contains_seed"] else 1, int(item["community_id"])))
    return assessments


def run_louvain_community_analysis(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    resolution: float | None = None,
    include_artificial_edges: bool | None = None,
) -> dict[str, Any]:
    dependency = louvain_dependency_status()
    base = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "community_map": {},
        "communities": [],
        "bridge_nodes": [],
        "has_bridges": False,
    }
    if not dependency["available"]:
        return {**base, "status": dependency["status"], "reason": dependency["reason"]}
    graph, node_attrs, graph_report = build_louvain_network(
        nodes,
        edges,
        include_artificial_edges=include_artificial_edges,
    )
    base.update(graph_report)
    if graph is None or graph.number_of_nodes() < 3 or graph.number_of_edges() < 2:
        return {
            **base,
            "status": "insufficient_structure",
            "reason": "Louvain requires at least three structurally connected papers and two relation edges.",
        }
    used_resolution = max(0.1, min(5.0, float(
        SCIENCE_LOUVAIN_RESOLUTION if resolution is None else resolution
    )))
    try:
        raw_communities = nx.algorithms.community.louvain_communities(
            graph,
            weight="weight",
            resolution=used_resolution,
            seed=42,
        )
        ordered_communities = sorted(
            (sorted(str(node_id) for node_id in members) for members in raw_communities),
            key=lambda members: (-len(members), members[0] if members else ""),
        )
        community_map = {
            node_id: community_id
            for community_id, members in enumerate(ordered_communities)
            for node_id in members
        }
        modularity = float(nx.algorithms.community.modularity(
            graph,
            [set(members) for members in ordered_communities],
            weight="weight",
            resolution=used_resolution,
        ))
    except Exception as exc:
        components = [sorted(str(node_id) for node_id in members) for members in nx.connected_components(graph)]
        ordered_components = sorted(components, key=lambda members: (-len(members), members[0] if members else ""))
        community_map = {
            node_id: component_id
            for component_id, members in enumerate(ordered_components)
            for node_id in members
        }
        bridges = identify_louvain_bridge_nodes(graph, community_map)
        return {
            **base,
            "status": "fallback_components",
            "reason": f"Louvain failed: {str(exc)[:240]}",
            "resolution_used": used_resolution,
            "community_map": community_map,
            "num_communities": len(ordered_components),
            "communities": summarize_louvain_communities(graph, community_map, node_attrs, bridges),
            "bridge_nodes": bridges[:20],
            "has_bridges": bool(bridges),
        }
    bridges = identify_louvain_bridge_nodes(graph, community_map)
    summaries = summarize_louvain_communities(graph, community_map, node_attrs, bridges)
    drift_assessment = assess_louvain_topic_drift(graph, community_map, node_attrs)
    return {
        **base,
        "status": "success",
        "resolution_used": used_resolution,
        "modularity": round(modularity, 6),
        "num_communities": len(ordered_communities),
        "community_map": community_map,
        "communities": summaries,
        "topic_drift_assessment": drift_assessment,
        "outlier_communities": [
            item for item in drift_assessment
            if item.get("disposition") in {"disconnected_review", "weakly_attached_review"}
        ],
        "bridge_nodes": bridges[:20],
        "has_bridges": bool(bridges),
    }


def louvain_recommended_expansion_seeds(
    nodes: list[dict[str, Any]],
    louvain_analysis: dict[str, Any],
    limit: int = 5,
) -> list[dict[str, Any]]:
    by_node_id = {
        str(node.get("node_id") or ""): node
        for node in nodes
        if isinstance(node, dict) and str(node.get("node_id") or "")
    }
    recommendations: list[dict[str, Any]] = []
    for bridge in louvain_analysis.get("bridge_nodes", []):
        if not isinstance(bridge, dict):
            continue
        node = by_node_id.get(str(bridge.get("node_id") or ""))
        if not node:
            continue
        recommendations.append(
            {
                "node_id": str(node.get("node_id") or ""),
                "title": str(node.get("title") or ""),
                "community": bridge.get("community"),
                "connected_communities": bridge.get("connected_communities", []),
                "bridge_score": bridge.get("bridge_score", 0.0),
                "publication_quality_score": node.get("publication_quality_score", 0.0),
                "relevance_score": node.get("relevance_score", 0.0),
                "semantic_scholar_id": node.get("semantic_scholar_id"),
            }
        )
    recommendations.sort(
        key=lambda item: (
            -float(item.get("bridge_score") or 0.0),
            -float(item.get("publication_quality_score") or 0.0),
            -float(item.get("relevance_score") or 0.0),
            str(item.get("node_id") or ""),
        )
    )
    return recommendations[: max(0, int(limit))]


def annotate_expansion_results_with_louvain(
    seed: dict[str, Any],
    results: list[dict[str, Any]],
    query: str,
) -> dict[str, Any]:
    try:
        from ._literature_search import literature_result_unique_key
    except ImportError:
        from _literature_search import literature_result_unique_key
    seed_node = relation_graph_node(seed, query, role="seed")
    nodes = {str(seed_node["node_id"]): seed_node}
    result_node_ids: dict[str, str] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        node = relation_graph_node(result, query, role="paper")
        node_id = str(node["node_id"])
        nodes[node_id] = node
        result_node_ids[literature_result_unique_key(result)] = node_id
    edges: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        result_key = literature_result_unique_key(result)
        node_id = result_node_ids.get(result_key)
        if not node_id:
            continue
        parent_id = result_node_ids.get(str(result.get("graph_parent_key") or "")) or str(seed_node["node_id"])
        edge = relation_graph_edge(parent_id, node_id, str(result.get("graph_relation") or "search_result"), result)
        if edge:
            edges.append(edge)
    analysis = run_louvain_community_analysis(list(nodes.values()), edges)
    community_map = analysis.get("community_map") if isinstance(analysis.get("community_map"), dict) else {}
    bridge_scores = {
        str(bridge.get("node_id") or ""): bridge
        for bridge in analysis.get("bridge_nodes", [])
        if isinstance(bridge, dict)
    }
    drift_by_community = {
        int(item.get("community_id")): item
        for item in analysis.get("topic_drift_assessment", [])
        if isinstance(item, dict) and item.get("community_id") is not None
    }
    for result in results:
        if not isinstance(result, dict):
            continue
        node_id = result_node_ids.get(literature_result_unique_key(result))
        if not node_id:
            continue
        if node_id in community_map:
            result["louvain_community"] = community_map[node_id]
            drift = drift_by_community.get(int(community_map[node_id]))
            if drift:
                result["louvain_community_disposition"] = drift.get("disposition")
                result["louvain_priority"] = drift.get("priority")
        bridge = bridge_scores.get(node_id)
        if bridge:
            result["louvain_bridge_score"] = bridge.get("bridge_score", 0.0)
            result["louvain_connected_communities"] = bridge.get("connected_communities", [])
    return analysis

def build_literature_relation_graph(
    search_id: str,
    query: str = "",
    max_nodes: int = 80,
    min_quality: float = 0.0,
    max_clusters: int = 8,
    run_louvain: bool = True,
    louvain_resolution: float | None = None,
) -> str:
    try:
        from ._literature_scoring import publication_quality_assessment
        from ._literature_search import literature_result_unique_key
        from ._project import load_search, save_search
        from ._utils import clamp_int, new_id
    except ImportError:
        from _literature_scoring import publication_quality_assessment
        from _literature_search import literature_result_unique_key
        from _project import load_search, save_search
        from _utils import clamp_int, new_id
    search = load_search(search_id)
    raw_results = [item for item in search.get("results", []) if isinstance(item, dict)]
    limit = clamp_int(max_nodes, 1, 200)
    query_text = query or str(search.get("query", ""))
    filtered = [
        item
        for item in raw_results
        if float(item.get("publication_quality_score") or publication_quality_assessment(item)["quality_score"]) >= float(min_quality or 0.0)
    ][:limit]

    seed = relation_graph_seed(search)
    nodes: dict[str, dict[str, Any]] = {}
    if seed:
        seed_node = relation_graph_node(seed, query_text, role="seed")
        nodes[seed_node["node_id"]] = seed_node
        seed_id = seed_node["node_id"]
    else:
        seed_id = "seed"
        nodes[seed_id] = {
            "node_id": seed_id,
            "role": "seed",
            "title": search.get("seed_title") or "Seed paper",
            "year": "",
            "venue": "",
            "field": "general",
            "mechanism_terms": [],
            "relevance_score": 0.0,
            "publication_quality_score": 1.0,
            "venue_quality": "",
            "journal_quartile": "",
            "citation_count": 0,
            "quality_flags": [],
        }

    result_node_ids: dict[str, str] = {}
    for result in filtered:
        node = relation_graph_node(result, query_text, role="paper")
        nodes[node["node_id"]] = node
        result_node_ids[literature_result_unique_key(result)] = node["node_id"]

    edges: list[dict[str, Any]] = []
    for result in filtered:
        node_id = result_node_ids.get(literature_result_unique_key(result))
        if not node_id:
            continue
        parent_id = result_node_ids.get(str(result.get("graph_parent_key") or "")) or seed_id
        relation = str(result.get("graph_relation") or "search_result")
        edge = relation_graph_edge(parent_id, node_id, relation, result)
        if edge:
            edges.append(edge)

    louvain_analysis = (
        run_louvain_community_analysis(
            list(nodes.values()),
            edges,
            resolution=louvain_resolution,
        )
        if run_louvain
        else {"status": "not_run", "reason": "run_louvain=False", "community_map": {}, "bridge_nodes": []}
    )
    louvain_map = louvain_analysis.get("community_map") if isinstance(louvain_analysis.get("community_map"), dict) else {}
    louvain_bridges = {
        str(bridge.get("node_id") or ""): bridge
        for bridge in louvain_analysis.get("bridge_nodes", [])
        if isinstance(bridge, dict)
    }
    drift_by_community = {
        int(item.get("community_id")): item
        for item in louvain_analysis.get("topic_drift_assessment", [])
        if isinstance(item, dict) and item.get("community_id") is not None
    }
    for node_id, node in nodes.items():
        if node_id in louvain_map:
            node["louvain_community"] = louvain_map[node_id]
            drift = drift_by_community.get(int(louvain_map[node_id]))
            if drift:
                node["louvain_community_disposition"] = drift.get("disposition")
                node["louvain_priority"] = drift.get("priority")
        if node_id in louvain_bridges:
            node["louvain_bridge_score"] = louvain_bridges[node_id].get("bridge_score", 0.0)
            node["louvain_connected_communities"] = louvain_bridges[node_id].get("connected_communities", [])

    clusters = build_mechanism_clusters(list(nodes.values()), edges, max_clusters=max_clusters)
    edge_summary = summarize_relation_edges(edges)
    edge_summary["louvain"] = {
        "status": louvain_analysis.get("status", "not_run"),
        "structural_node_count": louvain_analysis.get("structural_node_count", 0),
        "unique_edge_count": louvain_analysis.get("unique_edge_count", 0),
        "ignored_artificial_edge_count": louvain_analysis.get("ignored_artificial_edge_count", 0),
        "num_communities": louvain_analysis.get("num_communities", 0),
        "modularity": louvain_analysis.get("modularity"),
        "bridge_count": len(louvain_analysis.get("bridge_nodes", [])),
        "outlier_community_count": len(louvain_analysis.get("outlier_communities", [])),
    }
    community_summary = graph_community_summary(filtered)
    fallback_used = bool(search.get("fallback_used")) or any(edge.get("edge_type") == "artificial" for edge in edges)
    analysis_confidence = 0.65 if fallback_used else 1.0
    pagerank = compute_pagerank(list(nodes), edges)
    degree = compute_graph_degree(list(nodes), edges)
    for node_id, node in nodes.items():
        node["pagerank"] = round(pagerank.get(node_id, 0.0), 6)
        node["degree_centrality"] = round(degree.get(node_id, 0.0), 6)
        node["centrality_score"] = round(0.7 * pagerank.get(node_id, 0.0) + 0.3 * degree.get(node_id, 0.0), 6)

    ranked_nodes = sorted(
        nodes.values(),
        key=lambda item: (
            -float(item.get("centrality_score", 0.0)),
            -float(item.get("publication_quality_score", 0.0)),
            -float(item.get("relevance_score", 0.0)),
        ),
    )
    louvain_seed_recommendations = louvain_recommended_expansion_seeds(ranked_nodes, louvain_analysis)
    graph_id = new_id("relgraph")
    record = {
        "search_id": graph_id,
        "kind": "paper_relation_graph",
        "source_search_id": search_id,
        "query": query_text,
        "createdAt": time.time(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "cluster_count": len(clusters),
        "max_clusters": clamp_int(max_clusters, 1, 30),
        "fallback_used": fallback_used,
        "analysis_confidence": analysis_confidence,
        "edge_summary": edge_summary,
        "community_summary": community_summary,
        "louvain_analysis": louvain_analysis,
        "louvain_seed_recommendations": louvain_seed_recommendations,
        "nodes": ranked_nodes,
        "edges": edges,
        "clusters": clusters,
        "central_papers": [summarize_relation_node(item) for item in ranked_nodes[:10]],
        "mechanism_lineage": summarize_mechanism_lineage(clusters),
    }
    save_search({"search_id": graph_id, **record, "total_results": len(ranked_nodes), "results": ranked_nodes})
    log_event(
        "SCIENCE",
        "relation_graph_built",
        source_search_id=search_id,
        graph_id=graph_id,
        nodes=len(nodes),
        edges=len(edges),
        clusters=len(clusters),
        louvain_status=louvain_analysis.get("status", "not_run"),
        louvain_communities=louvain_analysis.get("num_communities", 0),
        louvain_modularity=louvain_analysis.get("modularity"),
    )
    response = {
        "relation_graph_id": graph_id,
        "source_search_id": search_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "cluster_count": len(clusters),
        "max_clusters": clamp_int(max_clusters, 1, 30),
        "fallback_used": fallback_used,
        "analysis_confidence": analysis_confidence,
        "edge_summary": edge_summary,
        "community_summary": community_summary,
        "louvain_analysis": {
            "status": louvain_analysis.get("status", "not_run"),
            "reason": louvain_analysis.get("reason", ""),
            "resolution_used": louvain_analysis.get("resolution_used"),
            "structural_node_count": louvain_analysis.get("structural_node_count", 0),
            "unique_edge_count": louvain_analysis.get("unique_edge_count", 0),
            "excluded_isolate_count": louvain_analysis.get("excluded_isolate_count", 0),
            "ignored_artificial_edge_count": louvain_analysis.get("ignored_artificial_edge_count", 0),
            "num_communities": louvain_analysis.get("num_communities", 0),
            "modularity": louvain_analysis.get("modularity"),
            "has_bridges": bool(louvain_analysis.get("has_bridges")),
            "outlier_communities": [
                {
                    "community_id": item.get("community_id"),
                    "size": item.get("size"),
                    "disposition": item.get("disposition"),
                    "priority": item.get("priority"),
                }
                for item in louvain_analysis.get("outlier_communities", [])
                if isinstance(item, dict)
            ],
            "communities": [
                {
                    "community_id": item.get("community_id"),
                    "size": item.get("size"),
                    "primary_field": item.get("primary_field"),
                    "bridge_count": item.get("bridge_count"),
                }
                for item in louvain_analysis.get("communities", [])
                if isinstance(item, dict)
            ],
            "bridge_nodes": louvain_seed_recommendations,
        },
        "central_papers": record["central_papers"],
        "clusters": clusters,
        "mechanism_lineage": record["mechanism_lineage"],
        "next_step": "Use central_papers for high-trust seeds, louvain_analysis.bridge_nodes for cross-community expansion, clusters for mechanism lineage, and edges/citation_contexts for claim-citation verification.",
    }
    return json.dumps(response, ensure_ascii=False, indent=2)

def relation_graph_seed(search: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._project import load_search
        from ._utils import normalize_space
    except ImportError:
        from _project import load_search
        from _utils import normalize_space
    seed_search_id = str(search.get("seed_search_id") or "")
    if seed_search_id:
        try:
            seed_search = load_search(seed_search_id)
            seed_index = int(search.get("seed_result_index") or 0)
            seed = seed_search.get("results", [])[seed_index]
            if isinstance(seed, dict):
                return seed
        except Exception:
            pass
    seed_title = normalize_space(search.get("seed_title", ""))
    if seed_title:
        return {"title": seed_title, "venue": "", "year": "", "provider": "seed_metadata"}
    return {}

def relation_graph_node(result: dict[str, Any], query: str, role: str = "paper") -> dict[str, Any]:
    try:
        from ._literature_scoring import publication_quality_assessment
        from ._literature_search import literature_result_unique_key
        from ._utils import normalize_key, numeric_value
    except ImportError:
        from _literature_scoring import publication_quality_assessment
        from _literature_search import literature_result_unique_key
        from _utils import normalize_key, numeric_value
    quality = publication_quality_assessment(result)
    terms = mechanism_terms(result, query)
    node_key = literature_result_unique_key(result)
    node_id = normalize_key(node_key)[:80]
    return {
        "node_id": node_id,
        "role": role,
        "result_index": result.get("result_index"),
        "title": result.get("title"),
        "year": result.get("year"),
        "venue": result.get("venue"),
        "field": quality["inferred_field"],
        "community": infer_literature_community(result),
        "mechanism_terms": terms,
        "mechanism_cluster_key": mechanism_cluster_key(quality["inferred_field"], terms),
        "relevance_score": result.get("relevance_score", 0.0),
        "publication_quality_score": result.get("publication_quality_score", quality["quality_score"]),
        "venue_quality": result.get("venue_quality", quality["venue_quality"]),
        "journal_quartile": result.get("journal_quartile", quality["journal_quartile"]),
        "citation_count": numeric_value(result.get("citation_count")),
        "influential_citation_count": numeric_value(result.get("influential_citation_count")),
        "quality_flags": result.get("quality_flags", quality["flags"]),
        "doi": result.get("doi"),
        "arxiv_id": result.get("arxiv_id"),
        "semantic_scholar_id": result.get("semantic_scholar_id"),
        "url": result.get("url"),
    }

def relation_graph_edge(parent_id: str, node_id: str, relation: str, result: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._utils import normalize_key, scalar, trim_text
    except ImportError:
        from _utils import normalize_key, scalar, trim_text
    if node_id == parent_id:
        return {}
    normalized = normalize_key(relation)
    is_bridge = bool(result.get("graph_cross_community_bridge")) or normalized.startswith("cross_community_bridge_")
    base_relation = normalized.removeprefix("cross_community_bridge_")
    is_second_layer = base_relation.startswith("second_layer_") or normalized.startswith("cross_community_bridge_")
    base_relation = base_relation.removeprefix("second_layer_")
    is_artificial = base_relation in {"keyword_fallback", "search_result", "cross_community_search"}
    weight = {
        "reference": 1.0,
        "citation": 1.0,
        "keyword_fallback": 0.08,
        "search_result": 0.06,
        "cross_community_search": 0.12,
    }.get(base_relation, 0.4)
    if is_second_layer and not is_artificial:
        weight *= 0.65
    if is_bridge and not is_artificial:
        weight = min(1.0, weight + max(0.0, float(SCIENCE_CROSS_COMMUNITY_EDGE_BONUS)))
    if base_relation == "reference":
        source, target = parent_id, node_id
    elif base_relation == "citation":
        source, target = node_id, parent_id
    else:
        source, target = parent_id, node_id
    contexts = [trim_text(scalar(item), 260) for item in (result.get("citation_contexts") or []) if scalar(item)]
    parent_community = str(result.get("graph_parent_community") or "")
    child_community = str(result.get("graph_community") or infer_literature_community(result))
    edge = {
        "source": source,
        "target": target,
        "relation": normalized,
        "base_relation": base_relation,
        "edge_type": "artificial" if is_artificial else "citation_graph",
        "expanded_depth": 2 if is_second_layer else int(result.get("expanded_depth") or 1),
        "weight": round(weight, 4),
        "citation_contexts": contexts[:3],
        "citation_intents": result.get("citation_intents") or [],
        "is_influential": bool(result.get("edge_is_influential")),
        "parent_title": result.get("graph_parent_title", ""),
        "manual_connection": is_artificial,
    }
    if is_bridge:
        edge["is_cross_community_bridge"] = True
        edge["bridge_communities"] = str(result.get("graph_bridge_communities") or f"{parent_community}->{child_community}")
        edge["parent_community"] = parent_community
        edge["child_community"] = child_community
    return edge

def mechanism_terms(result: dict[str, Any], query: str = "", limit: int = 6) -> list[str]:
    try:
        from ._literature_search import query_terms
        from ._utils import normalize_space, scalar, unique_preserve_order
    except ImportError:
        from _literature_search import query_terms
        from _utils import normalize_space, scalar, unique_preserve_order
    text = " ".join(
        normalize_space(result.get(key, "")).lower()
        for key in ("title", "abstract", "venue")
    )
    contexts = " ".join(scalar(item).lower() for item in (result.get("citation_contexts") or []))
    text = f"{text} {contexts}"
    vocab = [
        "adaptation",
        "analysis",
        "architecture",
        "attribution",
        "causality",
        "classification",
        "control",
        "coupling",
        "decomposition",
        "degradation",
        "discovery",
        "dynamics",
        "efficiency",
        "evaluation",
        "feedback",
        "generalization",
        "heterogeneity",
        "inference",
        "interaction",
        "interface",
        "measurement",
        "mechanism",
        "model",
        "optimization",
        "prediction",
        "reconstruction",
        "response",
        "robustness",
        "scalability",
        "screening",
        "sensitivity",
        "simulation",
        "stability",
        "structure",
        "transfer",
        "uncertainty",
        "validation",
        "planning",
        "workflow",
    ]
    hits = [term for term in vocab if term in text]
    query_hits = [term for term in query_terms(query) if term in text]
    if len(hits) + len(query_hits) < limit:
        words = [
            word
            for word in re.findall(r"[a-z][a-z0-9-]{3,}", text)
            if word not in set(query_terms("")) and word not in {"paper", "study", "using", "based", "with", "from", "this", "that"}
        ]
        common = [word for word, _ in Counter(words).most_common(limit * 2)]
    else:
        common = []
    return unique_preserve_order(hits + query_hits + common)[:limit]

def mechanism_cluster_key(field: str, terms: list[str]) -> str:
    if terms:
        return f"{field}:{terms[0]}"
    return f"{field}:general"

def build_mechanism_clusters(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], max_clusters: int = 8) -> list[dict[str, Any]]:
    try:
        from ._utils import normalize_key
    except ImportError:
        from _utils import normalize_key
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        if node.get("role") == "seed":
            continue
        grouped[str(node.get("mechanism_cluster_key") or "general:unknown")].append(node)
    grouped = merge_sparse_mechanism_groups(grouped, max_clusters=max_clusters)
    incoming = Counter(edge["target"] for edge in edges)
    outgoing = Counter(edge["source"] for edge in edges)
    artificial_nodes = {
        edge["target"]
        for edge in edges
        if edge.get("edge_type") == "artificial"
    } | {
        edge["source"]
        for edge in edges
        if edge.get("edge_type") == "artificial"
    }
    clusters: list[dict[str, Any]] = []
    for key, members in grouped.items():
        field, _, mechanism = key.partition(":")
        central = sorted(
            members,
            key=lambda item: (
                -(incoming[item["node_id"]] + outgoing[item["node_id"]]),
                -float(item.get("publication_quality_score", 0.0)),
                -float(item.get("relevance_score", 0.0)),
            ),
        )[:5]
        flags = sorted({flag for item in members for flag in item.get("quality_flags", [])})
        artificial_count = sum(1 for item in members if item.get("node_id") in artificial_nodes)
        clusters.append(
            {
                "cluster_id": normalize_key(key),
                "field": field or "general",
                "mechanism": mechanism or "general",
                "size": len(members),
                "merged_singletons": any(bool(item.get("merged_from_singleton")) for item in members),
                "artificial_connection_count": artificial_count,
                "connection_confidence": round(1.0 - (artificial_count / max(1, len(members))) * 0.6, 4),
                "avg_quality": round(sum(float(item.get("publication_quality_score", 0.0)) for item in members) / max(1, len(members)), 4),
                "avg_relevance": round(sum(float(item.get("relevance_score", 0.0)) for item in members) / max(1, len(members)), 4),
                "quality_flags": flags[:8],
                "representative_papers": [summarize_relation_node(item) for item in central],
            }
        )
    clusters.sort(key=lambda item: (-int(item["size"]), -float(item["avg_quality"]), item["cluster_id"]))
    return clusters

def merge_sparse_mechanism_groups(
    grouped: dict[str, list[dict[str, Any]]],
    max_clusters: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    try:
        from ._utils import clamp_int
    except ImportError:
        from _utils import clamp_int
    target = clamp_int(max_clusters, 1, 30)
    if len(grouped) <= target:
        return grouped
    merged: dict[str, list[dict[str, Any]]] = {key: list(value) for key, value in grouped.items()}
    singleton_keys = [key for key, members in merged.items() if len(members) == 1]
    for key in singleton_keys:
        if len(merged) <= target:
            break
        members = merged.pop(key, [])
        if not members:
            continue
        parent_key = nearest_mechanism_parent_key(key, members[0], merged)
        for member in members:
            member["merged_from_singleton"] = key
        merged[parent_key].extend(members)

    while len(merged) > target:
        smallest_key = min(merged, key=lambda item: (len(merged[item]), item))
        members = merged.pop(smallest_key)
        if not members:
            continue
        parent_key = nearest_mechanism_parent_key(smallest_key, members[0], merged)
        for member in members:
            member["merged_from_singleton"] = smallest_key
        merged[parent_key].extend(members)
    return merged

def nearest_mechanism_parent_key(
    source_key: str,
    node: dict[str, Any],
    grouped: dict[str, list[dict[str, Any]]],
) -> str:
    field, _, _ = source_key.partition(":")
    node_terms = set(node.get("mechanism_terms") or [])
    candidates: list[tuple[float, str]] = []
    for key, members in grouped.items():
        candidate_field, _, _ = key.partition(":")
        if candidate_field != field:
            continue
        term_sets = [set(item.get("mechanism_terms") or []) for item in members]
        overlap = max((len(node_terms & terms) for terms in term_sets), default=0)
        size_bonus = min(3, len(members)) * 0.1
        candidates.append((overlap + size_bonus, key))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]
    parent_key = f"{field or 'general'}:mixed"
    grouped.setdefault(parent_key, [])
    return parent_key

def compute_pagerank(node_ids: list[str], edges: list[dict[str, Any]], damping: float = 0.85, iterations: int = 30) -> dict[str, float]:
    try:
        from ._utils import unique_preserve_order
    except ImportError:
        from _utils import unique_preserve_order
    ids = unique_preserve_order(node_ids)
    if not ids:
        return {}
    outgoing: dict[str, list[tuple[str, float]]] = {node_id: [] for node_id in ids}
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source in outgoing and target in outgoing:
            outgoing[source].append((target, max(0.01, float(edge.get("weight") or 1.0))))
    n = len(ids)
    rank = {node_id: 1.0 / n for node_id in ids}
    base = (1.0 - damping) / n
    for _ in range(iterations):
        new_rank = {node_id: base for node_id in ids}
        dangling = sum(rank[node_id] for node_id in ids if not outgoing[node_id])
        dangling_share = damping * dangling / n
        for node_id in ids:
            new_rank[node_id] += dangling_share
        for source, targets in outgoing.items():
            total_weight = sum(weight for _, weight in targets)
            if total_weight <= 0:
                continue
            for target, weight in targets:
                new_rank[target] += damping * rank[source] * (weight / total_weight)
        rank = new_rank
    total = sum(rank.values()) or 1.0
    return {node_id: value / total for node_id, value in rank.items()}

def compute_graph_degree(node_ids: list[str], edges: list[dict[str, Any]]) -> dict[str, float]:
    try:
        from ._utils import unique_preserve_order
    except ImportError:
        from _utils import unique_preserve_order
    ids = unique_preserve_order(node_ids)
    degree = {node_id: 0.0 for node_id in ids}
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        weight = max(0.01, float(edge.get("weight") or 1.0))
        if source in degree:
            degree[source] += weight
        if target in degree:
            degree[target] += weight
    max_degree = max(degree.values(), default=0.0)
    if max_degree <= 0:
        return degree
    return {node_id: value / max_degree for node_id, value in degree.items()}

def summarize_relation_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id"),
        "title": node.get("title"),
        "year": node.get("year"),
        "venue": node.get("venue"),
        "field": node.get("field"),
        "mechanism_terms": node.get("mechanism_terms", []),
        "pagerank": node.get("pagerank"),
        "degree_centrality": node.get("degree_centrality"),
        "centrality_score": node.get("centrality_score"),
        "publication_quality_score": node.get("publication_quality_score"),
        "relevance_score": node.get("relevance_score"),
        "quality_flags": node.get("quality_flags", []),
    }

def summarize_relation_edges(edges: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = Counter(str(edge.get("edge_type") or "unknown") for edge in edges)
    by_relation = Counter(str(edge.get("relation") or "unknown") for edge in edges)
    depths = Counter(str(edge.get("expanded_depth") or 1) for edge in edges)
    return {
        "total_edges": len(edges),
        "citation_graph_edges": by_type.get("citation_graph", 0),
        "artificial_edges": by_type.get("artificial", 0),
        "cross_community_bridges": sum(1 for edge in edges if edge.get("is_cross_community_bridge")),
        "by_relation": dict(sorted(by_relation.items())),
        "by_depth": dict(sorted(depths.items())),
        "fallback_weight_policy": "keyword_fallback/search_result edges are artificial and use very low PageRank weight.",
    }

def summarize_mechanism_lineage(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lineage: list[dict[str, Any]] = []
    for cluster in clusters[:12]:
        representatives = cluster.get("representative_papers", [])
        lineage.append(
            {
                "mechanism": cluster.get("mechanism"),
                "field": cluster.get("field"),
                "paper_count": cluster.get("size"),
                "avg_quality": cluster.get("avg_quality"),
                "representative_titles": [item.get("title") for item in representatives[:3]],
                "interpretation": (
                    f"{cluster.get('field')} lineage centered on {cluster.get('mechanism')} "
                    f"with {cluster.get('size')} papers; inspect representative_papers before importing claims."
                ),
            }
        )
    return lineage

