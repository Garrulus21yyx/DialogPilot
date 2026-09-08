# WixQA 全量 PostgreSQL 导入完成

独立评测库：127.0.0.1:55432/dialogpilot_wixqa_eval_20260908，tenant=wixqa-eval。保留供后续检索与真实入口评测；未修改线上库，未导入问题标准答案。

生产PostgresKnowledgeStore完成25批导入，6221篇来源、11167片段，最终generation knowledge-generation-95a4fede15af901f0c3c20e2727070f0，状态ACTIVE。耗时590.71秒。片段原始位置与检索文本逐条匹配本地缓存，全部通过。缓存向量服务11167次，新增embedding0，外部API0。每批保留完整索引投影/激活流程，没有绕过4096批次预算；累计超限后继续成功。

脚本import_wixqa_cached_postgres.py校验原文/分片SHA、向量输入hash、shape/有限/L2；缓存仅响应精确文本，缺失直接失败，不重新调用模型。使用生产BGE模型profile，不使用hash向量。当前仍全代重建与建索引，所以每批后期耗时上升；本次不把导入耗时当在线检索延迟。

数据库名称固定且CREATE DATABASE拒绝覆盖；已存在时不要重复执行脚本。无凭据写入报告，身份信息与每批进度见artifacts/eval/wixqa-postgres-import-2026-09-08。后续应直接连接保留的评测库、使用最终generation与真实query模型，先验证PG分路结果，再运行Agent/Flash；导入完成不等于检索/答案评测完成。
