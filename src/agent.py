import os
import json
from typing import TypedDict, List, Dict, Any, Optional

from src.tool.server import (
    get_in_app_subscriptions,
    market_search,
    market_service_info,
    NodeMetadataModel,
)
from src.node.node_caps import parse_node_capabilities
from src.node.node_matcher import assign_nodes_to_tasks
from langgraph.graph import StateGraph, END
from src.utils.logger import log_event
from src.model.prompts import (
    SYSTEM_CONTROLLER_PROMPT,
    SYSTEM_SPLIT_PROMPT,
    build_split_user_prompt,
    summarize_caps,
    build_decide_user_prompt,
    summarize_state,
)


class AgentState(TypedDict, total=False):
    user_requirement: str
    tasks: List[Dict[str, Any]]
    description: str
    plan_text: Optional[str]
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


# ---------------------- 图节点函数 ----------------------
# 拆分策略 
# 1. 对于需要三方服务的，标识三方服务 ；2. 对于不需要三方服务的，直接标识节点
# 拆分字段设计
# {
#     "description": "任务描述",
#     "type": "platform", # platform / service
#     "node": "Prompt", # 具体节点类型 若第三方服务则暂时不需要
# }
# 后续三方服务: 先进行工具检索 然后 大模型匹配工具到任务 生成完整的任务拆分数据
def call_llm_split(user_requirement: str, caps: Dict[str, Any]) -> Dict[str, Any]:
    """调用大模型进行任务拆分并返回结构化结果。"""
    from src.model.llm_client import chat, build_messages, safe_json_parse, is_configured
    if not is_configured():
        return {"tasks": [], "description": None, "prompt_for_confirmation": None}
    system = SYSTEM_SPLIT_PROMPT or (
        "你是任务拆分专家。请将用户需求拆分为若干子任务，"
        "每个子任务对应单个节点能力（Prompt、脚本、循环、RAG、API、AI能力、MCP）。"
    )
    user = build_split_user_prompt(user_requirement, summarize_caps(caps))
    content = chat(build_messages(system, user), response_format_json=True)
    # 大模型 JSON parse
    data = safe_json_parse(content) or {}
    raw_tasks = data.get("tasks") or []
    tasks: List[Dict[str, Any]] = []
    # print("llm generate tasks",raw_tasks)
    for t in raw_tasks:
        try:
            tasks.append({
                "id": str(t.get("id")),
                "type": str(t.get("type")),
                "description": str(t.get("description", "")),
                "uses_service": (t.get("resource") in ("API", "MCP", "AI能力", "RAG")),
                "service": t.get("service"),
                "resource": (t.get("resource") if t.get("resource") in ("API", "MCP", "AI能力", "RAG") else None),
            })
        except Exception:
            continue
    desc = data.get("description")
    prompt_for_confirmation = data.get("prompt_for_confirmation")
    plan_text = _render_plan_text(tasks, desc) if tasks else None
    return {"tasks": tasks, "description": desc, "prompt_for_confirmation": prompt_for_confirmation, "plan_text": plan_text}

def call_llm_refine(user_requirement: str, caps: Dict[str, Any], feedback: str, previous_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据用户反馈调用大模型改写已有拆分结果。"""
    from src.model.llm_client import chat, build_messages, safe_json_parse, is_configured
    from src.model.prompts import build_refine_split_user_prompt
    if not is_configured():
        return {"tasks": [], "description": None, "prompt_for_confirmation": None}
    system = SYSTEM_SPLIT_PROMPT or (
        "你是任务拆分专家。需要根据用户的自然语言反馈对现有拆分进行改写，"
        "确保每个子任务可由单节点能力完成（Prompt、脚本、循环、RAG、API、AI能力、MCP）。"
    )
    caps_summary = summarize_caps(caps)
    user = build_refine_split_user_prompt(user_requirement, caps_summary, feedback, previous_tasks)
    content = chat(build_messages(system, user), response_format_json=True)
    data = safe_json_parse(content) or {}
    raw_tasks = data.get("tasks") or []
    tasks: List[Dict[str, Any]] = []
    for t in raw_tasks:
        try:
            tasks.append({
                "id": str(t.get("id")),
                "type": str(t.get("type")),
                "description": str(t.get("description", "")),
                # "uses_service": (t.get("resource") in ("API", "MCP", "AI能力", "RAG")),
                # "service": t.get("service"),
                # "resource": (t.get("resource") if t.get("resource") in ("API", "MCP", "AI能力", "RAG") else None),
            })
        except Exception:
            continue
    desc = data.get("description")
    prompt = data.get("prompt_for_confirmation")
    plan_text = _render_plan_text(tasks, desc) if tasks else None
    return {"tasks": tasks, "description": desc, "prompt_for_confirmation": prompt, "plan_text": plan_text}

def call_llm_decide_next(state: Dict[str, Any], allowed_steps: List[str]) -> Optional[str]:
    """调用大模型在候选步骤中选择下一步。"""
    from src.model.llm_client import chat, build_messages, safe_json_parse, is_configured
    if not is_configured():
        return None
    system = SYSTEM_CONTROLLER_PROMPT or (
        "你是流程控制器。请在候选步骤中选择一个最合理的下一步，"
        "遵循：拆分→订阅校验→市场检索→元数据→构图。"
    )
    summary = summarize_state(state)
    user = build_decide_user_prompt(summary, allowed_steps)
    content = chat(build_messages(system, user), response_format_json=True)
    data = safe_json_parse(content) or {}
    next_step = data.get("next_step")
    return next_step if next_step in allowed_steps else None


def _render_plan_text(tasks: List[Dict[str, Any]], description: Optional[str]) -> str:
    """将拆分任务与描述渲染为人类可读的规划文本。"""
    lines = []
    if description:
        lines.append(f"规划概述：{description}")
    lines.append("执行步骤：")
    for i, t in enumerate(tasks, start=1):
        base = f"{i}. {t.get('description', '')}（节点类型：{t.get('type', '')}）"
        # TODO 暂时不用附加服务
        # res = t.get("resource")
        # if t.get("uses_service") and res:
        #     svc = f"；使用服务：{res}{' - ' + t.get('service') if t.get('service') else ''}"
        #     base += svc
        lines.append(base)
    return "\n".join(lines)

def node_split(state: AgentState) -> AgentState:
    """图节点：执行拆分逻辑并写入规划状态。"""
    log_event("agent", "node_split", {"phase": "start"})
    caps = parse_node_capabilities(state.get("node_desc_path"))
    req_text = state.get("user_requirement") or ""
    # 先调用 LLM 进行拆分（支持用户反馈改写）
    feedback = state.get("user_feedback")
    previous_tasks = state.get("tasks") or []
    if feedback:
        llm_res = call_llm_refine(req_text, caps, feedback, previous_tasks)  # type: ignore[name-defined]
        log_event("agent", "split_refine", {"has_feedback": True, "prev_tasks": len(previous_tasks)})
    else:
        llm_res = call_llm_split(req_text, caps)
    llm_tasks = llm_res.get("tasks") or []

    # 规范化为“无偏节点”的子任务结构，仅保留描述/资源提示/服务线索
    raw_tasks: List[Dict[str, Any]] = []
    for t in llm_tasks:
        raw_tasks.append({
            "id": t.get("id") or f"task-{len(raw_tasks)+1}",
            "description": t.get("description") or "",
            "service": t.get("service"),
            "resource": t.get("resource"),  # 仅作为提示，不直接确定类型
        })

    # 进行节点匹配与赋型
    tasks: List[Dict[str, Any]] = assign_nodes_to_tasks(raw_tasks, caps) if raw_tasks else []

    # 描述与提示语：优先使用 LLM 返回
    desc = llm_res.get("description") or "根据用户需求拆分为可由单个节点完成的子任务（参考节点能力）"
    prompt = llm_res.get("prompt_for_confirmation") or "已完成子任务拆分。是否继续生成工作流？请确认（是/否）。"
    plan_text = llm_res.get("plan_text")

    # 评审：检查任务是否可由单节点完成、资源字段是否一致等
    review_notes = []
    allowed_types = {"Prompt节点", "脚本节点", "循环节点", "RAG节点", "API节点", "AI能力节点", "MCP节点"}
    for t in tasks:
        t_type = t.get("type")
        if t_type not in allowed_types:
            review_notes.append(f"未知节点类型：{t_type}")
        # res = t.get("resource")
        # uses_service = t.get("uses_service")
        # if uses_service and res not in ("API", "MCP", "AI能力", "RAG"):
        #     review_notes.append(f"服务型子任务资源不合法：{res}（任务 {t.get('id')}）")
        # if not uses_service and res:
        #     review_notes.append(f"非服务型子任务不应包含 resource：{res}（任务 {t.get('id')}）")
        # if uses_service and not t.get("service"):
        #     review_notes.append(f"缺少服务名称：资源 {res}（任务 {t.get('id')}）")

    if review_notes:
        desc = f"{desc}\n评审提示：\n- " + "\n- ".join(review_notes)
    # 若服务端未提供 plan_text 或走了规则兜底，使用本地渲染
    if not plan_text and tasks:
        plan_text = _render_plan_text(tasks, desc)

    state.update({
        "tasks": tasks,
        "description": desc,
        "plan_text": plan_text,
        "prompt_for_confirmation": prompt,
        "confirmed": state.get("confirmed", False),
        "status": "await_confirmation" if not state.get("confirmed") else "confirmed",
        "review_notes": review_notes,
    })
    log_event("agent", "node_split", {"phase": "end", "task_count": len(tasks), "has_review": bool(review_notes)})
    return state


def should_continue_after_split(state: AgentState) -> str:
    """拆分后决策：确定进入确认或订阅校验。"""
    log_event("agent", "decision_start", {"from": "split"})
    decision = call_llm_decide_next(state, ["pause_confirmation", "check_subs"]) or ""
    if decision in ("pause_confirmation", "check_subs"):
        log_event("agent", "decision_llm", {"next": decision})
        return decision
    return "check_subs" if state.get("confirmed") else "pause_confirmation"


def node_pause_confirmation(state: AgentState) -> AgentState:
    """图节点：暂停等待用户对规划进行确认。"""
    log_event("agent", "node_pause_confirmation", {"phase": "start"})
    state["status"] = "await_confirmation"
    log_event("agent", "node_pause_confirmation", {"phase": "end"})
    return state

# 检索已订阅工具 --> 大模型匹配工具到任务
# 工具使用优先级
#   已订阅 > 市场
#   AI能力 API节点 MCP节点
def node_check_subs(state: AgentState) -> AgentState:
    """图节点：校验应用内订阅并标记缺失服务。"""
    log_event("agent", "node_check_subs", {"phase": "start"})
    in_app = get_in_app_subscriptions()
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
    log_event("agent", "node_check_subs", {"phase": "end", "missing": len(missing)})
    return state


def should_continue_after_check(state: AgentState) -> str:
    """订阅校验后决策：选择市场检索或元数据生成。"""
    log_event("agent", "decision_start", {"from": "check_subs"})
    decision = call_llm_decide_next(state, ["search_market", "generate_metadata"]) or ""
    if decision in ("search_market", "generate_metadata"):
        log_event("agent", "decision_llm", {"next": decision})
        return decision
    return "generate_metadata" if not state.get("missing_services") else "search_market"


def node_search_market(state: AgentState) -> AgentState:
    """图节点：根据缺失服务从市场检索并给出建议。"""
    log_event("agent", "node_search_market", {"phase": "start"})
    miss = state.get("missing_services", [])
    required_names = [m.get("name") or m.get("resource") for m in miss]
    suggestions = market_search(required_names, state.get("tasks", []))

    # 查询更详细信息（名称、来源、链接）
    enrich_names = [s.get("name") for s in suggestions if s.get("name")]
    detailed = market_service_info(enrich_names)

    state["subscription_suggestions"] = detailed or suggestions
    state["status"] = "await_subscription"
    log_event("agent", "node_search_market", {"phase": "end", "suggestions": len(state["subscription_suggestions"])})
    return state


def node_pause_subscription(state: AgentState) -> AgentState:
    """图节点：暂停等待用户完成订阅后再继续。"""
    log_event("agent", "node_pause_subscription", {"phase": "start"})
    log_event("agent", "node_pause_subscription", {"phase": "end"})
    return state


def node_generate_metadata(state: AgentState) -> AgentState:
    """图节点：为每个子任务生成节点元数据。"""
    log_event("agent", "node_generate_metadata", {"phase": "start"})
    metas: List[Dict[str, Any]] = []
    for t in state.get("tasks", []):
        meta = _generate_node_metadata_local(t)
        metas.append(meta)
    state["node_metadata_list"] = metas
    state["status"] = "metadata_generated"
    log_event("agent", "node_generate_metadata", {"phase": "end", "count": len(metas)})
    return state


def node_build_graph(state: AgentState) -> AgentState:
    """图节点：根据元数据构建工作流图。"""
    log_event("agent", "node_build_graph", {"phase": "start"})
    graph = _build_graph_local(state.get("node_metadata_list", []))
    state["graph"] = graph
    state["status"] = "completed"
    log_event("agent", "node_build_graph", {"phase": "end", "edge_count": len(graph.get("edges", []))})
    return state


def _generate_node_metadata_local(subtask: Dict[str, Any]) -> Dict[str, Any]:
    """在本地调用大模型生成并校验单个节点元数据。"""
    from src.model.llm_client import chat, build_messages, safe_json_parse, is_configured
    from src.model.prompts import SYSTEM_METADATA_PROMPT, build_metadata_user_prompt, build_metadata_correction_prompt
    json_schema = {
        "type": "object",
        "required": ["id", "type", "name", "description", "config", "inputs", "outputs", "depends_on"],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "enum": ["Prompt", "脚本", "循环", "RAG", "API", "AI能力", "MCP"]},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "resource": {"type": "string", "enum": ["API", "MCP", "AI能力", "RAG"]},
            "config": {"type": "object"},
            "inputs": {"type": "array", "items": {"type": "object"}},
            "outputs": {"type": "array", "items": {"type": "object"}},
            "depends_on": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": True,
    }
    fallback_meta = {
        "id": subtask.get("id"),
        "type": subtask.get("type"),
        "name": subtask.get("service") or subtask.get("type"),
        "description": subtask.get("description"),
        "resource": subtask.get("resource"),
        "config": {},
        "inputs": [],
        "outputs": [],
        "depends_on": [],
    }
    if not is_configured():
        return fallback_meta
    system = SYSTEM_METADATA_PROMPT
    user = build_metadata_user_prompt(subtask, json_schema)
    attempts = 0
    max_attempts = 3
    validated = None
    last_error = None
    while attempts < max_attempts and validated is None:
        attempts += 1
        content = chat(build_messages(system, user), response_format_json=True)
        data = safe_json_parse(content)
        if not isinstance(data, dict):
            last_error = {"type": "parse_error", "message": "LLM 未返回有效 JSON"}
            user = build_metadata_correction_prompt(json.dumps(last_error, ensure_ascii=False), json_schema)
            continue
        try:
            validated = NodeMetadataModel(**data)
        except Exception as e:
            try:
                from pydantic import ValidationError
                last_error = e.errors() if isinstance(e, ValidationError) else {"error": str(e)}
            except Exception:
                last_error = {"error": str(e)}
            user = build_metadata_correction_prompt(json.dumps(last_error, ensure_ascii=False), json_schema)
            continue
    return validated.model_dump() if validated is not None else fallback_meta


def _build_graph_local(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """在本地根据节点列表生成边并形成有向图。"""
    edges: List[Dict[str, Any]] = []
    id_to_node = {n.get("id"): n for n in nodes if n.get("id") is not None}
    any_dep = False
    for n in nodes:
        deps = n.get("depends_on") or []
        if deps:
            any_dep = True
        for d in deps:
            if d in id_to_node:
                edges.append({"from": d, "to": n.get("id")})
    if not any_dep:
        for i in range(len(nodes) - 1):
            edges.append({"from": nodes[i].get("id"), "to": nodes[i + 1].get("id")})
    return {"nodes": nodes, "edges": edges}


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
        # 无法启用 SQLiteSaver 时，直接使用无检查点模式
        checkpointer = None
        log_event("agent", "checkpoint_disabled", {})

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
       "node_desc_path": "../templates/node/node_desc.txt",
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
