SYSTEM_CONTROLLER_PROMPT = (
    "你是工作流构建平台的流程控制器。根据用户需求与当前状态，"
    "在给定的候选步骤中选择下一步，确保流程符合：拆分任务→订阅校验→市场检索→节点元数据→构建图。"
)

SYSTEM_SPLIT_PROMPT = (
    "你是任务拆分专家。请将用户需求拆分为若干子任务，"
    "每个子任务对应单个节点能力，可选节点类型包含：Prompt节点、脚本节点、循环节点、RAG节点、API节点、AI能力节点、MCP节点。"
)

def build_split_user_prompt(requirement: str, caps_summary: str) -> str:
    return (
        f"用户需求：{requirement}\n"
        f"节点能力摘要：{caps_summary}\n"
        "输出：任务列表（id、type、description、resource 可选、service 可选）、"
        "简要描述、确认提示语。"
    )


def summarize_caps(caps: dict) -> str:
    parts = []
    for name, info in caps.items():
        res = info.get("resource")
        cat = info.get("category")
        parts.append(f"{name}({cat}{'/' + res if res else ''})")
    return ", ".join(parts)


def build_decide_user_prompt(state_summary: str, allowed_steps: list[str]) -> str:
    steps = ", ".join(allowed_steps)
    return (
        f"当前状态摘要：{state_summary}\n"
        f"候选下一步：{steps}\n"
        "请选择一个最合理的下一步。若需用户确认或订阅，请选择对应的暂停步骤。"
    )


def summarize_state(state: dict) -> str:
    status = state.get("status")
    confirmed = state.get("confirmed")
    tasks = state.get("tasks") or []
    missing = state.get("missing_services") or []
    return (
        f"status={status}, confirmed={confirmed}, "
        f"tasks={len(tasks)}, missing_services={len(missing)}"
    )


# ---------------- 元数据生成提示词 ----------------

SYSTEM_METADATA_PROMPT = (
    "你是节点元数据生成器。根据子任务信息，严格输出符合 JSON Schema 的节点元数据对象。"
    "只输出 JSON，不要任何额外文本或解释。"
)


def build_metadata_user_prompt(subtask: dict, json_schema: dict) -> str:
    import json
    return (
        "子任务信息如下（字段可能包含 id、type、description、resource、service）：\n"
        + json.dumps(subtask, ensure_ascii=False)
        + "\n请严格按以下 JSON Schema 生成节点元数据：\n"
        + json.dumps(json_schema, ensure_ascii=False)
        + "\n仅输出一个 JSON 对象，字段必须与 Schema 匹配。"
    )


def build_metadata_correction_prompt(error_msg: str, json_schema: dict) -> str:
    import json
    return (
        "上一次输出不符合要求，错误如下：\n"
        + error_msg
        + "\n请根据以下 JSON Schema 修正并重新输出，仅输出 JSON 对象：\n"
        + json.dumps(json_schema, ensure_ascii=False)
    )