# 先固定参考 query，再测召回

按用户要求，由本助手读取原始问题与官方真实角色历史后编写20条参考query，先写入文件并固定SHA256，再运行检索。此前已接触这些开发案例及部分答案，因此不是盲测、独立人工金标或线上改写验收；也不是人工query质量的理论上限。本轮无外部API调用、无答案生成，评分后未改查询。

冻结SHA256：`25b2deb575c0a51810ed7c9ecc147eff542eb0bcb7545d47c0ba4b7b0a695b64`。

输入控制：同20条会话；100文档/314个512/64结构child；本地BGE-M3 exact dense＋Python BM25，每路40；.5/.5 RRF10后保留20；本地BGE-reranker-v2-m3全文精排到5。raw与参考query分别用于各自的召回、精排。没有把gold答案、证据位置、电话或正确候选ID提供给检索/精排模型。

| 指标 | raw | 助手编写固定query |
|---|---:|---:|
| 候选Top20全部gold spans完整覆盖 | 17/20，85% | 19/20，95% |
| 精排Top5全部gold spans完整覆盖 | 16/20，80% | 18/20，90% |
| MRR@5，项目既有口径 | .6958 | .6875 |

候选与精排完整证据均救2伤0。MRR下降是部分成功案例首个gold片段位置后移，并不与完整证据覆盖上升矛盾。Top5不等于最终打包、ToolMessage或答案正确率。

## 查询构造边界与样例

- “How can I request?” → “How do I request a New York vehicle inspection extension if I will be out of state when the inspection expires?” 只补申请对象和已知情境，不填办理方式。
- ED拒绝/部分批准后的追问 → 检索forbearance、stopped collections和interest的后续影响，来自前面的讨论，不自动转成“如何申诉”。
- CCI保留原缩写，不擅自扩成Closed School。
- 2019、非符合30%评级等明确条件保留；不把“可以邮寄申请”写成“有资格获批”。
- 没有历史的“限制持续多久”无法确定限制类型，仅规范拼写，标为insufficient_restriction_type。该例碰巧命中不能算正确消解了限制类型。
- 西班牙语查询保持西班牙语，没有事后改成英文以追求命中。

## 剩余问题

1. 西班牙语pagaré问题：raw与固定query都未在候选20找齐标注证据。下一步可以预先声明“原语言＋英文翻译”的双语检索对照，也应检查是否检索到同义证据而因span标注不全被判失败；当前不能仅凭此例宣称embedding跨语言能力差。
2. Social Security申请时间：候选20有完整标注证据，精排Top5没有。应固定这份候选核查精排相关性与gold粒度，不再怪候选未召回。
3. 邮寄添加女儿、利息调整等query较长，附带条件可能分散排序关注；只能作为下一项假设，不能通过删掉必要条件来刷排名。

## 之后的query rewrite怎么做

仍由统一Conversation Agent理解目标。检索query应由“当前问题要查的对象＋必要指代消解＋相关且已知的条件”构成，使用双方真实角色历史；未知项保持未知，历史客服话语作为上下文而不是新的权威政策。原始问题与解析后的完整query同时保留，便于检查语义改变。

自动改写单独验收：实体/否定/时间/目标保真，加同预算检索救回和误伤。不能因为生成了合法JSON或写出更长句子就判通过。固定参考query的结果只用于判断检索是否还有提升空间，不冒充Flash的成绩。

复现：`PYTHONPATH=. .venv/bin/python scripts/run_rag_authored_query_pair.py`（输出目录必须不存在）。查询与结果见artifacts/eval/rag-authored-query20-2026-09-07。执行前需要原有Doc2Dial数据、本地embedding缓存和模型；脚本不会调用provider。
