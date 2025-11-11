import os
from typing import List, Dict, Any, Optional

import requests


TOOLS_BASE_URL = os.getenv("TOOLS_BASE_URL", "http://localhost:8000")
TOOLS_HTTP_TIMEOUT = int(os.getenv("TOOLS_HTTP_TIMEOUT", "60"))


def _get(url: str) -> Any:
    resp = requests.get(url, timeout=TOOLS_HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post(url: str, payload: Dict[str, Any]) -> Any:
    resp = requests.post(url, json=payload, timeout=TOOLS_HTTP_TIMEOUT)
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


def call_llm_split_subtasks(user_requirement: str, node_capabilities: Dict[str, Any], system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    payload = {
        "user_requirement": user_requirement,
        "node_capabilities": node_capabilities,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    try:
        return _post(f"{TOOLS_BASE_URL}/tools/llm/split_subtasks", payload)
    except Exception:
        return {"tasks": [], "description": "", "prompt_for_confirmation": ""}


def call_llm_decide(allowed_steps: List[str], state_summary: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    payload = {
        "allowed_steps": allowed_steps,
        "state_summary": state_summary,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    try:
        return _post(f"{TOOLS_BASE_URL}/tools/llm/decide_next", payload)
    except Exception:
        return {"next_step": None}


def call_llm_refine_subtasks(user_requirement: str, node_capabilities: Dict[str, Any], feedback: str, previous_tasks: List[Dict[str, Any]], system_prompt: Optional[str] = None, user_prompt: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "user_requirement": user_requirement,
        "node_capabilities": node_capabilities,
        "feedback": feedback,
        "previous_tasks": previous_tasks,
    }
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    if user_prompt is not None:
        payload["user_prompt"] = user_prompt
    try:
        return _post(f"{TOOLS_BASE_URL}/tools/llm/refine_subtasks", payload)
    except Exception:
        return {"tasks": [], "description": "", "prompt_for_confirmation": ""}
