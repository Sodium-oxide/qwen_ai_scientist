from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCIENCE_DIR = ROOT / ".science"
DEFAULT_OUTPUT_DIR = ROOT / "website"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def scalar(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return default


def short_text(value: Any, limit: int = 360) -> str:
    text = " ".join(scalar(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def format_time(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return scalar(value)
    if number <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(number))


def project_score(path: Path) -> tuple[int, float]:
    try:
        data = read_json(path)
    except Exception:
        return (0, path.stat().st_mtime)
    content_score = 0
    content_score += min(len(as_list(data.get("papergraph"))), 20) * 4
    content_score += min(len(as_list(data.get("knowledge_gaps"))), 20) * 3
    content_score += 8 if data.get("knowledge_map") else 0
    content_score += 4 if data.get("coverage_matrix") else 0
    return (content_score, path.stat().st_mtime)


def latest_project(science_dir: Path) -> Path:
    files = list((science_dir / "projects").glob("sci_*.json"))
    files += list((science_dir / "projects" / "tmp").glob("sci_*.json"))
    if not files:
        raise FileNotFoundError(f"No project JSON files under {science_dir / 'projects'}")
    return max(files, key=project_score)


def latest_subspace(science_dir: Path, domain: str = "") -> Path | None:
    files = list((science_dir / "subspaces").glob("subspace_*.json"))
    if not files:
        return None
    domain_key = domain.lower().strip()

    def score(path: Path) -> tuple[int, float]:
        try:
            data = read_json(path)
        except Exception:
            return (0, path.stat().st_mtime)
        sub_domain = scalar(data.get("domain")).lower()
        match = 1 if domain_key and (domain_key in sub_domain or sub_domain in domain_key) else 0
        return (match, path.stat().st_mtime)

    return max(files, key=score)


def paper_id_from_reference(reference: str, papers: list[dict[str, Any]]) -> str:
    ref = reference.lower()
    for paper in papers:
        citation = scalar(paper.get("citation")).lower()
        title = scalar(paper.get("title")).lower()
        if citation and citation in ref:
            return scalar(paper.get("id"))
        if title and title[:80] in ref:
            return scalar(paper.get("id"))
    return ""


def export_papers(project: dict[str, Any]) -> list[dict[str, Any]]:
    papers: list[dict[str, Any]] = []
    for index, paper in enumerate(as_list(project.get("papergraph"))):
        if not isinstance(paper, dict):
            continue
        papers.append(
            {
                "id": scalar(paper.get("paper_id")) or f"paper_{index + 1}",
                "title": scalar(paper.get("title"), "Untitled paper"),
                "citation": scalar(paper.get("citation")),
                "authors": [scalar(item) for item in as_list(paper.get("authors")) if scalar(item)],
                "year": scalar(paper.get("year")),
                "venue": scalar(paper.get("venue")),
                "provider": scalar(paper.get("provider")),
                "url": scalar(paper.get("url")),
                "doi": scalar(paper.get("doi")),
                "method": scalar(paper.get("method"), "unknown"),
                "scenario": scalar(paper.get("scenario"), "unknown"),
                "benchmark": scalar(paper.get("benchmark"), "unknown"),
                "credibility": paper.get("credibility_score", ""),
                "abstract": short_text(paper.get("abstract"), 520),
                "contribution": short_text(paper.get("contribution"), 360),
                "limitation": short_text(paper.get("limitation"), 360),
                "strengths": [scalar(item) for item in as_list(paper.get("strengths")) if scalar(item)],
                "improvements": [scalar(item) for item in as_list(paper.get("improvements")) if scalar(item)],
                "quality": paper.get("extraction_quality") if isinstance(paper.get("extraction_quality"), dict) else {},
            }
        )
    return papers


def export_subspaces(subspace_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not subspace_data:
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(as_list(subspace_data.get("subspaces"))):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": scalar(item.get("subspace_id")) or f"subspace_{index + 1}",
                "name": scalar(item.get("name"), "Untitled subspace"),
                "description": short_text(item.get("description"), 420),
                "keywords": [scalar(keyword) for keyword in as_list(item.get("keywords")) if scalar(keyword)][:10],
                "importance": item.get("strategic_importance", 0),
                "maturity": scalar(item.get("maturity"), "unknown"),
                "density": scalar(item.get("estimated_density"), "unknown"),
                "hitCount": int(item.get("probe_hit_count") or 0),
                "recentHitCount": int(item.get("recent_hit_count") or 0),
                "highImpactHitCount": int(item.get("high_impact_hit_count") or 0),
                "suggestedQuota": int(item.get("suggested_quota") or 0),
                "strategy": scalar(item.get("search_strategy")),
                "selected": scalar(item.get("search_strategy")) == "must_include"
                or int(item.get("strategic_importance") or 0) >= 8,
            }
        )
    return rows


def triples_from_knowledge_map(project: dict[str, Any]) -> list[dict[str, Any]]:
    knowledge_map = project.get("knowledge_map") if isinstance(project.get("knowledge_map"), dict) else {}
    triples = knowledge_map.get("method_scenario_benchmark_triples")
    if isinstance(triples, list):
        exported = []
        for item in triples:
            if not isinstance(item, dict):
                continue
            exported.append(
                {
                    "method": scalar(item.get("method"), "unknown"),
                    "scenario": scalar(item.get("scenario"), "unknown"),
                    "benchmark": scalar(item.get("benchmark"), "unknown"),
                    "references": [scalar(ref) for ref in as_list(item.get("references")) if scalar(ref)],
                    "evidenceTypes": item.get("evidence_type_annotations", []),
                }
            )
        if exported:
            return exported

    matrix = project.get("coverage_matrix") if isinstance(project.get("coverage_matrix"), dict) else {}
    exported: list[dict[str, Any]] = []
    for method, scenarios in matrix.items():
        if isinstance(scenarios, dict):
            for scenario, references in scenarios.items():
                exported.append(
                    {
                        "method": scalar(method, "unknown"),
                        "scenario": scalar(scenario, "unknown"),
                        "benchmark": "not specified",
                        "references": [scalar(ref) for ref in as_list(references) if scalar(ref)],
                        "evidenceTypes": [],
                    }
                )
    return exported


def export_gaps(project: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for index, gap in enumerate(as_list(project.get("knowledge_gaps"))):
        if not isinstance(gap, dict):
            continue
        gaps.append(
            {
                "id": scalar(gap.get("gap_id")) or f"gap_{index + 1}",
                "rank": index + 1,
                "type": scalar(gap.get("gap_type"), "unknown"),
                "description": scalar(gap.get("description"), "No description."),
                "novelty": gap.get("novelty_score", ""),
                "application": scalar(gap.get("application_value")),
                "feasibility": scalar(gap.get("feasibility")),
                "status": scalar(gap.get("status")),
                "recommendedApproach": scalar(gap.get("suggested_research_path")),
                "valueArgument": scalar(gap.get("value_argument")),
                "supportingReferences": [scalar(ref) for ref in as_list(gap.get("supporting_references")) if scalar(ref)],
                "requiresHumanReview": bool(gap.get("requires_human_review")),
                "overlapRisk": scalar(gap.get("overlap_risk")),
                "assessmentReason": scalar(gap.get("assessment_reason")),
            }
        )
    return gaps


def build_graph(triples: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    def add_node(label: str, kind: str) -> str:
        node_id = f"{kind}:{label}".lower()
        nodes.setdefault(node_id, {"id": node_id, "label": label, "kind": kind, "degree": 0})
        return node_id

    for triple in triples:
        method = scalar(triple.get("method"), "unknown")
        scenario = scalar(triple.get("scenario"), "unknown")
        benchmark = scalar(triple.get("benchmark"), "unknown")
        method_id = add_node(method, "method")
        scenario_id = add_node(scenario, "scenario")
        benchmark_id = add_node(benchmark, "benchmark")
        ref_count = len(as_list(triple.get("references")))
        links.append({"source": method_id, "target": scenario_id, "kind": "validated_in", "weight": max(1, ref_count)})
        links.append({"source": scenario_id, "target": benchmark_id, "kind": "measured_by", "weight": max(1, ref_count)})
        nodes[method_id]["degree"] += 1
        nodes[scenario_id]["degree"] += 2
        nodes[benchmark_id]["degree"] += 1
    return {"nodes": list(nodes.values()), "links": links}


def build_export(project_path: Path, subspace_path: Path | None) -> dict[str, Any]:
    project = read_json(project_path)
    subspace_data = read_json(subspace_path) if subspace_path and subspace_path.exists() else None
    papers = export_papers(project)
    triples = triples_from_knowledge_map(project)
    gaps = export_gaps(project)
    subspaces = export_subspaces(subspace_data)
    return {
        "meta": {
            "projectId": scalar(project.get("project_id")),
            "title": scalar(project.get("title"), "Science Research Project"),
            "domain": scalar(project.get("domain")),
            "objective": scalar(project.get("objective")),
            "strategicNeed": scalar(project.get("strategic_need")),
            "phase": scalar(project.get("phase")),
            "createdAt": format_time(project.get("createdAt")),
            "updatedAt": format_time(project.get("updatedAt")),
            "sourceProject": str(project_path),
            "sourceSubspace": str(subspace_path or ""),
        },
        "stats": {
            "totalPapers": len(papers),
            "totalSubspaces": len(subspaces),
            "totalGaps": len(gaps),
            "totalTriples": len(triples),
            "avgCredibility": round(
                sum(float(p.get("credibility") or 0) for p in papers) / max(1, len(papers)),
                3,
            ),
        },
        "subspaces": subspaces,
        "papers": papers,
        "triples": triples,
        "gaps": gaps,
        "graph": build_graph(triples),
    }


def write_outputs(data: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(data, ensure_ascii=False, indent=2)
    (output_dir / "website_data.json").write_text(json_text, encoding="utf-8")
    (output_dir / "website_data.js").write_text(
        "window.SCIENCE_WEBSITE_DATA = " + json_text + ";\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export v8 science project data for the static website.")
    parser.add_argument("--project", type=Path, help="Path to a sci_*.json project file.")
    parser.add_argument("--subspace", type=Path, help="Path to a subspace_*.json file.")
    parser.add_argument("--science-dir", type=Path, default=SCIENCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_path = args.project or latest_project(args.science_dir)
    project = read_json(project_path)
    subspace_path = args.subspace or latest_subspace(args.science_dir, scalar(project.get("domain")))
    data = build_export(project_path, subspace_path)
    write_outputs(data, args.output_dir)
    print(f"exported: {args.output_dir / 'website_data.json'}")
    print(f"project: {project_path}")
    if subspace_path:
        print(f"subspace: {subspace_path}")


if __name__ == "__main__":
    main()
