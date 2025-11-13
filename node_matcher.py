from typing import Dict, Any, List, Optional, Tuple

from utils.logger import log_event


# 关键词特征，用于规则打分（可按需扩展）
NODE_KEYWORDS: Dict[str, List[str]] = {
    "RAG节点": ["rag", "检索", "知识库", "召回", "向量"],
    "API节点": ["api", "接口", "http", "rest", "调用", "对接"],
    "AI能力节点": ["ai", "ocr", "识别", "图片", "语音", "翻译", "nlp"],
    "MCP节点": ["mcp", "git", "文件系统", "工具", "解析器"],
    "循环节点": ["循环", "批量", "重复", "列表", "遍历"],
    "脚本节点": ["脚本", "转换", "处理", "解析", "合并", "生成代码"],
    "Prompt节点": ["prompt", "汇总", "生成", "文案", "回答", "总结"],
}


RESOURCE_TYPES = {"API", "MCP", "AI能力", "RAG"}


def _kw_score(text: str, keywords: List[str]) -> int:
    t = (text or "").lower()
    score = 0
    for kw in keywords:
        if kw in t:
            score += 1
    return score


def _candidate_nodes(caps: Dict[str, Any]) -> List[str]:
    # 仅考虑我们支持的标准节点集合
    allowed = {"Prompt节点", "脚本节点", "循环节点", "RAG节点", "API节点", "AI能力节点", "MCP节点"}
    return [name for name in caps.keys() if name in allowed]


def match_task_to_node(task: Dict[str, Any], caps: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据任务文本与资源线索，选择最合适的节点类型。
    规则：
    - 若任务给出 resource 提示，优先匹配同资源的节点类型（RAG/API/AI能力/MCP）
    - 否则按关键词打分选择分数最高的节点
    - 打分接近或没有命中时，默认选择 Prompt节点
    """
    desc = task.get("description") or task.get("desc") or ""
    resource_hint = task.get("resource")
    candidates = _candidate_nodes(caps)

    # 基于资源提示的优先匹配
    if resource_hint in RESOURCE_TYPES:
        res_map = {
            "RAG": "RAG节点",
            "API": "API节点",
            "AI能力": "AI能力节点",
            "MCP": "MCP节点",
        }
        node_type = res_map.get(resource_hint)
        if node_type and node_type in candidates:
            selected = node_type
            resource = resource_hint
        else:
            selected = "Prompt节点"
            resource = None
    else:
        # 关键词打分
        scores: List[Tuple[str, int]] = []
        for name in candidates:
            kw = NODE_KEYWORDS.get(name, [])
            scores.append((name, _kw_score(desc, kw)))
        # 选最高分，若并列或全0则回退 Prompt
        scores.sort(key=lambda x: x[1], reverse=True)
        selected = scores[0][0] if scores and scores[0][1] > 0 else "Prompt节点"
        # 推导资源
        cap = caps.get(selected, {})
        resource = cap.get("resource")

    uses_service = resource in RESOURCE_TYPES
    matched = {
        "id": task.get("id"),
        "type": selected,
        "description": desc,
        "uses_service": uses_service,
        "service": task.get("service"),  # 保留原有服务名称线索（若有）
        "resource": resource,
    }
    log_event("matcher", "match_selected", {"task_id": matched["id"], "type": selected, "resource": resource})
    return matched


def assign_nodes_to_tasks(tasks: List[Dict[str, Any]], caps: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for t in tasks:
        try:
            results.append(match_task_to_node(t, caps))
        except Exception as e:
            log_event("matcher", "match_error", {"task_id": t.get("id"), "error": str(e)})
    log_event("matcher", "assign_done", {"count": len(results)})
    return results