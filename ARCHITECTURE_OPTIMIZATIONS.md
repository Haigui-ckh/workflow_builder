# 架构优化与参考实现

本文档汇总了对项目的架构优化与落地实践，并给出与主流 Agent 研究与开源实现的对齐建议。

## 已落地优化

- 结构化日志（Observability）
  - 新增 `logger.py`，在 `server.py` 与 `agent.py` 的关键路由与节点处输出 JSON 结构化日志，包含 `category/action/data` 字段。
  - 通过环境变量 `LOG_LEVEL` 控制日志等级（默认 `INFO`）。

- 拆分评审（Critic/Validator）
  - 在 `agent.py` 的 `node_split` 阶段增加规则评审，生成 `review_notes`：
    - 节点类型是否在允许集合（Prompt/脚本/循环/RAG/API/AI能力/MCP）。
    - 服务型子任务资源是否合法，是否缺少服务名等。
  - 评审提示会追加到 `description`，帮助用户在“确认”前快速发现风险与遗漏。

- 更智能的图构建（依赖连线）
  - `server.py` 的 `build_graph` 支持按节点元数据中的 `depends_on` 构建边；若不存在依赖则回退顺序连线。
  - `generate_metadata` 返回的占位元数据包含可选 `depends_on` 字段（空数组）。

- 检查点持久化（Pause/Resume）
  - 在 `agent.py` 中优先使用 LangGraph 的 `SQLiteSaver`（若环境支持）；否则回退到内存 `MemorySaver`。
  - 通过环境变量 `CHECKPOINT_PATH` 指定 SQLite 文件路径（默认 `.checkpoints.sqlite`）。

- 决策强约束化
  - LLM 决策路由 `/tools/llm/decide_next` 在服务端强校验 `next_step` 是否属于候选列表；非法决策将置空以触发回退逻辑。

- 元数据生成（Schema 绑定与自动重试）
  - 服务端路由 `/tools/node/generate_metadata` 使用专用系统/用户提示绑定 JSON Schema，引导 LLM 严格输出结构化 JSON。
  - 返回结果通过 Pydantic 模型校验；不合规时记录错误摘要，自动构造纠错提示并重试（最多 3 次）。
  - 未配置 LLM 或持续失败时回退到占位元数据，确保流程不中断；同时输出结构化日志便于观测与排障。

## 配置补充

- 新增环境变量：
  - `LOG_LEVEL`：日志等级（`DEBUG`/`INFO`/`WARNING`/`ERROR`）。
  - `CHECKPOINT_PATH`：检查点 SQLite 文件路径（如需持久化）。
- 路由返回的数据结构：
  - `generate_metadata` 增加可选 `depends_on` 字段，`build_graph` 优先依据依赖关系构建边。

## 参考论文与开源方向（不含链接）

- ReAct（Reasoning + Acting）：将推理与工具使用结合，提升任务分解与执行的可控性。
- Plan-and-Execute：将规划与执行解耦，由 Planner 输出计划，Controller 执行并监控完成度。
- LangGraph 官方示例：基于“有向图 + 检查点”的工作流编排，支持暂停与恢复及人机协同。
- AutoGen / Semantic Kernel：以工具注册、Agent 协作与结构化消息为核心的框架，强调“工具契约与策略可插拔”。

> 提示：以上参考为公开实践的方向性总结，请结合自身场景选择性落地。