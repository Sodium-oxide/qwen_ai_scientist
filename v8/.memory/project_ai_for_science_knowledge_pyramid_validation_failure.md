---
name: ai_for_science_knowledge_pyramid_validation_failure
description: Verification failed due to missing L0 high-impact review root node; retrieved 'survey' is an unreviewed arXiv preprint, violating root_policy and collapsing pyramid structure
type: project
---

The AI for Science 分层知识金字塔综合验证 project failed validation because `search_papers_stratified` returned no peer-reviewed, high-impact L0 review (e.g., Nature/Science/Cell/PNAS or top-tier journal survey). The top result — *'From AI for Science to Agentic Science: A Survey on Autonomous Scientific Discovery'* (arXiv:2508.14111v2) — lacks venue authority, citations, and editorial oversight. This invalidated the knowledge pyramid (`knowledge_pyramid` empty at L0), forced fallback-only relation graph (`citation_graph_edges: 0`, `artificial_edges: 15`), and rendered gap detection superficial. No flagship-paper越级 rule applied — none present. Root cause: field lacks mature consolidated reviews; search strategy requires Q1 journal filters and explicit `review`/`survey` constraints.
