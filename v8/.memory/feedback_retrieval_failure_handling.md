---
name: retrieval_failure_handling
description: Policy for zero-result searches
type: feedback
---

If search_literature returns total_results=0, execution must stop immediately and report failure — no fallback to memory,常识, or synthetic papers.
