import os

SYSTEM_CONTROLLER_PROMPT = (
    "你是工作流构建平台的流程控制器。根据用户需求与当前状态，"
    "在给定的候选步骤中选择下一步，确保流程符合：拆分任务→订阅校验→市场检索→节点元数据→构建图。"
    "只输出 json 对象，不要任何额外文本。"
)

SYSTEM_SPLIT_PROMPT = (
    "你是任务拆分专家。请将用户需求拆分为若干子任务，"
    "每个子任务对应单个节点能力，可选节点类型包含：Prompt节点、脚本节点、循环节点、RAG节点、API节点、AI能力节点、MCP节点。"
    "只输出 json，不要任何额外文本。"
)

SYSTEM_FEEDBACK_DECIDE_PROMPT = (
    "你是任务拆分评审器。根据用户输入判断是继续执行还是修改拆分。"
    "只输出 json 对象，包含 action 与 rationale。action 必须为 apply_refine 或 continue。"
)


def build_split_user_prompt(requirement: str, caps_summary: str) -> str:
    base_dir = os.path.dirname(__file__)
    path = os.path.join(base_dir, "../../templates", "prompts", "split_user_prompt.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            tpl = f.read()
        return tpl.replace("{requirement}", requirement).replace("{caps_summary}", caps_summary)
    except Exception:
        return

# 节点能力聚合
def summarize_caps(caps: dict | str) -> str:
    if isinstance(caps, str):
        return caps
    raw = caps.get("__raw_text__") if isinstance(caps, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw
    parts = []
    for name, info in caps.items():
        if not isinstance(info, dict):
            continue
        res = info.get("resource")
        cat = info.get("category")
        parts.append(f"{name}({cat}{'/' + res if res else ''})")
    return ", ".join(parts)


def build_decide_user_prompt(state_summary: str, allowed_steps: list[str]) -> str:
    steps = ", ".join(allowed_steps)
    return (
        f"当前状态摘要：{state_summary}\n"
        f"候选下一步：{steps}\n"
        "输出为 json：{next_step,rationale}。仅输出 json 对象。"
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

def build_feedback_decide_user_prompt(feedback: str, tasks: list[dict], caps_summary: str, allowed_actions: list[str]) -> str:
    import json
    return (
        "用户输入:\n"
        + (feedback or "")
        + "\n当前拆分任务:\n"
        + json.dumps(tasks or [], ensure_ascii=False)
        + "\n节点能力摘要:\n"
        + (caps_summary or "")
        + "\n输出为 json：{action,rationale}，其中 action 必须为 "
        + ", ".join(allowed_actions or ["apply_refine", "continue"])
    )


# ---------------- 元数据生成提示词 ----------------

SYSTEM_METADATA_PROMPT = (
    "你是节点元数据生成器。根据子任务信息，严格输出符合 JSON Schema 的节点元数据对象。"
    "只输出 json，不要任何额外文本或解释。"
)


def build_metadata_user_prompt(subtask: dict, json_schema: dict) -> str:
    import json
    return (
        "子任务信息如下（字段可能包含 id、type、description、resource、service）：\n"
        + json.dumps(subtask, ensure_ascii=False)
        + "\n请严格按以下 JSON Schema 生成节点元数据：\n"
        + json.dumps(json_schema, ensure_ascii=False)
        + "\n仅输出一个 json 对象，字段必须与 Schema 匹配。"
    )


def build_metadata_correction_prompt(error_msg: str, json_schema: dict) -> str:
    import json
    return (
        "上一次输出不符合要求，错误如下：\n"
        + error_msg
        + "\n请根据以下 JSON Schema 修正并重新输出，仅输出 json 对象：\n"
        + json.dumps(json_schema, ensure_ascii=False)
    )
