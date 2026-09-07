# 本地真实 Conversation Agent 规划校准

本轮把实际 `ConversationAgent.plan`、生产 provider 提示词、上下文预算、实体绑定、能力注册表及计划校验接入本地生成模型。替换的是推理传输；未使用预制模型答案。但尚未执行生成计划后的业务工具、RAG检索或最终回答，不能称为全链路验收。

选择本机可容纳的 [Qwen2.5-3B-Instruct 官方模型](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)，固定 revision `aa8e72537993ba99e69dfaafa59ed015b17504d1`。本机 Transformers4.46.3；float16、贪心生成、最多512新token，总输入输出上限8192且不截断。下载权重是公开文件传输，**外部推理调用0**。推理进程设置offline并禁用socket连接。权重位于本机缓存，模型与tokenizer文件摘要保存在报告中。

## 实测结果

复用20条已公开用于开发的模拟电商问题；三轮并非60条独立问题。

| 提示词 | RESOLVED | CLARIFY | OUT_OF_SCOPE | INVALID_PROVIDER_OUTPUT |
|---|---:|---:|---:|---:|
| 原生产提示词 | 0 | 0 | 18 | 2 |
| 补充缺失字段schema与政策/订单区别 | 0 | 3 | 17 | 0 |
| 再加三个输出示例（仅实验） | 4 | 12 | 2 | 2 |

RESOLVED仅表示通过结构与计划校验，不是任务成功或语义正确。四条生成了knowledge_search命令，invoice/address等问题的目标类别仍有混淆；省略句“不是。”仍未通过有效计划校验。不能据此宣传查询改写或答案准确率提升，也不能外推生产大模型表现。

## 证据支持的修复与未采用项

提示词要求缺失字段使用“给定schema”，实际planner payload却没有这个字段。现在由ConversationAgent这个校验owner输出`missing_fields_schema`，与支持的缺失字段集合一致。同时明确：通用政策/FAQ不因没有订单号而超出范围；具体订单资格/状态查询才依赖对应实体。

少量示例在本地模型上有部分帮助，但整体可靠性仍低，未保留为生产默认。实验原提示词分别保存在各run的`system-prompt.txt`，评测脚本可显式覆盖以重放；覆盖不会修改生产provider。

## 产物与复现

三个目录为 `artifacts/eval/rag-local-planner-dev-2026-09-06` 及其 `-v2`、`-v3`。每例保存真实模型system/messages、原始输出、token数、耗时、上下文、deterministic resolution及最终proposal；未将JSON合法率当作语义评分。

```bash
.venv/bin/python scripts/run_local_conversation_planner.py \
  --model /path/to/local/Qwen2.5-3B-Instruct \
  --output /path/to/new-output
```

重放某轮提示词可添加 `--system-prompt /path/to/system-prompt.txt`。这是开发校准脚本；需本地CUDA和完整权重，输出目录必须不存在。

下一步将有效规划接到实际知识工具，分别验证查询、证据和答案；同时需要验证更有能力的模型能否可靠处理完整规划。当前3B结果是能力边界证据，不是完成了整个RAG目标。
# 历史实验说明

2026-09-07：本文记录旧版本实验，不代表当前可执行入口。文本式 LocalPlanningClient 与当前结构化工具协议不兼容，其脚本已移除；历史代码可从 Git 检索。当前规划评测统一使用 run_api_conversation_planner.py 和生产 SDK 入口。
