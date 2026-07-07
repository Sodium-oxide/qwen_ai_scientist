---
name: zhizhi_tool_pipeline
description: Core ZhiZhi tool pipeline
type: reference
---

ZhiZhi scientific research pipeline tools: `create_research_project` → `import_literature_text` → `build_knowledge_map` (dimension=`method-scenario-benchmark`) → `detect_knowledge_gaps` → `assess_novelty` → `verify_uniqueness`. Gaps carry `dedupe_signature`, `deduped_from`, `literature_coverage_factor`, `strongest_overlap`, `overlap_risk`, `novelty_score`, `requires_human_review`.
