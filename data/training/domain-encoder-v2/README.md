# Domain Encoder v2：多轮训练补充

来源是人工编写的合成种子与既有 v1 数据，不是采集的真实客户会话。
中英文各新增 train 3,483 / calibration 302 / heldout 302；原 v1 行原样保留。
heldout 文件名沿用训练器接口，但此处是开发验收集，不是 fresh independent gold。

新增任务场景每语言24个训练、12个校准、12个开发，变体数量不能当作独立场景数。
场景语句与主要当前答复表达按split分开；领域内追问 FOLLOWUPS 共用，存在模板分布偏差。
history2：待回答的原请求；history4：领域内追问和补充条件；history6：撤回旧业务再提出新业务。
训练含保留、替换、撤销、同域追加、跨域追加和无指代目标。
关系只用于标注审计，不改变线上 Encoder 输出接口。

`addition` 中两个任务都被明确保留，只有相同领域才直接委派；能力咨询属于实质 general 请求，
不能和礼貌用语一样忽略。撤回无新任务、无明确指代目标标为 DEFER。
同一句“是，请继续说明”在不同历史下有不同领域标签，用于检验历史是否有用。

种子：../domain-multiturn-seeds-v2.json；生成器：evaluation/domain_multiturn_data.py。
manifest 保存原始文件、输出文件摘要及各split长度/关系分布。
旧独立120条不在本数据中；新独立集由未见训练内容的审查者另行编写，固定模型后才评测。

复现需使用新目录（不覆盖已有证据）：

```bash
.venv/bin/python -m evaluation.domain_multiturn_data --output /tmp/domain-v2-rebuild
```

初稿中 general 组合及无待办续接标注被审查否决，已修正后再生成并训练。
初稿仅保存在 /tmp/dialogpilot-domain-v2-unreviewed-20260908，不作为训练或结果证据。
