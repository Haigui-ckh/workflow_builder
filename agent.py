import os
from typing import TypedDict, List, Dict, Any, Optional
from pathlib import Path

import requests
from langgraph.graph import StateGraph, END
from langgraph.checkpoint import MemorySaver
from logger import log_event
from prompts import (
    SYSTEM_CONTROLLER_PROMPT,
    SYSTEM_SPLIT_PROMPT,
    build_split_user_prompt,
    summarize_caps,
    build_decide_user_prompt,
    summarize_state,
)


TOOLS_BASE_URL = os.getenv("TOOLS_BASE_URL", "http://localhost:8000")


class AgentState(TypedDict, total=False):
    user_requirement: str
    tasks: List[Dict[str, Any]]
    description: str
    prompt_for_confirmation: str
    confirmed: bool
    required_services: List[Dict[str, Any]]
    in_app_services: List[Dict[str, Any]]
    missing_services: List[Dict[str, Any]]
    subscription_suggestions: List[Dict[str, Any]]
    node_metadata_list: List[Dict[str, Any]]
    graph: Dict[str, Any]
    status: str
    node_desc_path: Optional[str]


# ---------------------- 工具调用包装 ----------------------

def _get(url: str) -> Any:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(url: str, payload: Dict[str, Any]) -> Any:
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def call_in_app_subscriptions() -> List[Dict[str, Any]]:
    return _get(f"{TOOLS_BASE_URL}/tools/in_app_subscriptions")


def call_market_search(required_services: List[str], from_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    data = {"required_services": required_services, "from_tasks": from_tasks}
    res = _post(f"{TOOLS_BASE_URL}/tools/market/search", data)
    return res.get("items", [])


def call_service_info(service_names: List[str], resource: Optional[str] = None) -> List[Dict[str, Any]]:
    data = {"service_names": service_names}
    if resource:
        data["resource"] = resource
    res = _post(f"{TOOLS_BASE_URL}/tools/market/service_info", data)
    return res.get("items", [])


def call_generate_node_metadata(subtask: Dict[str, Any]) -> Dict[str, Any]:
    data = {"subtask": subtask}
    res = _post(f"{TOOLS_BASE_URL}/tools/node/generate_metadata", data)
    return res.get("metadata", {})


def call_build_graph(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    data = {"nodes": nodes}
    res = _post(f"{TOOLS_BASE_URL}/tools/node/build_graph", data)
    return res.get("graph", {})


# ---------------------- 拆分与节点能力 ----------------------

def parse_node_capabilities(node_desc_path: Optional[str] = None) -> Dict[str, Any]:
    path = Path(node_desc_path or (Path.cwd() / "node_desc.txt"))
    if not path.exists():
        # 基础能力映射（兜底）
        return {
            "Prompt节点": {"category": "即时可用能力"},
            "脚本节点": {"category": "即时可用能力"},
            "循环节点": {"category": "即时可用能力"},
            "RAG节点": {"category": "三方服务生态", "resource": "RAG"},
            "API节点": {"category": "三方服务生态", "resource": "API"},
            "AI能力节点": {"category": "三方服务生态", "resource": "AI能力"},
            "MCP节点": {"category": "三方服务生态", "resource": "MCP"},
        }
    content = path.read_text(encoding="utf-8")
    caps: Dict[str, Any] = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("-- ") and "节点" in line:
            name = line.replace("-- ", "").split("：")[0]
            # 粗略判断来源
            resource = None
            if "RAG" in name:
                resource = "RAG"
            elif "API" in name:
                resource = "API"
            elif "MCP" in name:
                resource = "MCP"
            elif "AI能力" in name:
                resource = "AI能力"
            category = "三方服务生态" if resource else "即时可用能力"
            caps[name] = {"category": category}
            if resource:
                caps[name]["resource"] = resource
    # 回填基础能力，避免遗漏
    caps.setdefault("Prompt节点", {"category": "即时可用能力"})
    caps.setdefault("脚本节点", {"category": "即时可用能力"})
    caps.setdefault("循环节点", {"category": "即时可用能力"})
    caps.setdefault("RAG节点", {"category": "三方服务生态", "resource": "RAG"})
    caps.setdefault("API节点", {"category": "三方服务生态", "resource": "API"})
    caps.setdefault("AI能力节点", {"category": "三方服务生态", "resource": "AI能力"})
    caps.setdefault("MCP节点", {"category": "三方服务生态", "resource": "MCP"})
    return caps


def split_subtasks(user_requirement: str, caps: Dict[str, Any]) -> List[Dict[str, Any]]:
    # 规则化拆分（兜底，当 LLM 拆分失败或无结果时使用）
    tasks: List[Dict[str, Any]] = []
    req = user_requirement.lower()
    idx = 1

    def add_task(node_type: str, desc: str, uses_service: bool = False, resource: Optional[str] = None):
        nonlocal idx
        task = {
            "id": f"task-{idx}",
            "type": node_type,
            "description": desc,
            "uses_service": uses_service,
            "resource": resource,
        }
        idx += 1
        tasks.append(task)

    if any(k in req for k in ["知识库", "检索", "rag"]):
        add_task("RAG节点", "检索相关知识库文档片段", uses_service=True, resource="RAG")

    if any(k in req for k in ["接口", "api", "调用", "对接"]):
        add_task("API节点", "调用外部 API 完成业务逻辑", uses_service=True, resource="API")

    if any(k in req for k in ["识别", "解析", "ocr", "图片", "语音"]):
        add_task("AI能力节点", "使用官方 AI 能力处理文件/图像/语音", uses_service=True, resource="AI能力")

    if any(k in req for k in ["mcp", "git", "文件系统", "解析器"]):
        add_task("MCP节点", "通过 MCP 接入外部工具生态", uses_service=True, resource="MCP")

    if any(k in req for k in ["循环", "批量", "列表", "重复"]):
        add_task("循环节点", "对输入数组执行重复任务", uses_service=False)

    # 默认加入 Prompt 生成描述环节
    add_task("Prompt节点", "汇总上下文并生成最终文本结果", uses_service=False)

    return tasks


# ---------------------- 图节点函数 ----------------------

def call_llm_split(user_requirement: str, caps: Dict[str, Any]) -> Dict[str, Any]:
    try:
        res = _post(
            f"{TOOLS_BASE_URL}/tools/llm/split_subtasks",
            {
                "user_requirement": user_requirement,
                "node_capabilities": caps,
                "system_prompt": SYSTEM_SPLIT_PROMPT,
                "user_prompt": build_split_user_prompt(user_requirement, summarize_caps(caps)),
            },
        )
        return res
    except Exception:
        return {"tasks": [], "description": "", "prompt_for_confirmation": ""}

def call_llm_decide_next(state: Dict[str, Any], allowed_steps: List[str]) -> Optional[str]:
    try:
        res = _post(
            f"{TOOLS_BASE_URL}/tools/llm/decide_next",
            {
                "allowed_steps": allowed_steps,
                "state_summary": summarize_state(state),
                "system_prompt": SYSTEM_CONTROLLER_PROMPT,
                "user_prompt": build_decide_user_prompt(summarize_state(state), allowed_steps),
            },
        )
        return res.get("next_step")
    except Exception:
        return None

def node_split(state: AgentState) -> AgentState:
    log_event("agent", "node_start", {"node": "split"})
    caps = parse_node_capabilities(state.get("node_desc_path"))
    # 先调用 LLM 进行拆分
    llm_res = call_llm_split(state["user_requirement"], caps)
    llm_tasks = llm_res.get("tasks") or []

    # 规范化返回的任务结构，并标记 uses_service
    tasks: List[Dict[str, Any]] = []
    for t in llm_tasks:
        resource = t.get("resource")
        uses_service = resource in ("API", "MCP", "AI能力", "RAG")
        tasks.append({
            "id": t.get("id") or f"task-{len(tasks)+1}",
            "type": t.get("type") or "Prompt节点",
            "description": t.get("description") or "",
            "uses_service": uses_service,
            "service": t.get("service"),
            "resource": resource,
        })

    # 若 LLM 无结果或异常，回退到规则拆分
    if not tasks:
        tasks = split_subtasks(state["user_requirement"], caps)

    # 描述与提示语：优先使用 LLM 返回
    desc = llm_res.get("description") or "根据用户需求拆分为可由单个节点完成的子任务（参考节点能力）"
    prompt = llm_res.get("prompt_for_confirmation") or "已完成子任务拆分。是否继续生成工作流？请确认（是/否）。"

    # 评审：检查任务是否可由单节点完成、资源字段是否一致等
    review_notes = []
    allowed_types = {"Prompt节点", "脚本节点", "循环节点", "RAG节点", "API节点", "AI能力节点", "MCP节点"}
    for t in tasks:
        t_type = t.get("type")
        if t_type not in allowed_types:
            review_notes.append(f"未知节点类型：{t_type}")
        res = t.get("resource")
        uses_service = t.get("uses_service")
        if uses_service and res not in ("API", "MCP", "AI能力", "RAG"):
            review_notes.append(f"服务型子任务资源不合法：{res}（任务 {t.get('id')}）")
        if not uses_service and res:
            review_notes.append(f"非服务型子任务不应包含 resource：{res}（任务 {t.get('id')}）")
        if uses_service and not t.get("service"):
            review_notes.append(f"缺少服务名称：资源 {res}（任务 {t.get('id')}）")

    if review_notes:
        desc = f"{desc}\n评审提示：\n- " + "\n- ".join(review_notes)

    state.update({
        "tasks": tasks,
        "description": desc,
        "prompt_for_confirmation": prompt,
        "confirmed": state.get("confirmed", False),
        "status": "await_confirmation" if not state.get("confirmed") else "confirmed",
        "review_notes": review_notes,
    })
    log_event("agent", "node_end", {"node": "split", "task_count": len(tasks), "has_review": bool(review_notes)})
    return state


def should_continue_after_split(state: AgentState) -> str:
    log_event("agent", "decision_start", {"from": "split"})
    decision = call_llm_decide_next(state, ["pause_confirmation", "check_subs"]) or ""
    if decision in ("pause_confirmation", "check_subs"):
        log_event("agent", "decision_llm", {"next": decision})
        return decision
    return "check_subs" if state.get("confirmed") else "pause_confirmation"


def node_pause_confirmation(state: AgentState) -> AgentState:
    log_event("agent", "node_start", {"node": "pause_confirmation"})
    state["status"] = "await_confirmation"
    log_event("agent", "node_end", {"node": "pause_confirmation"})
    return state


def node_check_subs(state: AgentState) -> AgentState:
    log_event("agent", "node_start", {"node": "check_subs"})
    in_app = call_in_app_subscriptions()
    state["in_app_services"] = in_app

    # 仅对三方服务生态的子任务进行订阅校验
    required: List[Dict[str, Any]] = []
    for t in state.get("tasks", []):
        res = t.get("resource")
        if res in ("API", "MCP", "AI能力", "RAG"):
            name = t.get("service") or t.get("type")
            required.append({"name": name, "resource": res})
    state["required_services"] = required

    names_in_app = {(s.get("name"), s.get("resource")) for s in in_app}
    missing: List[Dict[str, Any]] = []
    for need in required:
        key = (need.get("name"), need.get("resource"))
        if key not in names_in_app:
            missing.append(need)
    state["missing_services"] = missing
    state["status"] = "subscriptions_ok" if not missing else "subscriptions_missing"
    log_event("agent", "node_end", {"node": "check_subs", "missing": len(missing)})
    return state


def should_continue_after_check(state: AgentState) -> str:
    log_event("agent", "decision_start", {"from": "check_subs"})
    decision = call_llm_decide_next(state, ["search_market", "generate_metadata"]) or ""
    if decision in ("search_market", "generate_metadata"):
        log_event("agent", "decision_llm", {"next": decision})
        return decision
    return "generate_metadata" if not state.get("missing_services") else "search_market"


def node_search_market(state: AgentState) -> AgentState:
    log_event("agent", "node_start", {"node": "search_market"})
    miss = state.get("missing_services", [])
    required_names = [m.get("name") or m.get("resource") for m in miss]
    suggestions = call_market_search(required_names, state.get("tasks", []))

    # 查询更详细信息（名称、来源、链接）
    enrich_names = [s.get("name") for s in suggestions if s.get("name")]
    detailed = call_service_info(enrich_names)

    state["subscription_suggestions"] = detailed or suggestions
    state["status"] = "await_subscription"
    log_event("agent", "node_end", {"node": "search_market", "suggestions": len(state["subscription_suggestions"])})
    return state


def node_pause_subscription(state: AgentState) -> AgentState:
    # 暂停，等待用户完成订阅后重试（返回第二步）
    log_event("agent", "node_start", {"node": "pause_subscription"})
    log_event("agent", "node_end", {"node": "pause_subscription"})
    return state


def node_generate_metadata(state: AgentState) -> AgentState:
    log_event("agent", "node_start", {"node": "generate_metadata"})
    metas: List[Dict[str, Any]] = []
    for t in state.get("tasks", []):
        meta = call_generate_node_metadata(t)
        metas.append(meta)
    state["node_metadata_list"] = metas
    state["status"] = "metadata_generated"
    log_event("agent", "node_end", {"node": "generate_metadata", "count": len(metas)})
    return state


def node_build_graph(state: AgentState) -> AgentState:
    log_event("agent", "node_start", {"node": "build_graph"})
    graph = call_build_graph(state.get("node_metadata_list", []))
    state["graph"] = graph
    state["status"] = "completed"
    log_event("agent", "node_end", {"node": "build_graph", "edge_count": len(graph.get("edges", []))})
    return state


def create_app():
    # 选择检查点实现：优先 SQLiteSaver（若可用），否则内存
    graph = StateGraph(AgentState)
    graph.add_node("split", node_split)
    graph.add_node("pause_confirmation", node_pause_confirmation)
    graph.add_node("check_subs", node_check_subs)
    graph.add_node("search_market", node_search_market)
    graph.add_node("pause_subscription", node_pause_subscription)
    graph.add_node("generate_metadata", node_generate_metadata)
    graph.add_node("build_graph", node_build_graph)

    graph.set_entry_point("split")
    graph.add_conditional_edges("split", should_continue_after_split, {
        "pause_confirmation": "pause_confirmation",
        "check_subs": "check_subs",
    })
    graph.add_conditional_edges("check_subs", should_continue_after_check, {
        "search_market": "search_market",
        "generate_metadata": "generate_metadata",
    })
    graph.add_edge("search_market", "pause_subscription")
    graph.add_edge("generate_metadata", "build_graph")
    graph.add_edge("build_graph", END)

    # 尝试使用 SQLiteSaver 做持久化检查点
    checkpointer = None
    try:
        from langgraph.checkpoint.sqlite import SQLiteSaver  # type: ignore
        db_path = os.getenv("CHECKPOINT_PATH", ".checkpoints.sqlite")
        checkpointer = SQLiteSaver(db_path)
        log_event("agent", "checkpoint_sqlite", {"path": db_path})
    except Exception:
        checkpointer = MemorySaver()
        log_event("agent", "checkpoint_memory", {})

    app = graph.compile(checkpointer=checkpointer)
    return app


"""
使用说明（简要）：

1) 启动工具路由服务（开发环境）
   uvicorn server:app --reload

2) 在 Python 中驱动流程：

   from agent import create_app
   app = create_app()

   thread_id = "demo-thread"  # 任意字符串，标识一次工作流会话
   # 第一次调用：进行拆分并暂停等待确认
   state = app.invoke({
       "user_requirement": "请对上传的产品清单进行统一查询，并输出摘要报告",
       "confirmed": False,
       "node_desc_path": "./node_desc.txt",
   }, config={"configurable": {"thread_id": thread_id}})
   print(state["prompt_for_confirmation"])  # 展示确认提示

   # 用户确认后继续（设置 confirmed=True）
   state = app.invoke({"confirmed": True}, config={"configurable": {"thread_id": thread_id}})

   # 若存在未订阅服务，此时会暂停并返回订阅建议列表
   suggestions = state.get("subscription_suggestions", [])
   # 用户前往订阅后，再次触发（重回第二步进行应用内订阅校验）
   state = app.invoke({}, config={"configurable": {"thread_id": thread_id}})

   # 全部订阅满足后，将继续生成节点元数据并构建图
   final_graph = state.get("graph")

注意：以上工具路由为占位实现，返回空列表或简单结构，需按实际服务对接。
"""