# E14 BM25词项候选访问修复

已知触发：多列来源版本关联使scoped估计1行、实际11167，matching逐PK读词项；长词数组&&匹配使查询约2.5秒。物理表含149330历史行，禁止nested-loop可能变成全表扫描约29秒。

目标合同：同租户/generation中含任一query term的片段ID集合，与既有完整授权/适用scoped相交；整个scoped提供N/avgdl，交集提供TF/DF；打分后截断、保持ID/分数/次序。query terms去重及空查询语义不变，不迁移、不改变超时/模型/查询/切块。

owner为PostgresHybridBackend._bm25。候选方案使用按词GIN访问形成去重posting候选ID，再读取授权交集的词项数组，避免将严重低估的scope驱动成逐片段全词匹配。不删来源合同、不全局强制计划。完整Wix实际SQL配对、三代/多scope/标量公式性质测试与新查询验证；API0。不利结果保留。仅通过这些验证说明bounded实现，不宣称已删除E12库复测或线上SLA。

## 执行结果与方案收敛

初始按词GIN方案因重复扫描及额外排序退化未采用。最终改为一次query数组InitPlan的GIN命中集、完整scope交集、ordinal聚合携带稳定ID、ANY参数及语句级prepare=False。全部授权与统计合同不变。6条主查询与6条追加查询ID/分数/排序等价；长句2255→261ms，常见词5125→555ms；追加仍2条750ms超时，整体R09不关闭。公开样本已消费，不称新鲜验收。最终报告docs/rag-bm25-postings-repair-2026-09-09.zh-CN.md，失败版本保留。下一项是近全库命中时在线词频统计成本，尚未设计迁移，不开启模型或微调实验。相干实现与证据本轮commit/push，实际交付以Git为准。
