---
name: pipeline_preferences_literature
description: Preferred parameters for literature pipeline
type: user
---

- `search_literature`: providers `['semantic_scholar']`, `max_results=5`
- `expand_literature_graph`: `direction='both'`, `max_results=30`, use `selected.result_index` from prior search
- Always call `select_literature_result` on both search_id and graph_search_id before importing
- Final summary must include: seed papers, graph expansion result count, final imported papers, quality_flags, gap count
