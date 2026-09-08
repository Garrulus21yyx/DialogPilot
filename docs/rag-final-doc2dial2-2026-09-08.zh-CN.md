# Doc2Dial 最终方案多轮入口验收（2026-09-08）

使用现有100篇开发语料，与300题离线对照范围相同；不声称完整488文档。按数据顺序取前两个具有至少两条历史且group不同的开发问题，不按结果挑题。全部历史按user/assistant写入真实turn store，未把参考答案或手工query提供给Agent。

真实Conversation Agent→knowledge_search→PostgreSQL→Flash精排→compose/verifier→publication；采用.5/.5，每路20/候选20/最终5/2600正文预算。上限12次Flash NONE，实际5次，未重跑模型获取更好答案。Doc2Dial公共评测能力目录明确政府服务范围，停用未针对该范围校准的中文电商encoder，生产目录不变。

| 问题 | 实际行为 | 验收 |
|---|---|---|
| 模糊投诉后否定“保护执照”，请求一些信息 | 1次规划，判信息不足，不调用检索；发布中文通用补充信息提示 | 不通过：英文会话使用中文且未提出针对性澄清；不能因Completed算通过 |
| 搬到纽约并注册车辆，回答以前州发过检验贴纸“Yes, it was.” | 4次调用，Agent自主补全问题，检索后回答有效期为贴纸到期或注册后一年取早 | 该用例主要要点有据，两个gold span实际可见；不把单例正确推算成总体准确率 |

前者合理澄清无需强制检索，但应保留语言与具体上下文，原参考同样为澄清。后者证明本次输入历史→query→证据→答案链路有效；不证明所有省略query都已解决。作者评审而非独立盲评。

## 执行环境和失败保留

前两次模型前KeyError，API均0。首轮旧评测库没有随HEAD升级，第二轮仅增加安全的栈位置和missing_key记录，定位selected_failure字段未迁移。最终采用独立dialogpilot_doc2dial_final_20260908数据库，启动执行现有迁移到0037，避免旧库待处理工作影响本轮。没有在生产代码下游兼容缺失字段，也未删除旧任务。

100来源使用本地embedding导入隔离库；全文原文/来源标注保持。首两次复用Wix评测数据库基础设施但使用独立doc2dial tenant，失败产物保留。最终对照只有v3的5次实际模型调用，前两次不可算模型失败率。

## 证据和复现

scripts/run_wixqa_real_entry_pair.py --dataset doc2dial --weight 0.5 --output <新目录>；脚本当前也支持Wix，默认Wix行为保留。原输出拒绝覆盖。

scripts/audit_doc2dial_final_entry.py 验证实际planner输入含完整两条历史，原文偏移/checksum和答案引用ID均通过；不将机械验证当语义证明。

artifacts/eval/rag-final-doc2dial2-{v2-,v3-}2026-09-08（另首轮无v编号）保留输入、完整API输出、来源分路和失败位置。

主线剩余MTRAG完整语料的真实Agent查询入口与三套统一答案汇总。该已知澄清缺口计入最终失败，不另开核验修复支线；微调暂停。
