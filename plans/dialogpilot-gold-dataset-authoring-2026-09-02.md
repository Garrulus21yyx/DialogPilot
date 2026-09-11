# DialogPilot 80 条合成合同生成与锁定记录

目标：依据历史 authoring prompt，使用仓库内可解析的合同、source、fixture、receipt 与合成媒体生成 80 条项目架构合同。它们已从 Gold 草稿重新定位为 `SYNTHETIC_CONTRACT_LOCKED`；来源仍是 `MODEL_AUTHORED_SYNTHETIC`，不越权声称人工 Gold。

## 进度

- [done] 1. 盘点 8 批生成配额、Schema、项目合同与权威 fixture
- [done] 2. 建立版本化数据目录、来源索引、fixture/annotation 清单
- [done] 3. 按 8 批各生成 10 条 JSONL case，并记录批次文件
- [done] 4. 实现 Schema、引用解析与确定性断言静态校验
- [done] 5. 运行校验，修复 Memory fixture 来源代数与反事实 group 一致性问题
- [done] 6. 生成 manifest、README、validation report 与审核交接摘要
- [done] 7. 将数据迁移为 `dialogpilot-synthetic-contract-v1`，修复 4 个失效测试符号造成的 8 条引用错误，冻结全文件 checksum 并校验到 `valid=true`

## 约束与正向合同

- 每条 expected fact 必须绑定可解析的权威 ref；缺失时输出 `REQUIRES_AUTHORING`。
- 模型产物不得标记为人工 `GOLD_APPROVED`；contract lock 与 authoring provenance 分开记录。
- 同一反事实 family 只改变一个关键变量，并共享 `group_id`。
- Tool 实时事实来自 fixture；静态政策来自 knowledge source；媒体事实来自 asset + annotation。
- 业务终态必须由 fixture 中的确定性迁移推出；副作用不明时使用 typed outcome 或等待/移交。
- 80 条按文档指定的 8 批配额生成，每批完成校验后才视为 done。

## 产物记录

- 本计划：`plans/dialogpilot-gold-dataset-authoring-2026-09-02.md`
- 生成器：`scripts/build_synthetic_contract_dataset.py`
- 校验器：`scripts/validate_synthetic_contract_dataset.py`
- 合并数据：`data/eval/dialogpilot-synthetic-contract-v1/cases.jsonl`
- 八个批次：`data/eval/dialogpilot-synthetic-contract-v1/<batch-id>.jsonl`
- 合成 fixture：`data/eval/dialogpilot-synthetic-contract-v1/synthetic-fixtures.json`
- 合成媒体：`data/eval/dialogpilot-synthetic-contract-v1/assets/`
- Schema/Manifest：`data/eval/dialogpilot-synthetic-contract-v1/schema.json`、`manifest.json`
- 校验报告：`data/eval/dialogpilot-synthetic-contract-v1/validation-report.json`

## 最终静态校验

- 80 cases，8 批，每批 10 条；全部为 `SYNTHETIC_CONTRACT_LOCKED`。
- Slice：PRODUCT_ID=20、INSTALLATION=20、DAMAGE=10、SCREENSHOT=10、POLICY_TOOL=10、SERVICE_CONTINUITY=4、MEMORY=2、HANDOFF=4。
- MediaNeed：L0=40、L1=20、L2=20；连续图片 case=20。
- Counterfactual pair=40；Clarify/Handoff/Await=24；HIGH/CRITICAL=13。
- 159 个唯一 ref 全部可解，asset checksum、annotation bbox、pair symmetry、自检位、batch 和 locked-file checksum 均通过，`valid=true`。
- `promotion_allowed=false`、`run_status=NOT_RUN`；合成 fixture 不等于自然业务事实或人工媒体签署，锁定也不等于真实主链已通过。
