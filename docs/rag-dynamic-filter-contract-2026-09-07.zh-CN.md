# 知识过滤目录的运行时注入

本次替代上一版代码内置 web/store 的设计。业务人员维护一次目录数据，运行时统一生成 Schema；新增渠道不修改 Agent prompt 或 Python 枚举。当前范围为现有公共知识库，并未新增商家私有知识库或多租户目录管理平台。

## 配置与使用

参考 `config/knowledge-filter-catalog.example.json`，复制为宿主配置文件并设置：

```sh
export KNOWLEDGE_FILTER_CATALOG_FILE=/absolute/path/to/knowledge-filter-catalog.json
```

```json
{
  "catalog_id": "public-customer-service-v1",
  "sales_channels": {
    "web": "官网购买：商家官方网站下单渠道",
    "store": "实体门店购买：线下门店下单渠道",
    "partner_shop": "授权经销商购买渠道"
  }
}
```

目录支持最多32个渠道，每个ID使用小写字母开头、字母数字下划线或连字符，最多64字符；含义必须非空且最多256字符。global 是来源通用标记，不能注册成可选购买渠道。目录为宿主配置，不能通过用户聊天或工具参数提交。配置错误明确失败，不静默替换为空目录。

未设置配置路径，或显式配置空 sales_channels，规划和工具 Schema 不包含 sales_channel。仍可通过完整 query 搜索政策。未知或目录未支持的购买渠道保留在查询文字中，不强制过滤。是否填选项由本次问题和可信上下文决定，目录本身不是用户的购买事实。

## 何时注入

1. 每轮开始，知识库组件读取并规范化当前目录；API将快照及其内容指纹放入宿主 execution_context。
2. ConversationManager 在调用统一 Conversation Agent 前，将同一目录快照放入规划上下文，并随 PreparedTurn 持久化。
3. Conversation Agent 的模型调用根据快照生成 knowledge_options Schema；没有知识直达能力时不暴露 knowledge_options。
4. 领域 Agent 构造允许工具时，以 trusted_context 中的同一快照生成 knowledge_search Schema。没有获准该工具的 Agent 不获得该选项。
5. ToolManager 和知识工具 handler 按该快照校验；所选值映射为内部 applicable_channel，SQL继续检索“所选渠道或global”。运行时身份、ACL、来源版本不由模型设置。

目录按轮次冻结。配置文件更新影响新轮次；执行中或从检查点恢复的轮次使用旧快照，避免规划和执行的枚举不一致。代码部署需正常更新服务；之后目录可通过原子替换配置文件更新。目录更新不是来源撤回或权限变更，来源时效、撤回及发布复验继续由原有组件负责。

## 导入与历史来源

SourceDocument、SourceRevision 和内部检索对象仅校验渠道ID的结构；当前目录成员资格由知识库导入组件和工具输入边界验证。这样读取历史来源不会因为目录调整就改变原始事实。批量API、文件上传和直接调用 import_documents 均经过同一个导入校验；未登记渠道在向量化和数据库写入前失败。通用来源 global 不需要渠道目录。

导入API的静态 OpenAPI 描述ID结构和配置来源，不发布启动时就会过时的固定枚举。Agent工具使用每轮生成的动态枚举。数据库列与来源版本算法不变，无需为本次合同变更重建索引。

升级前已规划了 sales_channel、但没有目录快照的旧任务，会按无目录合同拒绝该参数，需要重新发起知识查询；系统不替它猜目录，也不因此重做业务写操作。

## 验证与复现

`tests/test_dynamic_knowledge_filters.py` 覆盖：

- 空目录和两份不同目录，在规划、实际模型请求、领域工具投影、ToolManager校验中一致。
- 跨目录选值、shipping、未知值在调用handler前被拒绝。
- 仅改配置新增 partner_shop，实际导入成功，真实PostgreSQL召回该渠道和global、排除web。
- malformed配置不会静默禁用过滤；动态工具禁止未按上下文隔离的工具缓存。
- 目录快照随PreparedTurn序列化；真实PostgreSQL检查点在执行前故障后重新打开，即使提供新目录，仍用原目录执行，且不重新规划。

相关评测脚本使用同一配置读取入口，并在单次实验中冻结目录、记录到manifest。运行含web/store来源的电商评测时应显式设置目录路径，不能再依赖内置渠道值。历史实验产物保持原样。

运行测试需设置隔离的 TEST_DATABASE_URL：

```sh
PYTHONPATH=. .venv/bin/pytest -q tests/test_dynamic_knowledge_filters.py
```

本轮不调用外部推理API，不把合同与数据库测试成绩作为模型语义理解率或 Recall/nDCG 提升。合法枚举只能约束可选值，模型是否选对仍需真实对话评测。

## 本轮结果

在基于 94979b4 的隔离检出中，仅应用本次补丁：17个相关测试文件，244 passed，42.65秒，无跳过；外部API调用0。覆盖动态目录、Agent规划及领域工具、导入、PostgreSQL检索、真实检查点恢复、证据合同及现有安全回归。共享工作区另有未提交的订单答复时间标注改动，曾导致3项旧文案断言失败；它们未纳入本次补丁，也未用修改旧断言的方式掩盖。

渠道ID应保持业务含义稳定；改变含义需新ID并更新来源Metadata。移除目录选项只改变新请求的过滤能力，不代表撤回来源。实现与上述边界验证完成；自然语言选择正确率和完整RAG效果仍需独立评测。
