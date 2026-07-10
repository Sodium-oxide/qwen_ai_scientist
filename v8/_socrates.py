"""Socrates: mechanism enrichment through targeted literature search.

Socrates sits between TanXi (gap detection) and MingLi (hypothesis generation).
When a gap's mechanism contract has unresolved fields, Socrates:
1. Translates each unresolved field into specific literature search queries
2. Calls ZhiZhi to do targeted searches and import papers
3. Extracts mechanism evidence from newly imported papers
4. Updates the mechanism contract
5. Repeats until all fields are resolved (or max iterations reached)

Core principle: never fabricate mechanisms — all mechanism info must come from
literature evidence. If a field cannot be resolved through search, mark it as
"no_literature_support" rather than leaving it "unresolved".
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

try:
    from .log import log_event
except ImportError:
    from log import log_event

_logger = logging.getLogger(__name__)

# Maximum Socrates enrichment iterations per gap
SOCRATES_MAX_ITERATIONS = 3
# Maximum targeted searches per unresolved field per iteration
SOCRATES_MAX_SEARCHES_PER_FIELD = 2

# Mapping from mechanism contract fields to search query templates.
# Each template receives {domain}, {method}, {scenario}, {mediator} placeholders.
MECHANISM_FIELD_QUERY_TEMPLATES: dict[str, list[str]] = {
    "identity": [
        "{domain} {mediator} mechanism physical chemical nature",
        "{domain} {mediator} what is characterization",
    ],
    "location_or_scope": [
        "{domain} {mediator} spatial location site interface region",
        "{domain} {method} {scenario} where does occur",
    ],
    "dynamics": [
        "{domain} {mediator} kinetics rate accumulation time evolution",
        "{domain} {mediator} dynamics growth decay model",
    ],
    "reversibility": [
        "{domain} {mediator} reversibility recovery annealing restoration",
        "{domain} {mediator} irreversible permanent transient",
    ],
    "observability": [
        "{domain} {mediator} measurement technique spectroscopy microscopy detection",
        "{domain} {mediator} experimental observation characterization method",
    ],
    "intervention": [
        "{domain} {method} {mediator} manipulation control suppression enhancement",
        "{domain} {mediator} intervention ablation blocking experiment",
    ],
    "counterfactual": [
        "{domain} {mediator} absence without suppression control experiment",
        "{domain} if {mediator} does not occur then outcome unchanged",
    ],
}


def translate_unresolved_to_queries(
    unresolved_fields: list[str],
    domain: str,
    method: str = "",
    scenario: str = "",
    mediator: str = "",
) -> dict[str, list[str]]:
    """Translate unresolved mechanism fields into targeted literature search queries.

    For each unresolved field, generate 2-3 specific search queries using
    synonym expansion to cover multiple possibilities without presupposing answers.

    Returns: dict mapping field_name -> list of search query strings.
    """
    queries: dict[str, list[str]] = {}
    placeholders = {
        "domain": domain.strip()[:80],
        "method": (method or "").strip()[:60],
        "scenario": (scenario or "").strip()[:60],
        "mediator": (mediator or "").strip()[:60],
    }
    # Remove empty placeholders to avoid double spaces
    placeholders = {k: v for k, v in placeholders.items() if v}

    for field in unresolved_fields:
        templates = MECHANISM_FIELD_QUERY_TEMPLATES.get(field, [])
        field_queries: list[str] = []
        for tmpl in templates[:SOCRATES_MAX_SEARCHES_PER_FIELD]:
            try:
                query = tmpl.format(**placeholders)
            except KeyError:
                query = tmpl.format_map({k: placeholders.get(k, "") for k in re.findall(r"\{(\w+)\}", tmpl)})
            # Clean up double spaces and leading/trailing whitespace
            query = re.sub(r"\s{2,}", " ", query).strip()
            if query:
                field_queries.append(query)
        if field_queries:
            queries[field] = field_queries

    return queries


def extract_mechanism_evidence(
    project: dict[str, Any],
    target_fields: list[str],
    mediator: str = "",
    max_sentences: int = 5,
) -> dict[str, list[str]]:
    """Extract mechanism-relevant evidence sentences from the project's PaperGraph.

    Scans all papergraph records for sentences that mention the mediator or
    related terms and are relevant to the target mechanism fields.

    Returns: dict mapping field_name -> list of evidence sentences.
    """
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space

    evidence: dict[str, list[str]] = {field: [] for field in target_fields}
    mediator_lower = normalize_space(mediator).lower() if mediator else ""

    # Field-specific keyword sets for evidence matching
    field_keywords: dict[str, set[str]] = {
        "identity": {"mechanism", "nature", "characterized", "identified as", "consists of",
                      "composed of", "is a", "defined as", "corresponds to", "represents"},
        "location_or_scope": {"at the", "interface", "surface", "boundary", "region", "site",
                               "layer", "within", "near", "localized", "confined to"},
        "dynamics": {"rate", "kinetics", "accumulation", "growth", "decay", "evolution",
                      "time-dependent", "increases with", "decreases with", "proportional"},
        "reversibility": {"reversible", "irreversible", "recovery", "annealing", "restoration",
                           "permanent", "transient", "hysteresis", "memory effect"},
        "observability": {"measured by", "detected via", "characterized using", "observed with",
                           "spectroscopy", "microscopy", "diffraction", "imaging", "signal"},
        "intervention": {"controlled by", "suppressed", "enhanced", "blocked", "ablation",
                          "manipulated", "varied", "modulated", "inhibited"},
        "counterfactual": {"without", "in the absence of", "when suppressed", "if not",
                            "control experiment", "baseline", "null"},
    }

    papers = project.get("papergraph", [])
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        text_parts = [
            str(paper.get("abstract") or ""),
            str(paper.get("conclusion") or ""),
            str(paper.get("limitation") or ""),
            str(paper.get("full_text_excerpt") or ""),
        ]
        full_text = " ".join(text_parts)
        if not full_text.strip():
            continue

        # Split into sentences
        sentences = re.split(r"(?<=[.!?])\s+", full_text)
        for sentence in sentences:
            sentence_clean = normalize_space(sentence)
            if len(sentence_clean) < 20 or len(sentence_clean) > 500:
                continue
            sentence_lower = sentence_clean.lower()

            # Check if sentence mentions the mediator
            mediator_match = mediator_lower and mediator_lower in sentence_lower

            for field in target_fields:
                keywords = field_keywords.get(field, set())
                keyword_match = any(kw in sentence_lower for kw in keywords)
                if mediator_match or keyword_match:
                    if len(evidence[field]) < max_sentences:
                        evidence[field].append(sentence_clean[:300])

    return evidence


def check_mechanism_contract_completeness(
    contract: dict[str, Any],
) -> list[str]:
    """Check which fields in a mechanism contract are still unresolved.

    Returns a list of field names that are unresolved (empty, 'unresolved', or placeholder).
    """
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space

    unresolved_markers = {"", "unresolved", "unknown", "unspecified", "tbd", "n/a", "none", "pending"}
    check_fields = [
        "identity", "location_or_scope", "dynamics",
        "reversibility", "observability", "intervention", "counterfactual",
    ]
    spec = contract if isinstance(contract, dict) else {}
    # Also check mechanism_specification sub-dict
    mech_spec = spec.get("mechanism_specification", {}) if isinstance(spec.get("mechanism_specification"), dict) else {}

    unresolved: list[str] = []
    for field in check_fields:
        value = spec.get(field) or mech_spec.get(field) or ""
        normalized = normalize_space(str(value)).lower()
        if normalized in unresolved_markers or len(normalized) < 8:
            unresolved.append(field)
        # Special check for observability (should be a list with items)
        if field == "observability" and isinstance(value, list) and len(value) == 0:
            unresolved.append(field)

    return list(set(unresolved))  # dedupe


def socrates_mechanism_enrichment(
    project_id: str,
    gap: dict[str, Any],
    mechanism_contract: dict[str, Any],
    domain: str = "",
    providers: list[str] | None = None,
    max_iterations: int = SOCRATES_MAX_ITERATIONS,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Run the Socrates mechanism enrichment loop.

    For a gap with an incomplete mechanism contract:
    1. Identify unresolved fields
    2. Translate each into targeted search queries
    3. Search and import relevant papers via ZhiZhi
    4. Extract mechanism evidence from new papers
    5. Update the mechanism contract
    6. Repeat until all fields resolved or max iterations reached

    Returns the enriched mechanism contract + enrichment report.
    """
    try:
        from ._literature_search import search_semantic_scholar, search_arxiv, flatten_literature_results, dedupe_literature_results
        from ._literature_import import import_literature_search_result
        from ._project import load_project, save_project
    except ImportError:
        from _literature_search import search_semantic_scholar, search_arxiv, flatten_literature_results, dedupe_literature_results
        from _literature_import import import_literature_search_result
        from _project import load_project, save_project

    if not providers:
        providers = ["semantic_scholar", "arxiv"]

    contract = dict(mechanism_contract) if isinstance(mechanism_contract, dict) else {}
    enrichment_log: list[dict[str, Any]] = []
    total_searches = 0
    total_imports = 0

    # Extract context from gap for query generation
    gap_desc = str(gap.get("description") or "")[:200]
    ingredients = gap.get("hypothesis_ingredients", {}) if isinstance(gap.get("hypothesis_ingredients"), dict) else {}
    method = (ingredients.get("methods") or [""])[0] if ingredients.get("methods") else ""
    scenario = (ingredients.get("scenarios") or [""])[0] if ingredients.get("scenarios") else ""
    mediator = str(contract.get("identity") or contract.get("mechanism_claim") or "").strip()

    for iteration in range(max_iterations):
        unresolved = check_mechanism_contract_completeness(contract)
        if not unresolved:
            log_event("SCIENCE", "socrates_complete",
                      iteration=iteration, reason="all_fields_resolved")
            break

        log_event("SCIENCE", "socrates_iteration",
                  iteration=iteration + 1, max_iterations=max_iterations,
                  unresolved_fields=unresolved, unresolved_count=len(unresolved))

        # Translate unresolved fields to search queries
        field_queries = translate_unresolved_to_queries(
            unresolved, domain=domain, method=method,
            scenario=scenario, mediator=mediator,
        )

        iteration_searches = 0
        iteration_imports = 0

        for field, queries in field_queries.items():
            for query in queries[:SOCRATES_MAX_SEARCHES_PER_FIELD]:
                log_event("SCIENCE", "socrates_targeted_search",
                          field=field, query=query[:80],
                          iteration=iteration + 1)

                # Execute targeted search
                blocks: list[dict[str, Any]] = []
                if "semantic_scholar" in providers:
                    block = search_semantic_scholar(query, max_results=5)
                    blocks.append(block)
                if "arxiv" in providers:
                    arxiv_block = search_arxiv(query, max_results=3)
                    blocks.append(arxiv_block)

                results = dedupe_literature_results(flatten_literature_results(blocks))
                iteration_searches += 1
                total_searches += 1

                # Import top results
                search_id = f"socrates_{iteration}_{field}_{total_searches}"
                for idx, result in enumerate(results[:3]):
                    result["search_id"] = search_id
                    result["result_index"] = idx
                    try:
                        imported = json.loads(import_literature_search_result(
                            project_id, search_id, idx, use_llm=use_llm
                        ))
                        if imported.get("status") != "duplicate":
                            iteration_imports += 1
                            total_imports += 1
                            title = str(result.get("title") or "")[:100]
                            log_event("SCIENCE", "socrates_paper_imported",
                                      field=field, title=title,
                                      iteration=iteration + 1)
                    except Exception as exc:
                        log_event("SCIENCE", "socrates_import_failed",
                                  field=field, error=str(exc)[:200])

        # Extract mechanism evidence from updated PaperGraph
        project = load_project(project_id)
        evidence = extract_mechanism_evidence(
            project, target_fields=unresolved, mediator=mediator,
        )

        # Update contract with extracted evidence
        fields_updated = 0
        for field in unresolved:
            evidence_sentences = evidence.get(field, [])
            if evidence_sentences:
                # Use the best evidence sentence to fill the field
                if field == "observability":
                    contract[field] = evidence_sentences[:3]
                else:
                    contract[field] = evidence_sentences[0][:300]
                fields_updated += 1

        enrichment_log.append({
            "iteration": iteration + 1,
            "unresolved_at_start": list(unresolved),
            "searches_performed": iteration_searches,
            "papers_imported": iteration_imports,
            "fields_updated": fields_updated,
            "evidence_found": {f: len(e.get(f, [])) for f in unresolved},
        })

        log_event("SCIENCE", "socrates_iteration_complete",
                  iteration=iteration + 1,
                  searches=iteration_searches,
                  imports=iteration_imports,
                  fields_updated=fields_updated,
                  remaining_unresolved=len(unresolved) - fields_updated)

        # If no fields were updated this iteration, stop (no progress)
        if fields_updated == 0:
            log_event("SCIENCE", "socrates_stalled",
                      iteration=iteration + 1,
                      reason="no_evidence_found_for_unresolved_fields")
            break

    # Final completeness check
    remaining_unresolved = check_mechanism_contract_completeness(contract)
    contract["socrates_enrichment"] = {
        "iterations_run": len(enrichment_log),
        "total_searches": total_searches,
        "total_imports": total_imports,
        "enrichment_log": enrichment_log,
        "remaining_unresolved": remaining_unresolved,
        "verdict": "COMPLETE" if not remaining_unresolved else "PARTIAL",
    }

    # Mark fields that could not be resolved as "no_literature_support"
    for field in remaining_unresolved:
        contract[field] = "no_literature_support"

    log_event("SCIENCE", "socrates_finished",
              total_iterations=len(enrichment_log),
              total_searches=total_searches,
              total_imports=total_imports,
              remaining=len(remaining_unresolved),
              verdict=contract["socrates_enrichment"]["verdict"])

    return {
        "mechanism_contract": contract,
        "enrichment_report": contract["socrates_enrichment"],
        "gap_id": gap.get("gap_id", ""),
    }
