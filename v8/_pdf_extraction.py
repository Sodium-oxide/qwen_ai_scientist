"""Structure-aware PDF extraction for PaperGraph evidence ingestion."""
from __future__ import annotations

from io import BytesIO
from math import ceil
from pathlib import Path
from typing import Any
import re


SECTION_PRIORITY = {
    "methodology": 10,
    "mechanism": 9,
    "causal_chain": 9,
    "results": 8,
    "discussion": 7,
    "conclusion": 6,
    "introduction": 5,
    "background": 4,
    "acknowledgements": 1,
    "references": 1,
    "body": 5,
}

PAPER_TYPE_MULTIPLIERS = {
    "review": 1.5,
    "clinical_trial": 1.3,
    "mechanism": 1.2,
    "manufacturing": 1.5,
    "meta_analysis": 1.4,
    "short_communication": 0.8,
}

_PAPER_TYPE_PATTERNS = {
    "clinical_trial": ("clinical trial", "randomized", "randomised", "cohort", "patient", "phase i", "phase ii", "phase iii"),
    "manufacturing": ("manufacturing", "process development", "good manufacturing", "gmp", "cmc", "batch", "quality attribute"),
    "meta_analysis": ("meta-analysis", "systematic review", "prisma", "pooled analysis"),
    "review": ("review", "perspective", "overview", "state of the art"),
    "mechanism": ("mechanism", "mechanistic", "causal", "pathway", "mediated", "operando", "in situ"),
    "short_communication": ("short communication", "brief communication", "letter to the editor", "research letter"),
}

_SECTION_PATTERNS = (
    ("methodology", r"(?:materials?\s+and\s+)?methods?|methodology|experimental(?:\s+(?:section|procedures?))?|study\s+design|patients?\s+and\s+methods?|statistical\s+analysis|data\s+analysis|methods?\s+and\s+analysis"),
    ("results", r"results?(?:\s+and\s+discussion)?|findings?|observations?"),
    ("discussion", r"discussion|interpretation|implications?|results?\s+and\s+discussion"),
    ("conclusion", r"conclusions?|concluding\s+remarks?|summary"),
    ("introduction", r"introduction"),
    ("background", r"background|literature\s+review"),
    ("mechanism", r"mechanisms?|mechanistic\s+(?:insights?|analysis|study)|mode\s+of\s+action"),
    ("causal_chain", r"causal\s+(?:analysis|inference|pathway)|mediation\s+analysis"),
    ("acknowledgements", r"acknowledg(?:e)?ments?|funding|author\s+contributions?"),
    ("references", r"references|bibliography"),
)

_GENERIC_EVIDENCE_KEYWORDS = (
    "mechanism", "causal", "mediated", "pathway", "association", "intervention",
    "effect", "exposure", "response", "outcome", "toxicity", "dose", "dose-response",
    "validation", "confidence interval", "hazard ratio", "odds ratio", "limitation",
)

_SUBHYPOTHESIS_PATTERNS = {
    "sh1": ("cyp", "cytochrome", "metabolism", "pharmacokinetic", "dose", "dosage", "concentration", "exposure", "toxicity", "adverse", "safety"),
    "sh2": ("biomarker", "predictive", "response", "sensitivity", "resistance", "survival", "progression-free", "targeted", "immunotherapy", "checkpoint"),
    "sh3": ("manufacturing", "process", "batch", "quality", "cmc", "purity", "potency", "sterility", "identity", "consistency", "reproducibility", "turnaround"),
    "sh4": ("validation", "generalizability", "generalisability", "calibration", "ancestry", "ethnicity", "population", "subgroup", "platform", "assay", "center", "centre", "inter-laboratory"),
}


def classify_paper_type(metadata: dict[str, Any] | None = None, text: str = "") -> str:
    metadata = metadata or {}
    sample = " ".join(
        str(metadata.get(key) or "") for key in ("title", "abstract", "venue", "source_type")
    )
    sample = f"{sample} {str(text or '')[:6000]}".lower()
    scores = {
        paper_type: sum(sample.count(pattern) for pattern in patterns)
        for paper_type, patterns in _PAPER_TYPE_PATTERNS.items()
    }
    best_type, best_score = max(scores.items(), key=lambda item: item[1], default=("research_article", 0))
    return best_type if best_score else "research_article"


def get_extraction_params(
    paper_metadata: dict[str, Any] | None,
    pdf_info: dict[str, Any] | None,
    paper_type: str = "",
) -> tuple[int, int]:
    metadata = paper_metadata or {}
    info = pdf_info or {}
    detected_type = paper_type or classify_paper_type(metadata)
    multiplier = PAPER_TYPE_MULTIPLIERS.get(detected_type, 1.0)
    total_pages = max(1, int(info.get("num_pages") or 1))
    if total_pages <= 10:
        page_budget = total_pages
    else:
        page_budget = min(total_pages, max(4, min(12, ceil(4 * multiplier))))
    char_budget = min(20_000, max(8_000, int(8_000 * multiplier * min(1.5, page_budget / 4))))
    return page_budget, char_budget


def extract_pdf_content(
    pdf_source: bytes | bytearray | Path | str,
    paper_metadata: dict[str, Any] | None = None,
    sub_hypothesis: str | dict[str, Any] | None = None,
    max_output_chars: int = 20_000,
) -> dict[str, Any]:
    data = _load_pdf_bytes(pdf_source)
    page_texts, backend, page_errors = _extract_page_texts(data)
    if not page_texts or not any(item["text"].strip() for item in page_texts):
        raise RuntimeError("No extractable text found in PDF")
    metadata = dict(paper_metadata or {})
    source_url = str(
        metadata.get("source_url")
        or metadata.get("open_access_pdf")
        or metadata.get("url")
        or metadata.get("source_path")
        or ""
    ).strip()
    full_text = "\n\n".join(item["text"] for item in page_texts if item["text"])
    paper_type = classify_paper_type(metadata, full_text[:6000])
    page_budget, char_budget = get_extraction_params(
        metadata,
        {"num_pages": len(page_texts)},
        paper_type,
    )
    output_limit = max(2_000, min(int(max_output_chars), char_budget))
    keywords = extraction_keywords(metadata, sub_hypothesis)
    sections = detect_sections(page_texts)
    selected_sections = smart_extraction(sections, keywords, char_limit=max(1_500, int(output_limit * 0.6)))
    keyword_result = keyword_driven_extraction(full_text, sub_hypothesis, keywords, max_sentences=40)
    non_text = extract_non_text_content(
        data,
        keywords=keywords,
        page_texts=page_texts,
        source_url=source_url,
    )
    complex_content_review = assess_complex_content_review(page_texts, non_text)
    evidence_spans = build_evidence_spans(
        page_texts,
        source_url=source_url,
        keywords=keywords,
    )
    content = compose_extracted_content(
        metadata=metadata,
        paper_type=paper_type,
        selected_sections=selected_sections,
        keyword_result=keyword_result,
        non_text=non_text,
        char_limit=output_limit,
    )
    validation = validate_extraction(content, sub_hypothesis, keywords=keywords)
    selected_pages = sorted({page for section in selected_sections for page in section.get("pages", [])})
    report = {
        "status": "extracted",
        "attempted": True,
        "backend": backend,
        "page_count": len(page_texts),
        "pages_scanned": len(page_texts),
        "source_url": source_url,
        "page_layouts": [
            {"page": int(page.get("page") or 0), "layout": str(page.get("layout") or "unknown")}
            for page in page_texts
        ],
        "page_budget": page_budget,
        "selected_pages": selected_pages,
        "paper_type": paper_type,
        "excerpt_chars": len(content),
        "sections_detected": [
            {
                "heading": section["heading"],
                "type": section["type"],
                "pages": section["pages"],
                "chars": len(section["text"]),
            }
            for section in sections
        ],
        "sections_selected": [
            {
                "heading": section["heading"],
                "type": section["type"],
                "pages": section["pages"],
                "score": section["score"],
                "chars": len(section["text"]),
            }
            for section in selected_sections
        ],
        "keyword_extraction": {
            "keywords": keywords,
            "used_sentences": keyword_result["used_sentences"],
            "covered_keywords": keyword_result["covered_keywords"],
        },
        "non_text": {
            "table_count": len(non_text["tables"]),
            "caption_count": len(non_text["captions"]),
            "table_backend": non_text["table_backend"],
            "table_evidence": non_text.get("table_evidence", []),
            "caption_evidence": non_text.get("caption_evidence", []),
            "complex_content_review": complex_content_review,
        },
        "evidence_spans": evidence_spans,
        "validation": validation,
        "page_errors": page_errors[:6],
    }
    return {"text": content, "report": report}


def _load_pdf_bytes(pdf_source: bytes | bytearray | Path | str) -> bytes:
    if isinstance(pdf_source, (bytes, bytearray)):
        return bytes(pdf_source)
    return Path(pdf_source).read_bytes()


def _extract_page_texts(data: bytes) -> tuple[list[dict[str, Any]], str, list[str]]:
    pymupdf_pages, pymupdf_error = _extract_pages_with_pymupdf(data)
    if pymupdf_pages and any(item["text"].strip() for item in pymupdf_pages):
        return pymupdf_pages, "pymupdf_blocks", ([pymupdf_error] if pymupdf_error else [])
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF extraction requires pypdf. Install requirements.txt.") from exc
    reader = PdfReader(BytesIO(data))
    pages: list[dict[str, Any]] = []
    errors: list[str] = [pymupdf_error] if pymupdf_error else []
    for number, page in enumerate(reader.pages, start=1):
        try:
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except TypeError:
                text = page.extract_text() or ""
        except Exception as exc:
            text = ""
            errors.append(f"page_{number}: {exc}")
        normalized = normalize_pdf_text(text)
        pages.append(
            {
                "page": number,
                "text": normalized,
                "layout": "fallback_single_stream",
                "blocks": [
                    {
                        "block_index": 0,
                        "text": normalized,
                        "bbox": [],
                        "offset_start": 0,
                        "offset_end": len(normalized),
                    }
                ] if normalized else [],
            }
        )
    return pages, "pypdf", errors


def _extract_pages_with_pymupdf(data: bytes) -> tuple[list[dict[str, Any]], str]:
    try:
        import fitz
    except ImportError:
        return [], ""
    try:
        document = fitz.open(stream=data, filetype="pdf")
        pages: list[dict[str, Any]] = []
        for number, page in enumerate(document, start=1):
            ordered_blocks, layout = order_pymupdf_blocks(
                page.get_text("blocks"),
                float(page.rect.width),
                float(page.rect.height),
            )
            rendered_blocks: list[dict[str, Any]] = []
            parts: list[str] = []
            offset = 0
            for block_index, block in enumerate(ordered_blocks):
                text = normalize_pdf_text(str(block.get("text") or ""))
                if not text:
                    continue
                if parts:
                    offset += 1
                start = offset
                parts.append(text)
                offset += len(text)
                rendered_blocks.append(
                    {
                        "block_index": block_index,
                        "text": text,
                        "bbox": [round(float(value), 2) for value in block.get("bbox", ())[:4]],
                        "offset_start": start,
                        "offset_end": offset,
                    }
                )
            text = "\n".join(parts)
            pages.append(
                {
                    "page": number,
                    "text": text,
                    "layout": layout,
                    "blocks": rendered_blocks,
                }
            )
        document.close()
        return pages, ""
    except Exception as exc:
        return [], f"pymupdf: {exc}"


def order_pymupdf_blocks(
    raw_blocks: list[Any],
    page_width: float,
    page_height: float,
) -> tuple[list[dict[str, Any]], str]:
    blocks: list[dict[str, Any]] = []
    for raw in raw_blocks or []:
        if len(raw) < 5:
            continue
        text = str(raw[4] or "").strip()
        if not text:
            continue
        blocks.append(
            {
                "text": text,
                "bbox": tuple(float(value) for value in raw[:4]),
            }
        )
    if len(blocks) < 4 or page_width <= 0:
        return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0])), "single_column"

    width_threshold = page_width * 0.72
    full_width = [item for item in blocks if item["bbox"][2] - item["bbox"][0] >= width_threshold]
    body = [item for item in blocks if item not in full_width]
    midpoint = page_width * 0.5
    left = [item for item in body if item["bbox"][0] < midpoint and item["bbox"][2] <= page_width * 0.62]
    right = [item for item in body if item["bbox"][2] > midpoint and item["bbox"][0] >= page_width * 0.38]
    assigned = {id(item) for item in left + right}
    unassigned = [item for item in body if id(item) not in assigned]
    has_two_columns = len(left) >= 2 and len(right) >= 2
    if not has_two_columns:
        return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0])), "single_column"

    body_top = min(item["bbox"][1] for item in left + right)
    body_bottom = max(item["bbox"][3] for item in left + right)
    headers = [item for item in full_width if item["bbox"][1] <= body_top]
    footers = [item for item in full_width if item["bbox"][1] >= body_bottom]
    in_flow = [item for item in full_width if item not in headers and item not in footers]
    ordered = (
        sorted(headers, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        + sorted(in_flow, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        + sorted(left, key=lambda item: item["bbox"][1])
        + sorted(right, key=lambda item: item["bbox"][1])
        + sorted(unassigned, key=lambda item: (item["bbox"][1], item["bbox"][0]))
        + sorted(footers, key=lambda item: (item["bbox"][1], item["bbox"][0]))
    )
    return ordered, "two_column"


def normalize_pdf_text(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extraction_keywords(
    paper_metadata: dict[str, Any] | None,
    sub_hypothesis: str | dict[str, Any] | None = None,
) -> list[str]:
    metadata = paper_metadata or {}
    raw = " ".join(
        str(metadata.get(key) or "") for key in ("title", "abstract", "method", "scenario", "benchmark", "query")
    )
    if isinstance(sub_hypothesis, dict):
        raw = f"{raw} {' '.join(str(value) for value in sub_hypothesis.values())}"
        identifier = str(sub_hypothesis.get("sub_hypothesis_id") or sub_hypothesis.get("id") or "").lower()
    else:
        identifier = str(sub_hypothesis or "").lower()
        raw = f"{raw} {sub_hypothesis or ''}"
    words = re.findall(r"[A-Za-z][A-Za-z0-9+/-]{2,}|\b[A-Z]{2,}\b", raw)
    selected = list(_SUBHYPOTHESIS_PATTERNS.get(identifier, ()))
    for word in words:
        normalized = word.lower().strip("-+/")
        if len(normalized) >= 4 and normalized not in selected:
            selected.append(normalized)
        if len(selected) >= 22:
            break
    for keyword in _GENERIC_EVIDENCE_KEYWORDS:
        if keyword not in selected:
            selected.append(keyword)
    return selected[:30]


def detect_sections(page_texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current = {"heading": "Document body", "type": "body", "pages": [], "lines": []}
    for page in page_texts:
        page_number = int(page.get("page") or 0)
        lines = str(page.get("text") or "").splitlines()
        for line in lines:
            clean = line.strip()
            section_type = classify_section(clean)
            if section_type and is_heading(clean):
                if current["lines"]:
                    sections.append(finalize_section(current))
                current = {"heading": clean, "type": section_type, "pages": [page_number], "lines": []}
                continue
            if clean:
                current["lines"].append(clean)
                if page_number and page_number not in current["pages"]:
                    current["pages"].append(page_number)
    if current["lines"]:
        sections.append(finalize_section(current))
    return sections or [
        {
            "heading": "Document body",
            "type": "body",
            "pages": [item["page"] for item in page_texts],
            "text": "\n".join(item["text"] for item in page_texts),
        }
    ]


def is_heading(line: str) -> bool:
    clean = re.sub(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[IVXLC]+[.)]?)\s*", "", line.strip(), flags=re.IGNORECASE)
    if not clean or len(clean) > 120 or len(clean.split()) > 14 or clean.endswith((".", ",", ";")):
        return False
    return bool(classify_section(clean))


def classify_section(heading: str) -> str:
    cleaned = re.sub(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[IVXLC]+[.)]?)\s*", "", str(heading or "").strip().lower())
    for section_type, pattern in _SECTION_PATTERNS:
        if re.fullmatch(rf"(?:{pattern})(?:\s*[:.]?)", cleaned, flags=re.IGNORECASE):
            return section_type
    return ""


def finalize_section(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "heading": str(section["heading"]),
        "type": str(section["type"]),
        "pages": list(section["pages"]),
        "text": "\n".join(section["lines"]).strip(),
    }


def smart_extraction(
    sections: list[dict[str, Any]],
    keywords: list[str],
    char_limit: int = 12_000,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for section in sections:
        text = str(section.get("text") or "")
        if not text:
            continue
        hits = keyword_hits(text, keywords)
        score = SECTION_PRIORITY.get(str(section.get("type") or "body"), 5) + min(8, hits * 0.8)
        if len(text) > 8_000:
            score *= 0.8
        scored.append({**section, "score": round(score, 3), "keyword_hits": hits})
    scored.sort(key=lambda section: (-float(section["score"]), min(section.get("pages") or [999])))
    chosen: list[dict[str, Any]] = []
    used = 0
    for section in scored:
        remaining = char_limit - used
        if remaining < 300:
            break
        text = str(section["text"])
        if len(text) > remaining:
            text = extract_relevant_text(text, keywords, remaining)
        if len(text) < 200:
            continue
        chosen.append({**section, "text": text})
        used += len(text)
    chosen.sort(key=lambda section: min(section.get("pages") or [999]))
    return chosen


def keyword_driven_extraction(
    pdf_text: str,
    sub_hypothesis: str | dict[str, Any] | None,
    keywords: list[str] | None = None,
    max_sentences: int = 30,
) -> dict[str, Any]:
    selected_keywords = keywords or extraction_keywords({}, sub_hypothesis)
    sentences = split_sentences(pdf_text)
    if not sentences:
        return {"extracted_text": "", "used_sentences": 0, "covered_keywords": 0}
    scored: list[tuple[float, int, str]] = []
    total = max(1, len(sentences) - 1)
    for index, sentence in enumerate(sentences):
        hits = keyword_hits(sentence, selected_keywords)
        if not hits:
            continue
        middle_weight = 0.7 + 0.3 * (1 - abs(index / total - 0.5) * 2)
        length_weight = 1.15 if 45 <= len(sentence) <= 420 else 0.8
        scored.append((hits * middle_weight * length_weight, index, sentence))
    if not scored:
        return {"extracted_text": "", "used_sentences": 0, "covered_keywords": 0}
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected_indices: set[int] = set()
    for _, index, _ in scored[: max(1, max_sentences)]:
        selected_indices.add(index)
        if index > 0:
            selected_indices.add(index - 1)
        if index + 1 < len(sentences):
            selected_indices.add(index + 1)
    ordered = [sentences[index] for index in sorted(selected_indices)[: max_sentences + 12]]
    text = "\n".join(ordered)
    covered = sum(1 for keyword in selected_keywords if contains_keyword(text, keyword))
    return {"extracted_text": text, "used_sentences": len(ordered), "covered_keywords": covered}


def split_sentences(text: str) -> list[str]:
    values = re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
    return [value.strip() for value in values if len(value.strip()) >= 25]


def keyword_hits(text: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if contains_keyword(text, keyword))


def contains_keyword(text: str, keyword: str) -> bool:
    normalized = re.sub(r"[-_/]", " ", str(text or "").lower())
    target = re.sub(r"[-_/]", " ", str(keyword or "").lower()).strip()
    return bool(target and target in normalized)


def extract_relevant_text(text: str, keywords: list[str], limit: int) -> str:
    ranked = keyword_driven_extraction(text, None, keywords, max_sentences=24).get("extracted_text", "")
    if ranked:
        return truncate_text(ranked, limit)
    return truncate_text(text, limit)


def extract_non_text_content(
    data: bytes,
    keywords: list[str] | None = None,
    page_texts: list[dict[str, Any]] | None = None,
    source_url: str = "",
) -> dict[str, Any]:
    tables: list[str] = []
    table_evidence: list[dict[str, Any]] = []
    table_backend = "unavailable"
    try:
        import pdfplumber

        with pdfplumber.open(BytesIO(data)) as pdf:
            table_backend = "pdfplumber"
            for page_number, page in enumerate(pdf.pages, start=1):
                for table in page.extract_tables() or []:
                    rendered = table_to_text_description(table, page_number)
                    if rendered and (contains_numeric(rendered) or keyword_hits(rendered, keywords or []) > 0):
                        tables.append(rendered)
                        table_evidence.append(
                            {
                                "source_type": "table",
                                "page": page_number,
                                "source_url": source_url,
                                "text": rendered,
                            }
                        )
                    if len(tables) >= 5:
                        break
                if len(tables) >= 5:
                    break
    except ImportError:
        table_backend = "pdfplumber_not_installed"
    except Exception as exc:
        table_backend = f"pdfplumber_failed: {str(exc)[:160]}"
    extracted_pages = page_texts
    if extracted_pages is None:
        extracted_pages, _, _ = _extract_page_texts(data)
    captions: list[str] = []
    caption_evidence: list[dict[str, Any]] = []
    for page in extracted_pages:
        for line in str(page.get("text") or "").splitlines():
            clean = line.strip()
            if re.match(r"^(?:fig(?:ure)?|table)\s*\d+\b", clean, flags=re.IGNORECASE):
                rendered = f"Page {page.get('page')}: {clean}"
                captions.append(rendered)
                caption_evidence.append(
                    {
                        "source_type": "caption",
                        "page": int(page.get("page") or 0),
                        "source_url": source_url,
                        "text": clean,
                    }
                )
            if len(captions) >= 10:
                break
        if len(captions) >= 10:
            break
    return {
        "tables": tables[:5],
        "captions": captions[:10],
        "table_backend": table_backend,
        "table_evidence": table_evidence[:5],
        "caption_evidence": caption_evidence[:10],
    }


def table_to_text_description(table: list[list[Any]], page_number: int) -> str:
    rows = [[str(cell or "").strip() for cell in row] for row in table if row]
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""
    lines = [f"Table extracted from page {page_number}:"]
    for row in rows[:16]:
        lines.append(" | ".join(cell[:240] for cell in row))
    return "\n".join(lines)


def build_evidence_spans(
    page_texts: list[dict[str, Any]],
    source_url: str = "",
    keywords: list[str] | None = None,
    limit: int = 140,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    selected_keywords = keywords or []
    for page in page_texts:
        page_number = int(page.get("page") or 0)
        layout = str(page.get("layout") or "unknown")
        heading = "Document body"
        section_type = "body"
        blocks = page.get("blocks") if isinstance(page.get("blocks"), list) else []
        if not blocks:
            text = str(page.get("text") or "")
            blocks = [{"block_index": 0, "text": text, "bbox": [], "offset_start": 0, "offset_end": len(text)}]
        for block in blocks:
            block_text = str(block.get("text") or "")
            block_start = int(block.get("offset_start") or 0)
            line_offset = 0
            for line in block_text.splitlines() or [block_text]:
                clean = line.strip()
                line_start = block_start + line_offset + max(0, len(line) - len(line.lstrip()))
                line_offset += len(line) + 1
                if not clean:
                    continue
                detected_type = classify_section(clean)
                if detected_type and is_heading(clean):
                    heading = clean
                    section_type = detected_type
                    continue
                sentence_cursor = 0
                for sentence in split_sentences(clean):
                    local_offset = clean.find(sentence, sentence_cursor)
                    if local_offset < 0:
                        local_offset = sentence_cursor
                    sentence_cursor = local_offset + len(sentence)
                    if len(sentence) < 25:
                        continue
                    spans.append(
                        {
                            "span_id": f"p{page_number}_b{int(block.get('block_index') or 0)}_s{len(spans) + 1}",
                            "source_type": "body_text",
                            "source_url": source_url,
                            "page": page_number,
                            "section": heading,
                            "section_type": section_type,
                            "offset_start": line_start + local_offset,
                            "offset_end": line_start + local_offset + len(sentence),
                            "block_index": int(block.get("block_index") or 0),
                            "bbox": list(block.get("bbox") or []),
                            "layout": layout,
                            "text": sentence[:700],
                        }
                    )
    if len(spans) <= limit:
        return spans
    scored = sorted(
        spans,
        key=lambda item: (
            -(
                SECTION_PRIORITY.get(str(item.get("section_type") or "body"), 5)
                + min(6, keyword_hits(str(item.get("text") or ""), selected_keywords))
            ),
            int(item.get("page") or 0),
            int(item.get("offset_start") or 0),
        ),
    )[:limit]
    return sorted(scored, key=lambda item: (int(item.get("page") or 0), int(item.get("offset_start") or 0)))


def locate_evidence_span(evidence_text: str, evidence_spans: list[dict[str, Any]] | None) -> dict[str, Any]:
    candidate = normalize_evidence_text(evidence_text)
    if not candidate or not evidence_spans:
        return {}
    best: tuple[float, dict[str, Any]] | None = None
    candidate_terms = set(re.findall(r"[a-z0-9]{3,}", candidate))
    for span in evidence_spans:
        if not isinstance(span, dict):
            continue
        text = normalize_evidence_text(str(span.get("text") or ""))
        if not text:
            continue
        if candidate in text or text in candidate:
            score = min(len(candidate), len(text)) + 10_000
        else:
            span_terms = set(re.findall(r"[a-z0-9]{3,}", text))
            score = len(candidate_terms & span_terms) / max(1, len(candidate_terms | span_terms))
        if best is None or score > best[0]:
            best = (score, span)
    if best is None or best[0] < 0.2:
        return {}
    span = best[1]
    return {
        "span_id": str(span.get("span_id") or ""),
        "source_type": str(span.get("source_type") or "body_text"),
        "source_url": str(span.get("source_url") or ""),
        "page": int(span.get("page") or 0),
        "section": str(span.get("section") or ""),
        "section_type": str(span.get("section_type") or ""),
        "offset_start": int(span.get("offset_start") or 0),
        "offset_end": int(span.get("offset_end") or 0),
    }


def normalize_evidence_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").lower())
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def assess_complex_content_review(
    page_texts: list[dict[str, Any]],
    non_text: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    captions = non_text.get("captions") if isinstance(non_text.get("captions"), list) else []
    if any(re.search(r"(?:fig|figure)\s*\d+\b", str(caption), flags=re.IGNORECASE) for caption in captions):
        reasons.append("figure_caption_detected")
    formula_like_pages: list[int] = []
    for page in page_texts:
        text = str(page.get("text") or "")
        formula_count = len(re.findall(r"[=\u2264\u2265\u00b1\u2211\u2202\u222b\u03b1\u03b2\u03b3]", text))
        if formula_count >= 4:
            formula_like_pages.append(int(page.get("page") or 0))
    if formula_like_pages:
        reasons.append("formula_like_text_detected")
    return {
        "requires_human_or_visual_review": bool(reasons),
        "reasons": reasons,
        "formula_like_pages": formula_like_pages[:12],
        "policy": "captions may be used as text evidence; visual or formula semantics are not inferred automatically",
    }


def contains_numeric(text: str) -> bool:
    return bool(re.search(r"\b\d+(?:\.\d+)?(?:%|\s*(?:mg|ml|nm|µm|μm|hr|hours?|days?))?\b", str(text or ""), flags=re.IGNORECASE))


def compose_extracted_content(
    metadata: dict[str, Any],
    paper_type: str,
    selected_sections: list[dict[str, Any]],
    keyword_result: dict[str, Any],
    non_text: dict[str, Any],
    char_limit: int,
) -> str:
    parts = []
    title = str(metadata.get("title") or "").strip()
    if title:
        parts.append(f"[TITLE]\n{title}")
    parts.append(f"[PAPER_TYPE]\n{paper_type}")
    if selected_sections:
        section_text = "\n\n".join(
            f"[SECTION: {section['heading']} | pages {', '.join(map(str, section.get('pages') or []))}]\n{section['text']}"
            for section in selected_sections
        )
        parts.append(f"[PRIORITY_SECTIONS]\n{section_text}")
    keyword_text = str(keyword_result.get("extracted_text") or "").strip()
    if keyword_text:
        parts.append(f"[KEYWORD_EVIDENCE]\n{keyword_text}")
    if non_text.get("tables"):
        parts.append("[TABLES]\n" + "\n\n".join(str(table) for table in non_text["tables"]))
    if non_text.get("captions"):
        parts.append("[FIGURE_AND_TABLE_CAPTIONS]\n" + "\n".join(str(caption) for caption in non_text["captions"]))
    return truncate_text("\n\n".join(parts), char_limit)


def truncate_text(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 17)].rstrip() + "\n...[truncated]"


def validate_extraction(
    extracted_text: str,
    sub_hypothesis: str | dict[str, Any] | None = None,
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    if isinstance(sub_hypothesis, dict):
        identifier = str(sub_hypothesis.get("sub_hypothesis_id") or sub_hypothesis.get("id") or "").lower()
    else:
        identifier = str(sub_hypothesis or "").lower()
    required = _SUBHYPOTHESIS_PATTERNS.get(identifier)
    if required:
        groups = [required[index : index + 3] for index in range(0, len(required), 3)]
    else:
        selected = keywords or _GENERIC_EVIDENCE_KEYWORDS
        groups = [selected[index : index + 3] for index in range(0, min(len(selected), 12), 3)]
    coverage = []
    for group in groups:
        matched = [pattern for pattern in group if contains_keyword(extracted_text, pattern)]
        coverage.append({"patterns": list(group), "matched_patterns": matched, "matched": bool(matched)})
    score = sum(1 for item in coverage if item["matched"]) / len(coverage) if coverage else 0.0
    return {
        "coverage_score": round(score, 3),
        "coverage_detail": coverage,
        "needs_supplement": score < 0.5,
    }
