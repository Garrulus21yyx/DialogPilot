# 官方Markdown标题切分接入

新增生产导入可选策略 `markdown_headers`，通过现有 `RAG_CHUNK_STRATEGY=markdown_headers` 配置或 `PostgresKnowledgeStore(chunk_strategy="markdown_headers")` 使用。仅Markdown来源按标题分组，text/json继续原结构切分。现有默认保持structure_aware，旧generation不原地修改；策略改变经既有manifest指纹创建不同generation。

## 改了什么

`DocumentChunker`直接复用LangChain `MarkdownHeaderTextSplitter`，指定1至6级标题、保留标题。库版本显式锁定为当前已安装的langchain-text-splitters 1.1.2，不复制第三方标题解析实现。

官方组件会规范化空白。适配层逐序对齐所有非空白字符，验证输出没有遗漏或改写非空白内容，然后在原文上切出连续区间。原文空格、换行和字符偏移不变；对齐失败抛ChunkStructureError，不用模糊搜索猜偏移。含被组件删除的控制字符等输入可能被明确拒绝，调用方须修正来源，不伪造引用位置。

超长小节复用现有结构保护与token预算，表格、列表、代码仍作为原子结构；超预算明确失败。overlap仅在小节内部发生。标题路径经既有build_child_retrieval_text进入embedding/词法文本，并持久化section_path和source_span。未增加Agent或改写模型。

## 固定切块预算的开发原文审计

40个开发问题，6篇原始模拟文档；max_tokens512、overlap64，模型/API调用0。

| 指标 | structure_aware | markdown_headers |
|---|---:|---:|
| 六篇文档总片段 | 17 | 186 |
| 三份现行手册片段 | 8 | 93 |
| 现行片段中跨多个商品标题 | 8 | 0 |
| 单片段最多商品标题 | 20 | 1 |
| 必要证据完整包含 | 120/120 | 120/120 |

审计所有来源字符覆盖及精确offset。这里仅证明跨商品混块消除和原文证据保留，不是Recall、重排或答案准确率提升；没有重新导入6287篇全库或使用80题调参。

## 验证与边界

测试覆盖重复标题、嵌套标题、CRLF/空格、代码内伪标题、表格保护、超长小节、预算、typed对齐失败、非Markdown字面文本。数据库集成验证策略变化生成新generation，以及标题确实进入embedding输入和实际投影。77项切分/合同测试与23项真实PostgreSQL导入/API测试通过，共100项。

标题切分可能使来源级总则成为单独片段。业务适用metadata仍从来源继承，但未出现在metadata中的总则不能假定自动继承到每个答案；后续真实检索验收需检查这一点。片段数量增加也会改变候选竞争，不能在只看包含率后自动切生产默认。

下一步用开发集新索引执行同查询/模型/候选20/最终5/2600的比较，统计救回误伤和可见证据；旧缓存仅在实际输入相同处复用。通过后再考虑采用，不能把这次源码接入当成15个排序失败已修好。

原文审计复现：

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_markdown_section_chunks.py
```

结果在 `artifacts/eval/markdown-sections-dev-2026-09-09/report.json`。本轮未修改正在运行的服务配置、未部署，API0。
