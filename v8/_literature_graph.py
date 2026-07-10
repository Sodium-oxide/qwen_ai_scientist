from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote, urlencode
import ast
import json
import re
import time

try:
    from .config import (
        SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT,
        SEMANTIC_SCHOLAR_API_KEY,
    )
    from .log import log_event
except ImportError:
    from config import (
        SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT,
        SEMANTIC_SCHOLAR_API_KEY,
    )
    from log import log_event



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
    if selected_depth >= 2 and ranked:
        second_layer_results = expand_second_layer_graph_results(
            ranked,
            graph_query,
            edge_kinds,
            max_results=max_results,
            top_k=second_layer_top_k,
            errors=errors,
        )
        second_layer_count = len(second_layer_results)
        if second_layer_results:
            graph_results = dedupe_literature_results(graph_results + second_layer_results)
            ranked = rank_literature_results(graph_query, graph_results)[: clamp_int(max_results, 1, 200)]

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
    graph_search_id = new_id("graph")
    for index, item in enumerate(ranked):
        item["result_index"] = index
        item["search_id"] = graph_search_id
        item["expanded_from_search_id"] = search_id
        item["expanded_from_result_index"] = result_index
        item["seed_title"] = seed.get("title", "")

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
        "total_results": len(ranked),
        "selected": summarize_literature_result(selected) if selected else None,
        "top_results": [summarize_literature_result(item) for item in ranked[:10]],
        "llm_judgement": llm_judgement,
        "errors": errors,
        "fallback_used": fallback_used,
        "seed_not_indexed": seed_not_indexed,
        "next_step": "Use select_literature_result(graph_search_id) or import_literature_search_result(project_id, graph_search_id, result_index).",
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
) -> list[dict[str, Any]]:
    try:
        from ._literature_search import dedupe_literature_results, is_semantic_scholar_not_found_error, is_semantic_scholar_rate_limit_error, literature_result_unique_key, rank_literature_results
        from ._utils import clamp_int, normalize_key
    except ImportError:
        from _literature_search import dedupe_literature_results, is_semantic_scholar_not_found_error, is_semantic_scholar_rate_limit_error, literature_result_unique_key, rank_literature_results
        from _utils import clamp_int, normalize_key
    seeds = select_second_layer_seeds(first_layer_ranked, top_k=top_k)
    if not seeds:
        return []
    per_edge_limit = min(
        max(1, int(SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT)),
        max(1, clamp_int(max_results, 1, 200) // max(1, len(seeds) * max(1, len(edge_kinds)))),
    )
    expanded: list[dict[str, Any]] = []
    for parent in seeds:
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
                result["graph_relation"] = f"second_layer_{result.get('graph_relation') or normalize_key(edge_kind)}"
                result["graph_parent_key"] = parent_key
                result["graph_parent_title"] = parent.get("title", "")
                result["graph_parent_result_index"] = parent.get("result_index")
                result["expanded_depth"] = 2
                expanded.append(result)
    seed_keys = {literature_result_unique_key(item) for item in first_layer_ranked}
    expanded = [item for item in expanded if literature_result_unique_key(item) not in seed_keys]
    ranked = rank_literature_results(query, dedupe_literature_results(expanded))
    return ranked

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

def build_literature_relation_graph(
    search_id: str,
    query: str = "",
    max_nodes: int = 80,
    min_quality: float = 0.0,
    max_clusters: int = 8,
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

    clusters = build_mechanism_clusters(list(nodes.values()), edges, max_clusters=max_clusters)
    edge_summary = summarize_relation_edges(edges)
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
        "central_papers": record["central_papers"],
        "clusters": clusters,
        "mechanism_lineage": record["mechanism_lineage"],
        "next_step": "Use central_papers for high-trust seeds, clusters for mechanism lineage, and edges/citation_contexts for claim-citation verification.",
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
    base_relation = normalized.removeprefix("second_layer_")
    is_second_layer = normalized.startswith("second_layer_")
    is_artificial = base_relation in {"keyword_fallback", "search_result"}
    weight = {
        "reference": 1.0,
        "citation": 1.0,
        "keyword_fallback": 0.08,
        "search_result": 0.06,
    }.get(base_relation, 0.4)
    if is_second_layer and not is_artificial:
        weight *= 0.65
    if base_relation == "reference":
        source, target = parent_id, node_id
    elif base_relation == "citation":
        source, target = node_id, parent_id
    else:
        source, target = parent_id, node_id
    contexts = [trim_text(scalar(item), 260) for item in (result.get("citation_contexts") or []) if scalar(item)]
    return {
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


def build_keynote_knowledge_synthesis(project_id: str, max_clusters: int = 8) -> str:
    """Build a DeepSurvey-style paper -> cluster -> review knowledge layer.

    This is intentionally deterministic and evidence-preserving.  It organizes
    existing Keynotes for cross-paper reasoning; it does not manufacture a
    review or claim that a repository was inspected.
    """
    try:
        from ._project import load_project, save_project
        from ._utils import clamp_int, normalize_key, normalize_space, trim_text, unique_preserve_order
    except ImportError:
        from _project import load_project, save_project
        from _utils import clamp_int, normalize_key, normalize_space, trim_text, unique_preserve_order

    project = load_project(project_id)
    records = {
        str(item.get("paper_id") or ""): item
        for item in project.get("papergraph", [])
        if isinstance(item, dict) and item.get("paper_id")
    }
    raw_keynotes = [item for item in project.get("keynotes", []) if isinstance(item, dict)]
    units: list[dict[str, Any]] = []
    for item in raw_keynotes:
        keynote = item.get("keynote", {}) if isinstance(item.get("keynote"), dict) else {}
        paper_id = str(item.get("paper_id") or "")
        record = records.get(paper_id, {})
        title = str(item.get("title") or record.get("title") or keynote.get("title") or "")
        citation = str(record.get("citation") or title)
        methodology = keynote.get("methodology", {}) if isinstance(keynote.get("methodology"), dict) else {}
        experiments = keynote.get("experiments", {}) if isinstance(keynote.get("experiments"), dict) else {}
        if not methodology:
            methodology = {"approach": keynote.get("methods", []), "design_choices": [], "assumptions": keynote.get("assumptions", [])}
        if not experiments:
            experiments = {"setup": keynote.get("experiments_or_evidence", []), "baselines": [], "main_results": [], "additional_studies": []}
        units.append(
            {
                "paper_id": paper_id,
                "keynote_id": item.get("keynote_id"),
                "citation": citation,
                "title": title,
                "year": record.get("year", ""),
                "method": str(record.get("method") or ""),
                "scenario": str(record.get("scenario") or ""),
                "benchmark": str(record.get("benchmark") or ""),
                "tldr": str(keynote.get("tldr") or ""),
                "key_contributions": [str(value) for value in keynote.get("key_contributions", keynote.get("contributions", [])) if str(value).strip()],
                "methodology": methodology,
                "experiments": experiments,
                "limitations": [str(value) for value in keynote.get("limitations", []) if str(value).strip()],
                "future_directions": [item for item in keynote.get("future_directions", []) if isinstance(item, dict)],
                "critical_reflections": [item for item in keynote.get("critical_reflections", []) if isinstance(item, dict)],
                "important_claims": [item for item in keynote.get("important_claims", []) if isinstance(item, dict)],
                "repository_artifacts": [
                    item if isinstance(item, dict) else {"url_or_name": str(item), "artifact_type": "unknown", "evidence": str(item), "analysis_status": "not_fetched"}
                    for item in keynote.get("repository_artifacts", keynote.get("code_or_implementation", []))
                    if isinstance(item, dict) or str(item).strip()
                ],
            }
        )

    target_clusters = clamp_int(max_clusters, 1, 20)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        focus = normalize_space(unit.get("scenario") or "") or normalize_space(unit.get("method") or "") or "general"
        grouped[normalize_key(focus) or "general"].append(unit)
    if len(grouped) > target_clusters:
        merged: dict[str, list[dict[str, Any]]] = defaultdict(list)
        ordered = sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        for index, (key, members) in enumerate(ordered):
            merged[key if index < target_clusters - 1 else "other_related_work"].extend(members)
        grouped = dict(merged)

    clusters: list[dict[str, Any]] = []
    claim_evidence_index: list[dict[str, Any]] = []
    for cluster_key, members in grouped.items():
        citations = unique_preserve_order(str(item.get("citation") or "") for item in members if item.get("citation"))
        methods = unique_preserve_order(str(item.get("method") or "") for item in members if item.get("method"))
        scenarios = unique_preserve_order(str(item.get("scenario") or "") for item in members if item.get("scenario"))
        benchmarks = unique_preserve_order(str(item.get("benchmark") or "") for item in members if item.get("benchmark"))
        limitations = unique_preserve_order(
            str(value) for item in members for value in item.get("limitations", []) if str(value).strip()
        )
        futures = [
            direction
            for item in members
            for direction in item.get("future_directions", [])
            if isinstance(direction, dict) and str(direction.get("direction") or "").strip()
        ]
        comparison_table = []
        for item in members:
            experiment = item.get("experiments", {}) if isinstance(item.get("experiments"), dict) else {}
            methodology = item.get("methodology", {}) if isinstance(item.get("methodology"), dict) else {}
            comparison_table.append(
                {
                    "citation": item.get("citation"),
                    "method": item.get("method"),
                    "scenario": item.get("scenario"),
                    "benchmark": item.get("benchmark"),
                    "contributions": item.get("key_contributions", [])[:3],
                    "design_choices": methodology.get("design_choices", [])[:3],
                    "setup": experiment.get("setup", [])[:3],
                    "baselines": experiment.get("baselines", [])[:3],
                    "main_results": experiment.get("main_results", [])[:3],
                    "limitations": item.get("limitations", [])[:3],
                }
            )
            for claim in item.get("important_claims", []):
                if str(claim.get("claim") or "").strip():
                    claim_evidence_index.append(
                        {
                            "claim": trim_text(str(claim.get("claim") or ""), 420),
                            "evidence": trim_text(str(claim.get("evidence") or ""), 500),
                            "citation": item.get("citation"),
                            "paper_id": item.get("paper_id"),
                            "cluster_id": normalize_key(cluster_key),
                        }
                    )
        relations = keynote_cluster_relations(members)
        clusters.append(
            {
                "cluster_id": normalize_key(cluster_key),
                "label": scenarios[0] if scenarios else (methods[0] if methods else "general related work"),
                "paper_count": len(members),
                "paper_ids": [item.get("paper_id") for item in members if item.get("paper_id")],
                "citations": citations,
                "methods": methods,
                "scenarios": scenarios,
                "benchmarks": benchmarks,
                "comparison_table": comparison_table,
                "relations": relations,
                "cluster_insights": {
                    "common_limitations": limitations[:8],
                    "future_directions": futures[:8],
                    "questions_for_gap_detection": keynote_cluster_questions(methods, scenarios, benchmarks, limitations, relations),
                },
            }
        )

    clusters.sort(key=lambda item: (-int(item.get("paper_count") or 0), str(item.get("cluster_id") or "")))
    cross_cluster_comparisons = keynote_cross_cluster_comparisons(clusters)
    repository_artifacts = [
        {**artifact, "citation": unit.get("citation"), "paper_id": unit.get("paper_id")}
        for unit in units for artifact in unit.get("repository_artifacts", [])
        if isinstance(artifact, dict)
    ]
    synthesis = {
        "kind": "deepsurvey_keynote_knowledge_layers",
        "createdAt": time.time(),
        "paper_level": units,
        "cluster_level": clusters,
        "review_level": {
            "paper_count": len(units),
            "cluster_count": len(clusters),
            "cross_cluster_comparisons": cross_cluster_comparisons,
            "claim_evidence_index": claim_evidence_index[:120],
            "repository_artifacts": repository_artifacts[:80],
            "repository_analysis_notice": "Artifacts are pointers extracted from supplied literature. No remote repository inspection is claimed unless analysis_status is analyzed.",
        },
    }
    project["keynote_knowledge_synthesis"] = synthesis
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "keynote_knowledge_synthesis_built", project_id=project_id, papers=len(units), clusters=len(clusters))
    return json.dumps(
        {
            "status": "built",
            "project_id": project_id,
            "paper_count": len(units),
            "cluster_count": len(clusters),
            "synthesis": synthesis,
            "next_step": "Use cluster_insights and claim_evidence_index for TanXi gap reasoning and assign evidence to causal links before MingLi writes a hypothesis.",
        },
        ensure_ascii=False,
        indent=2,
    )


def keynote_cluster_relations(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """State only inspectable relation types; never infer a contradiction from topic overlap alone."""
    relations: list[dict[str, Any]] = []
    for index, left in enumerate(members):
        for right in members[index + 1:]:
            same_method = bool(left.get("method") and left.get("method") == right.get("method"))
            same_scenario = bool(left.get("scenario") and left.get("scenario") == right.get("scenario"))
            if same_method and same_scenario:
                relation = "replication_or_complementary_evidence"
                reason = "The papers share the recorded method and scenario; compare their conditions and outcomes before treating them as agreement."
            elif same_scenario:
                relation = "alternative_approaches_same_scenario"
                reason = "The papers address the same recorded scenario with different methods."
            elif same_method:
                relation = "method_transfer_across_scenarios"
                reason = "The papers use the same recorded method in different scenarios."
            else:
                continue
            relations.append(
                {
                    "source_citation": left.get("citation"),
                    "target_citation": right.get("citation"),
                    "relation": relation,
                    "reason": reason,
                    "evidence_status": "metadata_and_keynote_comparison",
                }
            )
    return relations[:30]


def keynote_cluster_questions(
    methods: list[str],
    scenarios: list[str],
    benchmarks: list[str],
    limitations: list[str],
    relations: list[dict[str, Any]],
) -> list[str]:
    questions: list[str] = []
    if limitations:
        questions.append(f"Which causal mechanism or boundary condition explains the recurring limitation: {limitations[0]}?")
    if len(methods) >= 2 and scenarios:
        questions.append(f"Under which conditions do {methods[0]} and {methods[1]} produce different outcomes in {scenarios[0]}?")
    if relations and benchmarks:
        questions.append(f"Are the reported conclusions comparable on a shared benchmark family such as {benchmarks[0]}?")
    return questions[:4]


def keynote_cross_cluster_comparisons(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for index, left in enumerate(clusters):
        for right in clusters[index + 1:]:
            shared_methods = sorted(set(left.get("methods", [])) & set(right.get("methods", [])))
            shared_benchmarks = sorted(set(left.get("benchmarks", [])) & set(right.get("benchmarks", [])))
            if not shared_methods and not shared_benchmarks:
                continue
            comparisons.append(
                {
                    "left_cluster_id": left.get("cluster_id"),
                    "right_cluster_id": right.get("cluster_id"),
                    "shared_methods": shared_methods,
                    "shared_benchmarks": shared_benchmarks,
                    "question": "Does the shared method or benchmark retain the same interpretation across these distinct research clusters?",
                    "evidence_status": "comparison_prompt_not_a_conclusion",
                }
            )
    return comparisons[:30]

