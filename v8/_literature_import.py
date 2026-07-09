from __future__ import annotations

from collections import Counter, defaultdict
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
        SCIENCE_LLM_EXTRACTOR,
        SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K,
    )
    from .log import log_event
except ImportError:
    from config import (
        SCIENCE_LLM_EXTRACTOR,
        SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K,
    )
    from log import log_event



def select_zhizhi_import_results(
    results: list[dict[str, Any]],
    import_top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from ._literature_scoring import zhizhi_import_candidate_key, zhizhi_import_minimum_plan, zhizhi_import_priority_score
        from ._models import ZHIZHI_IMPORT_LAYER_LABELS, ZHIZHI_IMPORT_LAYER_PRIORITY
        from ._utils import clamp_int
    except ImportError:
        from _literature_scoring import zhizhi_import_candidate_key, zhizhi_import_minimum_plan, zhizhi_import_priority_score
        from _models import ZHIZHI_IMPORT_LAYER_LABELS, ZHIZHI_IMPORT_LAYER_PRIORITY
        from _utils import clamp_int
    limit = clamp_int(import_top_k, 1, SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K)
    candidates = [dict(item) for item in results if isinstance(item, dict)]
    candidate_counts = Counter(str(item.get("stratified_layer") or "unlayered") for item in candidates)
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    min_plan = zhizhi_import_minimum_plan(limit)

    def add_candidate(candidate: dict[str, Any], reason: str) -> bool:
        key = zhizhi_import_candidate_key(candidate)
        if key in selected_keys or len(selected) >= limit:
            return False
        item = dict(candidate)
        item["zhizhi_import_reason"] = reason
        selected.append(item)
        selected_keys.add(key)
        return True

    for layer in ZHIZHI_IMPORT_LAYER_PRIORITY:
        needed = min_plan.get(layer, 0)
        if needed <= 0:
            continue
        layer_candidates = sorted(
            [item for item in candidates if str(item.get("stratified_layer") or "") == layer],
            key=zhizhi_import_priority_score,
            reverse=True,
        )
        picked = 0
        for candidate in layer_candidates:
            if add_candidate(candidate, f"layer_minimum:{layer}"):
                picked += 1
            if picked >= needed:
                break

    remaining = sorted(candidates, key=zhizhi_import_priority_score, reverse=True)
    for candidate in remaining:
        if len(selected) >= limit:
            break
        add_candidate(candidate, "score_backfill")

    selected_counts = Counter(str(item.get("stratified_layer") or "unlayered") for item in selected)
    missing_layers = [
        {
            "layer": layer,
            "label": ZHIZHI_IMPORT_LAYER_LABELS.get(layer, layer),
            "target": target,
            "selected": selected_counts.get(layer, 0),
            "candidates": candidate_counts.get(layer, 0),
        }
        for layer, target in min_plan.items()
        if selected_counts.get(layer, 0) < target
    ]
    report = {
        "strategy": "layer_minimum_then_score_backfill",
        "requested_import_top_k": import_top_k,
        "effective_import_top_k": limit,
        "min_per_layer": min_plan,
        "candidate_counts_by_layer": dict(candidate_counts),
        "selected_counts_by_layer": dict(selected_counts),
        "missing_layers": missing_layers,
        "selected_result_indexes": [item.get("result_index") for item in selected],
    }
    return selected, report

def import_literature_text(
    project_id: str,
    title: str = "",
    citation: str = "",
    text: str = "",
    provider: str = "manual",
    source_type: str = "abstract",
    url: str = "",
    doi: str = "",
    arxiv_id: str = "",
    semantic_scholar_id: str = "",
    authors: list[str] | None = None,
    year: str = "",
    venue: str = "",
    use_llm: bool = False,
) -> str:
    try:
        from ._utils import first_sentences, trim_text
    except ImportError:
        from _utils import first_sentences, trim_text
    parsed = extract_paper_structure(text, use_llm=use_llm)
    inferred_title = title or parsed.get("title") or first_sentences(text, 1) or "Untitled paper"
    inferred_doi = doi or parsed.get("doi", "")
    inferred_arxiv_id = arxiv_id or parsed.get("arxiv_id", "")
    inferred_authors = authors or parsed.get("authors", [])
    inferred_year = year or parsed.get("year", "")
    inferred_venue = venue or parsed.get("venue", "")
    inferred_citation = citation or parsed.get("citation") or build_citation(
        title=inferred_title,
        authors=inferred_authors,
        year=inferred_year,
        doi=inferred_doi,
        arxiv_id=inferred_arxiv_id,
    )
    return import_papergraph_record(
        project_id=project_id,
        title=inferred_title,
        citation=inferred_citation,
        authors=inferred_authors,
        year=inferred_year,
        venue=inferred_venue,
        provider=provider,
        source_type=source_type,
        doi=inferred_doi,
        arxiv_id=inferred_arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        url=url,
        abstract=parsed["abstract"],
        conclusion=parsed["conclusion"],
        strengths=parsed["strengths"],
        improvements=parsed["improvements"],
        method=parsed["method"],
        scenario=parsed["scenario"],
        benchmark=parsed["benchmark"],
        contribution=parsed["contribution"],
        limitation=parsed["limitation"],
        full_text_excerpt=trim_text(text, 16000) if source_type in {"file", "pdf", "full_text", "manual_file"} or len(text) > 2500 else "",
        gap_signals=parsed.get("gap_signals") if isinstance(parsed.get("gap_signals"), list) else None,
    )

def import_literature_file(
    project_id: str,
    path: str,
    title: str = "",
    citation: str = "",
    provider: str = "manual_file",
    source_type: str = "file",
    use_llm: bool = False,
) -> str:
    try:
        from ._utils import read_literature_file, safe_workspace_path
    except ImportError:
        from _utils import read_literature_file, safe_workspace_path
    target = safe_workspace_path(path)
    text = read_literature_file(target)
    inferred_title = title or target.stem.replace("_", " ")
    inferred_citation = citation or inferred_title
    return import_literature_text(
        project_id=project_id,
        title=inferred_title,
        citation=inferred_citation,
        text=text,
        provider=provider,
        source_type=source_type,
        use_llm=use_llm,
    )

def import_literature_search_result(
    project_id: str,
    search_id: str,
    result_index: int = 0,
    use_llm: bool = False,
) -> str:
    try:
        from ._literature_scoring import domain_relevance_assessment, publication_quality_assessment, should_reject_for_domain
        from ._literature_search import enrich_papergraph_payload
        from ._project import load_project, load_search
        from ._utils import trim_text
    except ImportError:
        from _literature_scoring import domain_relevance_assessment, publication_quality_assessment, should_reject_for_domain
        from _literature_search import enrich_papergraph_payload
        from _project import load_project, load_search
        from _utils import trim_text
    project = load_project(project_id)
    search_record = load_search(search_id)
    results = search_record.get("results", [])
    if not results:
        raise ValueError(
            f"Search {search_id} has no retrieved papers. Do not invent a substitute; retry search or import user-provided text."
        )
    try:
        index = int(result_index)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid result_index: {result_index}") from exc
    if index < 0 or index >= len(results):
        raise ValueError(f"result_index {index} out of range for search {search_id}; total_results={len(results)}")
    result = results[index]
    project_domain = str(project.get("domain") or search_record.get("domain") or "")
    # Preserve domain_relevance from search phase if already assessed
    existing_relevance = result.get("domain_relevance")
    if not isinstance(existing_relevance, dict) or not existing_relevance.get("score"):
        result["domain_relevance"] = domain_relevance_assessment(
            result,
            domain=project_domain,
            query=str(search_record.get("query") or ""),
        )
    if should_reject_for_domain(result, domain=project_domain):
        log_event(
            "SCIENCE",
            "import_rejected_by_domain_gate",
            search_id=search_id,
            result_index=index,
            title=trim_text(str(result.get("title") or ""), 120),
            domain=project_domain,
            score=result.get("domain_relevance", {}).get("score"),
            verdict=result.get("domain_relevance", {}).get("verdict"),
        )
        raise ValueError(
            "Search result rejected before import by domain relevance gate: "
            f"title={trim_text(str(result.get('title') or ''), 120)}, "
            f"domain={project_domain}, assessment={json.dumps(result['domain_relevance'], ensure_ascii=False)}"
        )
    payload = result.get("papergraph_input")
    if not isinstance(payload, dict):
        raise ValueError(f"Search result {index} has no papergraph_input")
    payload = dict(payload)
    quality = publication_quality_assessment(result)
    initial_extraction_quality = extraction_quality_report(payload)
    enrichment_sources: list[str] = []

    if initial_extraction_quality.get("needs_enrichment"):
        payload, enrichment_sources = enrich_papergraph_payload(payload, result)
        if enrichment_sources:
            log_event(
                "SCIENCE",
                "paper_metadata_enriched",
                search_id=search_id,
                result_index=index,
                sources=",".join(enrichment_sources),
            )

    llm_retry: dict[str, Any] = {"attempted": False, "succeeded": False, "error": ""}
    if use_llm or extraction_quality_report(payload).get("needs_llm_retry"):
        payload, llm_retry = maybe_llm_reextract_structure(payload, force=use_llm)
        if llm_retry.get("attempted"):
            log_event("SCIENCE", "paper_extraction_llm_retry", search_id=search_id, result_index=index)
        if llm_retry.get("error"):
            log_event("WARN", "paper_extraction_llm_retry_failed", error=llm_retry.get("error"))
    final_extraction_quality = extraction_quality_report(payload)
    final_extraction_quality["initial"] = initial_extraction_quality
    final_extraction_quality["llm_retry"] = llm_retry
    if payload.get("_enrichment_errors"):
        final_extraction_quality["enrichment_errors"] = payload.get("_enrichment_errors")

    imported = import_papergraph_record(
        project_id=project_id,
        title=str(payload.get("title", "")),
        citation=str(payload.get("citation", "")),
        authors=payload.get("authors") if isinstance(payload.get("authors"), list) else [],
        year=str(payload.get("year", "")),
        venue=str(payload.get("venue", "")),
        provider=str(payload.get("provider", result.get("provider", "search"))),
        source_type=str(payload.get("source_type", "api")),
        doi=str(payload.get("doi", "")),
        arxiv_id=str(payload.get("arxiv_id", "")),
        semantic_scholar_id=str(payload.get("semantic_scholar_id", "")),
        url=str(payload.get("url", "")),
        abstract=str(payload.get("abstract", "")),
        full_text_excerpt=str(payload.get("full_text_excerpt", "")),
        conclusion=str(payload.get("conclusion", "")),
        strengths=payload.get("strengths") if isinstance(payload.get("strengths"), list) else None,
        improvements=payload.get("improvements") if isinstance(payload.get("improvements"), list) else None,
        method=str(payload.get("method", "")),
        scenario=str(payload.get("scenario", "")),
        benchmark=str(payload.get("benchmark", "")),
        contribution=str(payload.get("contribution", "")),
        limitation=str(payload.get("limitation", "")),
        extraction_quality=final_extraction_quality,
        enrichment_sources=enrichment_sources,
        gap_signals=payload.get("gap_signals") if isinstance(payload.get("gap_signals"), list) else None,
    )
    try:
        imported_payload = json.loads(imported)
    except json.JSONDecodeError:
        return imported
    imported_payload["search_result_quality"] = quality
    imported_payload["extraction_quality"] = final_extraction_quality
    imported_payload["enrichment_sources"] = enrichment_sources
    imported_payload["requires_human_review"] = (
        quality["venue_quality"] in {"suspicious", "missing"}
        or quality["quality_score"] < 0.55
        or bool(final_extraction_quality.get("requires_human_review"))
    )
    return json.dumps(imported_payload, ensure_ascii=False, indent=2)

def import_papergraph_record(
    project_id: str,
    title: str,
    citation: str,
    authors: list[str] | None = None,
    year: str = "",
    venue: str = "",
    provider: str = "manual",
    source_type: str = "metadata",
    doi: str = "",
    arxiv_id: str = "",
    semantic_scholar_id: str = "",
    url: str = "",
    abstract: str = "",
    full_text_excerpt: str = "",
    conclusion: str = "",
    strengths: list[str] | None = None,
    improvements: list[str] | None = None,
    method: str = "",
    scenario: str = "",
    benchmark: str = "",
    contribution: str = "",
    limitation: str = "",
    extraction_quality: dict[str, Any] | None = None,
    enrichment_sources: list[str] | None = None,
    gap_signals: list[dict[str, Any]] | None = None,
) -> str:
    try:
        from ._gap_detection import extract_gap_signals_from_text, normalize_gap_signals
        from ._models import PaperEvidence, PaperGraphRecord
        from ._project import load_project, save_project
        from ._utils import find_by_id, first_sentences, is_unknown_value, new_id, normalize_space, repair_unknown_field
    except ImportError:
        from _gap_detection import extract_gap_signals_from_text, normalize_gap_signals
        from _models import PaperEvidence, PaperGraphRecord
        from _project import load_project, save_project
        from _utils import find_by_id, first_sentences, is_unknown_value, new_id, normalize_space, repair_unknown_field
    project = load_project(project_id)
    unique_key = paper_unique_key(title=title, citation=citation, doi=doi, arxiv_id=arxiv_id, semantic_scholar_id=semantic_scholar_id, url=url)
    duplicate = find_by_id(project.get("papergraph", []), "unique_key", unique_key)
    if duplicate is not None:
        log_event("SCIENCE", "paper_duplicate", project_id=project_id, paper_id=duplicate.get("paper_id"), unique_key=unique_key)
        return json.dumps(
            {
                "status": "duplicate",
                "unique_key": unique_key,
                "existing_record": duplicate,
            },
            ensure_ascii=False,
            indent=2,
        )

    # Title-based fuzzy dedup: catch same-paper imports from different providers/identifiers
    normalized_new_title = normalize_space(title).lower()
    if normalized_new_title and len(normalized_new_title) >= 10:
        new_title_tokens = set(re.findall(r"[a-z0-9]+", normalized_new_title))
        for existing in project.get("papergraph", []):
            if not isinstance(existing, dict):
                continue
            existing_title = normalize_space(str(existing.get("title") or "")).lower()
            if not existing_title or len(existing_title) < 10:
                continue
            existing_tokens = set(re.findall(r"[a-z0-9]+", existing_title))
            if not new_title_tokens or not existing_tokens:
                continue
            intersection = new_title_tokens & existing_tokens
            union = new_title_tokens | existing_tokens
            jaccard = len(intersection) / max(1, len(union))
            if jaccard >= 0.85:
                log_event(
                    "SCIENCE",
                    "paper_fuzzy_title_duplicate",
                    project_id=project_id,
                    paper_id=existing.get("paper_id"),
                    jaccard=round(jaccard, 3),
                    new_title=title[:80],
                    existing_title=existing_title[:80],
                )
                return json.dumps(
                    {
                        "status": "duplicate",
                        "reason": "fuzzy_title_match",
                        "jaccard": round(jaccard, 3),
                        "unique_key": unique_key,
                        "existing_record": existing,
                    },
                    ensure_ascii=False,
                    indent=2,
                )

    clean_abstract = "" if invalid_placeholder_abstract(abstract) else abstract
    parsed_fallback = parse_paper_text("\n\n".join(part for part in [clean_abstract, conclusion, full_text_excerpt, limitation] if part))
    final_abstract = clean_abstract or parsed_fallback["abstract"]
    if invalid_placeholder_abstract(final_abstract):
        final_abstract = first_sentences(
            "\n\n".join(
                part
                for part in [conclusion, full_text_excerpt, contribution, limitation, parsed_fallback.get("conclusion", "")]
                if normalize_space(part)
            ),
            4,
        )
    final_conclusion = conclusion or parsed_fallback["conclusion"]
    final_strengths = strengths or parsed_fallback["strengths"]
    final_improvements = improvements or parsed_fallback["improvements"]
    final_method = method or parsed_fallback["method"]
    final_scenario = scenario or parsed_fallback["scenario"]
    final_benchmark = benchmark or parsed_fallback["benchmark"]
    final_contribution = contribution or parsed_fallback["contribution"]
    final_limitation = limitation or parsed_fallback["limitation"]
    context_text = "\n".join(part for part in [title, final_abstract, conclusion, full_text_excerpt, final_contribution, final_limitation] if part)
    final_method = repair_unknown_field(final_method, context_text, "method")
    final_scenario = repair_unknown_field(final_scenario, context_text, "scenario")
    final_benchmark = repair_unknown_field(final_benchmark, context_text, "benchmark")
    extracted_gap_signals = extract_gap_signals_from_text(context_text, citation=citation or title)
    final_gap_signals = normalize_gap_signals(list(gap_signals or []) + extracted_gap_signals, citation=citation or title)
    if final_gap_signals and is_unknown_value(final_limitation):
        final_limitation = str(final_gap_signals[0].get("text", final_limitation))
    elif final_gap_signals and final_limitation == "No explicit limitation extracted.":
        final_limitation = str(final_gap_signals[0].get("text", final_limitation))
    final_extraction_quality = extraction_quality or extraction_quality_report(
        {
            "title": title,
            "abstract": final_abstract,
            "conclusion": final_conclusion,
            "full_text_excerpt": full_text_excerpt,
            "method": final_method,
            "scenario": final_scenario,
            "benchmark": final_benchmark,
            "contribution": final_contribution,
            "limitation": final_limitation,
        }
    )
    score, reasons = score_evidence_credibility(
        title=title,
        citation=citation,
        provider=provider,
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        url=url,
        abstract=final_abstract,
        conclusion=final_conclusion,
        venue=venue,
        year=year,
    )
    record = PaperGraphRecord(
        paper_id=new_id("paper"),
        unique_key=unique_key,
        title=title,
        citation=citation,
        authors=list(authors or []),
        year=str(year),
        venue=venue,
        provider=provider,
        source_type=source_type,
        doi=doi,
        arxiv_id=arxiv_id,
        semantic_scholar_id=semantic_scholar_id,
        url=url,
        abstract=final_abstract,
        full_text_excerpt=full_text_excerpt,
        conclusion=final_conclusion,
        strengths=final_strengths,
        improvements=final_improvements,
        method=final_method,
        scenario=final_scenario,
        benchmark=final_benchmark,
        contribution=final_contribution,
        limitation=final_limitation,
        credibility_score=score,
        credibility_reasons=reasons,
        extraction_quality=final_extraction_quality,
        enrichment_sources=list(enrichment_sources or []),
        gap_signals=final_gap_signals,
    )
    project.setdefault("papergraph", []).append(asdict(record))
    project.setdefault("evidence", []).append(
        asdict(
            PaperEvidence(
                evidence_id=new_id("ev"),
                title=title,
                citation=citation,
                method=final_method,
                scenario=final_scenario,
                benchmark=final_benchmark,
                contribution=final_contribution,
                limitation=final_limitation,
                url=url,
            )
        )
    )
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "paper_imported", project_id=project_id, paper_id=record.paper_id, credibility=score, title=str(title or "")[:120])
    return json.dumps({"status": "imported", "record": asdict(record)}, ensure_ascii=False, indent=2)

def extract_paper_keynote(
    project_id: str,
    paper_id: str = "",
    search_id: str = "",
    result_index: int = 0,
    text: str = "",
    use_llm: bool = True,
) -> str:
    try:
        from ._project import load_project, load_search, save_project
        from ._utils import find_by_id, new_id
    except ImportError:
        from _project import load_project, load_search, save_project
        from _utils import find_by_id, new_id
    project = load_project(project_id)
    source: dict[str, Any] = {}
    source_text = text
    if paper_id:
        source = find_by_id(project.get("papergraph", []), "paper_id", paper_id) or {}
        if not source:
            raise ValueError(f"Paper not found in project PaperGraph: {paper_id}")
        source_text = "\n\n".join(
            part for part in [source.get("title", ""), source.get("abstract", ""), source.get("conclusion", ""), source.get("limitation", "")] if part
        )
    elif search_id:
        search_record = load_search(search_id)
        results = search_record.get("results", [])
        try:
            source = results[int(result_index)]
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid search result {search_id}:{result_index}") from exc
        source_text = "\n\n".join(part for part in [source.get("title", ""), source.get("abstract", "")] if part)
    elif not source_text:
        raise ValueError("Provide paper_id, search_id/result_index, or text.")

    if use_llm:
        try:
            keynote = extract_keynote_with_llm(source_text)
        except Exception as exc:
            log_event("WARN", "keynote_llm_failed", error=str(exc))
            keynote = extract_keynote_heuristic(source_text)
            keynote["extractor"] = "heuristic_fallback"
            keynote["llm_error"] = str(exc)
    else:
        keynote = extract_keynote_heuristic(source_text)
        keynote["extractor"] = "heuristic"

    item = {
        "keynote_id": new_id("keynote"),
        "paper_id": paper_id,
        "search_id": search_id,
        "result_index": result_index if search_id else None,
        "title": source.get("title", keynote.get("title", "")),
        "createdAt": time.time(),
        "keynote": keynote,
    }
    project.setdefault("keynotes", []).append(item)
    save_project(project)
    return json.dumps(item, ensure_ascii=False, indent=2)

def list_papergraph_records(project_id: str) -> str:
    try:
        from ._project import load_project
    except ImportError:
        from _project import load_project
    project = load_project(project_id)
    records = project.get("papergraph", [])
    if not records:
        return "(no PaperGraph records)"
    lines = []
    for record in records:
        lines.append(
            f"{record.get('paper_id')} score={record.get('credibility_score')} "
            f"{record.get('citation')} - {record.get('title')}"
        )
    return "\n".join(lines)

def repair_payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._utils import first_sentences, is_unknown_value, record_context_text, repair_unknown_field
    except ImportError:
        from _utils import first_sentences, is_unknown_value, record_context_text, repair_unknown_field
    context_text = record_context_text(payload)
    source_text = record_source_text(payload)
    repaired = dict(payload)
    repaired["method"] = repair_unknown_field(repaired.get("method", ""), context_text, "method")
    repaired["scenario"] = repair_unknown_field(repaired.get("scenario", ""), context_text, "scenario")
    repaired["benchmark"] = repair_unknown_field(repaired.get("benchmark", ""), context_text, "benchmark")
    repaired = repair_unsupported_scenario(repaired, source_text or context_text)
    if is_unknown_value(repaired.get("contribution")):
        repaired["contribution"] = first_sentences(context_text, 1)
    if is_unknown_value(repaired.get("limitation")):
        repaired["limitation"] = "No explicit limitation extracted."
    return repaired

def repair_unsupported_scenario(payload: dict[str, Any], context_text: str) -> dict[str, Any]:
    try:
        from ._utils import is_unknown_value, normalize_label, normalize_space
    except ImportError:
        from _utils import is_unknown_value, normalize_label, normalize_space
    scenario = normalize_label(payload.get("scenario", ""))
    if not scenario or is_unknown_value(scenario):
        return payload
    lowered_context = normalize_space(context_text).lower()
    if scenario_is_supported_by_context(scenario, lowered_context):
        return payload
    inferred = infer_ontology_field(context_text, "scenario") or infer_generic_science_phrase(context_text, "scenario")
    if not inferred or normalize_label(inferred) == scenario:
        return payload
    repaired = dict(payload)
    repaired.setdefault("extraction_quality", {})
    if isinstance(repaired["extraction_quality"], dict):
        flags = repaired["extraction_quality"].setdefault("flags", [])
        if isinstance(flags, list):
            flags.append("scenario_domain_repaired")
        repaired["extraction_quality"]["scenario_before_repair"] = scenario
        repaired["extraction_quality"]["scenario_repair_reason"] = "scenario label was not supported by paper context"
        repaired["extraction_quality"]["requires_human_review"] = True
    repaired["scenario"] = inferred
    return repaired

def scenario_is_supported_by_context(scenario: str, lowered_context: str) -> bool:
    try:
        from ._literature_search import query_terms
        from ._models import SCENARIO_ONTOLOGY
        from ._utils import science_term_in_text
    except ImportError:
        from _literature_search import query_terms
        from _models import SCENARIO_ONTOLOGY
        from _utils import science_term_in_text
    scenario_terms = query_terms(scenario)
    if not scenario_terms:
        return False
    hits = [term for term in scenario_terms if science_term_in_text(term, lowered_context)]
    if hits:
        return True
    ontology_terms = SCENARIO_ONTOLOGY.get(scenario, [])
    return any(science_term_in_text(str(term), lowered_context) for term in ontology_terms)

def sync_evidence_from_record(project: dict[str, Any], record: dict[str, Any]) -> None:
    evidence_items = project.get("evidence", [])
    if not isinstance(evidence_items, list):
        return
    citation = str(record.get("citation") or "")
    title = str(record.get("title") or "")
    for evidence in evidence_items:
        if not isinstance(evidence, dict):
            continue
        if (citation and evidence.get("citation") == citation) or (title and evidence.get("title") == title):
            evidence["method"] = record.get("method", evidence.get("method", ""))
            evidence["scenario"] = record.get("scenario", evidence.get("scenario", ""))
            evidence["benchmark"] = record.get("benchmark", evidence.get("benchmark", ""))
            evidence["contribution"] = record.get("contribution", evidence.get("contribution", ""))
            evidence["limitation"] = record.get("limitation", evidence.get("limitation", ""))

def verify_citation_uniqueness(
    project_id: str,
    title: str = "",
    citation: str = "",
    doi: str = "",
    arxiv_id: str = "",
    semantic_scholar_id: str = "",
    url: str = "",
) -> str:
    try:
        from ._literature_search import search_literature
        from ._project import load_project, save_project
    except ImportError:
        from _literature_search import search_literature
        from _project import load_project, save_project
    project = load_project(project_id)
    unique_key = paper_unique_key(title=title, citation=citation, doi=doi, arxiv_id=arxiv_id, semantic_scholar_id=semantic_scholar_id, url=url)
    duplicates = [record for record in project.get("papergraph", []) if record.get("unique_key") == unique_key]
    checks = project.setdefault("citation_uniqueness_checks", [])
    prior_count = sum(1 for item in checks if isinstance(item, dict) and item.get("unique_key") == unique_key)
    result = {
        "unique": not duplicates,
        "unique_key": unique_key,
        "duplicates": duplicates,
        "repeated_check": prior_count > 0,
        "prior_check_count": prior_count,
        "next_step": (
            "This citation has already been checked in this run; do not repeat verify_citation_uniqueness. "
            "If it is unique, import only if it came from a real cached search result; otherwise continue with search_literature/select/import."
            if prior_count > 0
            else "Use this uniqueness result once. Do not repeatedly call verify_citation_uniqueness for the same citation."
        ),
    }
    checks.append(
        {
            "unique_key": unique_key,
            "title": title,
            "citation": citation,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "semantic_scholar_id": semantic_scholar_id,
            "url": url,
            "unique": not duplicates,
            "checkedAt": time.time(),
        }
    )
    if len(checks) > 200:
        project["citation_uniqueness_checks"] = checks[-200:]
    project["updatedAt"] = time.time()
    save_project(project)
    return json.dumps(result, ensure_ascii=False, indent=2)

def parse_literature_text(text: str, use_llm: bool = False) -> str:
    return json.dumps(extract_paper_structure(text, use_llm=use_llm), ensure_ascii=False, indent=2)

def extract_paper_structure(text: str, use_llm: bool = False) -> dict[str, Any]:
    heuristic = parse_paper_text(text)
    if not use_llm:
        heuristic["extractor"] = "heuristic"
        return heuristic
    try:
        llm = extract_paper_structure_with_llm(text)
    except Exception as exc:
        log_event("WARN", "paper_llm_extract_failed", error=str(exc))
        heuristic["extractor"] = "heuristic_fallback"
        heuristic["llm_error"] = str(exc)
        return heuristic
    merged = merge_paper_structures(heuristic, llm)
    merged["extractor"] = f"{SCIENCE_LLM_EXTRACTOR}_json"
    return merged

def extract_paper_structure_with_llm(text: str) -> dict[str, Any]:
    try:
        from ._llm import call_llm_json, normalize_llm_paper_structure
        from ._utils import trim_text
    except ImportError:
        from _llm import call_llm_json, normalize_llm_paper_structure
        from _utils import trim_text
    schema = {
        "title": "string",
        "citation": "string",
        "authors": ["string"],
        "year": "string",
        "venue": "string",
        "doi": "string",
        "arxiv_id": "string",
        "abstract": "string",
        "conclusion": "string",
        "strengths": ["string"],
        "improvements": ["string"],
        "method": "string",
        "scenario": "string",
        "benchmark": "string",
        "contribution": "string",
        "limitation": "string",
        "gap_signals": [{"signal_type": "limitation | future_work | open_problem | challenge | missing_evidence", "text": "string"}],
    }
    payload = call_llm_json(
        system="You are PaperGraph Extractor. You produce valid compact JSON only.",
        max_tokens=2500,
        prompt=(
            "Extract a scientific paper into strict JSON. Return JSON only, no markdown. "
            "Use empty strings or empty arrays when unavailable. Preserve factual wording; do not invent citations.\n\n"
            "General extraction rules:\n"
            "- method: the concrete research method, instrument, index, model, algorithm, experimental design, synthesis route, assay, or analysis approach actually used by the paper. "
            "Do not use a background sentence, research motivation, or broad topic as the method.\n"
            "- scenario: the scientific system, task, phenomenon, application setting, material class, organism/disease, environment, engineering system, or domain where the method is applied.\n"
            "- benchmark: the evaluated metric, observable, endpoint, dataset, response variable, performance criterion, experimental readout, or validation target.\n"
            "- contribution: the paper's main supported finding or methodological advance.\n"
            "- limitation: an explicit limitation, unresolved problem, boundary condition, or future-work point; use an empty string if not stated.\n\n"
            "- gap_signals: extract multiple explicit limitations, future-work directions, open problems, unresolved challenges, and missing-evidence statements when present, especially from PDF/full-text discussion, limitations, conclusion, and outlook sections.\n\n"
            "Cross-domain examples for choosing compact labels:\n"
            "- mathematics/statistics: method=theoretical proof | bayesian inference | causal inference; scenario=statistical inference | dynamical system; benchmark=uncertainty | convergence rate | effect size.\n"
            "- physics/astronomy/geoscience: method=spectroscopy | numerical simulation | seismic inversion | observational survey; scenario=quantum materials | astrophysical observation | earthquake and tectonics; benchmark=spectral feature | structural damage | prediction error.\n"
            "- chemistry/materials/engineering: method=organic synthesis | x-ray diffraction | density functional theory | finite element analysis; scenario=catalytic reaction | semiconductor device testing | structural system only when explicitly stated; benchmark=reaction yield | mechanical strength | device lifetime.\n"
            "- biology/agriculture/medicine/ecology: method=genome sequencing | clinical trial | field experiment | species distribution modeling; scenario=genetic disease | crop stress resilience | biodiversity and community ecology; benchmark=gene expression | clinical response | crop yield | species richness.\n"
            "- computer science/AI: method=deep learning model | graph neural network | reinforcement learning | knowledge graph construction; scenario=medical image analysis | software engineering | AI for science; benchmark=accuracy | robustness | latency | benchmark score.\n"
            "- environmental/earth-system studies: method=remote sensing | numerical model ensemble | spatial analysis | event attribution; scenario=extreme events | watershed system | ecosystem response; benchmark=event intensity | spatial extent | model error | recovery time.\n\n"
            "Guardrails:\n"
            "- Prefer concise normalized labels over long sentences.\n"
            "- If a field is not supported by the supplied text, return an empty string rather than guessing.\n"
            "- Avoid cross-domain leakage: only use a specialized metric label when the paper's domain supports it.\n"
            "- Scenario must be supported by title, abstract, conclusion, or paper metadata; never copy a scenario from examples when the paper text does not mention it.\n"
            "- If the abstract is truncated and no concrete method is stated, leave method empty rather than writing a vague phrase.\n\n"
            f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
            f"Paper text:\n{trim_text(text, 12000)}"
        ),
    )
    return normalize_llm_paper_structure(payload)

def extract_keynote_with_llm(text: str) -> dict[str, Any]:
    try:
        from ._llm import call_llm_json
        from ._utils import trim_text
    except ImportError:
        from _llm import call_llm_json
        from _utils import trim_text
    schema = {
        "title": "string",
        "core_problem": "string",
        "contributions": ["string"],
        "methods": ["string"],
        "experiments_or_evidence": ["string"],
        "assumptions": ["string"],
        "limitations": ["string"],
        "gap_signals": [{"signal_type": "string", "text": "string"}],
        "datasets_or_materials": ["string"],
        "code_or_implementation": ["string"],
        "important_claims": [{"claim": "string", "evidence": "string"}],
        "reuse_value_for_research": "string",
    }
    payload = call_llm_json(
        system="You are a DeepSurvey-style keynote reader. Extract grounded, reusable paper notes. JSON only.",
        max_tokens=2500,
        prompt=(
            "Extract a structured keynote for cross-paper comparison. Do not invent facts. "
            "If only abstract is provided, mark missing details as empty arrays.\n\n"
            f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
            f"Paper text:\n{trim_text(text, 14000)}"
        ),
    )
    return normalize_keynote(payload)

def extract_keynote_heuristic(text: str) -> dict[str, Any]:
    try:
        from ._utils import first_sentences, string_list
    except ImportError:
        from _utils import first_sentences, string_list
    parsed = parse_paper_text(text)
    return {
        "title": parsed.get("title", ""),
        "core_problem": first_sentences(parsed.get("abstract", "") or text, 1),
        "contributions": string_list(parsed.get("contribution")),
        "methods": string_list(parsed.get("method")) if parsed.get("method") != "unknown method" else [],
        "experiments_or_evidence": extract_bullets_or_sentences(text, ["experiment", "evaluate", "result", "dataset", "case study"], limit=5),
        "assumptions": extract_bullets_or_sentences(text, ["assume", "assumption", "under the condition"], limit=5),
        "limitations": string_list(parsed.get("limitation")) if parsed.get("limitation") else [],
        "gap_signals": parsed.get("gap_signals", []),
        "datasets_or_materials": extract_bullets_or_sentences(text, ["dataset", "benchmark", "data", "material", "sample"], limit=5),
        "code_or_implementation": extract_bullets_or_sentences(text, ["code", "repository", "implementation", "github"], limit=5),
        "important_claims": [{"claim": parsed.get("contribution", ""), "evidence": parsed.get("abstract", "")} if parsed.get("contribution") else {}],
        "reuse_value_for_research": "Useful as structured evidence if quality and citation checks pass.",
    }

def normalize_keynote(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._gap_detection import normalize_gap_signals
        from ._utils import scalar, string_list
    except ImportError:
        from _gap_detection import normalize_gap_signals
        from _utils import scalar, string_list
    claims = payload.get("important_claims", [])
    normalized_claims: list[dict[str, str]] = []
    if isinstance(claims, list):
        for item in claims:
            if isinstance(item, dict):
                normalized_claims.append({"claim": scalar(item.get("claim")), "evidence": scalar(item.get("evidence"))})
            elif scalar(item):
                normalized_claims.append({"claim": scalar(item), "evidence": ""})
    return {
        "title": scalar(payload.get("title")),
        "core_problem": scalar(payload.get("core_problem")),
        "contributions": string_list(payload.get("contributions")),
        "methods": string_list(payload.get("methods")),
        "experiments_or_evidence": string_list(payload.get("experiments_or_evidence")),
        "assumptions": string_list(payload.get("assumptions")),
        "limitations": string_list(payload.get("limitations")),
        "gap_signals": normalize_gap_signals(
            [
                item if isinstance(item, dict) else {"signal_type": "gap_signal", "text": scalar(item)}
                for item in (payload.get("gap_signals") if isinstance(payload.get("gap_signals"), list) else [])
            ]
            + [
                {"signal_type": "limitation", "text": item, "evidence_type": "author_opinion"}
                for item in string_list(payload.get("limitations"))
            ]
        ),
        "datasets_or_materials": string_list(payload.get("datasets_or_materials")),
        "code_or_implementation": string_list(payload.get("code_or_implementation")),
        "important_claims": normalized_claims,
        "reuse_value_for_research": scalar(payload.get("reuse_value_for_research")),
        "extractor": f"{SCIENCE_LLM_EXTRACTOR}_keynote",
    }

def merge_paper_structures(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, list):
            if value:
                merged[key] = value
        elif str(value or "").strip():
                merged[key] = value
    return merged

def extraction_quality_report(record: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._utils import is_unknown_value, normalize_label, normalize_space, unique_preserve_order
    except ImportError:
        from _utils import is_unknown_value, normalize_label, normalize_space, unique_preserve_order
    fields = {
        "method": normalize_label(record.get("method", "")),
        "scenario": normalize_label(record.get("scenario", "")),
        "benchmark": normalize_label(record.get("benchmark", "")),
    }
    unknown_fields = [name for name, value in fields.items() if is_unknown_value(value)]
    abstract = normalize_space(str(record.get("abstract") or ""))
    conclusion = normalize_space(str(record.get("conclusion") or ""))
    text = normalize_space(
        " ".join(
            str(record.get(key, ""))
            for key in ("title", "abstract", "conclusion", "full_text_excerpt", "contribution", "limitation")
            if record.get(key)
        )
    )
    flags: list[str] = []
    if invalid_placeholder_abstract(abstract):
        flags.append("invalid_placeholder_abstract")
    if not abstract or invalid_placeholder_abstract(abstract):
        flags.append("missing_abstract")
    elif len(abstract) < 220:
        flags.append("short_abstract")
    if looks_truncated(abstract):
        flags.append("truncated_abstract")
    if not conclusion:
        flags.append("missing_conclusion")
    if unknown_fields:
        flags.append("unknown_fields")
    if len(unknown_fields) >= 2:
        flags.append("unknown_fields_high")
    if fields["benchmark"] in {"unknown benchmark", "unspecified benchmark", "unknown"}:
        flags.append("missing_benchmark")
    if text and background_only_text(text):
        flags.append("background_only_text")
    unknown_ratio = round(len(unknown_fields) / max(1, len(fields)), 3)
    score = 1.0
    score -= 0.24 * len(unknown_fields)
    if "missing_abstract" in flags:
        score -= 0.25
    elif "short_abstract" in flags:
        score -= 0.12
    if "truncated_abstract" in flags:
        score -= 0.2
    if "background_only_text" in flags:
        score -= 0.12
    score = round(max(0.0, min(1.0, score)), 3)
    return {
        "score": score,
        "unknown_ratio": unknown_ratio,
        "unknown_fields": unknown_fields,
        "abstract_chars": len(abstract),
        "flags": unique_preserve_order(flags),
        "needs_enrichment": (
            "missing_abstract" in flags
            or "invalid_placeholder_abstract" in flags
            or "truncated_abstract" in flags
            or ("short_abstract" in flags and len(unknown_fields) >= 1)
        ),
        "needs_llm_retry": len(unknown_fields) >= 1 or "background_only_text" in flags,
        "requires_human_review": score < 0.55 or len(unknown_fields) >= 2,
    }

def invalid_placeholder_abstract(text: str) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    clean = normalize_space(text)
    if not clean:
        return True
    lowered = clean.lower().strip(" :;.-")
    if lowered in {"abstract", "summary", "conclusion", "conclusions", "result", "results", "not available", "no abstract available"}:
        return True
    if re.fullmatch(r"(abstract|summary|conclusion|conclusions|results?)\s*:?", clean, flags=re.IGNORECASE):
        return True
    return len(clean.split()) <= 2 and any(label in lowered for label in ("abstract", "summary", "conclusion", "result"))

def looks_truncated(text: str) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    stripped = normalize_space(text)
    if not stripped:
        return False
    lowered = stripped.lower().rstrip()
    if lowered.endswith("..."):
        return True
    return bool(re.search(r"\b(using|via|through|based on|with|by|as|an|a|the)\s*(?:\.\.\.)?$", lowered))

def background_only_text(text: str) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    lowered = normalize_space(text).lower()
    if not lowered:
        return False
    background_markers = [
        "is an effective approach",
        "is important",
        "has attracted",
        "developing cost-effective",
        "urgent need",
        "major challenge",
        "promising strategy",
        "broad interest",
        "critical problem",
    ]
    evidence_markers = [
        "accuracy",
        "assessed",
        "baseline",
        "benchmark",
        "characterized",
        "compared",
        "demonstrates",
        "evaluated",
        "experiment",
        "measured",
        "metric",
        "model",
        "performance",
        "prediction",
        "protocol",
        "readout",
        "response",
        "score",
        "stability",
        "validated",
        "results",
    ]
    return any(marker in lowered for marker in background_markers) and not any(marker in lowered for marker in evidence_markers)

def maybe_llm_reextract_structure(payload: dict[str, Any], *, force: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    quality = extraction_quality_report(payload)
    if not force and not quality.get("needs_llm_retry"):
        return payload, {"attempted": False, "succeeded": False, "error": ""}
    text = "\n\n".join(
        part
        for part in [
            f"Title: {payload.get('title', '')}",
            f"Venue: {payload.get('venue', '')}",
            f"Year: {payload.get('year', '')}",
            f"Citation: {payload.get('citation', '')}",
            f"Abstract: {payload.get('abstract', '')}",
            f"Conclusion: {payload.get('conclusion', '')}",
            f"Full text excerpt: {payload.get('full_text_excerpt', '')}",
        ]
        if normalize_space(part)
    )
    try:
        parsed = extract_paper_structure(text, use_llm=True)
    except Exception as exc:
        return payload, {"attempted": True, "succeeded": False, "error": str(exc)}
    merged = merge_paper_structures(payload, parsed)
    extractor = str(parsed.get("extractor") or "")
    error = str(parsed.get("llm_error") or "")
    return merged, {
        "attempted": True,
        "succeeded": extractor not in {"heuristic_fallback", "heuristic"} and not error,
        "error": error,
        "extractor": extractor,
    }

def is_low_information_field(value: str, field: str) -> bool:
    try:
        from ._models import GENERAL_BENCHMARK_CUES, GENERAL_METHOD_CUES, GENERAL_SCENARIO_CUES
        from ._utils import normalize_space
    except ImportError:
        from _models import GENERAL_BENCHMARK_CUES, GENERAL_METHOD_CUES, GENERAL_SCENARIO_CUES
        from _utils import normalize_space
    lowered = normalize_space(value).lower()
    if not lowered:
        return True
    generic_fragments = [
        "is an effective approach",
        "is important",
        "developing cost-effective",
        "has attracted",
        "urgent need",
        "background",
        "this study",
        "this paper",
        "research topic",
        "broad application",
        "significant challenge",
    ]
    if any(fragment in lowered for fragment in generic_fragments):
        return True
    if field in {"method", "benchmark"} and len(lowered) > 90:
        return True
    if field == "method" and not contains_any(lowered, GENERAL_METHOD_CUES):
        return len(lowered) > 80
    if field == "scenario" and not contains_any(lowered, GENERAL_SCENARIO_CUES):
        return len(lowered) > 100
    if field == "benchmark":
        if lowered in {"benchmark dataset", "benchmark data", "benchmark"}:
            return True
        if not contains_any(lowered, GENERAL_BENCHMARK_CUES):
            return len(lowered) > 80
    return False

def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)

def infer_ontology_field(text: str, field: str) -> str:
    try:
        from ._models import BENCHMARK_ONTOLOGY, METHOD_ONTOLOGY, SCENARIO_ONTOLOGY
        from ._utils import normalize_space, science_term_in_text
    except ImportError:
        from _models import BENCHMARK_ONTOLOGY, METHOD_ONTOLOGY, SCENARIO_ONTOLOGY
        from _utils import normalize_space, science_term_in_text
    lowered = normalize_space(text).lower()
    ontology = {
        "method": METHOD_ONTOLOGY,
        "scenario": SCENARIO_ONTOLOGY,
        "benchmark": BENCHMARK_ONTOLOGY,
    }.get(field, {})
    best_label = ""
    best_score = 0.0
    for label, patterns in ontology.items():
        if field == "benchmark" and not benchmark_allowed_for_context(label, lowered):
            continue
        score = sum(1.0 + min(len(pattern), 40) / 100.0 for pattern in patterns if science_term_in_text(pattern, lowered))
        if score > best_score:
            best_label = label
            best_score = score
    return best_label

def benchmark_allowed_for_context(label: str, lowered_text: str) -> bool:
    try:
        from ._models import FIELD_SPECIFIC_BENCHMARKS
    except ImportError:
        from _models import FIELD_SPECIFIC_BENCHMARKS
    required = FIELD_SPECIFIC_BENCHMARKS.get(label)
    if not required:
        return True
    return any(term in lowered_text for term in required)

def infer_generic_science_phrase(text: str, field: str) -> str:
    try:
        from ._utils import normalize_space, trim_text
    except ImportError:
        from _utils import normalize_space, trim_text
    clean = normalize_space(text)
    if not clean:
        return ""
    patterns = {
        "method": [
            r"\b([A-Za-z][A-Za-z0-9 -]{2,60}\s(?:analysis|model|modeling|simulation|algorithm|assay|index|inversion|sequencing|spectroscopy|microscopy|trial|experiment|synthesis|characterization|optimization|inference|regression|classification))\b",
            r"\b(?:using|via|with|based on|by applying)\s+([A-Za-z][A-Za-z0-9 -]{2,60})\b",
        ],
        "scenario": [
            r"\b([A-Za-z][A-Za-z0-9 -]{2,70}\s(?:application|case|cohort|condition|dataset|diagnosis|discovery|domain|environment|experiment|forecasting|material|phenomenon|platform|population|prediction|process|sample|screening|setting|system|task|therapy))\b",
            r"\b(?:in|for|under|within|across)\s+([A-Za-z][A-Za-z0-9 -]{2,70}\s(?:application|case|classification|cohort|conditions|context|dataset|diagnosis|discovery|domain|environment|forecasting|population|prediction|regime|sample|scenario|screening|setting|system|task|therapy))\b",
        ],
        "benchmark": [
            r"\b([A-Za-z][A-Za-z0-9 -]{2,60}\s(?:accuracy|baseline|criterion|efficiency|endpoint|error|index|metric|observable|performance|readout|response|score|stability|uncertainty|validation|yield))\b",
            r"\b(?:assessed by|benchmarked by|evaluated by|measured by|measures|reported by|reports|validated by|using)\s+([A-Za-z][A-Za-z0-9 -]{2,60})\b",
        ],
    }.get(field, [])
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if not match:
            continue
        phrase = normalize_space(match.group(1)).strip(" .,:;")
        phrase = clean_extracted_science_phrase(phrase, field)
        phrase = trim_text(phrase, 90)
        if phrase and not is_generic_phrase(phrase):
            return phrase.lower()
    return ""

def clean_extracted_science_phrase(phrase: str, field: str) -> str:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    cleaned = normalize_space(phrase)
    if field == "benchmark":
        for marker in (
            " and measures ",
            " and measured ",
            " and reports ",
            " and reported ",
            " and evaluates ",
            " and evaluated ",
            " with ",
        ):
            if marker in cleaned.lower():
                parts = re.split(re.escape(marker), cleaned, maxsplit=1, flags=re.IGNORECASE)
                cleaned = parts[-1]
                break
    if field == "scenario":
        cleaned = re.sub(r"^(a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
    return normalize_space(cleaned).strip(" .,:;")

def is_generic_phrase(phrase: str) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    lowered = normalize_space(phrase).lower()
    generic = {
        "this study",
        "the paper",
        "our results",
        "an effective approach",
        "a new method",
        "the proposed method",
        "current study",
    }
    if lowered in generic:
        return True
    return len(lowered.split()) > 9

def record_source_text(record: dict[str, Any]) -> str:
    return "\n".join(
        str(record.get(key, ""))
        for key in (
            "title",
            "citation",
            "abstract",
            "conclusion",
            "full_text_excerpt",
        )
        if record.get(key)
    )

def parse_paper_text(text: str) -> dict[str, Any]:
    try:
        from ._gap_detection import extract_gap_signals_from_text
        from ._utils import extract_section, extract_year, first_nonempty, first_sentences, last_sentences, normalize_space, repair_unknown_field
    except ImportError:
        from _gap_detection import extract_gap_signals_from_text
        from _utils import extract_section, extract_year, first_nonempty, first_sentences, last_sentences, normalize_space, repair_unknown_field
    clean = normalize_space(text)
    title = extract_labeled_value(clean, ["title"])
    doi = extract_doi(clean)
    arxiv_id = extract_labeled_value(clean, ["arxiv", "arxiv id", "arxiv_id"])
    authors = extract_authors(clean)
    year = extract_year(clean)
    venue = extract_labeled_value(clean, ["venue", "journal", "conference"])
    abstract = extract_section(clean, ["abstract", "summary"]) or first_sentences(clean, 3)
    conclusion = extract_section(clean, ["conclusion", "conclusions", "discussion"]) or last_sentences(clean, 3)
    strengths = extract_bullets_or_sentences(clean, ["advantage", "strength", "contribution", "novel", "improve"], limit=5)
    improvements = extract_bullets_or_sentences(clean, ["limitation", "future work", "weakness", "challenge", "remain"], limit=5)
    gap_signals = extract_gap_signals_from_text(clean, citation="", limit=12)
    method = infer_field(clean, ["method", "approach", "model", "framework"], default="")
    scenario = infer_field(clean, ["scenario", "application", "domain", "task"], default="")
    benchmark = infer_field(clean, ["benchmark", "dataset", "data set", "corpus"], default="")
    method = repair_unknown_field(method, clean, "method")
    scenario = repair_unknown_field(scenario, clean, "scenario")
    benchmark = repair_unknown_field(benchmark, clean, "benchmark")
    contribution = first_nonempty(strengths) or first_sentences(clean, 1)
    limitation = (
        str(gap_signals[0].get("text", ""))
        if gap_signals
        else first_nonempty(improvements) or "No explicit limitation extracted."
    )
    citation = build_citation(title=title, authors=authors, year=year, doi=doi, arxiv_id=arxiv_id) if title or doi or arxiv_id else ""
    return {
        "title": title,
        "citation": citation,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "abstract": abstract,
        "conclusion": conclusion,
        "strengths": strengths,
        "improvements": improvements,
        "method": method,
        "scenario": scenario,
        "benchmark": benchmark,
        "contribution": contribution,
        "limitation": limitation,
        "gap_signals": gap_signals,
    }

def extract_labeled_value(text: str, labels: list[str]) -> str:
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
    for label in labels:
        pattern = re.compile(rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$")
        match = pattern.search(text)
        if match:
            return trim_text(match.group(1), 300)
    return ""

def extract_doi(text: str) -> str:
    labeled = extract_labeled_value(text, ["doi"])
    if labeled:
        return normalize_doi(labeled)
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", text)
    return normalize_doi(match.group(0)) if match else ""

def normalize_doi(value: str) -> str:
    cleaned = str(value or "").strip().rstrip(".,;)")
    cleaned = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", cleaned)
    return cleaned

def extract_authors(text: str) -> list[str]:
    raw = extract_labeled_value(text, ["authors", "author"])
    if not raw:
        return []
    pieces = re.split(r"\s*(?:;|,|\band\b|&)\s*", raw)
    return [piece.strip() for piece in pieces if piece.strip()][:20]

def build_citation(
    *,
    title: str,
    authors: list[str],
    year: str,
    doi: str,
    arxiv_id: str,
) -> str:
    parts: list[str] = []
    if authors:
        first_author = authors[0]
        parts.append(f"{first_author} et al." if len(authors) > 1 else first_author)
    if year:
        parts.append(f"({year})")
    if title:
        parts.append(title)
    if doi:
        parts.append(f"doi:{doi}")
    elif arxiv_id:
        parts.append(f"arXiv:{arxiv_id}")
    return " ".join(parts).strip() or title or doi or arxiv_id or "uncited paper"

def extract_bullets_or_sentences(text: str, keywords: list[str], limit: int = 5) -> list[str]:
    try:
        from ._utils import split_sentences, trim_text, unique_preserve_order
    except ImportError:
        from _utils import split_sentences, trim_text, unique_preserve_order
    candidates: list[str] = []
    for line in text.splitlines():
        stripped = line.strip(" -*\t")
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(keyword in lowered for keyword in keywords):
            candidates.append(trim_text(stripped, 300))
    if candidates:
        return unique_preserve_order(candidates)[:limit]

    sentences = split_sentences(text)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            candidates.append(trim_text(sentence, 300))
    return unique_preserve_order(candidates)[:limit]

def infer_field(text: str, keywords: list[str], default: str) -> str:
    try:
        from ._utils import split_sentences, trim_text
    except ImportError:
        from _utils import split_sentences, trim_text
    for sentence in split_sentences(text):
        lowered = sentence.lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        if len(sentence) <= 220:
            return trim_text(sentence, 220)
    return default

def score_evidence_credibility(
    *,
    title: str,
    citation: str,
    provider: str,
    doi: str,
    arxiv_id: str,
    semantic_scholar_id: str,
    url: str,
    abstract: str,
    conclusion: str,
    venue: str,
    year: str,
) -> tuple[float, list[str]]:
    try:
        from ._literature_scoring import is_reputable_venue, publication_quality_assessment
        from ._models import LITERATURE_PROVIDERS
    except ImportError:
        from _literature_scoring import is_reputable_venue, publication_quality_assessment
        from _models import LITERATURE_PROVIDERS
    score = 0.2
    reasons: list[str] = ["base record"]
    if title and citation:
        score += 0.15
        reasons.append("has title and citation")
    if doi:
        score += 0.2
        reasons.append("has DOI")
    if arxiv_id or semantic_scholar_id:
        score += 0.15
        reasons.append("has scholarly identifier")
    if url:
        score += 0.05
        reasons.append("has URL")
    if len(abstract) > 200:
        score += 0.1
        reasons.append("has substantial abstract")
    if len(conclusion) > 100:
        score += 0.05
        reasons.append("has conclusion/discussion")
    if provider in LITERATURE_PROVIDERS or provider.startswith("manual"):
        score += 0.05
        reasons.append("provider recorded")
    if is_reputable_venue(venue.lower()) or any(marker in venue.lower() for marker in ("neurips", "icml", "iclr", "npj")):
        score += 0.1
        reasons.append("high-prestige venue marker")
    quality = publication_quality_assessment(
        {
            "venue": venue,
            "provider": provider,
            "url": url,
            "doi": doi,
            "year": year,
        }
    )
    if quality["venue_quality"] == "suspicious":
        score -= 0.25
        reasons.append("suspicious venue/publisher")
    elif quality["venue_quality"] == "reputable":
        score += 0.08
        reasons.append("reputable venue")
    elif quality["venue_quality"] == "preprint":
        score -= 0.03
        reasons.append("preprint venue")
    if quality["quality_score"] < 0.55:
        score -= 0.08
        reasons.append("requires human quality review")
    if quality["venue_quality"] == "suspicious":
        score *= 0.45
        reasons.append("credibility multiplied down by suspicious publication venue")
    elif quality["quality_score"] < 0.55:
        score *= 0.65
        reasons.append("credibility multiplied down by low publication quality")
    if re.fullmatch(r"\d{4}", str(year)):
        score += 0.05
        reasons.append("has publication year")
    return round(max(0.05, min(score, 1.0)), 2), reasons

def paper_unique_key(
    *,
    title: str,
    citation: str,
    doi: str,
    arxiv_id: str,
    semantic_scholar_id: str,
    url: str,
) -> str:
    if doi:
        return "doi:" + normalize_identifier(doi)
    if arxiv_id:
        return "arxiv:" + normalize_identifier(arxiv_id)
    if semantic_scholar_id:
        return "s2:" + normalize_identifier(semantic_scholar_id)
    if url:
        return "url:" + normalize_identifier(url)
    return "text:" + normalize_identifier(title or citation)

def normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_") or "unknown"

