# 两题固定证据答案对照：未显示parent-local答案优势

4次Flash NONE、最大输出800，固定Dense/parent_local证据、原reference.input历史、真实ResponseAssembler与compose provider。重建知识Fact进入合成，不冒充完整Conversation Agent检索/核验/发布。没有重写query或使用gold作生成输入。

4/4非空草稿；来源视图与冻结输入一致，4/4引用ID合法。离线1项四请求审计通过；这些是机械指标，不代表4/4答案正确。

Codex非盲来源复核：

- 语言题两臂都直接回答可创建非英语技能，保留语言列表与Another language条件，并有来源支持。parent_local没有显示可确认的答案优势，官方qrel Recall下降也没有导致这次核心答案错误。
- web题两臂都以“默认”说明右下角launcher，没有断言用户实际故障；均追问。Dense问是否定制/特定页面浏览器，parent_local问用户在找网页入口还是配置页面。不能把追问当已经修复用户问题。
- Dense补充“隐藏入口/其他按钮”来源于之前助手历史，只是条件性假设，当前工具片段未独立确认这一具体配置。它没有说用户确实隐藏了入口，不直接标成确定事实幻觉；证据与历史建议仍须区分。
- 两臂都未明确核对经典/新版产品适用。保留为覆盖边界，不声称版本验收通过。

结论：停止用这两条已消费见证继续调prompt/配额或付费生成；官方指标保持原样，parent_local暂不采用。检索标注、语义支持、实际任务解决三种成绩分列。下一项回到此前开放的领域Agent归档证据读回边界，随后其他公共集验收；这些工作不能由DIRECT合成小样本替代。

产物`artifacts/eval/rag-g4-parent-answers2-2026-09-08/`含冻结输入、四答案与请求、来源评审及身份。脚本`PYTHONPATH=. .venv/bin/python scripts/run_mtrag_parent_answers2.py`，已有目录会拒绝重复运行。此次仅4次API，无Pro/微调。
