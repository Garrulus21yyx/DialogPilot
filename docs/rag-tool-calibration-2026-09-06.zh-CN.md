# 固定查询的真实知识工具校准

本次用20条明确标记为模拟数据的中文电商开发问题，固定完整查询，在隔离PostgreSQL数据库中运行真实知识工具。语料包含19篇模拟政策和488篇Doc2Dial公共干扰文档，共507篇。一个省略追问改为预先写定的完整问题，并在manifest中明确记录；不将它计为Agent自动改写成功。

## 结果

| 项目 | 结果 |
|---|---:|
| 融合前五名已覆盖必要证据 | 20/20 |
| 实际ToolMessage完整证据 | 20/20 |
| 检索OK | 20/20 |
| 精排fallback／生成error | 0／0 |
| API调用 | 40：Flash精排20＋Pro生成20 |
| 输入／输出tokens | 60,318／5,503 |
| 另计cache-read输入tokens | 20,480 |
| 查询至回答延迟中位数／p95 | 3.84秒／4.53秒 |

延迟不包含建库与文档向量化。没有按账单核算金额。独立模型复核逐条核对原文切片、gold spans、引用与答案，未发现明确无依据的主张，部分答案包含额外但有来源的说明。这不是人工标注准确率。

融合前五名的完整覆盖从已捕获的可追溯chunk计算；一般是保守下界，本次达到20/20，所以足以确认这批全部覆盖。因精排之前已经覆盖全部证据，本批不能证明API精排带来了召回收益。

## 实际覆盖的路径

固定完整查询＋运行时身份 → `_knowledge_tool_handler` → `KnowledgeRetriever` → 本地BGE-M3＋PostgreSQL BM25／向量检索 → RRF → 现有API精排 → EvidencePack → `MCPToolManager._render_for_model` → 仅以模型可见证据构造现有GroundedAnswerGenerator输入 → 答案与引用。

源文件按text解析，经真实导入流程切块和向量化。使用独立的新建数据库并在结束后删除；清理异常路径有故障注入测试。调用上限80、SDK自动重试0，本次实际40次。

没有经过HTTP认证中间件、完整ToolManager调度、ConversationAgent或业务工具。运行时身份由评测程序注入，不代表认证或权限隔离已被这批验证。GroundedAnswerGenerator测的是固定知识回答组件，不代表统一Agent合并订单状态与政策后的最终回答。

## 复现

环境中提供测试实例的`TEST_DATABASE_URL`及现有模型API配置，数据库地址不会从生产.env自动读取：

```bash
.venv/bin/python scripts/run_rag_tool_calibration.py \
  --model /home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181 \
  --distractors artifacts/eval/rag-d-acceptance-2026-09-06/dataset \
  --output /tmp/rag-tool-calibration-new

python -m evaluation.rag_tool_calibration_report /tmp/rag-tool-calibration-new
```

版本化捕获保存为`artifacts/eval/rag-tool-dev-2026-09-06/cases.jsonl.gz`。脚本可直接读取压缩捕获并重算summary，无需重新调用API。

## 下一步

本批问题的答案来自较短、容易区分的中文政策，公共干扰语料主要是英文，不能代表困难中文电商召回。接下来要增加中文近似条款、长文档例外、地区／版本／撤回场景；开发时先重放检索层，冻结方案后再做配对答案验证。业务混合问题另用固定订单快照验收，允许知识和业务工具共同参与。整体RAG优化仍未完成。
