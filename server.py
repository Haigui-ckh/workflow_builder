from enum import Enum
from typing import List, Optional, Dict, Any
import os

from fastapi import FastAPI
from pydantic import BaseModel, Field, ValidationError
from llm_client import chat, build_messages, safe_json_parse, is_configured
import json
from logger import log_event
from prompts import (
    SYSTEM_METADATA_PROMPT,
    build_metadata_user_prompt,
    build_metadata_correction_prompt,
)


class ResourceEnum(str, Enum):
    API = "API"
    MCP = "MCP"
    AI_ABILITY = "AI能力"
    RAG = "RAG"


class SubscriptionItem(BaseModel):
    name: str
    resource: ResourceEnum
    link: Optional[str] = None


class MarketSearchRequest(BaseModel):
    required_services: List[str]
    from_tasks: Optional[List[Dict[str, Any]]] = None


class MarketSearchResponse(BaseModel):
    items: List[SubscriptionItem]


class ServiceInfoRequest(BaseModel):
    service_names: List[str]
    resource: Optional[ResourceEnum] = None


class ServiceInfoResponse(BaseModel):
    items: List[SubscriptionItem]


class Subtask(BaseModel):
    id: str
    type: str
    description: str
    uses_service: bool = False
    service: Optional[str] = None
    resource: Optional[ResourceEnum] = None


class NodeMetadataRequest(BaseModel):
    subtask: Subtask


class NodeMetadataResponse(BaseModel):
    metadata: Dict[str, Any]


class NodeMetadataModel(BaseModel):
    id: str
    type: str
    name: str
    description: str
    resource: Optional[ResourceEnum] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    inputs: List[Dict[str, Any]] = Field(default_factory=list)
    outputs: List[Dict[str, Any]] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)


class BuildGraphRequest(BaseModel):
    nodes: List[Dict[str, Any]]


class BuildGraphResponse(BaseModel):
    graph: Dict[str, Any]


app = FastAPI(title="Workflow Builder Tools", version="0.1.0")


# -------------------- 静态文件 Mock 支持 --------------------

def _mock_path(*segments: str) -> str:
    base_dir = os.path.dirname(__file__)
    return os.path.join(base_dir, "mock_data", *segments)


def _load_json_file(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_event("server", "mock_load_error", {"path": path, "error": str(e)})
        return default


@app.get("/tools/in_app_subscriptions", response_model=List[SubscriptionItem])
def list_in_app_subscriptions() -> List[SubscriptionItem]:
    # 从静态文件返回应用内已订阅的三方服务列表
    log_event("server", "route_start", {"path": "/tools/in_app_subscriptions"})
    raw = _load_json_file(_mock_path("in_app_subscriptions.json"), [])
    items: List[SubscriptionItem] = []
    for it in raw:
        try:
            items.append(SubscriptionItem(**it))
        except Exception:
            continue
    log_event("server", "route_end", {"path": "/tools/in_app_subscriptions", "count": len(items)})
    return items


@app.post("/tools/market/search", response_model=MarketSearchResponse)
def search_market_services(req: MarketSearchRequest) -> MarketSearchResponse:
    # 基于静态文件的市场目录进行检索
    log_event("server", "route_start", {"path": "/tools/market/search", "required_services": req.required_services})
    catalog = _load_json_file(_mock_path("market_catalog.json"), [])
    required_set = set(req.required_services or [])
    matched: List[SubscriptionItem] = []
    for it in catalog:
        name = it.get("name")
        resource = it.get("resource")
        # 命中条件：服务名在 required_services，或 required 包含资源枚举字符串
        if (name and name in required_set) or (resource and resource in required_set):
            try:
                matched.append(SubscriptionItem(**it))
            except Exception:
                continue
    resp = MarketSearchResponse(items=matched)
    log_event("server", "route_end", {"path": "/tools/market/search", "count": len(resp.items)})
    return resp


@app.post("/tools/market/service_info", response_model=ServiceInfoResponse)
def get_service_info(req: ServiceInfoRequest) -> ServiceInfoResponse:
    # 从静态市场目录查询服务的具体信息（名称、来源、服务链接）
    log_event("server", "route_start", {"path": "/tools/market/service_info", "service_names": req.service_names})
    catalog = _load_json_file(_mock_path("market_catalog.json"), [])
    name_set = set(req.service_names or [])
    found: List[SubscriptionItem] = []
    for it in catalog:
        if it.get("name") in name_set:
            try:
                found.append(SubscriptionItem(**it))
            except Exception:
                continue
    resp = ServiceInfoResponse(items=found)
    log_event("server", "route_end", {"path": "/tools/market/service_info", "count": len(resp.items)})
    return resp


@app.post("/tools/node/generate_metadata", response_model=NodeMetadataResponse)
def generate_node_metadata(req: NodeMetadataRequest) -> NodeMetadataResponse:
    # LLM驱动节点元数据生成：绑定 JSON Schema/Pydantic，违规自动重试并纠错。
    log_event("server", "route_start", {"path": "/tools/node/generate_metadata", "subtask_id": req.subtask.id})

    # 定义用于提示与校验的 JSON Schema（resource 为可选）
    json_schema = {
        "type": "object",
        "required": ["id", "type", "name", "description", "config", "inputs", "outputs", "depends_on"],
        "properties": {
            "id": {"type": "string"},
            "type": {
                "type": "string",
                "enum": ["Prompt", "脚本", "循环", "RAG", "API", "AI能力", "MCP"],
            },
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

    # 兜底占位（当模型未配置或多次校验失败时使用）
    fallback_meta = {
        "id": req.subtask.id,
        "type": req.subtask.type,
        "name": req.subtask.service or req.subtask.type,
        "description": req.subtask.description,
        "resource": req.subtask.resource,
        "config": {},
        "inputs": [],
        "outputs": [],
        "depends_on": [],
    }

    if not is_configured():
        log_event("server", "llm_unconfigured", {"path": "/tools/node/generate_metadata"})
        log_event("server", "route_end", {"path": "/tools/node/generate_metadata", "node_id": fallback_meta["id"], "mode": "fallback"})
        return NodeMetadataResponse(metadata=fallback_meta)

    system = SYSTEM_METADATA_PROMPT
    user = build_metadata_user_prompt(req.subtask.dict(), json_schema)

    attempts = 0
    max_attempts = 3
    last_error = None
    validated: Optional[NodeMetadataModel] = None

    while attempts < max_attempts and validated is None:
        attempts += 1
        log_event("server", "llm_call", {"path": "/tools/node/generate_metadata", "attempt": attempts})
        content = chat(build_messages(system, user), response_format_json=True)
        data = safe_json_parse(content)

        if not isinstance(data, dict):
            last_error = {"type": "parse_error", "message": "LLM 未返回有效 JSON"}
            log_event("server", "metadata_invalid", {"attempt": attempts, "error": last_error})
            user = build_metadata_correction_prompt(json.dumps(last_error, ensure_ascii=False), json_schema)
            continue

        try:
            # 允许 resource 缺省；如存在则校验枚举
            validated = NodeMetadataModel(**data)
            log_event("server", "metadata_valid", {"attempt": attempts, "node_id": validated.id})
        except ValidationError as e:
            last_error = e.errors()
            log_event("server", "metadata_invalid", {"attempt": attempts, "error": last_error})
            user = build_metadata_correction_prompt(json.dumps(last_error, ensure_ascii=False), json_schema)
            continue

    if validated is None:
        # 多次失败，回退占位结构
        log_event("server", "metadata_fallback", {"subtask_id": req.subtask.id, "error": last_error})
        resp = NodeMetadataResponse(metadata=fallback_meta)
        log_event("server", "route_end", {"path": "/tools/node/generate_metadata", "node_id": fallback_meta["id"], "mode": "fallback"})
        return resp

    meta = validated.model_dump()
    resp = NodeMetadataResponse(metadata=meta)
    log_event("server", "route_end", {"path": "/tools/node/generate_metadata", "node_id": meta["id"], "attempts": attempts})
    return resp


@app.post("/tools/node/build_graph", response_model=BuildGraphResponse)
def build_graph(req: BuildGraphRequest) -> BuildGraphResponse:
    # TODO: 将节点元数据串联成图并返回
    log_event("server", "route_start", {"path": "/tools/node/build_graph", "node_count": len(req.nodes)})
    nodes = req.nodes
    edges = []
    # 优先依据 depends_on 构建边；若不存在则顺序连线
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
    graph = {"nodes": nodes, "edges": edges}
    resp = BuildGraphResponse(graph=graph)
    log_event("server", "route_end", {"path": "/tools/node/build_graph", "edge_count": len(edges)})
    return resp


# -------------------- LLM 子任务拆分（占位） --------------------

class LLMSplitRequest(BaseModel):
    user_requirement: str
    node_capabilities: Dict[str, Any]
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None


class LLMSplitResponse(BaseModel):
    tasks: List[Subtask]
    description: Optional[str] = None
    prompt_for_confirmation: Optional[str] = None
    plan_text: Optional[str] = None


@app.post("/tools/llm/split_subtasks", response_model=LLMSplitResponse)
def llm_split_subtasks(req: LLMSplitRequest) -> LLMSplitResponse:
    # 若未配置 LLM，返回空结果以触发 Agent 兜底规则拆分
    log_event("server", "route_start", {"path": "/tools/llm/split_subtasks"})
    if not is_configured():
        log_event("server", "llm_unconfigured", {"path": "/tools/llm/split_subtasks"})
        return LLMSplitResponse(tasks=[], description=None, prompt_for_confirmation=None)

    # 默认简洁系统提示与用户提示（若未传入）
    system = req.system_prompt or (
        "你是任务拆分专家。请将用户需求拆分为若干子任务，"
        "每个子任务对应单个节点能力（Prompt、脚本、循环、RAG、API、AI能力、MCP）。"
    )
    user = req.user_prompt or (
        f"用户需求：{req.user_requirement}\n"
        f"节点能力：{json.dumps(req.node_capabilities, ensure_ascii=False)}\n"
        "请输出 JSON：{tasks:[{id,type,description,resource?,service?}]，description，prompt_for_confirmation}"
    )

    content = chat(build_messages(system, user), response_format_json=True)
    data = safe_json_parse(content) or {}

    # 提取 tasks，映射到响应模型
    raw_tasks = data.get("tasks") or []
    tasks: List[Subtask] = []
    for t in raw_tasks:
        try:
            tasks.append(Subtask(
                id=str(t.get("id")),
                type=str(t.get("type")),
                description=str(t.get("description", "")),
                uses_service=(t.get("resource") in ("API", "MCP", "AI能力", "RAG")),
                service=t.get("service"),
                resource=(t.get("resource") if t.get("resource") in ("API", "MCP", "AI能力", "RAG") else None),
            ))
        except Exception:
            continue

    desc = data.get("description")
    prompt = data.get("prompt_for_confirmation")
    # 服务端渲染自然语言规划
    def render_plan_text(tasks: List[Subtask], description: Optional[str]) -> str:
        lines = []
        if description:
            lines.append(f"规划概述：{description}")
        lines.append("执行步骤：")
        for i, t in enumerate(tasks, start=1):
            base = f"{i}. {t.description}（节点类型：{t.type}）"
            if t.uses_service and t.resource:
                svc = f"；使用服务：{t.resource}{' - ' + t.service if t.service else ''}"
                base += svc
            lines.append(base)
        return "\n".join(lines)

    plan_text = render_plan_text(tasks, desc) if tasks else None
    resp = LLMSplitResponse(tasks=tasks, description=desc, prompt_for_confirmation=prompt, plan_text=plan_text)
    log_event("server", "route_end", {"path": "/tools/llm/split_subtasks", "task_count": len(tasks)})
    return resp


# -------------------- LLM 拆分改写（依据用户反馈） --------------------

class LLMSplitRefineRequest(BaseModel):
    user_requirement: str
    node_capabilities: Dict[str, Any]
    feedback: str
    previous_tasks: Optional[List[Dict[str, Any]]] = None
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None


@app.post("/tools/llm/refine_subtasks", response_model=LLMSplitResponse)
def llm_refine_subtasks(req: LLMSplitRefineRequest) -> LLMSplitResponse:
    log_event("server", "route_start", {"path": "/tools/llm/refine_subtasks"})
    if not is_configured():
        log_event("server", "llm_unconfigured", {"path": "/tools/llm/refine_subtasks"})
        return LLMSplitResponse(tasks=[], description=None, prompt_for_confirmation=None)

    system = req.system_prompt or (
        "你是任务拆分专家。需要根据用户的自然语言反馈对现有拆分进行改写，"
        "确保每个子任务可由单节点能力完成（Prompt、脚本、循环、RAG、API、AI能力、MCP）。"
    )
    previous = req.previous_tasks or []
    caps_summary = json.dumps(req.node_capabilities, ensure_ascii=False)
    # 构建用户提示：需求 + 能力摘要 + 现有拆分 + 反馈
    from prompts import build_refine_split_user_prompt
    user = req.user_prompt or build_refine_split_user_prompt(
        req.user_requirement,
        caps_summary,
        req.feedback,
        previous,
    )

    content = chat(build_messages(system, user), response_format_json=True)
    data = safe_json_parse(content) or {}

    raw_tasks = data.get("tasks") or []
    tasks: List[Subtask] = []
    for t in raw_tasks:
        try:
            tasks.append(Subtask(
                id=str(t.get("id")),
                type=str(t.get("type")),
                description=str(t.get("description", "")),
                uses_service=(t.get("resource") in ("API", "MCP", "AI能力", "RAG")),
                service=t.get("service"),
                resource=(t.get("resource") if t.get("resource") in ("API", "MCP", "AI能力", "RAG") else None),
            ))
        except Exception:
            continue

    desc = data.get("description")
    prompt = data.get("prompt_for_confirmation")
    # 渲染自然语言规划
    def render_plan_text(tasks: List[Subtask], description: Optional[str]) -> str:
        lines = []
        if description:
            lines.append(f"规划概述：{description}")
        lines.append("执行步骤：")
        for i, t in enumerate(tasks, start=1):
            base = f"{i}. {t.description}（节点类型：{t.type}）"
            if t.uses_service and t.resource:
                svc = f"；使用服务：{t.resource}{' - ' + t.service if t.service else ''}"
                base += svc
            lines.append(base)
        return "\n".join(lines)

    plan_text = render_plan_text(tasks, desc) if tasks else None
    resp = LLMSplitResponse(tasks=tasks, description=desc, prompt_for_confirmation=prompt, plan_text=plan_text)
    log_event("server", "route_end", {"path": "/tools/llm/refine_subtasks", "task_count": len(tasks)})
    return resp


# ------------------- LLM 子任务拆分（占位） -------------------

class SplitTasksRequest(BaseModel):
    user_requirement: str
    node_caps: Dict[str, Any]
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None


class SplitTasksResponse(BaseModel):
    tasks: List[Subtask]
    description: str
    prompt_for_confirmation: str


@app.post("/tools/llm/split_tasks", response_model=SplitTasksResponse)
def llm_split_tasks(req: SplitTasksRequest) -> SplitTasksResponse:
    # TODO: 调用大模型根据用户需求与节点能力进行任务拆分
    # 这里返回空列表，Agent 端将回退到规则拆分
    log_event("server", "route_start", {"path": "/tools/llm/split_tasks"})
    resp = SplitTasksResponse(
        tasks=[],
        description="",
        prompt_for_confirmation=""
    )
    log_event("server", "route_end", {"path": "/tools/llm/split_tasks", "task_count": 0})
    return resp

# ------------------- LLM 流程决策（占位） -------------------

class DecideNextRequest(BaseModel):
    allowed_steps: List[str]
    state_summary: str
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None


class DecideNextResponse(BaseModel):
    next_step: Optional[str] = None
    rationale: Optional[str] = None


@app.post("/tools/llm/decide_next", response_model=DecideNextResponse)
def llm_decide_next(req: DecideNextRequest) -> DecideNextResponse:
    log_event("server", "route_start", {"path": "/tools/llm/decide_next", "allowed": req.allowed_steps})
    if not is_configured():
        log_event("server", "llm_unconfigured", {"path": "/tools/llm/decide_next"})
        return DecideNextResponse(next_step=None, rationale="")
    system = req.system_prompt or (
        "你是流程控制器。请在候选步骤中选择一个最合理的下一步，"
        "遵循：拆分→订阅校验→市场检索→元数据→构图。"
    )
    user = req.user_prompt or (
        f"当前状态：{req.state_summary}\n"
        f"候选步骤：{', '.join(req.allowed_steps)}\n"
        "输出 JSON：{next_step, rationale}，其中 next_step 必须为候选之一。"
    )
    content = chat(build_messages(system, user), response_format_json=True)
    data = safe_json_parse(content) or {}
    next_step = data.get("next_step")
    rationale = data.get("rationale")
    if next_step not in req.allowed_steps:
        log_event("server", "decision_invalid", {"next_step": next_step, "allowed": req.allowed_steps})
        next_step = None
    resp = DecideNextResponse(next_step=next_step, rationale=rationale)
    log_event("server", "route_end", {"path": "/tools/llm/decide_next", "next_step": next_step})
    return resp