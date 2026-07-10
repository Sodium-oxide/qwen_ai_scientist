from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any
import ast
import json
import re
import time
import xml.etree.ElementTree as ET

try:
    from .log import log_event
except ImportError:
    from log import log_event



def generate_debate_brief(project: dict[str, Any], hypothesis_text: str, record: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a shared, evidence-bounded context package for the debate agents."""
    try:
        from ._pipeline import project_records_for_mapping
        from ._utils import normalize_space, trim_text, unique_preserve_order
        from ._verification import yanzhen_context_text
    except ImportError:
        from _pipeline import project_records_for_mapping
        from _utils import normalize_space, trim_text, unique_preserve_order
        from _verification import yanzhen_context_text
    record = record or {}
    source_gap = record.get("source_gap", {}) if isinstance(record.get("source_gap"), dict) else {}
    refs = [str(item) for item in source_gap.get("supporting_references", []) if item]
    packets = record.get("evidence_packets", []) if isinstance(record.get("evidence_packets"), list) else []
    if not packets and isinstance(source_gap.get("evidence_packets"), list):
        packets = source_gap.get("evidence_packets", [])
    refs.extend(str(item.get("citation") or item.get("title") or "") for item in packets if isinstance(item, dict))
    ref_keys = {normalize_space(item).lower() for item in refs if item}
    records = [item for item in project_records_for_mapping(project) if isinstance(item, dict)]
    hypothesis_terms = set(re.findall(r"[a-z][a-z0-9_-]{3,}", normalize_space(hypothesis_text).lower()))
    supporting: list[dict[str, Any]] = []
    contradicting: list[dict[str, Any]] = []
    contradiction_markers = ("contradict", "inconsistent", "however", "whereas", "fails", "no effect", "not support", "limitation", "unclear")
    for item in records:
        citation = normalize_space(str(item.get("citation") or item.get("title") or ""))
        text = yanzhen_context_text(item)
        overlap = len(hypothesis_terms & set(re.findall(r"[a-z][a-z0-9_-]{3,}", text.lower())))
        if citation.lower() in ref_keys or overlap >= 3:
            compact = {
                "citation": citation,
                "title": str(item.get("title") or ""),
                "method": str(item.get("method") or ""),
                "scenario": str(item.get("scenario") or ""),
                "benchmark": str(item.get("benchmark") or ""),
                "evidence_text": trim_text(text, 500),
                "record": item,
            }
            supporting.append(compact)
            if any(marker in text.lower() for marker in contradiction_markers):
                contradicting.append(compact)
    supporting, contradicting = supporting[:6], contradicting[:4]
    key_terms = []
    for item in supporting:
        for kind in ("method", "scenario", "benchmark"):
            term = normalize_space(str(item.get(kind) or ""))
            if term and term.lower() not in {"unknown", "unspecified"}:
                key_terms.append({"term": term, "role": kind, "source": item.get("citation")})
    seen_terms: set[str] = set()
    key_terms = [item for item in key_terms if not (item["term"].lower() in seen_terms or seen_terms.add(item["term"].lower()))][:10]
    leaves = source_gap.get("counterfactual_leaves", []) if isinstance(source_gap.get("counterfactual_leaves"), list) else []
    anchors = [f"Counterfactual test: {trim_text(str(item), 240)}" for item in leaves[:2]]
    anchors.extend(f"Contradictory or limiting evidence: {item.get('citation')}" for item in contradicting[:2])
    if source_gap.get("description"):
        anchors.append(f"Gap to resolve: {trim_text(str(source_gap.get('description')), 260)}")
    anchors = unique_preserve_order(anchors)[:5]
    baseline = {
        "methods": unique_preserve_order(item["method"] for item in supporting if item.get("method"))[:4],
        "benchmarks": unique_preserve_order(item["benchmark"] for item in supporting if item.get("benchmark"))[:4],
        "common_failure_signals": unique_preserve_order(trim_text(item["evidence_text"], 180) for item in contradicting if item.get("evidence_text"))[:3],
    }
    missing = []
    if not supporting:
        missing.append("supporting_evidence")
    if not anchors:
        missing.append("debate_anchors")
    return {
        "hypothesis": trim_text(hypothesis_text, 1800),
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "key_terms": key_terms,
        "domain_baseline": baseline,
        "debate_anchors": anchors,
        "context_validation": {"ready": not missing, "missing": missing, "supporting_count": len(supporting), "contradicting_count": len(contradicting)},
    }


def run_socratic_hypothesis_debate(
    project_id: str,
    hypothesis_id: str = "",
    hypothesis: str = "",
    max_rounds: int = 5,
    proponent_model_family: str = "qwen-plus",
    opponent_model_family: str = "qwen-max",
    judge_model_family: str = "qwen-deep-research",
    verifier_model_family: str = "qwen-plus",
    shifted_conditions: list[Any] | None = None,
    auto_literature_supplement: bool = True,
    supplement_providers: list[str] | None = None,
    use_llm_revisions: bool = True,
) -> str:
    try:
        from ._project import load_project, save_project
        from ._supplement import zhizhi_supplement_from_audit
        from ._utils import clamp_int, new_id
        from ._verification import extract_causal_chain, run_yanzhen_mechanism_verification, yanzhen_mechanism_operationalization_audit, yanzhen_mechanism_specification, yanzhen_mechanism_text, yanzhen_sources_for_hypothesis
    except ImportError:
        from _project import load_project, save_project
        from _supplement import zhizhi_supplement_from_audit
        from _utils import clamp_int, new_id
        from _verification import extract_causal_chain, run_yanzhen_mechanism_verification, yanzhen_mechanism_operationalization_audit, yanzhen_mechanism_specification, yanzhen_mechanism_text, yanzhen_sources_for_hypothesis
    project = load_project(project_id)
    record = debate_hypothesis_record(project, hypothesis_id) if hypothesis_id else {}
    text = hypothesis or debate_hypothesis_text(record)
    if not text:
        raise ValueError("BianLun requires hypothesis text or hypothesis_id.")
    safety = debate_safety_gates(
        proponent_model_family=proponent_model_family,
        opponent_model_family=opponent_model_family,
        judge_model_family=judge_model_family,
        verifier_model_family=verifier_model_family,
    )
    if not safety["passed"]:
        report = {
            "thought": "BianLun stopped before debate because an ARIS-style safety gate failed.",
            "action": {"type": "run_socratic_hypothesis_debate", "status": "blocked"},
            "debate_report": {
                "debate_id": new_id("debate"),
                "hypothesis_id": hypothesis_id,
                "rounds": [],
                "safety_gates": safety,
                "refined_hypothesis": {},
                "unresolved_issues": safety["issues"],
                "final_decision": "human_review",
            },
        }
        project.setdefault("socratic_debates", []).append(report["debate_report"])
        project["phase"] = "Socratic Debate"
        project["updatedAt"] = time.time()
        save_project(project)
        return json.dumps(report, ensure_ascii=False, indent=2)

    rounds: list[dict[str, Any]] = []
    max_rounds = clamp_int(max_rounds, 4, 7)
    mechanism = yanzhen_mechanism_text(record) or text
    sources = yanzhen_sources_for_hypothesis(project, record)
    debate_brief = generate_debate_brief(project, text, record)
    brief_validation = debate_brief.get("context_validation", {})
    if not brief_validation.get("ready"):
        log_event(
            "SCIENCE",
            "debate_brief_incomplete",
            project_id=project_id,
            hypothesis_id=hypothesis_id or str(record.get("hypothesis_id") or ""),
            missing=brief_validation.get("missing", []),
        )
    brief_records = [
        item.get("record")
        for item in debate_brief.get("supporting_evidence", []) + debate_brief.get("contradicting_evidence", [])
        if isinstance(item, dict) and isinstance(item.get("record"), dict)
    ]
    sources = list(sources) + [item for item in brief_records if item not in sources]
    working_text = text
    working_mechanism = mechanism
    yanzhen_body: dict[str, Any] = {}
    mechanism_specification = yanzhen_mechanism_specification(record)
    initial_operationalization = yanzhen_mechanism_operationalization_audit(
        f"{working_text} {working_mechanism}", mechanism_specification, sources
    )

    # AHOIS-style quality tracking across rounds
    quality_history: list[dict[str, Any]] = []
    initial_quality = evaluate_hypothesis_quality(working_text, working_mechanism, sources)
    quality_history.append({"round": 0, "phase": "initial", **initial_quality})
    log_event("SCIENCE", "socratic_quality_baseline",
              overall=initial_quality["overall"],
              pc=initial_quality["physics_consistency"],
              hc=initial_quality["hypothesis_completeness"],
              uc=initial_quality["uncertainty_calibration"])

    round1_questions = duzhi_generate_questions(
        working_text,
        working_mechanism,
        sources,
        allowed_types=["conceptual_clarification", "constraint_check"],
        max_questions=8,
        debate_brief=debate_brief,
        mechanism_specification=mechanism_specification,
        operationalization_audit=initial_operationalization,
    )
    round1_revision = mingli_revision_from_questions(
        project,
        record,
        working_text,
        working_mechanism,
        round1_questions,
        {},
        "Socratic Clarification",
        use_llm=use_llm_revisions,
        mechanism_specification=mechanism_specification,
        debate_brief=debate_brief,
    )
    working_text = str(round1_revision.get("revised_hypothesis") or working_text)
    working_mechanism = str(round1_revision.get("revised_mechanism") or working_mechanism)
    if isinstance(round1_revision.get("mechanism_specification"), dict):
        mechanism_specification = round1_revision["mechanism_specification"]
    r1_quality = evaluate_hypothesis_quality(working_text, working_mechanism, sources)
    quality_history.append({"round": 1, "phase": "Socratic Clarification", **r1_quality})
    rounds.append(
        {
            "round": 1,
            "name": "Socratic Clarification",
            "proponent_position": debate_proponent_position(text, mechanism, record),
            "opponent_questions": round1_questions,
            "proponent_response": round1_revision,
            "quality_scores": r1_quality,
            "quality_delta": round(r1_quality["overall"] - quality_history[-2]["overall"], 2),
            "moderator_verdict": "revise" if any(q.get("severity") in {"high", "fatal"} for q in round1_questions) else "advance",
        }
    )
    if max_rounds >= 2:
        yanzhen_json = json.loads(
            run_yanzhen_mechanism_verification(
                project_id,
                hypothesis=working_text,
                reasoning_chain=extract_causal_chain(f"{working_text} {working_mechanism}"),
                original_sources=sources,
                shifted_conditions=shifted_conditions,
            )
        )
        yanzhen_body = yanzhen_json.get("mechanism_fidelity_report", {})
        round2_questions = duzhi_generate_questions(
            working_text,
            working_mechanism,
            sources,
            allowed_types=["causal_probe", "constraint_check"],
            max_questions=8,
            yanzhen_report=yanzhen_body,
            debate_brief=debate_brief,
            mechanism_specification=mechanism_specification,
        )
        round2_questions = filter_new_debate_questions(round2_questions, rounds, min_keep=3)
        round2_revision = mingli_revision_from_questions(
            project,
            record,
            working_text,
            working_mechanism,
            round2_questions,
            yanzhen_body,
            "Evidence and CAWM Layer 1-2",
            use_llm=use_llm_revisions,
            mechanism_specification=mechanism_specification,
            debate_brief=debate_brief,
        )
        working_text = str(round2_revision.get("revised_hypothesis") or working_text)
        working_mechanism = str(round2_revision.get("revised_mechanism") or working_mechanism)
        if isinstance(round2_revision.get("mechanism_specification"), dict):
            mechanism_specification = round2_revision["mechanism_specification"]
        r2_quality = evaluate_hypothesis_quality(working_text, working_mechanism, sources)
        quality_history.append({"round": 2, "phase": "Evidence and CAWM Layer 1-2", **r2_quality})
        r2_delta = round(r2_quality["overall"] - quality_history[-2]["overall"], 2)
        log_event("SCIENCE", "socratic_quality_round", round=2, overall=r2_quality["overall"], delta=r2_delta)
        rounds.append(
            {
                "round": 2,
                "name": "Evidence and CAWM Layer 1-2",
                "yanzhen_report": yanzhen_body,
                "opponent_questions": round2_questions,
                "proponent_response": round2_revision,
                "quality_scores": r2_quality,
                "quality_delta": r2_delta,
                "moderator_verdict": "revise" if yanzhen_body.get("overall_verdict") in {"CAWM_DETECTED", "REQUIRES_HUMAN_REVIEW"} else "advance",
            }
        )
    if max_rounds >= 3:
        round3_questions = duzhi_generate_questions(
            working_text,
            working_mechanism,
            sources,
            allowed_types=["counterexample_challenge", "constraint_check"],
            max_questions=8,
            yanzhen_report=yanzhen_body,
            debate_brief=debate_brief,
            mechanism_specification=mechanism_specification,
        )
        round3_questions = filter_new_debate_questions(round3_questions, rounds, min_keep=3)
        round3_revision = mingli_revision_from_questions(
            project,
            record,
            working_text,
            working_mechanism,
            round3_questions,
            yanzhen_body,
            "Methodology and Regime Shift",
            use_llm=use_llm_revisions,
            mechanism_specification=mechanism_specification,
            debate_brief=debate_brief,
        )
        working_text = str(round3_revision.get("revised_hypothesis") or working_text)
        working_mechanism = str(round3_revision.get("revised_mechanism") or working_mechanism)
        if isinstance(round3_revision.get("mechanism_specification"), dict):
            mechanism_specification = round3_revision["mechanism_specification"]
        r3_quality = evaluate_hypothesis_quality(working_text, working_mechanism, sources)
        quality_history.append({"round": 3, "phase": "Methodology and Regime Shift", **r3_quality})
        r3_delta = round(r3_quality["overall"] - quality_history[-2]["overall"], 2)
        # Convergence detection: if last 2 deltas are both < 0.1, quality has plateaued
        convergence_detected = False
        if len(quality_history) >= 3:
            prev_delta = quality_history[-2]["overall"] - quality_history[-3]["overall"]
            if r3_delta < 0.1 and prev_delta < 0.1:
                convergence_detected = True
                log_event("SCIENCE", "socratic_convergence_detected",
                          round=3, overall=r3_quality["overall"],
                          last_two_deltas=[round(prev_delta, 2), r3_delta])
        else:
            log_event("SCIENCE", "socratic_quality_round", round=3, overall=r3_quality["overall"], delta=r3_delta)
        layer3 = yanzhen_body.get("layer_3_regime_shift_test", {}) if isinstance(yanzhen_body, dict) else {}
        rounds.append(
            {
                "round": 3,
                "name": "Methodology and Regime Shift",
                "experiment_plan": record.get("test_plan") or debate_experiment_text(record),
                "regime_shift_summary": layer3,
                "opponent_questions": round3_questions,
                "proponent_response": round3_revision,
                "quality_scores": r3_quality,
                "quality_delta": r3_delta,
                "convergence_detected": convergence_detected,
                "moderator_verdict": "converge" if convergence_detected else ("revise" if layer3.get("cawm_risk_level") == "HIGH" or any(q.get("severity") in {"high", "fatal"} for q in round3_questions) else "advance"),
            }
        )
    if max_rounds >= 4:
        final_yanzhen_body = yanzhen_body
        if working_text != text and max_rounds >= 3:
            try:
                final_yanzhen_json = json.loads(
                    run_yanzhen_mechanism_verification(
                        project_id,
                        hypothesis=working_text,
                        reasoning_chain=extract_causal_chain(f"{working_text} {working_mechanism}"),
                        original_sources=sources,
                        shifted_conditions=shifted_conditions,
                    )
                )
                final_yanzhen_body = final_yanzhen_json.get("mechanism_fidelity_report", yanzhen_body)
            except Exception:
                final_yanzhen_body = yanzhen_body
        yanzhen_body = final_yanzhen_body
        audit_feedback = yanzhen_debate_feedback(yanzhen_body)
        literature_supplement: dict[str, Any] = {"attempted": False, "reason": "not required"}
        if auto_literature_supplement and yanzhen_body.get("verdict") != "PASS" and yanzhen_body.get("unsupported_claims"):
            literature_supplement = zhizhi_supplement_from_audit(
                project_id=project_id,
                audit_report=yanzhen_body,
                hypothesis_text=working_text,
                providers=supplement_providers,
                max_claims=2,
                per_claim_imports=1,
                use_llm=True,
            )
            if literature_supplement.get("attempted") and literature_supplement.get("imports"):
                project = load_project(project_id)
                sources = yanzhen_sources_for_hypothesis(project, record)
                debate_brief = generate_debate_brief(project, working_text, record)
                try:
                    refreshed_json = json.loads(
                        run_yanzhen_mechanism_verification(
                            project_id,
                            hypothesis=working_text,
                            reasoning_chain=extract_causal_chain(f"{working_text} {working_mechanism}"),
                            original_sources=sources,
                            shifted_conditions=shifted_conditions,
                        )
                    )
                    yanzhen_body = refreshed_json.get("mechanism_fidelity_report", yanzhen_body)
                    audit_feedback = yanzhen_debate_feedback(yanzhen_body)
                except Exception:
                    pass
        if yanzhen_body.get("verdict") != "PASS" and max_rounds >= 5:
            audit_questions = duzhi_questions_from_yanzhen_actions(yanzhen_body, working_text, working_mechanism)
            audit_questions = filter_new_debate_questions(audit_questions, rounds, min_keep=2)
            round4_revision = mingli_revision_from_questions(
                project,
                record,
                working_text,
                working_mechanism,
                audit_questions,
                yanzhen_body,
                "Mechanism Audit Feedback",
                use_llm=use_llm_revisions,
                mechanism_specification=mechanism_specification,
                debate_brief=debate_brief,
            )
            working_text = str(round4_revision.get("revised_hypothesis") or working_text)
            working_mechanism = str(round4_revision.get("revised_mechanism") or working_mechanism)
            if isinstance(round4_revision.get("mechanism_specification"), dict):
                mechanism_specification = round4_revision["mechanism_specification"]
            rounds.append(
                {
                    "round": 4,
                    "name": "Mechanism Audit Feedback and Literature Completion",
                    "yanzhen_report": yanzhen_body,
                    "audit_feedback": audit_feedback,
                    "literature_supplement": literature_supplement,
                    "opponent_questions": audit_questions,
                    "proponent_response": round4_revision,
                    "moderator_verdict": "revise" if yanzhen_body.get("verdict") != "PASS" else "advance",
                }
            )
            try:
                final_after_revision_json = json.loads(
                    run_yanzhen_mechanism_verification(
                        project_id,
                        hypothesis=working_text,
                        reasoning_chain=extract_causal_chain(f"{working_text} {working_mechanism}"),
                        original_sources=sources,
                        shifted_conditions=shifted_conditions,
                    )
                )
                yanzhen_body = final_after_revision_json.get("mechanism_fidelity_report", yanzhen_body)
            except Exception:
                pass
        # Additional rounds are genuine DuZhi -> MingLi exchanges, not repeated
        # copies of the first question set.  They are available when a caller
        # asks for the AHOIS-style seven-round schedule.
        for round_no in range(5, max_rounds):
            follow_up_questions = duzhi_generate_questions(
                working_text,
                working_mechanism,
                sources,
                allowed_types=["conceptual_clarification", "constraint_check", "causal_probe", "counterexample_challenge"],
                max_questions=8,
                yanzhen_report=yanzhen_body,
                debate_brief=debate_brief,
                mechanism_specification=mechanism_specification,
            )
            follow_up_questions = filter_new_debate_questions(follow_up_questions, rounds, min_keep=2)
            follow_up_revision = mingli_revision_from_questions(
                project,
                record,
                working_text,
                working_mechanism,
                follow_up_questions,
                yanzhen_body,
                f"Socratic Follow-up {round_no}",
                use_llm=use_llm_revisions,
                mechanism_specification=mechanism_specification,
                debate_brief=debate_brief,
            )
            working_text = str(follow_up_revision.get("revised_hypothesis") or working_text)
            working_mechanism = str(follow_up_revision.get("revised_mechanism") or working_mechanism)
            if isinstance(follow_up_revision.get("mechanism_specification"), dict):
                mechanism_specification = follow_up_revision["mechanism_specification"]
            follow_up_quality = evaluate_hypothesis_quality(working_text, working_mechanism, sources)
            quality_history.append({"round": round_no, "phase": f"Socratic Follow-up {round_no}", **follow_up_quality})
            try:
                refreshed_json = json.loads(
                    run_yanzhen_mechanism_verification(
                        project_id,
                        hypothesis=working_text,
                        reasoning_chain=extract_causal_chain(f"{working_text} {working_mechanism}"),
                        original_sources=sources,
                        shifted_conditions=shifted_conditions,
                    )
                )
                yanzhen_body = refreshed_json.get("mechanism_fidelity_report", yanzhen_body)
            except Exception:
                pass
            rounds.append(
                {
                    "round": round_no,
                    "name": f"Socratic Follow-up {round_no}",
                    "yanzhen_report": yanzhen_body,
                    "opponent_questions": follow_up_questions,
                    "proponent_response": follow_up_revision,
                    "quality_scores": follow_up_quality,
                    "quality_delta": round(follow_up_quality["overall"] - quality_history[-2]["overall"], 2),
                    "moderator_verdict": "revise" if any(item.get("severity") in {"high", "fatal"} for item in follow_up_questions) else "advance",
                }
            )
        refined = debate_refined_hypothesis(project, record, working_text, working_mechanism, rounds, yanzhen_body)
        execution_validation = execution_level_validation(project, refined, yanzhen_body, rounds)
        final_decision = debate_final_decision(rounds, yanzhen_body, refined, execution_validation)
        final_round_number = max([int(item.get("round") or 0) for item in rounds] or [0]) + 1
        rounds.append(
            {
                "round": final_round_number,
                "name": "Synthesis and Convergence",
                "refined_hypothesis": refined,
                "yanzhen_report": yanzhen_body,
                "audit_feedback": yanzhen_debate_feedback(yanzhen_body),
                "execution_validation": execution_validation,
                "final_decision": final_decision,
                "moderator_verdict": "finalize" if final_decision == "accept_for_experiment" else final_decision,
            }
        )
    else:
        refined = debate_refined_hypothesis(project, record, working_text, working_mechanism, rounds, yanzhen_body)
        execution_validation = execution_level_validation(project, refined, yanzhen_body, rounds)
        final_decision = debate_final_decision(rounds, yanzhen_body, refined, execution_validation)

    unresolved = debate_unresolved_issues(
        [q for round_item in rounds for q in round_item.get("opponent_questions", []) if isinstance(q, dict)],
        yanzhen_body,
    )
    brief_validation = debate_brief.get("context_validation", {})
    if not brief_validation.get("ready"):
        unresolved.append(
            "Debate context is incomplete: "
            + ", ".join(str(item) for item in brief_validation.get("missing", []) if item)
            + ". Boxue should request targeted ZhiZhi supplementation before accepting the hypothesis."
        )
        if final_decision == "accept_for_experiment":
            final_decision = "human_review"
            if rounds and rounds[-1].get("name") == "Synthesis and Convergence":
                rounds[-1]["final_decision"] = final_decision
                rounds[-1]["moderator_verdict"] = final_decision
    debate_report = {
        "debate_id": new_id("debate"),
        "hypothesis_id": hypothesis_id or str(record.get("hypothesis_id") or ""),
        "model_families": {
            "proponent": proponent_model_family,
            "opponent": opponent_model_family,
            "judge": judge_model_family,
            "verifier": verifier_model_family,
        },
        "rounds": rounds,
        "debate_state": {},
        "quality_trajectory": quality_history,
        "final_quality": quality_history[-1] if quality_history else {},
        "safety_gates": safety,
        "debate_brief": debate_brief,
        "refined_hypothesis": refined,
        "unresolved_issues": unresolved,
        "final_decision": final_decision,
    }
    debate_report["debate_state"] = build_debate_state(
        hypothesis_id=debate_report["hypothesis_id"],
        rounds=rounds,
        max_rounds=max_rounds,
        unresolved=unresolved,
        final_decision=final_decision,
    )
    project = load_project(project_id)
    project.setdefault("socratic_debates", []).append(debate_report)
    project.setdefault("hypothesis_revisions", []).append(
        {
            "revision_id": new_id("rev"),
            "hypothesis_id": debate_report["hypothesis_id"],
            "source_debate_id": debate_report["debate_id"],
            "refined_hypothesis": refined,
            "decision": final_decision,
            "createdAt": time.time(),
        }
    )
    project["phase"] = "Socratic Debate"
    project["updatedAt"] = time.time()
    save_project(project)
    log_event("SCIENCE", "socratic_debate_completed", project_id=project_id, hypothesis_id=debate_report["hypothesis_id"], decision=final_decision)
    return json.dumps(
        {
            "thought": "BianLun ran the triangle loop: Socratic debate, YanZhen mechanism audit, targeted literature completion when needed, MingLi revision, and final synthesis.",
            "action": {"type": "run_socratic_hypothesis_debate", "rounds": len(rounds), "max_rounds": max_rounds},
            "debate_report": debate_report,
        },
        ensure_ascii=False,
        indent=2,
    )

def debate_hypothesis_record(project: dict[str, Any], hypothesis_id: str) -> dict[str, Any]:
    try:
        from ._utils import find_by_id
    except ImportError:
        from _utils import find_by_id
    found = find_by_id(project.get("hypotheses", []), "hypothesis_id", hypothesis_id)
    if found is None:
        found = find_by_id(project.get("mingli_finalized_ideas", []), "hypothesis_id", hypothesis_id)
    if found is None:
        raise ValueError(f"Unknown hypothesis_id for project {project.get('project_id', '')}: {hypothesis_id}")
    return found

def debate_hypothesis_text(record: dict[str, Any]) -> str:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    if not record:
        return ""
    final = record.get("mingli_final_idea") if isinstance(record.get("mingli_final_idea"), dict) else {}
    return normalize_space(
        " ".join(
            str(part)
            for part in (
                final.get("title", ""),
                final.get("hypothesis", ""),
                final.get("abstract", ""),
                record.get("statement", ""),
                record.get("mechanism", ""),
            )
            if part
        )
    )

def duzhi_generate_questions(
    hypothesis_text: str,
    mechanism: str,
    sources: list[Any],
    *,
    allowed_types: list[str] | None = None,
    max_questions: int = 12,
    yanzhen_report: dict[str, Any] | None = None,
    debate_brief: dict[str, Any] | None = None,
    mechanism_specification: dict[str, Any] | None = None,
    operationalization_audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    try:
        from ._gap_detection import text_jaccard
        from ._utils import clamp_int, normalize_space, trim_text
        from ._verification import default_regime_shifts, extract_causal_chain, render_shift_condition, yanzhen_context_text, yanzhen_mechanism_operationalization_audit
    except ImportError:
        from _gap_detection import text_jaccard
        from _utils import clamp_int, normalize_space, trim_text
        from _verification import default_regime_shifts, extract_causal_chain, render_shift_condition, yanzhen_context_text, yanzhen_mechanism_operationalization_audit
    allowed = set(allowed_types or [])
    questions: list[dict[str, Any]] = []
    text = normalize_space(f"{hypothesis_text} {mechanism}")
    lowered = text.lower()
    source_text = normalize_space(" ".join(yanzhen_context_text(item) for item in sources))
    evidence_terms = socratic_evidence_terms(sources, text)
    chain = extract_causal_chain(text)
    brief = debate_brief if isinstance(debate_brief, dict) else {}
    anchors = brief.get("debate_anchors", []) if isinstance(brief.get("debate_anchors"), list) else []
    contradicting = brief.get("contradicting_evidence", []) if isinstance(brief.get("contradicting_evidence"), list) else []
    operationalization = operationalization_audit if isinstance(operationalization_audit, dict) else {}
    if not operationalization and isinstance(yanzhen_report, dict):
        operationalization = yanzhen_report.get("mechanism_operationalization_audit", {}) if isinstance(yanzhen_report.get("mechanism_operationalization_audit"), dict) else {}
    if not operationalization:
        operationalization = yanzhen_mechanism_operationalization_audit(
            text,
            mechanism_specification if isinstance(mechanism_specification, dict) else {},
            sources,
        )

    def include(kind: str) -> bool:
        return not allowed or kind in allowed

    def add(kind: str, question: str, target: str, why: str, revision: str, severity: str) -> None:
        if not include(kind):
            return
        questions.append(
            {
                "question_type": kind,
                "question": question,
                "target_claim": trim_text(target, 220),
                "why_it_matters": why,
                "required_revision": revision,
                "severity": severity,
            }
        )

    if include("conceptual_clarification"):
        if not any(term in lowered for term in ("measure", "metric", "observable", "readout", "quantif", "primary")):
            add(
                "conceptual_clarification",
                "Which part of the hypothesis is directly measurable, and which part is inferred from those measurements?",
                hypothesis_text,
                "AHOIS-style clarification requires separating observables from inferred mechanisms before testing.",
                "Add explicit observables, inferred constructs, and the mapping between them.",
                "high",
            )
        if any(term in lowered for term in ("improve", "enhance", "better", "stable")) and not re.search(r"\b\d+(?:\.\d+)?\s*(?:%|fold|x|times|sigma|unit|score)\b", lowered):
            add(
                "conceptual_clarification",
                "What threshold converts the claimed improvement into a successful result rather than a vague positive trend?",
                hypothesis_text,
                "A falsifiable hypothesis needs a decision threshold or preregistered effect direction.",
                "Define a quantitative or ordinal success threshold and the minimum meaningful effect.",
                "medium",
            )
        if not any(term in lowered for term in ("baseline", "control", "negative control", "standard")):
            add(
                "conceptual_clarification",
                "What is the nearest domain-standard baseline or negative control that would make the claim nontrivial?",
                hypothesis_text,
                "Without a baseline, the hypothesis cannot distinguish genuine mechanism from general performance drift.",
                "Name at least one domain-standard baseline and one failure-mode or negative control.",
                "high",
            )

    if include("constraint_check"):
        dimensions = operationalization.get("dimensions", {}) if isinstance(operationalization.get("dimensions"), dict) else {}
        strict_prompts = {
            "identity": "What exact entity, state, variable, or mathematical object is the mediator? Give its formal definition or composition, not a broad label.",
            "location_or_scope": "Where exactly does this mediator exist: which system component, boundary, cohort, data representation, or scale?",
            "dynamics": "What evolution rule, threshold, rate, or discriminating schedule governs this mediator under the intervention?",
            "reversibility": "What removal, recovery, rollback, washout, or counterfactual test distinguishes an irreversible mechanism from a transient response?",
            "observability": "Which two independent observation modalities or tests distinguish the mediator from endpoint correlation, and what signal/criterion will each produce?",
        }
        for dimension, prompt in strict_prompts.items():
            detail = dimensions.get(dimension, {}) if isinstance(dimensions.get(dimension), dict) else {}
            if detail.get("verdict") == "FAIL":
                add(
                    "constraint_check",
                    prompt,
                    mechanism,
                    "A causal mediator cannot remain a narrative placeholder. The response must operationalize this dimension or explicitly downgrade the claim to phenomenological.",
                    f"Provide the required {dimension} field using source evidence or a preregistered measurement/test plan; otherwise remove the causal-mediator claim.",
                    "high",
                )
        for anchor in anchors[:2]:
            add(
                "constraint_check",
                f"How does the hypothesis address this pre-identified debate anchor: {trim_text(str(anchor), 220)}?",
                mechanism,
                "The critic must use the shared debate brief rather than an isolated hypothesis string.",
                "State whether the anchor is supported, contradicted, or unresolved, and attach the relevant evidence or falsification test.",
                "high",
            )
        if not any(term in lowered for term in ("constraint", "assumption", "boundary", "regime", "limit", "under ", "unless", "when")):
            add(
                "constraint_check",
                "Under what validity regime is the mechanism expected to hold, and where should it fail?",
                mechanism,
                "Unstated boundary conditions are a common CAWM risk under regime shift.",
                "Add explicit assumptions, validity range, and at least one expected failure condition.",
                "high",
            )
        if not any(term in lowered for term in ("data", "sample", "instrument", "simulation", "experiment", "cohort", "dataset", "measurement")):
            add(
                "constraint_check",
                "What data, instrument, simulation, or experimental platform can actually observe the claimed causal step?",
                mechanism,
                "A hypothesis can be conceptually attractive but infeasible if the decisive mechanism is not observable.",
                "Specify the observation platform and feasibility constraint for the decisive causal link.",
                "medium",
            )
        if source_text and text_jaccard(hypothesis_text, source_text) < 0.06:
            add(
                "constraint_check",
                "Which sentence or result in the imported PaperGraph evidence grounds the strongest mechanistic premise?",
                hypothesis_text,
                "ARIS-style evidence gates require claim-to-source traceability, not just thematic similarity.",
                "Map each central premise to a PaperGraph citation or mark it as speculative.",
                "high",
            )
        if evidence_terms:
            term_list = ", ".join(evidence_terms[:5])
            add(
                "constraint_check",
                f"The evidence repeatedly mentions {term_list}; which of these domain-specific constraints is actually required for the mechanism to hold?",
                mechanism,
                "A strong critique should test the hypothesis against the concrete variables found in the retrieved literature, not only generic stress tests.",
                "Name the required evidence-derived constraint, its allowed range or qualitative regime, and how it will be monitored.",
                "medium",
            )

    if include("causal_probe"):
        if len(chain) < 2:
            add(
                "causal_probe",
                "Can you rewrite the hypothesis as an explicit input -> mechanism -> output chain with evidence for each arrow?",
                hypothesis_text,
                "A single broad sentence hides missing causal links and prevents targeted revision.",
                "Provide a three-to-five-step causal chain and cite or label the evidence for each link.",
                "fatal",
            )
        if any(term in lowered for term in ("because", "therefore", "leads to", "causes", "drives")) and not source_text:
            add(
                "causal_probe",
                "What source evidence supports the causal connector rather than only the endpoint observation?",
                mechanism,
                "Causal connectors are where correct-answer/wrong-mechanism failures often enter.",
                "Add evidence for the causal link or downgrade it to a testable assumption.",
                "high",
            )
        if evidence_terms:
            add(
                "causal_probe",
                f"For the evidence-derived terms {', '.join(evidence_terms[:4])}, which exact term sits at the intervention, mediator, and output positions of the causal chain?",
                mechanism,
                "Domain-targeted causal probing forces MingLi to connect the hypothesis to field-specific entities without hardcoding the field.",
                "Rewrite the causal chain using at least two evidence-derived terms and mark unsupported links as assumptions.",
                "high",
            )
        report = yanzhen_report or {}
        layer_1 = report.get("layer_1_internal_consistency", {}) if isinstance(report, dict) else {}
        for issue in layer_1.get("issues_found", [])[:3] if isinstance(layer_1.get("issues_found"), list) else []:
            add(
                "causal_probe",
                f"How will the hypothesis be revised to address YanZhen Layer 1 issue: {issue}",
                mechanism,
                "Internal consistency issues must be resolved before evidence or experiments can rescue the claim.",
                "Revise the mechanism so the logical chain is explicit and self-consistent.",
                "fatal" if "unsupported causal link" in str(issue).lower() else "high",
            )

    if include("counterexample_challenge"):
        for item in contradicting[:2]:
            citation = str(item.get("citation") or "a contradicting PaperGraph record")
            evidence = trim_text(str(item.get("evidence_text") or ""), 240)
            add(
                "counterexample_challenge",
                f"How does the hypothesis survive or delimit the potentially conflicting evidence from {citation}: {evidence}?",
                mechanism,
                "A counterexample must be tied to a specific contradicting or limiting source when one is available.",
                "Explain whether this evidence falsifies, narrows, or motivates a discriminating experiment for the claim.",
                "high",
            )
        shifts = default_regime_shifts(text)
        for shift in shifts[:3]:
            add(
                "counterexample_challenge",
                f"What outcome should occur if {render_shift_condition(shift)}, and would that falsify the mechanism or only weaken it?",
                mechanism,
                "Counterexamples reveal whether the mechanism has real explanatory content across regimes.",
                "Add predicted behavior under this shifted condition and define pass/fail interpretation.",
                "medium",
            )
        report = yanzhen_report or {}
        layer_3 = report.get("layer_3_regime_shift_test", {}) if isinstance(report, dict) else {}
        if layer_3.get("cawm_risk_level") in {"MEDIUM", "HIGH"}:
            add(
                "counterexample_challenge",
                f"YanZhen reports {layer_3.get('cawm_risk_level')} CAWM risk; which assumption collapses first under regime shift?",
                mechanism,
                "The debate must localize the brittle assumption before accepting a refined hypothesis.",
                "Name the brittle assumption, restrict the validity regime, or propose a discriminating test.",
                "fatal" if layer_3.get("cawm_risk_level") == "HIGH" else "high",
            )

    if not questions:
        add(
            "causal_probe" if include("causal_probe") else "conceptual_clarification",
            "What single observation would most strongly change your belief in this hypothesis?",
            hypothesis_text,
            "Even apparently complete hypotheses need a belief-updating observation to remain falsifiable.",
            "Add a decisive observation and the expected update direction.",
            "low",
        )
    questions = dedupe_socratic_questions(questions)
    severity_rank = {"fatal": 4, "high": 3, "medium": 2, "low": 1}
    questions.sort(key=lambda item: (-severity_rank.get(str(item.get("severity")), 0), item.get("question_type", ""), item.get("question", "")))
    return questions[: clamp_int(max_questions, 1, 40)]

def socratic_evidence_terms(sources: list[Any], fallback_text: str = "", limit: int = 8) -> list[str]:
    try:
        from ._literature_import import is_low_information_field
        from ._literature_search import query_terms
        from ._utils import clamp_int, is_unknown_value, normalize_label, unique_preserve_order
        from ._verification import yanzhen_context_text
    except ImportError:
        from _literature_import import is_low_information_field
        from _literature_search import query_terms
        from _utils import clamp_int, is_unknown_value, normalize_label, unique_preserve_order
        from _verification import yanzhen_context_text
    records: list[dict[str, Any]] = [item for item in sources if isinstance(item, dict)]
    terms: list[str] = []
    for record in records:
        for key in ("method", "scenario", "benchmark"):
            value = normalize_label(record.get(key, ""))
            if value and not is_unknown_value(value) and not is_low_information_field(value, key):
                terms.append(value)
    if not terms:
        source_text = " ".join(yanzhen_context_text(item) for item in sources)
        terms = query_terms(source_text or fallback_text)
    return unique_preserve_order(term for term in terms if term and len(term) >= 3)[: clamp_int(limit, 1, 20)]

def dedupe_socratic_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from ._utils import normalize_key
    except ImportError:
        from _utils import normalize_key
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in questions:
        key = normalize_key(str(item.get("question") or ""))[:120]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped

def filter_new_debate_questions(
    questions: list[dict[str, Any]],
    previous_rounds: list[dict[str, Any]],
    *,
    min_keep: int = 2,
) -> list[dict[str, Any]]:
    previous: set[str] = set()
    for round_item in previous_rounds:
        for item in round_item.get("opponent_questions", []) if isinstance(round_item.get("opponent_questions"), list) else []:
            previous.add(question_similarity_key(str(item.get("question") or "")))
    fresh: list[dict[str, Any]] = []
    repeats: list[dict[str, Any]] = []
    for item in questions:
        key = question_similarity_key(str(item.get("question") or ""))
        if key and key in previous:
            repeated = dict(item)
            repeated["repeated_from_prior_round"] = True
            repeats.append(repeated)
            continue
        fresh.append(item)
    if len(fresh) >= min_keep:
        return fresh
    return fresh + repeats[: max(0, min_keep - len(fresh))]

def question_similarity_key(question: str) -> str:
    try:
        from ._literature_search import query_terms
    except ImportError:
        from _literature_search import query_terms
    terms = [term for term in query_terms(question) if term not in {"hypothesis", "mechanism", "evidence", "claim"}]
    return " ".join(terms[:10])

def socratic_overall_severity(questions: list[dict[str, Any]]) -> str:
    order = ["low", "medium", "high", "fatal"]
    best = 0
    for item in questions:
        try:
            best = max(best, order.index(str(item.get("severity") or "low")))
        except ValueError:
            continue
    return order[best]


def evaluate_hypothesis_quality(hypothesis_text: str, mechanism: str = "", sources: list | None = None) -> dict[str, Any]:
    """Evaluate hypothesis on three AHOIS-inspired dimensions (0.0-5.0 each).

    Dimensions:
    - physics_consistency: absence of internal contradictions, presence of domain constraints,
      causal chain completeness.
    - hypothesis_completeness: coverage of method/scenario/benchmark components, evidence
      linkage to PaperGraph sources, causal chain length.
    - uncertainty_calibration: distinguishing established facts from assumptions and
      unresolved propositions, hedging language, falsification criteria.

    Returns dict with per-dimension scores, overall score, and convergence-ready flag.
    """
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    text = normalize_space(f"{hypothesis_text} {mechanism}").lower()
    if not text.strip():
        return {"physics_consistency": 0.0, "hypothesis_completeness": 0.0,
                "uncertainty_calibration": 0.0, "overall": 0.0, "convergence_ready": False}

    # --- Physics Consistency (0-5) ---
    pc_score = 2.0  # baseline
    # Constraint/boundary terms → positive signal
    constraint_terms = ["constraint", "assumption", "boundary", "regime", "limit",
                        "condition", "threshold", "conservation", "equilibrium"]
    constraint_hits = sum(1 for t in constraint_terms if t in text)
    pc_score += min(constraint_hits * 0.3, 1.5)
    # Contradiction markers → negative signal
    contradiction_terms = ["contradicts", "inconsistent", "conflicts with", "paradox",
                           "however", "although", "despite", "nevertheless"]
    contradiction_hits = sum(1 for t in contradiction_terms if t in text)
    if contradiction_hits >= 3:
        pc_score -= 0.5  # many hedging terms without resolution
    elif contradiction_hits == 0:
        pc_score += 0.3  # clean logic
    # Causal chain presence
    causal_markers = ["because", "leads to", "causes", "results in", "therefore",
                      "consequently", "triggers", "mechanism"]
    causal_hits = sum(1 for t in causal_markers if t in text)
    pc_score += min(causal_hits * 0.3, 1.0)
    pc_score = max(0.0, min(5.0, pc_score))

    # --- Hypothesis Completeness (0-5) ---
    hc_score = 1.5  # baseline
    # Method/scenario/benchmark coverage
    component_terms = {
        "method": ["method", "technique", "algorithm", "model", "approach", "framework",
                    "simulation", "experiment", "measurement", "analysis"],
        "scenario": ["scenario", "system", "environment", "condition", "setup",
                      "application", "domain", "context", "case"],
        "benchmark": ["benchmark", "metric", "measure", "indicator", "criterion",
                       "accuracy", "efficiency", "performance", "yield", "rate"],
    }
    for component, terms in component_terms.items():
        hits = sum(1 for t in terms if t in text)
        hc_score += min(hits * 0.2, 0.5)
    # Evidence linkage
    source_count = len(sources) if sources else 0
    if source_count >= 3:
        hc_score += 0.5
    elif source_count >= 1:
        hc_score += 0.25
    # Numerical specificity
    if re.search(r"\b\d+\.?\d*\s*(?:%|fold|times|sigma|units|score|kV|MW|km|°C)\b", text):
        hc_score += 0.5
    # Causal chain length (from extract_causal_chain)
    chain = extract_causal_chain(text)
    chain_steps = len(chain.get("steps", []))
    if chain_steps >= 3:
        hc_score += 0.5
    elif chain_steps >= 2:
        hc_score += 0.25
    hc_score = max(0.0, min(5.0, hc_score))

    # --- Uncertainty Calibration (0-5) ---
    uc_score = 1.5  # baseline
    # Hedging/uncertainty markers → positive (distinguishes known from assumed)
    hedge_terms = ["assumed", "hypothesized", "predicted", "likely", "possible",
                   "uncertain", "unresolved", "to be determined", "pending",
                   "preliminary", "tentative", "estimated"]
    hedge_hits = sum(1 for t in hedge_terms if t in text)
    uc_score += min(hedge_hits * 0.3, 1.0)
    # Falsification criteria
    falsification_terms = ["falsif", "refut", "disprov", "test criterion",
                           "pass criterion", "fail if", "reject if"]
    falsification_hits = sum(1 for t in falsification_terms if t in text)
    uc_score += min(falsification_hits * 0.5, 1.0)
    # Established vs assumed distinction
    distinction_terms = ["established", "known", "shown", "demonstrated",
                         "we assume", "we hypothesize", "it is proposed"]
    distinction_hits = sum(1 for t in distinction_terms if t in text)
    uc_score += min(distinction_hits * 0.3, 0.5)
    uc_score = max(0.0, min(5.0, uc_score))

    overall = round((pc_score + hc_score + uc_score) / 3.0, 2)
    convergence_ready = pc_score >= 4.0 and hc_score >= 3.5 and uc_score >= 3.0

    return {
        "physics_consistency": round(pc_score, 2),
        "hypothesis_completeness": round(hc_score, 2),
        "uncertainty_calibration": round(uc_score, 2),
        "overall": overall,
        "convergence_ready": convergence_ready,
    }

def debate_safety_gates(
    *,
    proponent_model_family: str,
    opponent_model_family: str,
    judge_model_family: str,
    verifier_model_family: str,
) -> dict[str, Any]:
    # Model-family independence firewall removed: all roles use Qwen-family models by design.
    # Independence is enforced by distinct role prompts and adversarial structure, not model divergence.
    warnings: list[str] = []
    if not judge_model_family:
        warnings.append("BianLun/judge model family is not recorded.")
    return {
        "passed": True,
        "issues": [],
        "warnings": warnings,
        "independence": {
            "proponent_model_family": proponent_model_family,
            "opponent_model_family": opponent_model_family,
            "judge_model_family": judge_model_family,
            "verifier_model_family": verifier_model_family,
            "policy": "All-Qwen multi-role setup (qwen-plus/qwen-max/qwen-deep-research). Independence enforced by role prompts.",
        },
        "evidence_gate": "Debate revisions are adopted only if tied to PaperGraph evidence, YanZhen issue, or an explicit missing-evidence condition.",
        "convergence_gate": "If two rounds add no substantive revision, terminate with best current hypothesis and unresolved issues.",
    }

def is_qwen_model_id(value: str) -> bool:
    try:
        from ._utils import normalize_key
    except ImportError:
        from _utils import normalize_key
    normalized = normalize_key(value)
    return normalized.startswith("qwen") or normalized.startswith("tongyi") or normalized.startswith("dashscope")

def debate_proponent_position(text: str, mechanism: str, record: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._utils import trim_text
        from ._verification import extract_causal_chain, yanzhen_cited_data_for_hypothesis
    except ImportError:
        from _utils import trim_text
        from _verification import extract_causal_chain, yanzhen_cited_data_for_hypothesis
    return {
        "hypothesis": trim_text(text, 800),
        "claimed_mechanism": trim_text(mechanism, 800),
        "causal_chain": extract_causal_chain(f"{text} {mechanism}"),
        "evidence_refs": yanzhen_cited_data_for_hypothesis({"papergraph": []}, record) if record else [],
        "falsification_plan": record.get("test_plan", "") if isinstance(record, dict) else "",
    }

def debate_experiment_text(record: dict[str, Any]) -> str:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    final = record.get("mingli_final_idea") if isinstance(record.get("mingli_final_idea"), dict) else {}
    experiments = final.get("experiments") if isinstance(final.get("experiments"), dict) else {}
    return normalize_space(" ".join(str(experiments.get(key) or "") for key in ("setup", "metrics", "baselines")) or str(record.get("test_plan") or ""))

def debate_refined_hypothesis(
    project: dict[str, Any],
    record: dict[str, Any],
    text: str,
    mechanism: str,
    rounds: list[dict[str, Any]],
    yanzhen_body: dict[str, Any],
) -> dict[str, Any]:
    try:
        from ._hypothesis import hypothesis_boundary_condition, hypothesis_control_variable, infer_gap_components, mechanism_contract_for_candidate
        from ._utils import find_by_id, normalize_space, unique_preserve_order
        from ._verification import default_regime_shifts, extract_causal_chain
    except ImportError:
        from _hypothesis import hypothesis_boundary_condition, hypothesis_control_variable, infer_gap_components, mechanism_contract_for_candidate
        from _utils import find_by_id, normalize_space, unique_preserve_order
        from _verification import default_regime_shifts, extract_causal_chain
    all_questions = [q for round_item in rounds for q in round_item.get("opponent_questions", []) if isinstance(q, dict)]
    actual_revisions = [
        item.get("proponent_response", {})
        for item in rounds
        if isinstance(item.get("proponent_response"), dict)
        and item.get("proponent_response", {}).get("revision_status") == "REVISED"
    ]
    high_revisions = unique_preserve_order(
        str(item)
        for response in actual_revisions
        for item in response.get("adopted_revision_requirements", [])
        if item
    )
    gap_id = str(record.get("gap_id") or "")
    source_gap = find_by_id(project.get("knowledge_gaps", []), "gap_id", gap_id) if gap_id else {}
    components = infer_gap_components(project, source_gap or {})
    variable = hypothesis_control_variable(source_gap or {}, components.get("method", ""), components.get("scenario", ""))
    boundary = hypothesis_boundary_condition(source_gap or {})
    causal_chain = extract_causal_chain(f"{text} {mechanism}")
    if len(causal_chain) < 2:
        causal_chain = [
            f"Input/intervention: vary {variable}",
            f"Mechanism: test whether the claimed causal pathway remains valid at {boundary}",
            f"Output: measure {components.get('benchmark') or 'the preregistered primary metric'} against baselines",
        ]
    latest_revision = actual_revisions[-1] if actual_revisions else {}
    specification = latest_revision.get("mechanism_specification", {}) if isinstance(latest_revision.get("mechanism_specification"), dict) else {}
    final_idea = record.get("mingli_final_idea", {}) if isinstance(record.get("mingli_final_idea"), dict) else {}
    bridge = latest_revision.get("cross_domain_bridge", final_idea.get("cross_domain_bridge", {}))
    bridge = bridge if isinstance(bridge, dict) else {}
    contract = mechanism_contract_for_candidate(
        {
            "statement": text,
            "mechanism": mechanism,
            "causal_chain": causal_chain,
            "mechanism_specification": specification,
            "cross_domain_bridge": bridge,
            "collision_source": final_idea.get("collision_source", record.get("collision_source", {})),
            "null_hypothesis": final_idea.get("null_hypothesis", ""),
            "alternative_hypothesis": final_idea.get("alternative_hypothesis", ""),
            "testable_subhypotheses": final_idea.get("testable_subhypotheses", []),
            "evidence_assignment": latest_revision.get("evidence_assignment", final_idea.get("evidence_assignment", [])),
        }
    )
    refined_statement = normalize_space(text)
    layer_3 = yanzhen_body.get("layer_3_regime_shift_test", {}) if isinstance(yanzhen_body, dict) else {}
    return {
        "hypothesis": refined_statement,
        "mechanism": normalize_space(mechanism),
        "causal_chain": causal_chain,
        "adopted_revisions": high_revisions[:10],
        "revision_status": latest_revision.get("revision_status", "UNCHANGED"),
        "mechanism_contract": contract,
        "evidence_assignment": latest_revision.get("evidence_assignment", final_idea.get("evidence_assignment", [])),
        "evidence_requirements": [
            "Map each central claim to a PaperGraph citation or mark it as speculative.",
            "Provide evidence for causal connectors, not only endpoint performance.",
        ],
        "falsification_conditions": [
            f"No mechanism-separating change in {components.get('benchmark') or 'the primary metric'} when {variable} crosses {boundary}.",
            "YanZhen Layer 1 or Layer 2 remains FAIL after revision.",
            "Regime-shift stability collapses unexpectedly under at least two shifted conditions.",
        ],
        "regime_shift_requirements": layer_3.get("shifted_conditions_tested", default_regime_shifts(refined_statement)[:2]),
    }

def build_mingli_revision_prompt(
    *,
    hypothesis_text: str,
    mechanism: str,
    questions: list[dict[str, Any]],
    evidence_packets: list[dict[str, Any]],
    yanzhen_body: dict[str, Any],
    mechanism_specification: dict[str, Any],
    cross_domain_bridge: dict[str, Any],
    round_name: str,
) -> str:
    """Build an evidence-bound reply request for a genuine debate revision."""
    evidence_text = "\n".join(
        f"- {item.get('citation') or item.get('title')}: {str(item.get('evidence_text') or item.get('abstract') or '')[:340]}"
        for item in evidence_packets[:5]
        if isinstance(item, dict)
    ) or "- No concise evidence packet is available; do not claim source support that is absent."
    question_text = "\n".join(
        f"- [{item.get('question_type')}/{item.get('severity')}] {item.get('question')} Required revision: {item.get('required_revision')}"
        for item in questions[:8]
        if isinstance(item, dict)
    ) or "- No opponent question was supplied."
    audit_text = json.dumps(
        {
            "overall_verdict": yanzhen_body.get("overall_verdict"),
            "required_actions": yanzhen_body.get("required_actions", []),
            "unsupported_claims": yanzhen_body.get("unsupported_claims", []),
        },
        ensure_ascii=False,
    )
    return f"""You are MingLi revising a scientific hypothesis after a Socratic challenge round named {round_name}.

Current hypothesis:
{hypothesis_text}

Current mechanism:
{mechanism}

PaperGraph evidence (the only source of factual support):
{evidence_text}

DuZhi's questions which must be answered individually:
{question_text}

YanZhen audit feedback:
{audit_text}

Current mechanism specification:
{json.dumps(mechanism_specification, ensure_ascii=False)}

Current cross-domain bridge:
{json.dumps(cross_domain_bridge, ensure_ascii=False)}

Return strict JSON. Do a real rewrite, rather than appending generic controls or a method comparison.
{{
  "revision_status": "REVISED | NEEDS_EVIDENCE | DOWNGRADED_TO_PHENOMENOLOGY",
  "revised_hypothesis": "If X, then Y, because concrete Z",
  "revised_mechanism": "explicit causal mechanism",
  "causal_chain": [{{"from":"X","to":"Z","relation":"causes","evidence_status":"source_supported | novel_candidate"}}, {{"from":"Z","to":"Y","relation":"causes","evidence_status":"source_supported | novel_candidate"}}],
  "evidence_assignment": [{{"causal_link":"X -> Z","citations":["exact PaperGraph citation"],"support_level":"direct | partial | novel_candidate"}}, {{"causal_link":"Z -> Y","citations":["exact PaperGraph citation"],"support_level":"direct | partial | novel_candidate"}}],
  "mechanism_specification": {{"identity":"...","location_or_scope":"...","dynamics":"...","reversibility":"...","intervention":"...","counterfactual":"...","observability":[{{"modality":"...","signal":"..."}},{{"modality":"...","signal":"..."}}]}},
  "cross_domain_bridge": {{"source_domain":"...","abstract_structure":"...","target_role_mapping":[{{"lens_role":"...","papergraph_entity":"..."}},{{"lens_role":"...","papergraph_entity":"..."}}],"novel_mechanism_claim":"..."}},
  "null_hypothesis":"...",
  "alternative_hypothesis":"...",
  "testable_subhypotheses":["...","...","..."],
  "question_responses":[{{"question":"exact DuZhi question","response":"specific answer or explicit evidence deficit","action":"revised | needs_evidence | downgraded"}}],
  "evidence_needed": ["targeted missing evidence, only if necessary"]
}}

Rules: Do not invent numbers, chemical species, formulas, citations, data, or instrument signatures. A novel mediator is permitted only as `novel_candidate` with an intervention and counterfactual. If evidence cannot make Z concrete, return NEEDS_EVIDENCE or DOWNGRADED_TO_PHENOMENOLOGY and preserve intellectual honesty. Do not use generic terms such as damage, complexity, instability, regulation, or state change as Z unless concretely defined in every required field."""


def mingli_llm_revision_from_questions(
    *,
    record: dict[str, Any],
    hypothesis_text: str,
    mechanism: str,
    questions: list[dict[str, Any]],
    evidence_packets: list[dict[str, Any]],
    yanzhen_body: dict[str, Any],
    mechanism_specification: dict[str, Any],
    round_name: str,
) -> dict[str, Any] | None:
    """Return an accepted semantic revision, or None when it cannot meet the contract."""
    try:
        from ._hypothesis import mechanism_contract_for_candidate
        from ._llm import call_llm_json
        from ._utils import normalize_space, trim_text, unique_preserve_order
    except ImportError:
        from _hypothesis import mechanism_contract_for_candidate
        from _llm import call_llm_json
        from _utils import normalize_space, trim_text, unique_preserve_order
    final_idea = record.get("mingli_final_idea", {}) if isinstance(record.get("mingli_final_idea"), dict) else {}
    bridge = final_idea.get("cross_domain_bridge", record.get("cross_domain_bridge", {}))
    bridge = bridge if isinstance(bridge, dict) else {}
    try:
        payload = call_llm_json(
            "You are MingLi. Revise hypotheses with causal precision and epistemic honesty.",
            build_mingli_revision_prompt(
                hypothesis_text=hypothesis_text,
                mechanism=mechanism,
                questions=questions,
                evidence_packets=evidence_packets,
                yanzhen_body=yanzhen_body,
                mechanism_specification=mechanism_specification,
                cross_domain_bridge=bridge,
                round_name=round_name,
            ),
            max_tokens=2200,
        )
    except Exception as exc:
        log_event("WARN", "mingli_debate_revision_llm_failed", round=round_name, error=str(exc))
        return None
    revised_hypothesis = normalize_space(str(payload.get("revised_hypothesis") or ""))
    revised_mechanism = normalize_space(str(payload.get("revised_mechanism") or ""))
    if not revised_hypothesis or not revised_mechanism:
        log_event("WARN", "mingli_debate_revision_incomplete", round=round_name)
        return None
    raw_chain = payload.get("causal_chain", []) if isinstance(payload.get("causal_chain"), list) else []
    causal_chain = []
    for item in raw_chain:
        if isinstance(item, dict):
            causal_chain.append(
                normalize_space(
                    f"{item.get('from') or ''} -> {item.get('to') or ''} "
                    f"({item.get('relation') or 'relates_to'}; {item.get('evidence_status') or 'unclassified'})"
                )
            )
        elif normalize_space(str(item)):
            causal_chain.append(normalize_space(str(item)))
    specification = payload.get("mechanism_specification") if isinstance(payload.get("mechanism_specification"), dict) else {}
    revised_bridge = payload.get("cross_domain_bridge") if isinstance(payload.get("cross_domain_bridge"), dict) else bridge
    candidate = {
        "statement": revised_hypothesis,
        "mechanism": revised_mechanism,
        "causal_chain": causal_chain,
        "mechanism_specification": specification,
        "cross_domain_bridge": revised_bridge,
        "collision_source": record.get("collision_source", {}),
        "null_hypothesis": payload.get("null_hypothesis", ""),
        "alternative_hypothesis": payload.get("alternative_hypothesis", ""),
        "testable_subhypotheses": payload.get("testable_subhypotheses", []),
        "evidence_assignment": payload.get("evidence_assignment", []),
    }
    contract = mechanism_contract_for_candidate(candidate)
    question_responses = payload.get("question_responses", []) if isinstance(payload.get("question_responses"), list) else []
    revision_status = str(payload.get("revision_status") or "NEEDS_EVIDENCE")
    if revision_status == "REVISED" and contract.get("verdict") != "READY":
        revision_status = "NEEDS_EVIDENCE"
    revised_question_texts = {
        normalize_space(str(item.get("question") or ""))
        for item in question_responses
        if isinstance(item, dict) and str(item.get("action") or "").lower() == "revised"
    }
    adopted_requirements = unique_preserve_order(
        str(item.get("required_revision") or "")
        for item in questions
        if isinstance(item, dict)
        and normalize_space(str(item.get("question") or "")) in revised_question_texts
        and str(item.get("required_revision") or "")
    )
    return {
        "round_name": round_name,
        "revision_status": revision_status,
        "proponent_response": (
            "MingLi supplied a question-specific causal rewrite."
            if revision_status == "REVISED"
            else "MingLi could not honestly complete the causal contract and requests targeted evidence before another rewrite."
        ),
        "addressed_opponent_questions": question_responses,
        "adopted_revision_requirements": adopted_requirements,
        "revision_delta": [
            f"[{item.get('action') or 'response'}] {trim_text(str(item.get('response') or ''), 300)}"
            for item in question_responses if isinstance(item, dict) and item.get("response")
        ],
        "revision_diff_table": [
            {
                "opponent_concern": trim_text(str(item.get("question") or ""), 180),
                "revision_applied": trim_text(str(item.get("response") or ""), 240),
                "question_type": "llm_socratic_response",
            }
            for item in question_responses if isinstance(item, dict)
        ],
        "revised_hypothesis": trim_text(revised_hypothesis if revision_status == "REVISED" else hypothesis_text, 2400),
        "revised_mechanism": trim_text(revised_mechanism if revision_status == "REVISED" else mechanism, 1600),
        "causal_chain_after_revision": causal_chain,
        "mechanism_specification": specification,
        "cross_domain_bridge": revised_bridge,
        "mechanism_contract": contract,
        "evidence_assignment": payload.get("evidence_assignment", []),
        "evidence_needed": payload.get("evidence_needed", []),
        "remaining_speculative_claims": [] if revision_status == "REVISED" else contract.get("missing_knowledge", []),
    }


def mingli_revision_from_questions(
    project: dict[str, Any],
    record: dict[str, Any],
    hypothesis_text: str,
    mechanism: str,
    questions: list[dict[str, Any]],
    yanzhen_body: dict[str, Any],
    round_name: str,
    *,
    use_llm: bool = False,
    mechanism_specification: dict[str, Any] | None = None,
    debate_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from ._hypothesis import hypothesis_boundary_condition, hypothesis_control_variable, infer_gap_components
        from ._utils import find_by_id, normalize_space, trim_text, unique_preserve_order
        from ._verification import extract_causal_chain
    except ImportError:
        from _hypothesis import hypothesis_boundary_condition, hypothesis_control_variable, infer_gap_components
        from _utils import find_by_id, normalize_space, trim_text, unique_preserve_order
        from _verification import extract_causal_chain
    serious = [
        item
        for item in questions
        if item.get("severity") in {"high", "fatal"} and normalize_space(str(item.get("required_revision") or ""))
    ]
    adopted = unique_preserve_order(str(item.get("required_revision") or "") for item in serious)[:8]
    gap_id = str(record.get("gap_id") or "")
    source_gap = find_by_id(project.get("knowledge_gaps", []), "gap_id", gap_id) if gap_id else {}
    components = infer_gap_components(project, source_gap or {})
    method = components.get("method") or "the proposed intervention or method"
    scenario = components.get("scenario") or project.get("domain") or "the target scenario"
    benchmark = components.get("benchmark") or "the preregistered primary metric"
    variable = hypothesis_control_variable(source_gap or {}, method, scenario)
    boundary = hypothesis_boundary_condition(source_gap or {})
    chain = extract_causal_chain(f"{hypothesis_text} {mechanism}")
    if len(chain) < 3:
        chain = [
            f"Input/intervention: change {variable} while holding the closest baseline/control fixed.",
            f"Mechanism: test whether {method} changes the relevant state or process inside {scenario}.",
            f"Output: measure {benchmark} and compare it with baseline and failure-mode controls.",
        ]
    layer_1 = yanzhen_body.get("layer_1_internal_consistency", {}) if isinstance(yanzhen_body, dict) else {}
    layer_2 = yanzhen_body.get("layer_2_data_consistency", {}) if isinstance(yanzhen_body, dict) else {}
    layer_3 = yanzhen_body.get("layer_3_regime_shift_test", {}) if isinstance(yanzhen_body, dict) else {}
    adaptability = yanzhen_body.get("domain_adaptability_audit", {}) if isinstance(yanzhen_body, dict) else {}
    audit_requirements: list[str] = []
    if layer_1.get("verdict") == "FAIL":
        for issue in layer_1.get("issues_found", [])[:3]:
            audit_requirements.append(f"Layer 1 repair: {issue}")
    if layer_2.get("verdict") == "FAIL":
        audit_requirements.append("separate source-supported claims from speculative mechanism claims")
    if layer_3.get("verdict") == "FAIL" or layer_3.get("cawm_risk_level") in {"MEDIUM", "HIGH"}:
        audit_requirements.append("restrict the validity regime and add at least two regime-shift predictions")
    if adaptability.get("verdict") == "FAIL":
        for cond in adaptability.get("adaptation_conditions", [])[:2]:
            audit_requirements.append(f"Domain adaptability: {cond}")
    elif adaptability.get("verdict") == "WARN":
        for issue in adaptability.get("issues_found", [])[:2]:
            audit_requirements.append(f"Adaptability concern: {issue}")
    adopted = unique_preserve_order(adopted + audit_requirements)[:12]
    evidence_packets = source_gap.get("evidence_packets", []) if isinstance(source_gap, dict) else []
    if not evidence_packets and isinstance(record.get("evidence_packets"), list):
        evidence_packets = record.get("evidence_packets", [])
    if not evidence_packets:
        final_idea = record.get("mingli_final_idea", {}) if isinstance(record.get("mingli_final_idea"), dict) else {}
        evidence_packets = final_idea.get("evidence_packets", []) if isinstance(final_idea.get("evidence_packets"), list) else []
    if adopted and use_llm:
        llm_revision = mingli_llm_revision_from_questions(
            record=record,
            hypothesis_text=hypothesis_text,
            mechanism=mechanism,
            questions=serious,
            evidence_packets=evidence_packets,
            yanzhen_body=yanzhen_body,
            mechanism_specification=mechanism_specification or {},
            round_name=round_name,
        )
        if llm_revision is not None:
            return llm_revision

    if adopted:
        # Do not claim a rewrite when no model has produced a concrete mediator.
        # The deterministic path records the exact evidence debt so Boxue can
        # request ZhiZhi supplementation instead of laundering it into prose.
        addressed = mingli_address_questions(serious, method, scenario, benchmark, variable, boundary, evidence_packets=evidence_packets)
        revision_delta = [
            f"[{item.get('question_type')}] evidence or a concrete mechanism definition is still required: {item.get('adopted_revision')}"
            for item in addressed
        ]
        revision_diff_table = [
            {
                "opponent_concern": item.get("opponent_question", ""),
                "revision_applied": "not yet applied; requires evidence-backed semantic rewrite",
                "question_type": item.get("question_type", "general"),
            }
            for item in addressed
        ]
        response = (
            f"MingLi cannot honestly claim a completed revision for {round_name} without a concrete mediator contract; "
            "the listed questions become targeted evidence and rewrite requirements."
        )
        revised_hypothesis = hypothesis_text
        revised_mechanism = mechanism
        revision_status = "NEEDS_EVIDENCE"
    else:
        addressed = mingli_address_questions(questions[:3], method, scenario, benchmark, variable, boundary, evidence_packets=evidence_packets)
        revision_delta = ["No high-severity critique required a structural revision in this round."]
        revision_diff_table = []
        revised_hypothesis = hypothesis_text
        revised_mechanism = mechanism
        response = "MingLi keeps the current hypothesis but records the opponent questions as monitoring checks."
        revision_status = "UNCHANGED"
    return {
        "round_name": round_name,
        "revision_status": revision_status,
        "proponent_response": response,
        "addressed_opponent_questions": addressed,
        "adopted_revision_requirements": adopted,
        "revision_delta": revision_delta,
        "revision_diff_table": revision_diff_table,
        "revised_hypothesis": trim_text(revised_hypothesis, 2400),
        "revised_mechanism": trim_text(revised_mechanism, 1600),
        "causal_chain_after_revision": chain,
        "mechanism_specification": mechanism_specification or {},
        "remaining_speculative_claims": mingli_remaining_speculative_claims(questions, yanzhen_body),
    }

def mingli_address_questions(
    questions: list[dict[str, Any]],
    method: str,
    scenario: str,
    benchmark: str,
    variable: str,
    boundary: str,
    *,
    evidence_packets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    try:
        from ._utils import normalize_space, trim_text
    except ImportError:
        from _utils import normalize_space, trim_text
    evidence_packets = [item for item in (evidence_packets or []) if isinstance(item, dict)]
    evidence = evidence_packets[0] if evidence_packets else {}
    evidence_ref = str(evidence.get("citation") or evidence.get("title") or "the cited PaperGraph record")
    evidence_text = trim_text(str(evidence.get("evidence_text") or ""), 220)
    addressed: list[dict[str, Any]] = []
    for question in questions[:8]:
        qtext = normalize_space(str(question.get("question") or ""))
        required = normalize_space(str(question.get("required_revision") or ""))
        qtype = str(question.get("question_type") or "")
        if not qtext:
            continue
        lower = f"{qtext} {required}".lower()
        if qtype == "adaptability_challenge" or any(term in lower for term in ("incompatib", "method family", "data type", "cross-domain", "bridging", "adaptation condition")):
            response = (
                f"Restrict the claim to the representation explicitly supported by {evidence_ref}; define the input and output "
                f"required by {method} in {scenario}, and treat the transfer as unvalidated unless that bridge is observed."
            )
        elif any(term in lower for term in ("threshold", "metric", "measurable", "observable", "success")):
            response = f"Treat {benchmark} as the measured quantity, distinguish it from the inferred mechanism, and preregister a comparison against the closest evidence-backed baseline from {evidence_ref}."
        elif any(term in lower for term in ("boundary", "regime", "condition", "shift", "noise", "scale")):
            response = f"Limit the claim to {boundary}; state the expected observation outside that regime and label the mechanism uncertain if the source-derived condition is not met."
        elif any(term in lower for term in ("evidence", "citation", "papergraph", "unsupported", "source")):
            response = f"Map the premise to {evidence_ref}{f': {evidence_text}' if evidence_text else ''}; any causal connector not covered by that evidence is downgraded to an explicit falsifiable assumption."
        elif any(term in lower for term in ("causal", "chain", "input", "mediator", "output")):
            response = f"Rewrite the chain as input={variable}; candidate mediator={method} acting in {scenario}; measured output={benchmark}; require the mediator to change before the output."
        elif any(term in lower for term in ("baseline", "control", "negative")):
            response = f"Compare {variable} with a matched baseline and a negative control; do not interpret a change in {benchmark} as mechanistic unless the mediator ordering is observed."
        else:
            response = f"Treat this as a constraint on the {method} -> {scenario} mechanism and record it as a falsification check."
        addressed.append(
            {
                "question_type": qtype,
                "opponent_question": trim_text(qtext, 260),
                "mingli_direct_response": response,
                "adopted_revision": required,
            }
        )
    return addressed

def mingli_remaining_speculative_claims(questions: list[dict[str, Any]], yanzhen_body: dict[str, Any]) -> list[str]:
    try:
        from ._utils import trim_text, unique_preserve_order
    except ImportError:
        from _utils import trim_text, unique_preserve_order
    claims = [
        str(item.get("target_claim") or item.get("question") or "")
        for item in questions
        if item.get("severity") in {"high", "fatal"}
    ]
    if isinstance(yanzhen_body, dict):
        layer_2 = yanzhen_body.get("layer_2_data_consistency", {})
        if isinstance(layer_2, dict) and layer_2.get("verdict") == "FAIL":
            claims.append("Mechanism-data alignment remains incomplete until source quotations or structured evidence are attached.")
    return unique_preserve_order(trim_text(item, 180) for item in claims if item)[:8]

def yanzhen_debate_feedback(yanzhen_body: dict[str, Any]) -> dict[str, Any]:
    try:
        from ._verification import yanzhen_public_verdict
    except ImportError:
        from _verification import yanzhen_public_verdict
    if not isinstance(yanzhen_body, dict) or not yanzhen_body:
        return {"verdict": "NOT_RUN", "required_actions": [], "unsupported_claims": []}
    return {
        "verdict": yanzhen_body.get("verdict") or yanzhen_public_verdict(str(yanzhen_body.get("overall_verdict") or "")),
        "overall_verdict": yanzhen_body.get("overall_verdict", ""),
        "required_actions": yanzhen_body.get("required_actions", []),
        "unsupported_claims": yanzhen_body.get("unsupported_claims", []),
        "audit_layers": {
            "internal": (yanzhen_body.get("layer_1_internal_consistency") or {}).get("verdict"),
            "data": (yanzhen_body.get("layer_2_data_consistency") or {}).get("verdict"),
            "regime_shift": (yanzhen_body.get("layer_3_regime_shift_test") or {}).get("verdict"),
            "feasibility": (yanzhen_body.get("feasibility_audit") or {}).get("verdict"),
            "domain_adaptability": (yanzhen_body.get("domain_adaptability_audit") or {}).get("verdict"),
        },
    }

def duzhi_questions_from_yanzhen_actions(
    yanzhen_body: dict[str, Any],
    hypothesis_text: str,
    mechanism: str,
) -> list[dict[str, Any]]:
    try:
        from ._utils import trim_text
    except ImportError:
        from _utils import trim_text
    questions: list[dict[str, Any]] = []
    actions = yanzhen_body.get("required_actions", []) if isinstance(yanzhen_body.get("required_actions"), list) else []
    unsupported = yanzhen_body.get("unsupported_claims", []) if isinstance(yanzhen_body.get("unsupported_claims"), list) else []
    for claim in unsupported[:4]:
        questions.append(
            {
                "question_type": "evidence_completion_challenge",
                "question": f"YanZhen marked this claim as unsupported: {claim}. Will MingLi attach a PaperGraph citation, narrow the claim, or remove it?",
                "target_claim": trim_text(str(claim), 220),
                "why_it_matters": "Unsupported links are exactly where CAWM and selective-citation failures enter the hypothesis.",
                "required_revision": "Attach evidence for the unsupported claim or downgrade it to an explicit assumption with a falsification test.",
                "severity": "high",
            }
        )
    for action in actions[:5]:
        action_name = str(action.get("action") or "")
        is_adaptability_fatal = action_name in {
            "resolve_method_scenario_incompatibility",
            "address_domain_adaptability_concerns",
        }
        is_causal_fatal = action_name in {"mingli_rewrite_causal_chain", "restrict_validity_regime_and_add_shift_tests"}
        severity = "fatal" if (is_causal_fatal or is_adaptability_fatal) else "high"
        suggested = str(action.get("suggested_revision") or action.get("reason") or action_name)
        questions.append(
            {
                "question_type": "adaptability_challenge" if is_adaptability_fatal else "audit_action_challenge",
                "question": f"YanZhen requires `{action_name}`. What concrete revision satisfies this action before the hypothesis advances?",
                "target_claim": mechanism or hypothesis_text,
                "why_it_matters": str(action.get("reason") or "Mechanism audit actions must be resolved before BianLun can accept the claim."),
                "required_revision": suggested,
                "severity": severity,
            }
        )
    if not questions:
        questions.append(
            {
                "question_type": "audit_action_challenge",
                "question": "YanZhen did not provide a concrete action; what audit evidence would make the mechanism pass rather than require review?",
                "target_claim": hypothesis_text,
                "why_it_matters": "A debate cannot close without a clear pass criterion.",
                "required_revision": "State the pass criterion and attach it to the refined hypothesis.",
                "severity": "medium",
            }
        )
    return dedupe_socratic_questions(questions)

def debate_question_adopted(question: dict[str, Any], rounds: list[dict[str, Any]]) -> bool:
    try:
        from ._utils import normalize_space
    except ImportError:
        from _utils import normalize_space
    requirement = normalize_space(str(question.get("required_revision") or ""))
    if not requirement:
        return False
    for round_item in rounds:
        response = round_item.get("proponent_response")
        if not isinstance(response, dict):
            continue
        adopted = [normalize_space(str(item)) for item in response.get("adopted_revision_requirements", [])]
        if requirement in adopted:
            return True
    return False

def build_debate_state(
    *,
    hypothesis_id: str,
    rounds: list[dict[str, Any]],
    max_rounds: int,
    unresolved: list[str],
    final_decision: str,
) -> dict[str, Any]:
    try:
        from ._models import DebateArgument, DebateState
        from ._utils import trim_text
        from ._verification import yanzhen_public_verdict
    except ImportError:
        from _models import DebateArgument, DebateState
        from _utils import trim_text
        from _verification import yanzhen_public_verdict
    state = DebateState(
        hypothesis_id=hypothesis_id,
        round=max([int(item.get("round") or 0) for item in rounds] or [0]),
        max_rounds=max_rounds,
        unresolved_issues=list(unresolved),
        status=debate_status_from_decision(final_decision),
    )
    for round_item in rounds:
        round_no = int(round_item.get("round") or 0)
        if round_item.get("proponent_position"):
            state.arguments.append(
                asdict(
                    DebateArgument(
                        round=round_no,
                        speaker="MingLi",
                        role="proponent",
                        content=trim_text(json.dumps(round_item.get("proponent_position"), ensure_ascii=False), 900),
                        verdict=str(round_item.get("moderator_verdict") or ""),
                    )
                )
            )
        for question in round_item.get("opponent_questions", []) if isinstance(round_item.get("opponent_questions"), list) else []:
            state.arguments.append(
                asdict(
                    DebateArgument(
                        round=round_no,
                        speaker="DuZhi",
                        role="opponent",
                        content=trim_text(str(question.get("question") or ""), 900),
                        verdict=str(question.get("severity") or ""),
                    )
                )
            )
        response = round_item.get("proponent_response")
        if isinstance(response, dict):
            state.revisions.append(
                {
                    "round": round_no,
                    "adopted_revision_requirements": response.get("adopted_revision_requirements", []),
                    "revision_delta": response.get("revision_delta", []),
                    "remaining_speculative_claims": response.get("remaining_speculative_claims", []),
                }
            )
        if isinstance(round_item.get("yanzhen_report"), dict):
            state.mechanism_audits.append(
                {
                    "round": round_no,
                    "verdict": round_item["yanzhen_report"].get("verdict") or yanzhen_public_verdict(str(round_item["yanzhen_report"].get("overall_verdict") or "")),
                    "overall_verdict": round_item["yanzhen_report"].get("overall_verdict"),
                    "required_actions": round_item["yanzhen_report"].get("required_actions", []),
                    "unsupported_claims": round_item["yanzhen_report"].get("unsupported_claims", []),
                }
            )
        if isinstance(round_item.get("literature_supplement"), dict):
            state.literature_supplements.append(round_item["literature_supplement"])
    return asdict(state)

def debate_status_from_decision(final_decision: str) -> str:
    if final_decision == "accept_for_experiment":
        return "CONCLUDED"
    if final_decision in {"human_review", "revise"}:
        return "ESCALATED"
    return "CONCLUDED"

def debate_unresolved_issues(questions: list[dict[str, Any]], yanzhen_body: dict[str, Any]) -> list[str]:
    try:
        from ._utils import unique_preserve_order
    except ImportError:
        from _utils import unique_preserve_order
    issues = [
        f"{item.get('question_type')}: {item.get('question')}"
        for item in questions
        if item.get("severity") in {"high", "fatal"}
    ]
    if isinstance(yanzhen_body, dict):
        if yanzhen_body.get("overall_verdict") in {"CAWM_DETECTED", "REQUIRES_HUMAN_REVIEW"}:
            issues.append(f"YanZhen overall verdict: {yanzhen_body.get('overall_verdict')}")
        for layer_key in ("layer_1_internal_consistency", "layer_2_data_consistency", "layer_3_regime_shift_test"):
            layer = yanzhen_body.get(layer_key, {})
            if isinstance(layer, dict):
                issues.extend(str(issue) for issue in layer.get("issues_found", [])[:4] if issue)
    return unique_preserve_order(issues)[:20]

def debate_final_decision(
    rounds: list[dict[str, Any]],
    yanzhen_body: dict[str, Any],
    refined: dict[str, Any],
    execution_validation: dict[str, Any] | None = None,
) -> str:
    all_questions = [q for round_item in rounds for q in round_item.get("opponent_questions", []) if isinstance(q, dict)]
    unadopted_serious = [
        item
        for item in all_questions
        if item.get("severity") in {"high", "fatal"} and not debate_question_adopted(item, rounds)
    ]
    if any(item.get("severity") == "fatal" for item in unadopted_serious):
        return "revise"
    if yanzhen_body.get("overall_verdict") == "CAWM_DETECTED":
        return "revise"
    if yanzhen_body.get("overall_verdict") == "REQUIRES_HUMAN_REVIEW":
        return "human_review"
    contract = refined.get("mechanism_contract", {}) if isinstance(refined.get("mechanism_contract"), dict) else {}
    if contract.get("verdict") != "READY":
        return "revise"
    if len(refined.get("causal_chain", [])) < 2:
        return "revise"
    if execution_validation:
        verdict = execution_validation.get("verdict")
        if verdict == "FAIL":
            return "revise"
        if verdict == "REQUIRES_HUMAN_REVIEW":
            return "human_review"
    if any(item.get("severity") == "high" for item in unadopted_serious):
        return "revise"
    return "accept_for_experiment"

def execution_level_validation(
    project: dict[str, Any],
    refined: dict[str, Any],
    yanzhen_body: dict[str, Any],
    rounds: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        from ._utils import normalize_space
        from ._verification import yanzhen_public_verdict
    except ImportError:
        from _utils import normalize_space
        from _verification import yanzhen_public_verdict
    text = normalize_space(
        " ".join(
            [
                str(refined.get("hypothesis") or ""),
                " ".join(str(item) for item in refined.get("causal_chain", []) if item),
                " ".join(str(item) for item in refined.get("falsification_conditions", []) if item),
                " ".join(str(item) for item in refined.get("evidence_requirements", []) if item),
            ]
        )
    ).lower()
    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, severity: str, reason: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "severity": severity, "reason": reason})

    causal_chain = refined.get("causal_chain", []) if isinstance(refined.get("causal_chain"), list) else []
    falsifiers = refined.get("falsification_conditions", []) if isinstance(refined.get("falsification_conditions"), list) else []
    evidence_requirements = refined.get("evidence_requirements", []) if isinstance(refined.get("evidence_requirements"), list) else []
    add_check(
        "causal_chain_operationalized",
        len(causal_chain) >= 3,
        "fatal",
        "Requires at least input/intervention, mediator/mechanism, and output/readout steps.",
    )
    add_check(
        "has_falsification_condition",
        bool(falsifiers),
        "fatal",
        "Requires explicit evidence that would falsify or weaken the hypothesis.",
    )
    add_check(
        "claim_to_evidence_plan",
        bool(evidence_requirements),
        "high",
        "Requires a plan for mapping claims to PaperGraph evidence or marking assumptions.",
    )
    add_check(
        "observable_and_baseline_declared",
        any(term in text for term in ("measure", "metric", "readout", "observable", "benchmark", "primary")) and any(term in text for term in ("baseline", "control")),
        "high",
        "Requires both an observable outcome and a baseline/control comparison.",
    )
    add_check(
        "regime_shift_ready",
        any(term in text for term in ("regime", "boundary", "shift", "stress", "condition", "outside", "under")),
        "high",
        "Requires boundary conditions or regime-shift stress tests before execution.",
    )
    unsupported = yanzhen_body.get("unsupported_claims", []) if isinstance(yanzhen_body.get("unsupported_claims"), list) else []
    supplement_rounds = [
        item.get("literature_supplement")
        for item in rounds
        if isinstance(item.get("literature_supplement"), dict) and item.get("literature_supplement", {}).get("attempted")
    ]
    supplement_resolved = not unsupported or any(
        supplement.get("imports") or any(
            imp.get("status") == "no_relevance_pass"
            for claim in supplement.get("claims", []) if isinstance(claim, dict)
            for imp in claim.get("imports", []) if isinstance(imp, dict)
        )
        for supplement in supplement_rounds
    )
    add_check(
        "unsupported_claims_handled",
        supplement_resolved,
        "high",
        "Unsupported YanZhen claims must be supported, narrowed, or recorded as no-relevant-evidence after targeted search.",
    )
    yanzhen_verdict = yanzhen_body.get("verdict") or yanzhen_public_verdict(str(yanzhen_body.get("overall_verdict") or ""))
    add_check(
        "mechanism_audit_not_failed",
        yanzhen_verdict not in {"REJECTED"},
        "fatal",
        "A rejected YanZhen mechanism audit cannot advance to execution.",
    )
    # Domain adaptability gate — method must be compatible with scenario data types
    adaptability = yanzhen_body.get("domain_adaptability_audit", {}) if isinstance(yanzhen_body, dict) else {}
    adaptability_verdict = adaptability.get("verdict", "PASS") if isinstance(adaptability, dict) else "PASS"
    add_check(
        "method_scenario_compatible",
        adaptability_verdict != "FAIL",
        "fatal",
        "Method-scenario incompatibility detected: the method's data-type requirements cannot be satisfied by the scenario.",
    )

    failed_fatal = [item for item in checks if not item["passed"] and item["severity"] == "fatal"]
    failed_high = [item for item in checks if not item["passed"] and item["severity"] == "high"]
    if failed_fatal:
        verdict = "FAIL"
    elif failed_high or yanzhen_verdict in {"REQUIRES_REVISION", "REQUIRES_HUMAN_REVIEW"} or adaptability_verdict == "WARN":
        verdict = "REQUIRES_HUMAN_REVIEW"
    else:
        verdict = "PASS"
    return {
        "verdict": verdict,
        "checks": checks,
        "failed_checks": [item for item in checks if not item["passed"]],
        "execution_gate": "A hypothesis can enter Gewu only after it is operational, falsifiable, evidence-mapped, and stress-test-ready.",
    }

