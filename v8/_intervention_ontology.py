"""Shared ontology guardrails for scientific interventions and mechanism roles.

The research pipeline handles two different worlds:

* epistemic operations describe how knowledge is collected or summarized;
* operational interventions change a physical, biological, chemical,
  environmental, engineering, or explicitly simulated system.

Treating the first class as the second creates grammatically complete but
scientifically meaningless causal chains (for example, ``literature review ->
cell proliferation``).  This module provides a small, deterministic gate used
by TanXi, Socrates, MingLi, and YanZhen so all stages enforce the same boundary.
"""
from __future__ import annotations

import re
from typing import Any


EPISTEMIC_METHOD_MARKERS = (
    "literature review", "systematic review", "scoping review", "narrative review",
    "meta-analysis", "evidence synthesis", "bibliometric", "survey of the literature",
    "review article", "perspective article", "consensus statement", "expert opinion",
    "knowledge synthesis", "paper review", "database search", "literature search",
)

DESCRIPTIVE_EVIDENCE_MARKERS = (
    "accumulating evidence", "evidence suggests", "evidence indicates", "has been reported",
    "review highlights", "review summarizes", "is associated with", "correlates with",
    "observational association", "descriptive analysis", "retrospective observation",
)

MEASUREMENT_RESOURCE_MARKERS = (
    "benchmark dataset", "validation dataset", "reference dataset", "literature corpus",
    "evidence base", "knowledge base", "review evidence", "publication count",
)

GENERIC_PLACEHOLDER_MARKERS = (
    "key controllable variable", "controllable variable named by", "targeted intervention",
    "proposed intervention", "appropriate intervention", "domain-appropriate intervention",
    "relevant parameter", "selected modality", "the intervention", "the input variable",
    "unresolved", "unspecified", "unknown intervention", "requires_direct_intervention_evidence",
)

DIRECT_EXPERIMENTAL_ACTION_MARKERS = (
    "knockout", "knock out", "knockdown", "knock down", "overexpress", "over-expression",
    "silence", "silencing", "crispr", "inhibit", "inhibition", "activate", "activation",
    "inhibited", "enhanced", "suppressed", "manipulated", "controlled", "block", "blocking",
    "agonist", "antagonist", "administer", "administration", "treat with", "treatment with",
    "treated with", "add ", "added ", "remove ", "deplete", "depletion", "neutralize",
    "transfect", "transduction", "mutate", "mutation", "delete", "deletion", "ablate",
    "ablation", "expose", "exposure", "irradiate", "stimulation", "stimulate", "perturb",
    "vary ", "varied ", "increase ", "decrease ", "titrate", "clamp", "apply ",
)

DIRECT_COMPUTATIONAL_ACTION_MARKERS = (
    "parameter sweep", "set the parameter", "vary the parameter", "simulation intervention",
    "in silico perturb", "feature ablation", "component ablation", "remove the module",
    "disable the module", "replace the module", "modify the algorithm", "inject noise",
    "counterfactual simulation", "boundary-condition sweep",
)

MANIPULABLE_QUANTITY_MARKERS = (
    "concentration", "dose", "temperature", "pressure", "voltage", "current density", "ph",
    "frequency", "light intensity", "electric field", "magnetic field", "mechanical stress",
    "strain", "flow rate", "oxygen level", "glucose level", "cytokine level", "expression level",
    "gene dosage", "drug level", "incubation time", "exposure time", "humidity", "loading",
)

OBSERVATIONAL_DESIGN_MARKERS = (
    "observational study", "cohort study", "cross-sectional", "case-control", "retrospective",
    "prospective cohort", "association analysis", "correlation analysis", "stratify by",
)


def normalize_scientific_role_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _marker_hits(text: str, markers: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [marker for marker in markers if marker in lowered]


def _has_concrete_object(text: str) -> bool:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+\-]{1,}|[\u4e00-\u9fff]{2,}", text)
    generic = {
        "the", "a", "an", "and", "or", "of", "in", "to", "for", "with", "using",
        "variable", "parameter", "intervention", "method", "modality", "condition", "system",
        "appropriate", "relevant", "selected", "proposed", "named", "source", "evidence",
    }
    return any(token.lower() not in generic for token in tokens)


def classify_intervention_candidate(
    value: Any,
    *,
    evidence_grade: str = "",
    evidence_type: str = "",
) -> dict[str, Any]:
    """Classify whether ``value`` may occupy an intervention slot.

    Grades C/D may remain rationale, but never authorize a core intervention.
    This intentionally makes the intervention field stricter than ordinary
    mechanism evidence because a wrong category here invalidates the whole
    causal experiment.
    """
    text = normalize_scientific_role_text(value)
    lowered = text.lower()
    grade = str(evidence_grade or "").strip().upper()
    evidence_kind = str(evidence_type or "").strip().lower()
    result: dict[str, Any] = {
        "candidate": text,
        "category": "unresolved",
        "ontology_level": "unresolved",
        "admissible_as_intervention": False,
        "allowed_roles": ["unresolved"],
        "evidence_grade": grade,
        "evidence_type": evidence_kind,
        "matched_markers": [],
        "reason": "No intervention candidate was supplied.",
    }
    if not text:
        return result

    placeholder_hits = _marker_hits(lowered, GENERIC_PLACEHOLDER_MARKERS)
    epistemic_hits = _marker_hits(lowered, EPISTEMIC_METHOD_MARKERS)
    resource_hits = _marker_hits(lowered, MEASUREMENT_RESOURCE_MARKERS)
    descriptive_hits = _marker_hits(lowered, DESCRIPTIVE_EVIDENCE_MARKERS)
    observational_hits = _marker_hits(lowered, OBSERVATIONAL_DESIGN_MARKERS)
    computational_hits = _marker_hits(lowered, DIRECT_COMPUTATIONAL_ACTION_MARKERS)
    experimental_hits = _marker_hits(lowered, DIRECT_EXPERIMENTAL_ACTION_MARKERS)
    quantity_hits = _marker_hits(lowered, MANIPULABLE_QUANTITY_MARKERS)

    if placeholder_hits:
        result.update(
            category="generic_placeholder",
            ontology_level="linguistic_placeholder",
            allowed_roles=["retrieval_requirement"],
            matched_markers=placeholder_hits,
            reason="A placeholder names no concrete manipulable object or operation.",
        )
        return result
    if epistemic_hits or evidence_kind in {"review", "systematic_review", "meta_analysis", "perspective"}:
        result.update(
            category="epistemic_method",
            ontology_level="information",
            allowed_roles=["rationale", "related_work", "evidence_source"],
            matched_markers=epistemic_hits or [evidence_kind],
            reason="A knowledge-synthesis operation can support rationale but cannot change the studied system.",
        )
        return result
    if resource_hits:
        result.update(
            category="measurement_or_evidence_resource",
            ontology_level="information",
            allowed_roles=["measurement_resource", "benchmark", "rationale"],
            matched_markers=resource_hits,
            reason="A dataset or evidence resource may support measurement, but it is not an intervention.",
        )
        return result
    if descriptive_hits or observational_hits:
        result.update(
            category="observational_or_descriptive",
            ontology_level="observation",
            allowed_roles=["rationale", "alternative_explanation", "study_design"],
            matched_markers=descriptive_hits + observational_hits,
            reason="Descriptive or observational evidence does not itself manipulate the causal system.",
        )
        return result

    direct_category = ""
    direct_markers: list[str] = []
    if computational_hits:
        direct_category = "direct_computational_intervention"
        direct_markers = computational_hits
    elif experimental_hits or quantity_hits:
        direct_category = "direct_experimental_intervention"
        direct_markers = experimental_hits + quantity_hits

    if direct_category and _has_concrete_object(text):
        result.update(
            category=direct_category,
            ontology_level="computational_system" if computational_hits else "physical_system",
            matched_markers=direct_markers,
            allowed_roles=["intervention", "experimental_condition"],
        )
        if grade in {"C", "D"}:
            result["reason"] = (
                f"The operation is potentially manipulable, but evidence grade {grade} is too weak "
                "to authorize it as the core intervention."
            )
            result["allowed_roles"] = ["rationale", "candidate_intervention"]
            return result
        result["admissible_as_intervention"] = True
        result["reason"] = "The candidate names a concrete operation or manipulable quantity."
        return result

    result.update(
        category="entity_or_method_without_operation",
        ontology_level="physical_or_conceptual_entity",
        allowed_roles=["mediator_candidate", "rationale", "measurement_target"],
        matched_markers=direct_markers,
        reason="The text may name an entity or method, but it does not specify how that object is manipulated.",
    )
    return result


def classify_mediator_candidate(value: Any) -> dict[str, Any]:
    """Reject narrative claims and epistemic artifacts from mediator slots."""
    text = normalize_scientific_role_text(value)
    intervention = classify_intervention_candidate(text)
    lowered = text.lower()
    narrative = bool(_marker_hits(lowered, DESCRIPTIVE_EVIDENCE_MARKERS)) or len(text.split()) > 24
    operational_action = bool(
        _marker_hits(lowered, DIRECT_EXPERIMENTAL_ACTION_MARKERS)
        or _marker_hits(lowered, DIRECT_COMPUTATIONAL_ACTION_MARKERS)
    )
    invalid_categories = {
        "unresolved", "generic_placeholder", "epistemic_method",
        "measurement_or_evidence_resource", "observational_or_descriptive",
    }
    admissible = (
        bool(text)
        and not narrative
        and not operational_action
        and intervention["category"] not in invalid_categories
    )
    return {
        "candidate": text,
        "category": "mechanistic_entity_or_state" if admissible else "non_mechanistic_narrative_or_artifact",
        "admissible_as_mediator": admissible,
        "reason": (
            "The candidate is a compact entity/state label that may be tested as a mediator."
            if admissible
            else "Mediator slots require a concrete entity or state, not a review method, evidence narrative, resource, or full sentence."
        ),
        "source_role_assessment": intervention,
    }


def intervention_gate_from_values(values: list[dict[str, Any] | str]) -> dict[str, Any]:
    """Return the first admissible candidate and retain a full audit trail."""
    assessments: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, dict):
            candidate = value.get("candidate") or value.get("claim") or value.get("excerpt") or value.get("value")
            assessment = classify_intervention_candidate(
                candidate,
                evidence_grade=str(value.get("evidence_grade") or ""),
                evidence_type=str(value.get("evidence_type") or value.get("source_design") or ""),
            )
            assessment["candidate_source"] = str(value.get("candidate_source") or value.get("source") or "")
        else:
            assessment = classify_intervention_candidate(value)
        assessments.append(assessment)
        if assessment.get("admissible_as_intervention"):
            return {
                "verdict": "PASS",
                "admissible": True,
                "selected_intervention": assessment["candidate"],
                "selected_assessment": assessment,
                "assessments": assessments,
                "reason": assessment["reason"],
            }
    return {
        "verdict": "FAIL",
        "admissible": False,
        "selected_intervention": "",
        "selected_assessment": {},
        "assessments": assessments,
        "reason": (
            "No evidence-backed direct physical, chemical, biological, engineering, environmental, "
            "or explicit computational intervention was found."
        ),
    }
