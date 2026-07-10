from __future__ import annotations

import json
import re
import logging
import time
from pathlib import Path
from typing import Any

try:
    from .config import PACKAGE_DIR
    from .log import log_event
except ImportError:
    from config import PACKAGE_DIR
    from log import log_event

_logger = logging.getLogger(__name__)


AUTOGEN_DIR = PACKAGE_DIR / ".science" / "autogen_groupchats"
AUTOGEN_RUN_DIR = PACKAGE_DIR / ".science" / "autogen_runs"

DEFAULT_AUTOGEN_AGENTS = ["boxue", "zhizhi", "tanxi", "mingli", "yanzhen", "duzhi", "bianlun"]


def create_autogen_groupchat(
    project_id: str,
    goal: str = "",
    agents: list[str] | None = None,
    max_round: int = 12,
    speaker_selection_method: str = "round_robin",
    human_input_mode: str = "TERMINATE",
    use_native_autogen: bool = False,
) -> str:
    groupchat_id = new_autogen_groupchat_id()
    selected_agents = normalize_agent_list(agents)
    spec = {
        "groupchat_id": groupchat_id,
        "project_id": project_id,
        "goal": goal,
        "framework": "autogen_2_groupchat",
        "native_autogen": native_autogen_status(use_native=use_native_autogen),
        "groupchat": {
            "max_round": clamp_int(max_round, 4, 40),
            "speaker_selection_method": normalize_speaker_selection(speaker_selection_method),
            "allow_repeat_speaker": False,
            "human_input_mode": normalize_human_input_mode(human_input_mode),
            "termination_marker": "TERMINATE",
        },
        "agents": [science_agent_to_autogen_agent(agent) for agent in selected_agents],
        "tools": build_autogen_tool_registry(),
        "round_protocol": build_socratic_groupchat_protocol(),
        "execution_policy": {
            "worktree": "disabled",
            "background_threads": "disabled",
            "state_owner": "groupchat_manager",
            "shared_project_writes": "serialized_by_autogen_flow",
            "token_policy": "structured_turns_not_freeform_chat",
        },
        "createdAt": time.time(),
    }
    save_json(AUTOGEN_DIR / f"{groupchat_id}.json", spec)
    log_event("AUTOGEN", "groupchat_created", groupchat_id=groupchat_id, project_id=project_id)
    return json.dumps(spec, ensure_ascii=False, indent=2)


def enforce_qwen_model_family(value: str, default: str) -> str:
    """Validate that *value* is a qwen-family model name.

    If the value is non-empty and starts with 'qwen' (case-insensitive),
    return it unchanged.  Otherwise log a warning and return *default*.
    """
    stripped = (value or "").strip()
    if stripped and stripped.lower().startswith("qwen"):
        return stripped
    if stripped:
        _logger.warning(
            "Non-qwen model family '%s' was passed; silently replacing with default '%s'.",
            value,
            default,
        )
    return default


def run_autogen_research_flow(
    project_id: str,
    goal: str = "",
    groupchat_id: str = "",
    providers: list[str] | None = None,
    max_results: int = 50,
    import_top_k: int = 20,
    use_llm: bool = True,
    live_search: bool = False,
    run_debate: bool = True,
    max_round: int = 12,
    speaker_selection_method: str = "round_robin",
    human_input_mode: str = "TERMINATE",
    proponent_model_family: str = "qwen-plus",
    opponent_model_family: str = "qwen-max",
    judge_model_family: str = "qwen-deep-research",
    verifier_model_family: str = "qwen-plus",
    use_native_autogen: bool = False,
) -> str:
    try:
        from .science_core import (
            ask_socratic_questions,
            default_literature_providers,
            design_experiment,
            finalize_idea,
            generate_idea,
            grade_knowledge_sufficiency,
            load_project,
            run_socratic_hypothesis_debate,
            run_tanxi_gap_exploration,
            run_yanzhen_mechanism_verification,
            run_zhizhi_literature_analysis,
        )
    except ImportError:
        from science_core import (
            ask_socratic_questions,
            default_literature_providers,
            design_experiment,
            finalize_idea,
            generate_idea,
            grade_knowledge_sufficiency,
            load_project,
            run_socratic_hypothesis_debate,
            run_tanxi_gap_exploration,
            run_yanzhen_mechanism_verification,
            run_zhizhi_literature_analysis,
        )

    # Enforce qwen-family models for all debate/verification roles.
    proponent_model_family = enforce_qwen_model_family(proponent_model_family, "qwen-plus")
    opponent_model_family = enforce_qwen_model_family(opponent_model_family, "qwen-max")
    judge_model_family = enforce_qwen_model_family(judge_model_family, "qwen-deep-research")
    verifier_model_family = enforce_qwen_model_family(verifier_model_family, "qwen-plus")

    # Enforce minimum search/import budgets to prevent LLM from starving the pipeline
    max_results = max(int(max_results or 50), 50)
    import_top_k = max(int(import_top_k or 20), 20)

    # Enforce minimum provider set: always include semantic_scholar + arxiv for preprint coverage
    MINIMUM_PROVIDERS = ["semantic_scholar", "arxiv"]
    if not providers:
        providers = list(MINIMUM_PROVIDERS)
    else:
        normalized = [p.strip().lower().replace("-", "_") for p in providers]
        for required in MINIMUM_PROVIDERS:
            if required not in normalized:
                providers = list(providers) + [required]
                _logger.warning(f"Provider '{required}' was missing from LLM request; auto-added for coverage.")

    project = load_project(project_id)
    if groupchat_id:
        groupchat_spec = load_json(AUTOGEN_DIR / f"{groupchat_id}.json")
    else:
        groupchat_spec = json.loads(
            create_autogen_groupchat(
                project_id=project_id,
                goal=goal or str(project.get("objective", "")),
                max_round=max_round,
                speaker_selection_method=speaker_selection_method,
                human_input_mode=human_input_mode,
                use_native_autogen=use_native_autogen,
            )
        )
        groupchat_id = str(groupchat_spec.get("groupchat_id"))

    run_id = new_autogen_run_id()
    domain = str(project.get("domain") or project.get("title") or "")
    # Derive search query from domain (research field), NOT from goal (project objective).
    # Goal describes what the agent should DO; domain describes what to SEARCH FOR.
    search_query = domain or str(project.get("title") or project.get("objective") or "")
    # Strip Chinese characters and non-search text from the query
    search_query = re.sub(r"[\u4e00-\u9fff]+", "", search_query).strip()
    search_query = re.sub(r"[/\|]+", " ", search_query).strip()
    search_query = re.sub(r"\s{2,}", " ", search_query).strip()
    if not search_query:
        search_query = goal or str(project.get("objective") or "")
    query = search_query
    selected_providers = providers or default_literature_providers(domain=domain, query=query)
    turns: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "project_id": project_id,
        "groupchat_id": groupchat_id,
        "goal": query,
        "framework": "autogen_2_groupchat",
        "hypothesis_id": "",
        "draft_idea_id": "",
        "final_decision": "not_started",
    }

    def record_turn(round_name: str, speaker: str, content: Any, status: str = "completed", error: str = "") -> None:
        turns.append(
            {
                "round": round_name,
                "speaker": speaker,
                "status": status,
                "content": safe_json_output(content),
                "error": error,
                "timestamp": time.time(),
            }
        )

    log_event(
        "AUTOGEN",
        "groupchat_start",
        groupchat_id=groupchat_id,
        run_id=run_id,
        project_id=project_id,
        max_round=groupchat_spec.get("groupchat", {}).get("max_round"),
    )
    try:
        if "zhizhi" in autogen_agent_keys(groupchat_spec):
            output = json.loads(
                run_zhizhi_literature_analysis(
                    project_id=project_id,
                    domain=domain,
                    query=query,
                    max_results=max_results,
                    providers=selected_providers,
                    import_top_k=import_top_k,
                    use_llm=use_llm,
                    live_coverage_check=True,
                )
            )
            record_turn("round_0_literature_reading", "ZhiZhi_ToolAgent", summarize_output(output))
            state["zhizhi_status"] = output.get("status", "completed")
            # ZhiZhi mutates the persisted PaperGraph.  Reload before any
            # downstream gate, otherwise the same run evaluates stale state.
            project = load_project(project_id)

        if "tanxi" in autogen_agent_keys(groupchat_spec):
            output = json.loads(run_tanxi_gap_exploration(project_id=project_id, target_domain=domain, max_gaps=10))
            record_turn("round_0_gap_exploration", "TanXi_ToolAgent", summarize_output(output))
            project = load_project(project_id)
            explicit_gaps = autogen_extract_ranked_gaps(output)
            state["tanxi_gap_count"] = len(explicit_gaps)

            # Multi-gap selector: pick best combination by ingredient richness + type diversity
            try:
                from ._gap_detection import select_gap_combination_for_hypothesis, prefilter_gap_combination
            except ImportError:
                from _gap_detection import select_gap_combination_for_hypothesis, prefilter_gap_combination
            selected_gaps = select_gap_combination_for_hypothesis(project, explicit_gaps, strategy="auto")
            state["selected_gap_count"] = len(selected_gaps)
            state["best_gap_context"] = autogen_gap_context(selected_gaps if selected_gaps else explicit_gaps[:5])

            # GRADE pre-screening: check literature coverage before hypothesis generation
            if selected_gaps:
                grade_ok, grade_reason, grade_coverage = prefilter_gap_combination(project, selected_gaps)
                state["grade_prefilter"] = {
                    "sufficient": grade_ok,
                    "reason": grade_reason,
                    "coverage": round(grade_coverage, 3),
                }
                log_event("AUTOGEN", "grade_gap_prefilter", sufficient=grade_ok, coverage=round(grade_coverage, 3), reason=grade_reason)

        # === SOCRATES: mechanism enrichment via targeted literature search ===
        if "tanxi" in autogen_agent_keys(groupchat_spec) and "mingli" in autogen_agent_keys(groupchat_spec):
            try:
                from ._socrates import socrates_mechanism_enrichment, check_mechanism_contract_completeness
                from ._hypothesis import mechanism_contract_for_candidate
            except ImportError:
                from _socrates import socrates_mechanism_enrichment, check_mechanism_contract_completeness
                from _hypothesis import mechanism_contract_for_candidate

            enrichment_gaps = state.get("best_gap_context") or []
            socrates_reports: list[dict[str, Any]] = []
            for gap_ctx in enrichment_gaps[:3]:  # Enrich top 3 gaps
                if not isinstance(gap_ctx, dict) or not gap_ctx.get("description"):
                    continue
                # Build a candidate-like dict for mechanism contract check
                candidate_like = {
                    "statement": str(gap_ctx.get("description", "")),
                    "mechanism": str(gap_ctx.get("value_argument", "")),
                    "causal_chain": [],
                    "mechanism_specification": gap_ctx.get("mechanism_specification", {}),
                }
                contract = mechanism_contract_for_candidate(candidate_like)
                unresolved = check_mechanism_contract_completeness(contract)
                if unresolved:
                    log_event("AUTOGEN", "socrates_enrichment_start",
                              gap_id=gap_ctx.get("gap_id", ""),
                              unresolved_count=len(unresolved),
                              unresolved_fields=unresolved[:5])
                    result = socrates_mechanism_enrichment(
                        project_id=project_id,
                        gap=gap_ctx,
                        mechanism_contract=contract,
                        domain=domain,
                        providers=selected_providers,
                        max_iterations=2,
                        use_llm=use_llm,
                    )
                    enriched_contract = result.get("mechanism_contract", {})
                    report = result.get("enrichment_report", {})
                    socrates_reports.append({
                        "gap_id": gap_ctx.get("gap_id", ""),
                        "verdict": report.get("verdict", ""),
                        "total_searches": report.get("total_searches", 0),
                        "total_imports": report.get("total_imports", 0),
                        "remaining_unresolved": report.get("remaining_unresolved", []),
                    })
                    # Update gap context with enriched mechanism info
                    if enriched_contract:
                        gap_ctx["mechanism_specification"] = enriched_contract
                        gap_ctx["socrates_enrichment"] = report
                    log_event("AUTOGEN", "socrates_enrichment_done",
                              gap_id=gap_ctx.get("gap_id", ""),
                              verdict=report.get("verdict", ""),
                              searches=report.get("total_searches", 0),
                              imports=report.get("total_imports", 0),
                              remaining=len(report.get("remaining_unresolved", [])))
            if socrates_reports:
                state["socrates_enrichment"] = socrates_reports

        if "mingli" in autogen_agent_keys(groupchat_spec):
            # Collect all valid gaps for multi-gap aggregation with rotation on retry
            all_valid_gaps = [
                g for g in (state.get("best_gap_context") or [])
                if isinstance(g, dict) and g.get("description")
            ]
            gap_context = autogen_select_gap_for_mingli(state)
            mingli_max_attempts = 3
            for mingli_attempt in range(1, mingli_max_attempts + 1):
                # On retry, rotate gap order so different gaps get primary emphasis
                if mingli_attempt > 1 and len(all_valid_gaps) > 1:
                    shift = mingli_attempt - 1
                    rotated = all_valid_gaps[shift:] + all_valid_gaps[:shift]
                    gap_context = autogen_aggregate_gaps_for_mingli(rotated)
                    retry_guidance = {
                        "anti_template_guidance": (
                            "CRITICAL: Your previous hypothesis was rejected for using forbidden generic templates "
                            "or lacking domain specificity. Do NOT use phrases like 'conflicting claims', "
                            "'retested under matched conditions', or 'mechanism-stress intervention'. "
                            "Instead, include concrete domain-specific numbers/units, a named controllable variable, "
                            "a domain-specific measurable metric, and an explicit causal pathway."
                        ),
                        "specificity_requirements": (
                            "Include at least one numerical bound (e.g., temperature, concentration, voltage), "
                            "a specific operating condition, a measurable domain-specific outcome, "
                            "and a named causal mechanism. Avoid generic cross-domain metric lists."
                        ),
                        "mechanism_contract_requirements": (
                            "Your hypothesis must contain 3 elements: (1) a causal chain with at least 2 steps "
                            "(Input→Mediator→Output), (2) a mechanism paragraph explaining how the intervention "
                            "produces the outcome, (3) a falsification condition stating what observation would "
                            "refute the claim. Scope, dynamics, null/alternative hypotheses, and subhypotheses "
                            "are verified later by YanZhen — do not block on them here."
                        ),
                        "attempt_number": mingli_attempt,
                    }
                    if isinstance(gap_context, dict):
                        gap_context.update(retry_guidance)
                    else:
                        gap_context = retry_guidance

                try:
                    draft = json.loads(generate_idea(project_id=project_id, gap=gap_context, style="innovative", use_llm=use_llm))
                    state["draft_idea_id"] = str(draft.get("draft_idea_id") or "")
                    record_turn(f"round_1_proponent_position_attempt_{mingli_attempt}", "MingLi_AssistantAgent", summarize_output(draft))

                    contract = draft.get("mechanism_contract", {}) if isinstance(draft.get("mechanism_contract"), dict) else {}
                    if contract.get("verdict") != "READY":
                        final_status = "rejected_mechanism_contract"
                        record_turn(
                            f"round_1_mechanism_contract_attempt_{mingli_attempt}",
                            "MingLi_AssistantAgent",
                            summarize_output(contract),
                            final_status,
                        )
                        log_event(
                            "AUTOGEN",
                            "mingli_retry",
                            attempt=mingli_attempt,
                            reason=final_status,
                            missing=contract.get("missing_knowledge", []),
                        )
                        if mingli_attempt == mingli_max_attempts:
                            state["finalize_status"] = final_status
                            state["mingli_attempts"] = mingli_attempt
                            state["hypothesis_id"] = ""
                        continue

                    experiment = json.loads(
                        design_experiment(
                            project_id=project_id,
                            idea_id=state["draft_idea_id"],
                            constraints="academic lab scale; evidence-traceable; include baselines, ablations, regime shifts, and falsification criteria",
                        )
                    )
                    record_turn(f"round_3_methodology_attempt_{mingli_attempt}", "MingLi_AssistantAgent", summarize_output(experiment))

                    final = json.loads(
                        finalize_idea(
                            project_id=project_id,
                            idea_id=state["draft_idea_id"],
                            live_search=live_search,
                            providers=selected_providers,
                        )
                    )
                    final_status = str(final.get("status") or "completed")
                    record_turn(f"round_1_hypothesis_finalization_attempt_{mingli_attempt}", "MingLi_AssistantAgent", summarize_output(final), final_status)

                    # Check if finalized successfully
                    if final_status == "finalized":
                        state["hypothesis_id"] = str(final.get("hypothesis_id") or final.get("stored_hypothesis", {}).get("hypothesis_id") or "")
                        state["finalize_status"] = final_status
                        state["mingli_attempts"] = mingli_attempt
                        break
                    elif final_status in ("rejected_template", "rejected_specificity", "rejected_mechanism_contract"):
                        # Retry with guidance
                        log_event("AUTOGEN", "mingli_retry", attempt=mingli_attempt, reason=final_status)
                        if mingli_attempt == mingli_max_attempts:
                            state["finalize_status"] = final_status
                            state["mingli_attempts"] = mingli_attempt
                            state["hypothesis_id"] = ""
                        continue
                    else:
                        # Other rejection (overlap, semantic plausibility, etc.) - don't retry
                        state["hypothesis_id"] = str(final.get("hypothesis_id") or "")
                        state["finalize_status"] = final_status
                        state["mingli_attempts"] = mingli_attempt
                        break
                except Exception as mingli_exc:
                    record_turn(f"mingli_error_attempt_{mingli_attempt}", "MingLi_AssistantAgent", {}, "error", str(mingli_exc))
                    if mingli_attempt == mingli_max_attempts:
                        state["finalize_status"] = "error"
                        state["mingli_attempts"] = mingli_attempt
                    continue

        # GRADE is an audit signal, not a gate that suppresses verification.
        # PaperGraph records already hold primary evidence for many providers, so
        # a separate legacy ``evidence`` list must not make a well-read project
        # look empty and skip the very audit intended to catch that uncertainty.
        if "yanzhen" in autogen_agent_keys(groupchat_spec) and state.get("hypothesis_id"):
            project = load_project(project_id)
            papergraph = project.get("papergraph", [])
            paper_count = len([p for p in papergraph if isinstance(p, dict)])
            evidence_count = len([e for e in project.get("evidence", []) if isinstance(e, dict)])
            hypothesis_text = autogen_hypothesis_text(project, state["hypothesis_id"])
            grade_report = grade_knowledge_sufficiency(hypothesis_text, project)
            grade_sufficient = paper_count >= 5 and grade_report.get("verdict") != "knowledge_insufficient"
            state["grade_knowledge_check"] = {
                "paper_count": paper_count,
                "evidence_count": evidence_count,
                "sufficient": grade_sufficient,
                "coverage": grade_report,
                "threshold": {"min_papers": 5, "hypothesis_coverage": "knowledge_partial_or_better"},
            }
            record_turn(
                "grade_knowledge_sufficiency",
                "GRADE_CheckPoint",
                {
                    "paper_count": paper_count,
                    "evidence_count": evidence_count,
                    "sufficient": grade_sufficient,
                    "coverage": grade_report,
                    "decision": "proceed_to_yanzhen" if grade_sufficient else "proceed_to_yanzhen_with_context_warning",
                },
            )
            if not grade_sufficient:
                log_event("AUTOGEN", "grade_insufficient_knowledge", paper_count=paper_count, evidence_count=evidence_count, action="audit_not_skipped")
                state["knowledge_context_warning"] = grade_report

        if "yanzhen" in autogen_agent_keys(groupchat_spec) and state.get("hypothesis_id"):
            report = json.loads(
                run_yanzhen_mechanism_verification(
                    project_id=project_id,
                    hypothesis_id=state["hypothesis_id"],
                )
            )
            body = report.get("mechanism_fidelity_report", {})
            record_turn("round_2_cawm_layer_1_2_and_round_3_layer_3", "YanZhen_ToolAgent", summarize_output(report), str(body.get("overall_verdict") or "completed"))
            state["yanzhen_verdict"] = str(body.get("overall_verdict") or "")

        if run_debate and {"duzhi", "bianlun"} & set(autogen_agent_keys(groupchat_spec)):
            if state.get("hypothesis_id"):
                try:
                    debate = json.loads(
                        run_socratic_hypothesis_debate(
                            project_id=project_id,
                            hypothesis_id=state["hypothesis_id"],
                            max_rounds=min(clamp_int(max_round, 5, 40), 7),
                            proponent_model_family=proponent_model_family,
                            opponent_model_family=opponent_model_family,
                            judge_model_family=judge_model_family,
                            verifier_model_family=verifier_model_family,
                            auto_literature_supplement=True,
                            supplement_providers=selected_providers,
                            use_llm_revisions=use_llm,
                        )
                    )
                    report = debate.get("debate_report", {})
                    record_turn("round_4_groupchat_synthesis", "BianLun_GroupChatManager", summarize_output(debate), str(report.get("final_decision") or "completed"))
                    state["final_decision"] = str(report.get("final_decision") or "debate_completed")
                except Exception as debate_exc:
                    # A debate implementation fault must not erase the research
                    # run after ZhiZhi/TanXi/MingLi/YanZhen have completed.
                    # Preserve an evidence-backed DuZhi handoff for a retry.
                    critique = json.loads(
                        ask_socratic_questions(
                            project_id=project_id,
                            hypothesis_id=state["hypothesis_id"],
                            question_types=["conceptual_clarification", "constraint_check", "causal_probe", "counterexample_challenge"],
                            max_questions=12,
                        )
                    )
                    record_turn(
                        "round_4_debate_runtime_recovery",
                        "DuZhi_Opponent",
                        summarize_output(critique),
                        "revision_required",
                        str(debate_exc),
                    )
                    state["debate_runtime_error"] = str(debate_exc)
                    state["final_decision"] = "revision_required"
            else:
                critique = json.loads(
                    ask_socratic_questions(
                        project_id=project_id,
                        hypothesis="No finalized hypothesis was available; critique the failed MingLi finalization and required evidence gates.",
                    )
                )
                record_turn("round_2_missing_hypothesis_interrogation", "DuZhi_AssistantAgent", summarize_output(critique), "revision_required")

                # BianLun synthesis: synthesize the failed state and produce revision guidance
                synthesis_state = project.get("papergraph", [])
                gaps_state = project.get("knowledge_gaps", [])
                mingli_runs = project.get("mingli_hypothesis_evolution_runs", [])
                rejected_ideas = project.get("mingli_rejected_ideas", [])
                synthesis = {
                    "synthesis_type": "no_hypothesis_revision_guidance",
                    "papergraph_size": len(synthesis_state),
                    "knowledge_gaps_count": len(gaps_state),
                    "mingli_evolution_runs": len(mingli_runs),
                    "rejected_ideas_count": len(rejected_ideas),
                    "revision_guidance": (
                        f"Project has {len(synthesis_state)} papers and {len(gaps_state)} gaps. "
                        f"MingLi made {len(mingli_runs)} evolution runs with {len(rejected_ideas)} rejected ideas. "
                        "Recommended actions: (1) expand PaperGraph with more domain-specific literature, "
                        "(2) refine gap descriptions with concrete mechanisms, "
                        "(3) re-run MingLi with improved gap context and specificity requirements."
                    ),
                    "finalize_status": state.get("finalize_status", "unknown"),
                    "mingli_attempts": state.get("mingli_attempts", 0),
                }
                record_turn("round_4_bianlun_synthesis", "BianLun_GroupChatManager", synthesis, "revision_required")
                state["bianlun_synthesis"] = synthesis
                state["final_decision"] = "revision_required"

        if state["final_decision"] == "not_started":
            state["final_decision"] = "completed"
    except Exception as exc:
        record_turn("groupchat_error", "GroupChatManager", {}, "error", str(exc))
        state["final_decision"] = "error"
        state["error"] = str(exc)

    run_record = {
        "run_id": run_id,
        "groupchat_id": groupchat_id,
        "project_id": project_id,
        "framework": "autogen_2_groupchat",
        "groupchat_spec": groupchat_spec,
        "state": state,
        "messages": autogen_messages_from_turns(turns),
        "turns": turns,
        "createdAt": time.time(),
        "native_autogen": native_autogen_status(use_native=use_native_autogen),
        "next_step": autogen_next_step(state),
    }
    save_json(AUTOGEN_RUN_DIR / f"{run_id}.json", run_record)
    log_event("AUTOGEN", "groupchat_end", groupchat_id=groupchat_id, run_id=run_id, decision=state.get("final_decision"))
    return json.dumps(run_record, ensure_ascii=False, indent=2)


def list_autogen_groupchats(project_id: str = "") -> str:
    rows: list[dict[str, Any]] = []
    for path in sorted(AUTOGEN_DIR.glob("agc_*.json")):
        payload = load_json(path)
        if project_id and payload.get("project_id") != project_id:
            continue
        rows.append(
            {
                "groupchat_id": payload.get("groupchat_id"),
                "project_id": payload.get("project_id"),
                "goal": payload.get("goal"),
                "framework": payload.get("framework"),
                "agents": [agent.get("name") for agent in payload.get("agents", [])],
                "max_round": payload.get("groupchat", {}).get("max_round"),
                "createdAt": payload.get("createdAt"),
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def get_autogen_run(run_id: str) -> str:
    return json.dumps(load_json(AUTOGEN_RUN_DIR / f"{run_id}.json"), ensure_ascii=False, indent=2)


def science_agent_to_autogen_agent(agent_key: str) -> dict[str, Any]:
    try:
        from .science_core import (
            BIANLUN_FULL_PROMPT,
            BOXUE_FULL_PROMPT,
            DUZHI_FULL_PROMPT,
            MINGLI_FULL_PROMPT,
            SCIENCE_AGENTS,
            YANZHEN_FULL_PROMPT,
            ZHIZHI_FULL_PROMPT,
        )
    except ImportError:
        from science_core import (
            BIANLUN_FULL_PROMPT,
            BOXUE_FULL_PROMPT,
            DUZHI_FULL_PROMPT,
            MINGLI_FULL_PROMPT,
            SCIENCE_AGENTS,
            YANZHEN_FULL_PROMPT,
            ZHIZHI_FULL_PROMPT,
        )
    prompts = {
        "boxue": BOXUE_FULL_PROMPT,
        "zhizhi": ZHIZHI_FULL_PROMPT,
        "tanxi": "You are TanXi, the Knowledge Gap Discovery AssistantAgent. Detect source-grounded, semantic-plausible, evidence-traceable gaps from PaperGraph.",
        "mingli": MINGLI_FULL_PROMPT,
        "yanzhen": YANZHEN_FULL_PROMPT,
        "duzhi": DUZHI_FULL_PROMPT,
        "bianlun": BIANLUN_FULL_PROMPT,
    }
    spec = SCIENCE_AGENTS.get(agent_key, {})
    role_map = {
        "boxue": "UserProxyAgent",
        "zhizhi": "ToolAgent",
        "tanxi": "ToolAgent",
        "mingli": "AssistantAgent",
        "yanzhen": "ToolAgent",
        "duzhi": "AssistantAgent",
        "bianlun": "GroupChatManager",
    }
    return {
        "name": autogen_agent_name(agent_key),
        "key": agent_key,
        "autogen_type": role_map.get(agent_key, "AssistantAgent"),
        "role": spec.get("title") or agent_key,
        "goal": spec.get("mission") or f"Complete {agent_key} responsibilities.",
        "system_message": prompts.get(agent_key, spec.get("mission", "")),
        "llm_config_ref": autogen_llm_config_ref(agent_key),
        "tools": spec.get("tools", []),
    }


def autogen_agent_name(agent_key: str) -> str:
    return {
        "boxue": "Boxue_UserProxy",
        "zhizhi": "ZhiZhi_ToolAgent",
        "tanxi": "TanXi_ToolAgent",
        "mingli": "MingLi_Proponent",
        "yanzhen": "YanZhen_ToolAgent",
        "duzhi": "DuZhi_Opponent",
        "bianlun": "BianLun_GroupChatManager",
    }.get(agent_key, f"{agent_key}_AssistantAgent")


def autogen_llm_config_ref(agent_key: str) -> str:
    return {
        "mingli": "qwen-max",
        "duzhi": "qwen-plus",
        "bianlun": "qwen-deep-research",
        "yanzhen": "qwen-plus",
        "zhizhi": "tool_backed_retriever",
        "tanxi": "tool_backed_gap_miner",
        "boxue": "human_orchestrator_proxy",
    }.get(agent_key, "qwen_default")


def build_autogen_tool_registry() -> list[dict[str, str]]:
    return [
        {"name": "run_zhizhi_literature_analysis", "owner": "ZhiZhi_ToolAgent"},
        {"name": "run_tanxi_gap_exploration", "owner": "TanXi_ToolAgent"},
        {"name": "generate_idea", "owner": "MingLi_Proponent"},
        {"name": "design_experiment", "owner": "MingLi_Proponent"},
        {"name": "finalize_idea", "owner": "MingLi_Proponent"},
        {"name": "run_yanzhen_mechanism_verification", "owner": "YanZhen_ToolAgent"},
        {"name": "ask_socratic_questions", "owner": "DuZhi_Opponent"},
        {"name": "run_socratic_hypothesis_debate", "owner": "BianLun_GroupChatManager"},
    ]


def build_socratic_groupchat_protocol() -> list[dict[str, Any]]:
    return [
        {"round": 0, "speaker": "ZhiZhi_ToolAgent", "objective": "Read literature and build PaperGraph evidence."},
        {"round": 0, "speaker": "TanXi_ToolAgent", "objective": "Mine source-grounded gaps from PaperGraph."},
        {"round": 1, "speaker": "MingLi_Proponent", "objective": "State and defend a gap-traceable hypothesis."},
        {"round": 2, "speaker": "DuZhi_Opponent", "objective": "Ask Socratic clarification, causal, constraint, and counterexample questions."},
        {"round": 2, "speaker": "YanZhen_ToolAgent", "objective": "Run CAWM Layer 1 and Layer 2 evidence checks."},
        {"round": 3, "speaker": "MingLi_Proponent", "objective": "Present an experiment and falsification plan."},
        {"round": 3, "speaker": "YanZhen_ToolAgent", "objective": "Run regime-shift CAWM Layer 3."},
        {"round": 4, "speaker": "BianLun_GroupChatManager", "objective": "Synthesize refined hypothesis or revision decision."},
    ]


def native_autogen_status(*, use_native: bool) -> dict[str, Any]:
    if not use_native:
        return {
            "requested": False,
            "available": False,
            "mode": "structured_groupchat_executor",
            "reason": "Native AutoGen runtime disabled by default to control token use; v8 executes a deterministic GroupChat-compatible protocol.",
        }
    try:
        import autogen_agentchat  # noqa: F401

        return {"requested": True, "available": True, "mode": "native_autogen_agentchat_available"}
    except Exception as exc:
        try:
            import autogen  # noqa: F401

            return {"requested": True, "available": True, "mode": "native_autogen_legacy_available"}
        except Exception:
            return {"requested": True, "available": False, "mode": "structured_groupchat_executor", "reason": str(exc)}


def normalize_agent_list(agents: list[str] | None) -> list[str]:
    values = [normalize_key(item) for item in (agents or DEFAULT_AUTOGEN_AGENTS) if str(item).strip()]
    return unique_preserve_order([agent for agent in values if agent])


def autogen_agent_keys(groupchat_spec: dict[str, Any]) -> list[str]:
    return [str(agent.get("key") or "").lower() for agent in groupchat_spec.get("agents", []) if isinstance(agent, dict)]


def normalize_speaker_selection(value: str) -> str:
    key = normalize_key(value)
    if key in {"auto", "round_robin", "manual", "random"}:
        return key
    if key in {"roundrobin", "round-robin"}:
        return "round_robin"
    return "round_robin"


def normalize_human_input_mode(value: str) -> str:
    key = normalize_key(value).upper()
    if key in {"ALWAYS", "TERMINATE", "NEVER"}:
        return key
    return "TERMINATE"


def summarize_output(output: Any) -> Any:
    if isinstance(output, dict):
        keep = [
            "status",
            "project_id",
            "search_id",
            "hypothesis_id",
            "final_decision",
            "overall_verdict",
            "next_step",
            "import_plan",
            "action",
            "thought",
        ]
        summary = {key: output.get(key) for key in keep if key in output}
        if "debate_report" in output and isinstance(output["debate_report"], dict):
            summary["debate_report"] = {
                "debate_id": output["debate_report"].get("debate_id"),
                "final_decision": output["debate_report"].get("final_decision"),
                "unresolved_issues": output["debate_report"].get("unresolved_issues", [])[:5],
            }
        if "mechanism_fidelity_report" in output and isinstance(output["mechanism_fidelity_report"], dict):
            summary["mechanism_fidelity_report"] = {
                "overall_verdict": output["mechanism_fidelity_report"].get("overall_verdict"),
                "hypothesis_id": output["mechanism_fidelity_report"].get("hypothesis_id"),
            }
        return summary or trim_text(json.dumps(output, ensure_ascii=False), 2000)
    return trim_text(str(output), 2000)


def autogen_hypothesis_text(project: dict[str, Any], hypothesis_id: str) -> str:
    """Read the persisted hypothesis text without assuming one storage shape."""
    candidates = list(project.get("hypotheses", [])) + list(project.get("mingli_finalized_ideas", []))
    for item in candidates:
        if not isinstance(item, dict) or str(item.get("hypothesis_id") or "") != str(hypothesis_id or ""):
            continue
        final = item.get("mingli_final_idea") if isinstance(item.get("mingli_final_idea"), dict) else item
        parts = [
            final.get("title", ""),
            final.get("hypothesis", ""),
            final.get("abstract", ""),
            item.get("statement", ""),
            item.get("mechanism", ""),
        ]
        return " ".join(str(part).strip() for part in parts if part).strip()
    return ""


def autogen_extract_ranked_gaps(tanxi_output: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = tanxi_output.get("ranked_gaps")
    if isinstance(ranked, list):
        return [item for item in ranked if isinstance(item, dict)]
    fallback = tanxi_output.get("knowledge_gaps")
    if isinstance(fallback, list):
        return [item for item in fallback if isinstance(item, dict)]
    return []


def autogen_gap_context(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for gap in gaps:
        compact.append(
            {
                "gap_id": gap.get("gap_id"),
                "gap_type": gap.get("gap_type") or gap.get("type"),
                "description": gap.get("description"),
                "supporting_references": gap.get("supporting_references", [])[:5]
                if isinstance(gap.get("supporting_references"), list)
                else [],
                "suggested_research_path": gap.get("suggested_research_path"),
                "value_argument": gap.get("value_argument"),
                "semantic_plausibility": gap.get("semantic_plausibility", {}),
                "mechanism_issue_signal": gap.get("mechanism_issue_signal", {}),
                "gap_signal": gap.get("gap_signal", {}),
                "priority_score": gap.get("priority_score"),
                "novelty_score": gap.get("novelty_score"),
                "feasibility": gap.get("feasibility"),
                "counterfactual_tree": gap.get("counterfactual_tree"),
                "tabi_chain": gap.get("tabi_chain"),
                "tabi_warrant": gap.get("tabi_warrant"),
                "tabi_claim": gap.get("tabi_claim"),
                "hypothesis_ingredients": gap.get("hypothesis_ingredients"),
                "counterfactual_leaves": gap.get("counterfactual_leaves"),
            }
        )
    return compact


def autogen_select_gap_for_mingli(state: dict[str, Any]) -> dict[str, Any]:
    """Select and aggregate multiple gaps for MingLi hypothesis generation.

    Instead of picking a single gap (which often leads to template rejections),
    aggregate all available gaps into a synthetic super-gap that provides
    cross-gap context: richer descriptions, more supporting references, and
    domain-specific parameters from multiple angles.
    """
    gaps = state.get("best_gap_context")
    if isinstance(gaps, list):
        valid = [g for g in gaps if isinstance(g, dict) and g.get("description")]
        if valid:
            return autogen_aggregate_gaps_for_mingli(valid)
    state["mingli_gap_handoff"] = "no_explicit_gap_from_tanxi; using PaperGraph fallback inside MingLi"
    return {}


def autogen_aggregate_gaps_for_mingli(gaps: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate multiple gaps into a single synthetic gap for hypothesis generation.

    Combines descriptions, supporting references, counterfactual trees, and TABI
    chains from all input gaps so that MingLi receives enough domain-specific
    context to produce concrete, non-template hypotheses.
    """
    if not gaps:
        return {}
    if len(gaps) == 1:
        return gaps[0]

    descriptions = [str(g.get("description", "")).strip() for g in gaps if g.get("description")]
    all_refs: list[str] = []
    all_gap_types: list[str] = []
    all_counterfactual_trees: list[dict] = []
    all_tabi_chains: list[dict] = []

    for g in gaps:
        refs = g.get("supporting_references", [])
        if isinstance(refs, list):
            all_refs.extend(refs[:5])
        gt = g.get("gap_type", "")
        if gt:
            all_gap_types.append(gt)
        ct = g.get("counterfactual_tree")
        if ct and isinstance(ct, dict):
            all_counterfactual_trees.append(ct)
        tc = g.get("tabi_chain")
        if tc and isinstance(tc, dict):
            all_tabi_chains.append(tc)

    # Build composite description: numbered per-gap summaries for clarity
    combined_desc_parts = []
    for i, d in enumerate(descriptions):
        combined_desc_parts.append(f"[Gap {i + 1}] {d}")
    combined_desc = " ; ".join(combined_desc_parts) if combined_desc_parts else descriptions[0] if descriptions else ""

    # Deduplicate references while preserving order
    seen: set[str] = set()
    unique_refs: list[str] = []
    for r in all_refs:
        key = str(r).strip().lower()[:120]
        if key and key not in seen:
            seen.add(key)
            unique_refs.append(str(r))

    primary = gaps[0]
    return {
        "gap_id": str(primary.get("gap_id", "")),
        "source_gap_ids": [str(g.get("gap_id", "")) for g in gaps],
        "gap_type": primary.get("gap_type", ""),
        "gap_types_aggregated": all_gap_types,
        "description": combined_desc,
        "supporting_references": unique_refs[:10],
        "suggested_research_path": str(
            max(
                (g for g in gaps if g.get("supporting_references")),
                key=lambda g: len(g.get("supporting_references", [])),
                default=primary,
            ).get("suggested_research_path")
            or ""
        ),
        "value_argument": "Multi-gap synthesis from " + ", ".join(all_gap_types[:4]),
        "novelty_score": max((g.get("novelty_score", 0) for g in gaps), default=0),
        "feasibility": max(
            (g.get("feasibility", "low") for g in gaps),
            key=lambda f: {"high": 3, "medium": 2, "low": 1}.get(str(f).lower(), 0),
            default="low",
        ),
        "counterfactual_trees": all_counterfactual_trees,
        "tabi_chains": all_tabi_chains,
    }


def autogen_messages_from_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(turn.get("speaker") or "unknown"),
            "role": "assistant" if "UserProxy" not in str(turn.get("speaker") or "") else "user",
            "content": json.dumps(turn.get("content", {}), ensure_ascii=False),
            "round": turn.get("round"),
            "status": turn.get("status"),
        }
        for turn in turns
    ]


def safe_json_output(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def autogen_next_step(state: dict[str, Any]) -> str:
    decision = str(state.get("final_decision") or "")
    if decision == "accept_for_experiment":
        return "Proceed to GeWu experiment planning or implementation."
    if decision in {"revision_required", "revise", "human_review"}:
        return "Inspect AutoGen GroupChat messages and regenerate or revise the hypothesis."
    if decision == "error":
        return "Inspect the failed AutoGen turn before retrying."
    return "Review the AutoGen GroupChat run and decide whether to continue to experiment design."


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def new_autogen_groupchat_id() -> str:
    return f"agc_{time.time_ns()}"


def new_autogen_run_id() -> str:
    return f"agr_{time.time_ns()}"


def normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def clamp_int(value: Any, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = low
    return max(low, min(high, parsed))


def trim_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 15)] + "...[truncated]"
