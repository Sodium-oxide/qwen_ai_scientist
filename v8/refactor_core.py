"""Refactor science_core.py into submodules using AST-based extraction.

This script:
1. Parses science_core.py to find all function/class definitions with line ranges
2. Assigns each definition to a target module based on a mapping
3. Extracts source code and writes module files with appropriate imports
4. Rewrites science_core.py as a thin re-export facade
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

V8_DIR = Path(__file__).resolve().parent
SOURCE = V8_DIR / "science_core_backup.py"
if not SOURCE.exists():
    SOURCE = V8_DIR / "science_core.py"


# ─── Function-to-Module Mapping ───────────────────────────────────────────
# Each key is a target module name, each value is a set of function/class names
# that should be placed in that module.

UTILS_FUNCTIONS = {
    "normalize_space", "xml_text", "clamp_int", "numeric_value", "scalar",
    "string_list", "split_sentences", "first_sentences", "last_sentences",
    "first_nonempty", "unique_preserve_order", "trim_text", "references_for_gap",
    "safe_workspace_path", "read_literature_file", "find_by_id", "normalize_label",
    "normalize_key", "new_id", "extract_task_id",
    # Utility functions shared across modules (moved from _literature_import to break circular dep)
    "extract_section", "extract_year", "is_unknown_value", "record_context_text",
    "science_term_in_text", "repair_project_extraction_quality", "repair_unknown_field",
}

PROJECT_FUNCTIONS = {
    "create_research_project", "list_research_projects", "get_research_project",
    "load_project", "save_project", "load_search", "save_search",
    "load_subspace_map", "save_subspace_map", "search_path", "searches_dir",
    "subspace_map_path", "subspaces_dir", "project_path", "projects_dir",
    "list_literature_providers", "live_literature_provider_names",
    "default_literature_providers", "list_science_agents",
    "zhizhi_output_schema", "mingli_output_schema", "duzhi_output_schema",
    "bianlun_output_schema", "yanzhen_output_schema", "get_science_agent_prompt",
    # Domain subspace exploration
    "explore_domain_subspaces", "generate_domain_subspaces",
    "generate_domain_subspaces_with_llm", "compact_domain_label",
    "normalize_domain_subspace", "probe_domain_subspace",
    "enrich_subspace_with_probe", "build_subspace_probe_budget",
    "estimate_subspace_density", "suggested_subspace_quota",
    "domain_subspace_map_confidence", "build_subspace_coverage_plan",
    "query_plan_from_subspace_map", "build_serial_subspace_query_plan", "build_subspace_selection_interaction",
    "post_retrieval_subspace_coverage", "record_matches_terms",
    "summarize_imported_record_for_subspace",
    "build_post_retrieval_alignment_interaction",
}

LLM_FUNCTIONS = {
    "call_llm_json", "get_science_llm_client", "render_llm_response_text",
    "parse_json_object_from_text", "fenced_json_blocks", "json_repair_candidates",
    "extract_keyed_partial_array_object", "extract_keyed_partial_array",
    "extract_complete_json_objects_from_array", "first_balanced_object",
    "first_balanced_array", "parse_jsonish_dict", "normalize_llm_paper_structure",
}

LITERATURE_SEARCH_FUNCTIONS = {
    "search_papers", "search_papers_stratified", "database_to_provider",
    "extract_structured_info", "search_literature_provider_block",
    "search_literature", "search_literature_stratified",
    "diverse_rerank_literature_results", "stratified_literature_quotas", "normalize_stratified_layer_quotas",
    "stratified_literature_layers", "build_domain_query_plan",
    "expanded_ranking_query", "live_probe_literature_branch",
    "build_branch_user_interaction", "fetch_stratified_layer_blocks",
    "stratified_layer_retrieval_query", "stratified_layer_retrieval_strategy",
    "fetch_regular_backfill_blocks", "build_knowledge_pyramid",
    "choose_pyramid_review_root", "pyramid_root_score", "pyramid_relation_for_layer",
    "stratified_candidate_matches", "is_preprint_literature_result",
    "has_suspicious_literature_flags", "recover_stratified_layer_candidates",
    "is_review_like_paper", "milestone_citation_threshold", "is_top_venue_result",
    "is_low_quality_literature_result", "stratified_selection_reason",
    "flatten_literature_results", "rank_literature_results", "select_literature_result",
    "choose_seed_with_review_root_policy", "pyramid_root_from_search_record",
    "chosen_is_allowed_seed_override", "is_flagship_root_override_candidate",
    "result_identity", "summarize_literature_results", "summarize_provider_blocks",
    "summarize_literature_result", "judge_literature_candidates_with_llm",
    "query_terms", "search_arxiv", "search_semantic_scholar", "search_pubmed",
    "search_preprint_api", "search_biorxiv_or_medrxiv", "search_chemrxiv",
    "dedupe_literature_results", "literature_result_unique_key",
    "arxiv_entry_to_result", "arxiv_categories", "semantic_scholar_item_to_result",
    "pubmed_article_to_result", "biorxiv_item_to_result", "crossref_chemrxiv_item_to_result",
    "split_author_string", "first_year", "crossref_year", "provider_error_result",
    "enrich_papergraph_payload", "fetch_semantic_scholar_paper_detail",
    "merge_semantic_scholar_detail", "fetch_arxiv_by_id", "fetch_pdf_text_excerpt",
    "merge_nonempty", "http_get_text", "http_get_json", "semantic_scholar_get_json",
    "wait_for_semantic_scholar_circuit_if_needed",
    "semantic_scholar_retry_wait_seconds", "semantic_scholar_strict_interval_seconds",
    "semantic_scholar_retry_buffer_seconds", "semantic_scholar_circuit_open",
    "semantic_scholar_circuit_seconds", "register_semantic_scholar_429",
    "semantic_scholar_skip_block", "arxiv_circuit_open", "arxiv_circuit_seconds",
    "register_arxiv_429", "arxiv_skip_block", "semantic_scholar_backoff_seconds",
    "semantic_scholar_get_text", "arxiv_get_text", "semantic_scholar_retry_after_seconds",
    "semantic_scholar_cache_get", "semantic_scholar_cache_put",
    "log_semantic_scholar_key_status", "is_semantic_scholar_rate_limit_error",
    "is_rate_limit_error", "is_semantic_scholar_not_found_error",
    "wait_for_semantic_scholar_rate_limit", "wait_for_arxiv_rate_limit",
    "wall_time_from_monotonic", "read_semantic_scholar_rate_timestamp",
    "write_semantic_scholar_rate_timestamp", "read_provider_rate_timestamp",
    "write_provider_rate_timestamp", "acquire_semantic_scholar_process_lock",
    "acquire_provider_process_lock", "release_semantic_scholar_process_lock",
    "release_provider_process_lock", "ssl_context",
}

LITERATURE_SCORING_FUNCTIONS = {
    "literature_selection_base_score", "zhizhi_import_minimum_plan",
    "zhizhi_import_priority_score", "zhizhi_import_candidate_key",
    "literature_result_text_similarity", "domain_topic_profile",
    "infer_domain_topic_profile_with_llm", "infer_domain_topic_profile_heuristic",
    "normalize_domain_topic_profile", "normalize_profile_topic_list", "slug_label",
    "domain_relevance_assessment", "should_reject_for_domain", "core_domain_alignment",
    "core_domain_terms", "extract_core_domain_phrases", "core_domain_term_is_specific",
    "literature_domain_coverage_diagnostic", "literature_relevance_score",
    "literature_recency_score", "literature_impact_score",
    "publication_quality_assessment", "is_suspicious_venue", "has_suspicious_publisher",
    "is_reputable_venue", "journal_metric_for_venue", "journal_quartile_score",
    "is_mature_paper", "is_recent_paper", "publication_channel_is_strong",
    "publication_quality_assessment_no_citation", "infer_research_field",
    "fields_are_incompatible", "infer_arxiv_field", "field_citation_baseline",
    "venue_quality_label", "strip_markup",
}

LITERATURE_GRAPH_FUNCTIONS = {
    "expand_literature_graph", "expand_second_layer_graph_results",
    "select_second_layer_seeds", "second_layer_seed_score",
    "fetch_semantic_scholar_edges", "semantic_scholar_edge_to_result",
    "semantic_scholar_lookup_id", "semantic_scholar_lookup_ids",
    "build_literature_relation_graph", "relation_graph_seed", "relation_graph_node",
    "relation_graph_edge", "mechanism_terms", "mechanism_cluster_key",
    "build_mechanism_clusters", "merge_sparse_mechanism_groups",
    "nearest_mechanism_parent_key", "compute_pagerank", "compute_graph_degree",
    "summarize_relation_node", "summarize_relation_edges", "summarize_mechanism_lineage",
}

LITERATURE_IMPORT_FUNCTIONS = {
    "select_zhizhi_import_results", "import_literature_text", "import_literature_file",
    "import_literature_search_result", "import_papergraph_record",
    "extract_paper_keynote", "list_papergraph_records",
    "repair_payload_fields",
    "repair_unsupported_scenario", "scenario_is_supported_by_context",
    "sync_evidence_from_record", "verify_citation_uniqueness",
    "parse_literature_text", "extract_paper_structure", "extract_paper_structure_with_llm",
    "extract_keynote_with_llm", "extract_keynote_heuristic", "normalize_keynote",
    "merge_paper_structures", "extraction_quality_report",
    "invalid_placeholder_abstract", "looks_truncated", "background_only_text",
    "maybe_llm_reextract_structure", "is_low_information_field",
    "contains_any", "infer_ontology_field", "benchmark_allowed_for_context",
    "infer_generic_science_phrase", "clean_extracted_science_phrase", "is_generic_phrase",
    "record_source_text", "parse_paper_text",
    "extract_labeled_value", "extract_doi", "normalize_doi", "extract_authors",
    "build_citation", "extract_bullets_or_sentences",
    "infer_field", "score_evidence_credibility", "paper_unique_key",
    "normalize_identifier",
}

GAP_FUNCTIONS = {
    "add_literature_evidence", "build_knowledge_map", "build_coverage_matrix",
    "detect_reasoning_gaps", "detect_contradiction_gaps", "detect_anomaly_gaps",
    "contradiction_relation", "record_claim_text", "record_reference",
    "claim_polarity", "phrase_in_text", "first_polar_sentence",
    "first_sentence_with_terms", "detect_knowledge_gaps", "run_tanxi_gap_exploration",
    "tanxi_gap_exploration_report", "scan_coverage_density", "find_unconnected_pairs",
    "detect_suspended_problems", "prioritize_gaps", "gaps_from_density_holes",
    "gaps_from_unconnected_pairs", "gaps_from_suspended_problems",
    "tanxi_importance_score", "record_field", "concepts_are_connected",
    "concept_bridge_exists", "cross_field_synergy", "infer_barrier_to_progress",
    "align_gap_with_strategic_needs", "strategic_need_keywords",
    "default_strategic_domains", "tanxi_gap_priority_score", "importance_label",
    "evolve_domain_subspaces", "synthesize_subspace_map_from_project",
    "records_matching_subspace", "subspace_terms", "record_search_text",
    "subspace_state_metrics", "detect_subspace_fission_signals",
    "detect_subspace_fusion_signals", "detect_subspace_decline_signals",
    "detect_emergent_subspaces", "top_record_terms", "record_identity",
    "jaccard_score", "build_temporal_knowledge_graph", "temporal_yearly_counts",
    "temporal_lifecycle", "predict_temporal_hotspots", "detect_structural_knowledge_gaps",
    "build_concept_graph", "structural_gap_items", "detect_bottleneck_gap_items",
    "detect_missing_bridge_items", "connected_components", "references_for_field_pair",
    "find_structural_analogy_transfers", "encode_problem_structure",
    "classify_problem_type", "classify_data_type", "classify_constraint_type",
    "classify_problem_scale", "classify_objective_type", "problem_structure_similarity",
    "methods_for_scenario", "analogy_feasibility", "make_gap",
    "semantic_plausibility_for_pair", "method_input_requirements",
    "scenario_data_affordances", "semantic_bridge_terms", "method_looks_like_narrow_tool",
    "ambiguous_short_method_label", "project_context_mentions_pair",
    "migration_noise_risk", "count_gap_type", "dedupe_knowledge_gaps",
    "filter_low_value_gaps", "gap_signature", "gap_signature_is_subset",
    "text_jaccard", "parse_gap_input", "assess_gap_dict", "detect_migration_gaps",
    "detect_gap_signal_gaps", "detect_mechanism_issue_gaps",
    "extract_mechanism_issue_signals", "mechanism_issue_axis",
    "mechanism_issue_confidence", "mechanism_gap_description",
    "mechanism_gap_research_path", "research_path_for_gap_signal", "detect_problem_gaps",
    "local_idea_overlap", "literature_coverage_factor", "summarize_uniqueness_live_search",
    "zhizhi_standard_output", "knowledge_map_unknown_summary",
    "extract_gap_signals_from_text", "extract_gap_relevant_sections",
    "classify_gap_signal", "gap_signal_confidence", "normalize_gap_signals",
}

HYPOTHESIS_FUNCTIONS = {
    "run_mingli_hypothesis_evolution", "select_gaps_for_hypothesis",
    "seed_hypothesis_population", "infer_gap_components",
    "normalize_hypothesis_benchmark", "first_matching_label",
    "specific_mechanism_text", "method_capability_description",
    "scenario_target_description", "make_hypothesis_seed",
    "score_hypothesis_population", "score_hypothesis_candidate",
    "hypothesis_disciplinary_plausibility", "hypothesis_control_variable",
    "hypothesis_boundary_condition", "non_negated_phrase_in_text",
    "hypothesis_surprise_score", "select_diverse_hypothesis_finalists",
    "tournament_select_hypotheses", "evolve_hypothesis_offspring",
    "collect_project_analogies", "collect_project_hotspots", "best_hypothesis_score",
    "generate_idea", "design_experiment", "finalize_idea", "mingli_resolve_gap",
    "mingli_fallback_gap_from_papergraph", "mingli_resolve_idea_json",
    "mingli_candidate_to_idea_json", "mingli_title_from_statement",
    "conservative_hypothesis_statement", "innovative_hypothesis_statement",
    "mingli_risk_text", "mingli_final_schema_missing", "create_hypothesis",
}

VERIFICATION_FUNCTIONS = {
    "check_internal_consistency", "check_data_consistency", "regime_shift_test",
    "detect_selective_citation", "causal_chain_audit",
    "run_yanzhen_mechanism_verification", "run_mechanism_check",
    "ask_socratic_questions", "ask_critical_questions", "find_counterexamples",
    "stress_test_assumptions", "moderate_round", "latest_yanzhen_report",
    "summarize_positions", "extract_emergent_method", "classify_method_domain",
    "check_method_scenario_adaptability",
    "yanzhen_internal_consistency_report", "yanzhen_data_consistency_report",
    "yanzhen_regime_shift_report", "yanzhen_selective_citation_report",
    "yanzhen_causal_chain_report", "yanzhen_hypothesis_text", "yanzhen_mechanism_text",
    "yanzhen_sources_for_hypothesis", "yanzhen_cited_data_for_hypothesis",
    "yanzhen_original_conditions", "extract_causal_chain", "default_regime_shifts",
    "normalize_shifted_conditions", "render_shift_condition", "yanzhen_context_text",
    "yanzhen_evidence_contradictions", "yanzhen_conflict_or_limitation_terms",
    "yanzhen_feasibility_audit", "yanzhen_overall_verdict", "yanzhen_public_verdict",
    "yanzhen_unsupported_claims", "yanzhen_required_actions", "yanzhen_detailed_reasoning",
    "mechanism_internal_issues", "shared_terms",
}

DEBATE_FUNCTIONS = {
    "run_socratic_hypothesis_debate", "debate_hypothesis_record",
    "debate_hypothesis_text", "duzhi_generate_questions", "socratic_evidence_terms",
    "dedupe_socratic_questions", "filter_new_debate_questions",
    "question_similarity_key", "socratic_overall_severity", "debate_safety_gates",
    "is_qwen_model_id", "debate_proponent_position", "debate_experiment_text",
    "debate_refined_hypothesis", "mingli_revision_from_questions",
    "mingli_address_questions", "mingli_remaining_speculative_claims",
    "yanzhen_debate_feedback", "duzhi_questions_from_yanzhen_actions",
    "debate_question_adopted", "build_debate_state", "debate_status_from_decision",
    "debate_unresolved_issues", "debate_final_decision", "execution_level_validation",
}

SUPPLEMENT_FUNCTIONS = {
    "zhizhi_auto_supplement_blind_spots", "zhizhi_supplement_from_audit",
    "extract_academic_keyword", "build_audit_supplement_query",
    "causal_link_terms", "audit_supplement_candidate_relevance",
}

PIPELINE_FUNCTIONS = {
    "create_science_pipeline_tasks", "create_boxue_delegation_tasks",
    "boxue_default_task_specs", "boxue_delegation_task_description",
    "boxue_prompt_alignment_summary", "run_boxue_research_round",
     "boxue_run_pipeline_specialists",
    "boxue_run_autogen_groupchat_pipeline", "boxue_mark_autogen_tasks_completed",
    "boxue_completed_agents_from_autogen_run", "boxue_finalize_autogen_round",
    "boxue_execute_specialist_task", "boxue_research_query",
    "summarize_json_output", "boxue_force_complete_task", "boxue_task_state",
    "boxue_task_dependencies_completed_by_id", "boxue_load_or_create_plan",
    "boxue_find_active_plan", "boxue_specialist_prompt",
    "boxue_task_dependencies_completed", "boxue_consume_inbox",
    "boxue_review_completed_tasks", "boxue_create_revision_tasks_for_failures",
    "boxue_task_snapshot", "boxue_round_is_finished", "boxue_finalize_round",
    "boxue_round_next_step", "create_science_delegation_tasks",
    "science_delegation_branch_plan", "science_delegation_artifact_relpath",
    "science_branch_scout_description", "science_synthesis_gate_description",
    "export_research_plan", "assess_novelty", "verify_uniqueness",
    "run_zhizhi_literature_analysis", "run_zhizhi_serial_subspace_analysis", "agents_for_phase",
    "supporting_references_for_method_or_scenario", "project_records_for_mapping",
    "classify_record_evidence", "classify_evidence_claims",
}


# ─── Module ordering and metadata ──────────────────────────────────────────
MODULE_MAP = [
    ("_utils", UTILS_FUNCTIONS),
    ("_models", set()),  # handled specially — constants and dataclasses
    ("_project", PROJECT_FUNCTIONS),
    ("_llm", LLM_FUNCTIONS),
    ("_literature_search", LITERATURE_SEARCH_FUNCTIONS),
    ("_literature_scoring", LITERATURE_SCORING_FUNCTIONS),
    ("_literature_graph", LITERATURE_GRAPH_FUNCTIONS),
    ("_literature_import", LITERATURE_IMPORT_FUNCTIONS),
    ("_gap_detection", GAP_FUNCTIONS),
    ("_hypothesis", HYPOTHESIS_FUNCTIONS),
    ("_verification", VERIFICATION_FUNCTIONS),
    ("_debate", DEBATE_FUNCTIONS),
    ("_supplement", SUPPLEMENT_FUNCTIONS),
    ("_pipeline", PIPELINE_FUNCTIONS),
]

# Module → set of function names it owns
OWNERS: dict[str, set[str]] = {name: funcs for name, funcs in MODULE_MAP}


# ─── AST Parsing ───────────────────────────────────────────────────────────
def parse_definitions(source_text: str) -> list[dict[str, Any]]:
    """Parse all top-level function and class definitions with their line ranges."""
    tree = ast.parse(source_text)
    defs: list[dict[str, Any]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            defs.append({
                "name": node.name,
                "kind": "function",
                "start_line": start,
                "end_line": node.end_lineno or node.lineno,
                "decorators": [ast.dump(d) for d in node.decorator_list],
            })
        elif isinstance(node, ast.ClassDef):
            # Include decorator lines in the start line
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            defs.append({
                "name": node.name,
                "kind": "class",
                "start_line": start,
                "end_line": node.end_lineno or node.lineno,
                "decorators": [ast.dump(d) for d in node.decorator_list],
            })
        elif isinstance(node, ast.Assign):
            # Module-level assignments (constants, state)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defs.append({
                        "name": target.id,
                        "kind": "assignment",
                        "start_line": node.lineno,
                        "end_line": node.end_lineno or node.lineno,
                    })
        elif isinstance(node, ast.AnnAssign):
            # Annotated assignments like SCIENCE_AGENTS: dict[...] = {...}
            if isinstance(node.target, ast.Name):
                defs.append({
                    "name": node.target.id,
                    "kind": "assignment",
                    "start_line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                })
    return defs


def find_owner(name: str) -> str | None:
    """Find which module owns a given name."""
    for module_name, func_set in MODULE_MAP:
        if name in func_set:
            return module_name
    return None


# ─── Source extraction ──────────────────────────────────────────────────────
def extract_lines(source_lines: list[str], start: int, end: int) -> str:
    """Extract source lines (1-indexed start, inclusive end)."""
    return "\n".join(source_lines[start - 1 : end])


def build_module(
    module_name: str,
    func_names: set[str],
    all_defs: list[dict[str, Any]],
    source_lines: list[str],
    source_text: str,
) -> str:
    """Build the source code for a submodule with lazy cross-module imports."""
    # Find all definitions assigned to this module
    my_defs = [d for d in all_defs if d["name"] in func_names]

    # For _literature_search, also include the mutable state variables
    extra_defs: list[dict[str, Any]] = []
    if module_name == "_literature_search":
        extra_defs = [
            d for d in all_defs
            if d["kind"] == "assignment" and d["name"] in MUTABLE_STATE_IN_LIT_SEARCH
        ]

    all_my_defs = sorted(my_defs + extra_defs, key=lambda d: d["start_line"])

    # Build the set of all names owned by THIS module (for exclusion)
    my_names = set(func_names) | {d["name"] for d in extra_defs}

    # Build a name→module mapping for all cross-module names
    name_to_module: dict[str, str] = {}
    for other_name, other_funcs in MODULE_MAP:
        if other_name == module_name:
            continue
        for fname in other_funcs:
            name_to_module[fname] = other_name

    # Also add _models.py constants/classes to the mapping
    # These are module-level assignments and classes not in any function set
    if module_name != "_models":
        models_defs = [d for d in all_defs if d["kind"] in ("assignment", "class")]
        # Only include names NOT already in a function set (to avoid conflicts)
        all_func_names: set[str] = set()
        for _, funcs in MODULE_MAP:
            all_func_names.update(funcs)
        for d in models_defs:
            if d["name"] not in all_func_names and not d["name"].startswith("_"):
                name_to_module[d["name"]] = "_models"

    # Build import header — ONLY stdlib + config, NO cross-module imports
    my_source = "\n".join(
        extract_lines(source_lines, d["start_line"], d["end_line"])
        for d in all_my_defs
    )
    header = build_import_header_no_cross(module_name, my_source)

    # Build module body with lazy cross-module imports inside each function
    body_parts: list[str] = []
    for d in all_my_defs:
        code = extract_lines(source_lines, d["start_line"], d["end_line"])
        if d["kind"] == "function":
            code = inject_lazy_imports(code, name_to_module, my_names)
        body_parts.append(code)
        body_parts.append("")  # blank line between defs

    body = "\n".join(body_parts)

    return f'{header}\n\n\n{body}\n'


def inject_lazy_imports(func_code: str, name_to_module: dict[str, str], my_names: set[str]) -> str:
    """Inject lazy cross-module import statements at the start of a function body.

    Scans the function source for references to cross-module names and adds
    'from _module import name' at the beginning of the function body.
    """
    lines = func_code.split("\n")
    if len(lines) < 2:
        return func_code

    # Find the line that ends the function signature (the line with the colon)
    sig_end_idx = -1
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped.endswith(":"):
            sig_end_idx = i
            break
    if sig_end_idx < 0:
        return func_code  # can't find signature end, skip

    # Find the indentation of the function body (first non-empty line after sig)
    body_indent = ""
    for line in lines[sig_end_idx + 1:]:
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'''"):
            body_indent = line[:len(line) - len(stripped)]
            break
    if not body_indent:
        body_indent = "    "

    # Scan the function body for cross-module name references
    needed_imports: dict[str, list[str]] = {}  # module -> [names]
    func_body_text = "\n".join(lines[sig_end_idx + 1:])
    for name, mod in name_to_module.items():
        if name in my_names:
            continue
        if re.search(r'\b' + re.escape(name) + r'\b', func_body_text):
            needed_imports.setdefault(mod, []).append(name)

    if not needed_imports:
        return func_code

    # Build lazy import lines with try/except for relative/absolute compatibility
    import_lines: list[str] = []
    try_lines: list[str] = []
    except_lines: list[str] = []
    for mod, names in sorted(needed_imports.items()):
        names_str = ", ".join(sorted(names))
        try_lines.append(f"{body_indent}    from .{mod} import {names_str}")
        except_lines.append(f"{body_indent}    from {mod} import {names_str}")
    if try_lines:
        import_lines.append(f"{body_indent}try:")
        import_lines.extend(try_lines)
        import_lines.append(f"{body_indent}except ImportError:")
        import_lines.extend(except_lines)

    # Insert after the signature line
    result = lines[:sig_end_idx + 1]
    result.extend(import_lines)
    result.extend(lines[sig_end_idx + 1:])
    return "\n".join(result)


def build_import_header_no_cross(module_name: str, my_source: str) -> str:
    """Build import header with ONLY stdlib and config imports (no cross-module)."""
    lines = ['from __future__ import annotations', '']

    # Standard library imports
    stdlib_imports = []
    stdlib_map = {
        "ast": "import ast",
        "json": "import json",
        "math": "import math",
        "re": "import re",
        "ssl": "import ssl",
        "threading": "import threading",
        "time": "import time",
        "ThreadPoolExecutor": "from concurrent.futures import ThreadPoolExecutor, as_completed",
        "as_completed": "from concurrent.futures import ThreadPoolExecutor, as_completed",
        "Counter": "from collections import Counter, defaultdict",
        "defaultdict": "from collections import Counter, defaultdict",
        "asdict": "from dataclasses import asdict, dataclass, field",
        "dataclass": "from dataclasses import asdict, dataclass, field",
        "field": "from dataclasses import asdict, dataclass, field",
        "date": "from datetime import date, timedelta",
        "timedelta": "from datetime import date, timedelta",
        "BytesIO": "from io import BytesIO",
        "Path": "from pathlib import Path",
        "Any": "from typing import Any",
        "HTTPError": "from urllib.error import HTTPError, URLError",
        "URLError": "from urllib.error import HTTPError, URLError",
        "quote": "from urllib.parse import quote, urlencode",
        "urlencode": "from urllib.parse import quote, urlencode",
        "Request": "from urllib.request import Request, urlopen",
        "urlopen": "from urllib.request import Request, urlopen",
        "ET": "import xml.etree.ElementTree as ET",
    }
    seen_imports: set[str] = set()
    for marker, imp in stdlib_map.items():
        if marker in my_source and imp not in seen_imports:
            stdlib_imports.append(imp)
            seen_imports.add(imp)
    if stdlib_imports:
        lines.extend(sorted(set(stdlib_imports)))
        lines.append("")

    # Config imports
    config_names = [
        "QWEN_API_BASE", "QWEN_API_KEY", "QWEN_MODEL_ID",
        "SCIENCE_ARXIV_CIRCUIT_SECONDS", "SCIENCE_ARXIV_MIN_INTERVAL_SECONDS",
        "SCIENCE_DIR", "SCIENCE_INSECURE_SSL", "SCIENCE_LLM_EXTRACTOR",
        "SCIENCE_SEMANTIC_SCHOLAR_429_BACKOFF_SECONDS",
        "SCIENCE_SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS",
        "SCIENCE_SEMANTIC_SCHOLAR_CIRCUIT_SECONDS",
        "SCIENCE_SEMANTIC_SCHOLAR_EDGE_LIMIT",
        "SCIENCE_SEMANTIC_SCHOLAR_FAIL_FAST_ON_429",
        "SCIENCE_SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS",
        "SCIENCE_SEMANTIC_SCHOLAR_PROBE_VARIANTS",
        "SCIENCE_SEMANTIC_SCHOLAR_RETRY_LIMIT",
        "SCIENCE_STRATIFIED_MAX_BRANCHES_PER_LAYER",
        "SCIENCE_SUBSPACE_PROBE_MAX_CALLS_PER_PROVIDER",
        "SCIENCE_ZHIZHI_DEFAULT_IMPORT_TOP_K", "SCIENCE_ZHIZHI_MAX_IMPORT_TOP_K",
        "SEMANTIC_SCHOLAR_API_KEY", "WORKDIR",
    ]
    needed_config = [n for n in config_names if n in my_source]
    needs_log_event = "log_event" in my_source

    if needed_config or needs_log_event:
        if needed_config:
            config_str = ",\n        ".join(needed_config)
            lines.append("try:")
            lines.append(f"    from .config import (\n        {config_str},\n    )")
            if needs_log_event:
                lines.append("    from .log import log_event")
            lines.append("except ImportError:")
            lines.append(f"    from config import (\n        {config_str},\n    )")
            if needs_log_event:
                lines.append("    from log import log_event")
        else:
            # Only log_event needed, no config vars
            lines.append("try:")
            lines.append("    from .log import log_event")
            lines.append("except ImportError:")
            lines.append("    from log import log_event")
        lines.append("")

    return "\n".join(lines)


# ─── _models.py special handler ────────────────────────────────────────────
# Mutable state variables that must stay in _literature_search.py
# because the functions that WRITE them are in that module
MUTABLE_STATE_IN_LIT_SEARCH = {
    "SEMANTIC_SCHOLAR_LAST_REQUEST_AT",
    "SEMANTIC_SCHOLAR_COOLDOWN_UNTIL",
    "SEMANTIC_SCHOLAR_429_COUNT",
    "SEMANTIC_SCHOLAR_KEY_STATUS_LOGGED",
    "SEMANTIC_SCHOLAR_RESPONSE_CACHE",
    "ARXIV_LAST_REQUEST_AT",
    "ARXIV_COOLDOWN_UNTIL",
    "ARXIV_429_COUNT",
}


def build_models_module(source_lines: list[str], all_defs: list[dict[str, Any]]) -> str:
    """Build _models.py which contains constants, dataclasses, and state."""
    # All assignments and class definitions go here EXCEPT mutable state
    assignments = [
        d for d in all_defs
        if d["kind"] == "assignment" and d["name"] not in MUTABLE_STATE_IN_LIT_SEARCH
    ]
    classes = [d for d in all_defs if d["kind"] == "class"]

    all_items = sorted(assignments + classes, key=lambda d: d["start_line"])

    header = """from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .config import SCIENCE_DIR
    from .log import log_event
except ImportError:
    from config import SCIENCE_DIR
    from log import log_event
"""
    body_parts = []
    for d in all_items:
        code = extract_lines(source_lines, d["start_line"], d["end_line"])
        body_parts.append(code)
        body_parts.append("")

    return f"{header}\n\n{chr(10).join(body_parts)}\n"


# ─── Main refactoring logic ───────────────────────────────────────────────
def main():
    source_text = SOURCE.read_text(encoding="utf-8")
    source_lines = source_text.splitlines()
    all_defs = parse_definitions(source_text)

    print(f"Parsed {len(all_defs)} top-level definitions")

    # Check for unassigned functions
    all_assigned: set[str] = set()
    for _, funcs in MODULE_MAP:
        all_assigned.update(funcs)

    unassigned_funcs = [
        d for d in all_defs
        if d["kind"] == "function" and d["name"] not in all_assigned
    ]
    if unassigned_funcs:
        print(f"WARNING: {len(unassigned_funcs)} functions not assigned to any module:")
        for d in unassigned_funcs[:20]:
            print(f"  - {d['name']} (line {d['start_line']})")
        if len(unassigned_funcs) > 20:
            print(f"  ... and {len(unassigned_funcs) - 20} more")

    # Check for duplicate assignments
    seen: dict[str, str] = {}
    for mod_name, funcs in MODULE_MAP:
        for f in funcs:
            if f in seen:
                print(f"WARNING: {f} assigned to both {seen[f]} and {mod_name}")
            seen[f] = mod_name

    # Build _models.py first
    print("\nBuilding _models.py...")
    models_code = build_models_module(source_lines, all_defs)
    models_path = V8_DIR / "_models.py"
    models_path.write_text(models_code, encoding="utf-8")
    print(f"  Written {models_path} ({len(models_code.splitlines())} lines)")

    # Build each function module
    for module_name, func_names in MODULE_MAP:
        if module_name == "_models":
            continue  # already handled
        if not func_names:
            continue

        print(f"Building {module_name}.py...")
        code = build_module(module_name, func_names, all_defs, source_lines, source_text)
        path = V8_DIR / f"{module_name}.py"
        path.write_text(code, encoding="utf-8")
        print(f"  Written {path} ({len(code.splitlines())} lines)")

    # Build science_core.py facade
    print("\nBuilding science_core.py facade...")
    facade = build_facade()
    facade_path = V8_DIR / "science_core.py"
    # Backup original
    backup_path = V8_DIR / "science_core_backup.py"
    if not backup_path.exists():
        import shutil
        shutil.copy2(facade_path, backup_path)
        print(f"  Backed up original to {backup_path}")
    facade_path.write_text(facade, encoding="utf-8")
    print(f"  Written {facade_path} ({len(facade.splitlines())} lines)")

    print("\nDone! Run 'python -c \"import science_core\"' to verify.")


def build_facade() -> str:
    """Build the re-export facade for science_core.py."""
    module_names = [name for name, _ in MODULE_MAP]

    lines = [
        '"""Science Core — Re-export facade.',
        '',
        'This module re-exports all public symbols from the split submodules.',
        'External code importing from science_core should see no change.',
        '"""',
        'from __future__ import annotations',
        '',
        'try:',
    ]

    for mod in module_names:
        lines.append(f"    from .{mod} import *  # noqa: F401,F403")

    lines.append("except ImportError:")
    for mod in module_names:
        lines.append(f"    from {mod} import *  # noqa: F401,F403")

    lines.append("")
    lines.append("# Make submodule references available for internal use")
    lines.append("try:")
    for mod in module_names:
        lines.append(f"    from . import {mod}")
    lines.append("except ImportError:")
    for mod in module_names:
        lines.append(f"    import {mod}")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
