# 来源内定位机会核查：先解决父级身份与定位

本轮开发32题，固定官方query、Dense/BM25各Top20；API0、新模型评分0。原始四域archive SHA与锁定adapter manifest一致。仅检查完整HTTP(S) URL，不按passage ID猜父级、不忽略URL参数。

发现源数据身份陷阱：ClapNQ全部183408条的url是同一正则占位符；FiQA全部60984条缺url；Govt另24条非有效HTTP(S) URL。首次按非空字段分组的结果无效，已保留并标INVALID_REASON；不能用它宣称父级命中。修正后仅Cloud72439条和Govt49583条具有可用于本轮精确URL比较的地址。HTTP地址通过语法检查不等于证明其历史来源真实，本轮未访问网页。

32题中15题在两路并集仍缺部分gold，共漏28条标注passage。其中只有10条有有效URL：4条的同URL片段出现在至少一路Top20，6条没有；另18条因缺有效URL不能判断。不能把18条算作父级未命中。

四条有机会的来源分别有114、17、7、9个片段，其首次不同来源排名分别最佳10、13、6、5。**没有一条位于本轮任一路前三个不同来源。** 因而从当前child排名直接抽Top3父级再搜child，不能期待救回这些已观察遗漏；这不否定独立文档级检索或query修复后的parent策略。

Web chat例尤为明确：原query同URL首次Dense19/BM2517；先前手工补背景后同URL可到第1。这说明query目标/背景与父级定位是串联条件，不能跳过前者把parent-child当万能修复。

结论：本轮不启动基于现有Top3来源的付费父级实验。下一步先建立可复验的官方document→passage映射，再选择开发范围比较独立父文档定位与child级定位；缺映射的域不凭ID前缀分组。原Doc2Dial三条失败仍按自身回归证据处理，不被本MTRAG结果覆盖。

复现：`PYTHONPATH=. .venv/bin/python scripts/audit_mtrag_parent_opportunity.py`，输出目录必须不存在。有效报告为`artifacts/eval/rag-g4-parent-opportunity32-2026-09-08/report.json`，含逐例URL、排名、片段数与SHA。该报告是诊断机会，不是检索改进成绩。
