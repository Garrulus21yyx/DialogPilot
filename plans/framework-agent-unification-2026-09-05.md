# Target framework Agent unification

Status: in_progress

Target delegated objectives use one create_agent runtime. Conversation planning, direct
tools and governed workflows retain their respective owners. LangGraph owns working
messages and step recovery; ToolManager retains execution evidence and permissions.

1. implemented: shared model/tool control middleware and actual call limits.
2. implemented: all six delegated domains use create_agent; deleted TargetAgentExecutor.
3. verified: PostgreSQL child checkpoint recovery after real process exit; completed tool
   called once, typed evidence survives recovery. ToolMessage requires a versioned artifact
   envelope because its serialization turns nested dataclasses into dictionaries.
4. pending: remove old API/orchestrator/ReAct consumers and SQLite RunStore together.
5. pending: migrate SQLite customer_operations, badcase_registry and evolution registry
   to PostgreSQL, update their consumers/tests and remove SQLite configuration.

Target path has no old-engine fallback. Repository-wide SQLite deletion is not complete.
Existing unrelated RAG working-tree changes are excluded from this migration's commits.

Acceptance: correction during model execution cannot start stale tools; completed tool
steps survive recovery; concurrent users remain isolated; actual model/tool call counts
are bounded; each model request fits its context budget without changing stored evidence.
