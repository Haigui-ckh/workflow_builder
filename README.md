# 工作流构建 Agent（基于 LangGraph）

本项目实现一个可根据用户需求自动生成工作流元数据的 Agent。Agent 流程严格遵循任务拆分、订阅校验、市场检索、节点元数据生成与节点图构建的步骤，并支持在关键环节进行暂停与恢复（人机协同）。

## 功能流程

1. 拆分子任务（优先由大模型执行）
   - 调用 LLM 路由依据节点能力进行拆分；若 LLM 无结果或异常，回退到规则拆分兜底。
   - 节点能力描述文件：`node_desc.txt`（可直接在项目根目录维护）。
   - 生成：简要描述、拆分结果以及获取用户确认的提示语。
   - 暂停流程，等待用户确认后继续。

2. 应用内订阅校验
   - 检索“应用内”三方服务订阅列表，判断是否已订阅全部需要用到的三方服务。
   - 若全部存在，进入“节点元数据生成”流程；否则进入市场检索。

3. 市场检索与引导订阅
   - 对使用到“三方服务生态”节点的任务，在市场检索服务并查询具体信息，形成列表（需要服务名称、来源和服务链接）。
   - 暂停流程，引导用户完成订阅；订阅后重新触发检索，回到第二步进行“应用内订阅列表”校验。

4. 节点元数据生成
   - 按顺序为每个子任务调用工具生成节点元数据，形成按顺序的列表。
   - 严格使用 JSON 格式的元数据（由工具路由返回）。

5. 构建节点图
   - 调用工具将节点元数据串联成图，作为最终结果返回。

## 架构设计

- Agent 层（LangGraph）：`src/agent.py` 基于 `StateGraph` 构建流程图，节点包含 `split`、`pause_confirmation`、`check_subs`、`search_market`、`pause_subscription`、`generate_metadata`、`build_graph`，并通过条件边控制推进（`src/agent.py:402-425`）。
- 工具路由（FastAPI）：`src/tool/server.py` 提供 HTTP 路由与 Pydantic 模型校验，包含订阅检索、市场检索、节点元数据生成与图构建等（例如 `src/tool/server.py:116-121`, `140-169`, `172-256`, `259-281`）。
- 能力与匹配：`src/node/node_caps.py` 汇总节点能力与资源类型，`src/node/node_matcher.py` 通过关键词与资源提示将任务匹配到具体节点类型（`src/node/node_matcher.py:36-86`）。
- 模型适配：`src/model/llm_client.py` 统一封装模型调用与 JSON 解析，支持环境变量配置与容错（`src/model/llm_client.py:31-69`）。
- 日志观测：`src/utils/logger.py` 输出结构化 JSON 日志，便于调试观测（`src/utils/logger.py:25-43`）。
- 模板与示例：提示词模板位于 `templates/`，示例脚本位于 `examples/demo_agent.py`。

## 工具路由（占位实现）

> 规则：上述流程中提到的工具均生成对应的路由接口，无需填充具体实现内容。

- `GET /tools/in_app_subscriptions`：检索应用内订阅列表。
- `POST /tools/llm/split_tasks`：调用大模型进行子任务拆分（输入：用户需求、节点能力）。
- `POST /tools/llm/refine_subtasks`：依据用户自然语言反馈改写拆分（输入：`user_requirement`、`node_capabilities`、`feedback`、`previous_tasks`；输出：`tasks`、`description`、`prompt_for_confirmation`、`plan_text`）。
- `POST /tools/llm/decide_next`：调用大模型控制流程决策（输入：候选步骤与状态摘要，输出：下一步）。
- `POST /tools/market/search`：市场检索三方服务（输入：所需服务列表与来源子任务）。
- `POST /tools/market/service_info`：查询服务具体信息（名称、来源、链接）。
- `POST /tools/node/generate_metadata`：生成节点元数据（严格 JSON）。
- `POST /tools/node/build_graph`：将节点元数据串联为图。


三方服务订阅列表示例：
```
[
  {
    "name": "W+产品统一查询",
    "resource": "API",
    "link": "https://xxxx"
  }
]
```

## 运行与使用

1) 安装依赖（需 Python 3.10+）：
   - 参考 `requirements.txt`。

2) 启动工具路由服务（项目根目录执行）：
   - `uvicorn src.tool.server:app --reload`

3) 在 Python 中驱动 Agent（示意）：
   ```python
   from src.agent import create_app
   app = create_app()

   thread_id = "demo-thread"
   state = app.invoke({
       "user_requirement": "请对上传的产品清单进行统一查询，并输出摘要报告",
       "confirmed": False,
       "node_desc_path": "templates/node/node_desc.txt",
   }, config={"configurable": {"thread_id": thread_id}})

   print(state.get("plan_text"))  # 向用户展示自然语言规划
   print(state.get("prompt_for_confirmation"))  # 展示确认提示

   # 若用户提出自然语言修改意见（例如：“把RAG检索改为调用品牌目录API，并新增英文翻译”）
   state = app.invoke({
       "user_feedback": "把RAG检索改为调用品牌目录API，并新增英文翻译",
   }, config={"configurable": {"thread_id": thread_id}})
   print(state.get("plan_text"))  # 更新后的自然语言规划
   print(state.get("prompt_for_confirmation"))

   # 用户确认后继续
   state = app.invoke({"confirmed": True}, config={"configurable": {"thread_id": thread_id}})

   # 若存在未订阅服务，Agent 会返回订阅建议并暂停
   suggestions = state.get("subscription_suggestions", [])
   # 完成订阅后再次触发（会回到应用内订阅检索）
   state = app.invoke({}, config={"configurable": {"thread_id": thread_id}})

   final_graph = state.get("graph")
   ```

## 模型接入与配置

- 环境变量（参考 `.env.sample`，复制为 `.env` 并填充）：
  - `LLM_BASE_URL`：OpenAI-compatible 的基础地址（例如 `https://api.xxx.com`）。
  - `LLM_API_KEY`：模型服务的 API Key。
  - `LLM_MODEL`：模型 ID。
  - `TOOLS_BASE_URL`：Agent 调用工具路由的地址，默认为 `http://localhost:8000`。
- 配置加载：项目使用 `python-dotenv`，启动时自动加载 `.env`。
- 未配置模型时，LLM 路由会返回空结果，Agent 自动使用规则拆分与兜底决策，流程可继续但准确性降低。

## 正式运行前检查项

- 依赖与环境：
  - 已安装 `requirements.txt` 中所有依赖。
  - `.env` 已填充并存在于项目根目录（或通过环境变量注入）。
- 路由与网络：
  - 工具路由服务正常启动并可访问（`/tools/*`）。
  - 若代理或内网访问模型服务，需确保 `LLM_BASE_URL` 在当前网络可达。
- 接口契约：
  - LLM 路由返回为 JSON 字符串，遵循本 README 中约定字段；若模型返回包含多余文本，服务端会进行 JSON 提取尝试。
  - 三方服务订阅与市场检索路由需要与真实系统对接（当前为占位实现）。
- 健壮性：
  - 对于 LLM 调用失败或解析失败，Agent 自动回退，避免阻塞流程。
  - 如需更严格的校验，可在 `server.py` 中为返回结构增加 Pydantic 校验与错误处理。

## 逻辑完善与不通顺之处优化

- 明确订阅校验的范围：
  - 仅对“RAG节点”“API节点”“AI能力节点”“MCP节点”这类三方服务生态子任务进行订阅校验；“Prompt节点”“脚本节点”“循环节点”无需订阅校验。
- 市场检索后的信息完善：
  - 检索返回后需主动调用“服务信息查询”接口补全“服务名称、来源、链接”的字段，避免用户订阅时信息不全。
- 暂停与恢复机制：
  - 步骤 1 在“确认继续”处暂停；步骤 3 在“完成订阅”处暂停。恢复时分别将 `confirmed=True` 或重新调用检查订阅的步骤（第二步）。
- 任务拆分兜底：
  - 若需求未包含明显的外部能力关键词，默认追加“Prompt节点”作为结果生成或汇总环节，保证流程可继续。
- LLM 优先与规则兜底：
  - Agent 首先调用 `POST /tools/llm/split_subtasks` 使用大模型拆分；若接口异常或返回空任务，则回退到规则拆分（keywords 规则），提升稳健性。
- 元数据格式一致性：
  - 节点元数据由工具路由统一返回 JSON；Agent 不自行拼装复杂结构，确保一致性与可替换性。

## 文件结构

- `src/agent.py`：LangGraph Agent 逻辑与工具调用封装。
- `src/tool/server.py`：工具路由接口（FastAPI，占位实现）。
- `src/tool/tools_client.py`：工具路由 HTTP 客户端封装。
- `src/model/llm_client.py`：模型接入与 JSON 解析封装。
- `src/model/prompts.py`：系统与用户提示词构建。
- `src/node/node_caps.py`：节点能力与资源类型汇总。
- `src/node/node_matcher.py`：基于关键词与资源提示的匹配器。
- `src/utils/logger.py`：结构化日志。
- `templates/`：提示词与节点能力模板。
- `examples/`：示例脚本。
- `mock_data/`：示例订阅与市场数据。
- `requirements.txt`：依赖清单。

## 已知问题与注意

- Mock 数据路径：`src/tool/server.py` 默认在其目录下查找 `mock_data/`，当前仓库的 `mock_data/` 位于项目根目录，导致路由返回空数据。开发时可将 `mock_data/` 复制到 `src/tool/` 下或调整加载逻辑。
- 路由与本地调用：`src/agent.py` 在单进程模式下直接调用工具函数（非 HTTP），也可改为通过 `TOOLS_BASE_URL` 使用 HTTP 路由（参见 `src/tool/tools_client.py`）。

## 备注

- 当前工具路由均为占位实现，返回空列表或简单结构；需要按实际三方服务与生成工具补足逻辑。
- Agent 的拆分逻辑为规则占位，后续可替换为 LLM 调用以提升准确性。
### 提示词模板

- 控制器（系统提示）：你是工作流构建平台的流程控制器，负责在给定候选步骤中选择下一步，遵循“拆分→订阅校验→市场检索→元数据→构图”。
- 拆分（系统提示）：你是任务拆分专家，需将用户需求拆分为单节点可完成的子任务，节点类型限定为 Prompt、脚本、循环、RAG、API、AI能力、MCP。
- 用户提示（由代码生成）：包含节点能力摘要与状态摘要，保持简洁。