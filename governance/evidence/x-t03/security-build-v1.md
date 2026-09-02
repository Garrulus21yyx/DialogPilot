# X-T03 security build evidence v1

日期：2026-09-02。状态：`IMPLEMENTED / PRODUCTION REVIEW PENDING`。

本证据证明当前文本客服范围的安全控制已实现并通过本地确定性回归；它不等于独立渗透测试、生产部署
验证或 M2 Exit 签署。

## 有界合同

- 资产、信任边界、八类 threat、control owner、disable action 与 residual risk 由
  `governance/security/x-t03-threat-model-v1.json` 唯一声明。
- 直接用户注入在 Application ingress 阻断；Memory/RAG/Ticket/tool 等不可信数据只可作为 data，命中
  注入规则时由 Context/Tool Runtime 隔离，不能进入模型输入。
- principal、approval binding、operation key、effect receipt 与 deletion/generation refs 仍由各自 Owner
  决定；模型文本和 caller 参数不能升级权限或覆盖事实。
- 当前 Knowledge upload 只接受 UTF-8 文本格式；binary、archive、executable、image/PDF magic 与 NUL
  内容在 parse/persistence 前 typed reject。
- checkpoint 持久化前清除 credential，SQLite 主库/WAL/SHM 均为 `0600`；Trace 对敏感 key、credential、
  email/phone 做 value redaction，且不记录 raw prompt/tool output。
- 当前没有 customer media/VLM 执行路径，因此 `VLM_HIDDEN_INSTRUCTION` 明确为 `NOT_APPLICABLE`；M5
  启用前必须发布新 threat-model revision 并把该 threat 转为 ACTIVE。

## 冻结产物

| 产物 | 标识 |
|---|---|
| Threat model | `dialogpilot-x-t03-v1` |
| Threat model SHA-256 | `8186d7eabd3c0bd6ca5db240b02ee0c3115fbf853536282e9d4c9f3bf331f758` |
| Adversarial corpus | `security-x-t03-v1`，13 cases |
| Corpus SHA-256 | `be2ab32b7f8e2446805e40531da590da6748a6a3a287cb2bde17d08722a6127b` |
| Incident procedure | `docs/customer-service-security-incident-disable-runbook.zh-CN.md` |

## 可复现验证

```text
TEST_DATABASE_URL=postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot \
PYTHONPATH=. .venv/bin/pytest -q
→ 799 passed in 18.66s

PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_stateful_runner.py tests/test_security_threat_model.py \
  tests/test_context_memory.py tests/test_tool_security_trace.py \
  tests/test_knowledge_ingestion_api.py tests/test_react_resume.py
→ 86 passed in 5.34s
```

零容忍回归覆盖跨身份/tenant scope、approval/argument replay、write effect replay/unknown、直接与间接
Prompt Injection、恶意 text-upload polyglot、checkpoint/trace secret scan。旧评测中“恶意内容转义后仍进入
Prompt”的合同已迁移为“到达 ContextAssembler 后隔离”；安全高优先级 section 仍单独验证保留。

## 尚未 VERIFIED 的部署证据

- 独立 Security reviewer/penetration test 与 fresh unseen adversarial review；
- 生产数据库 role/RLS、加密卷、checkpoint 存储 ACL 与 trace exporter 配置核验；
- 真实 credential rotation、incident drill、write reconciliation 与 Bundle disable 演练；
- M5 media malware scanner、OCR/VLM hidden-instruction control（当前面不存在）。

以上项目不否定当前 build completion，但在对应生产 Gate 前必须补齐；本报告不创建或签署 M2 Gate
evidence/decision。
