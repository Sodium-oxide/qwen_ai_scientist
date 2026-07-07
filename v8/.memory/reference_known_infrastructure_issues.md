---
name: known_infrastructure_issues
description: Recurring backend degradations to watch for
type: reference
---

- Semantic Scholar graph expansion can fail with `'NoneType' object is not iterable` on the references edge for very new papers (e.g., 2026 pubs) that have no indexed reference/citation graph yet
- LLM extraction endpoint has returned HTTP 503 `无可用渠道 (distributor)` errors, causing `extract_paper_keynote` to fall back to `heuristic_fallback` extractor with sparse keynote fields
