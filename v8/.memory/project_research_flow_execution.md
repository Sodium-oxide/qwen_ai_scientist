---
name: research_flow_execution
description: 将执行AutoGen研究流程，使用指定的文献来源和参数设置。
type: project
---

使用Semantic Scholar、PubMed、bioRxiv、medRxiv作为优先文献来源；允许系统加入arXiv。每个子假设独立检索和导入，避免把所有主题混成一个宽泛查询。使用max_results=72、import_top_k=30、max_round=12、run_debate=true；若已配置Qwen/DashScope API，则use_llm=true，否则use_llm=false，但无论哪种情况都必须继续执行基于工具的证据闭环。
