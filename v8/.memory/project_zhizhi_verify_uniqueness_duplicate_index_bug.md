---
name: zhizhi_verify_uniqueness_duplicate_index_bug
description: Observed duplicate index bug in verify_uniqueness
type: project
---

`verify_uniqueness` in ZhiZhi returns duplicate `local_matches` for the same citation (one with `venue="Nature Energy"`, one with `venue=""`), indicating papergraph likely has multi-entry duplicates on import. Suggested fix: add a citation+title secondary dedup on the import path.
