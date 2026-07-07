---
name: retrieval_honesty_policy
description: User expects no fabrication when retrieval fails
type: feedback
---

Do not invent or substitute papers when retrieval returns zero results. Report failure transparently (API errors, empty graph neighbors, LLM 503s) and only import real retrieved records. Falling back to importing the seed itself is acceptable when graph expansion fails, as long as it's disclosed.
