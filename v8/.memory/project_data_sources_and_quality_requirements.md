---
name: data_sources_and_quality_requirements
description: 文献与证据知识库的数据源与质量边界要求，包括使用的主要数据源、文献筛选标准等。
type: project
---

主要使用 semantic_scholar、PubMed、arXiv；临床前沿预印本可补充 medRxiv/bioRxiv，但必须明确其未经同行评审状态。分层导入至少 15 篇经质量筛选的文献；数量不是目标，必须覆盖每个已选子假设的证据窗口。对摘要缺失、unknown 字段、来源可疑或 requires_human_review 的记录，执行提取修复、降权或停留在背景层，不得作为机制合同的唯一证据。
