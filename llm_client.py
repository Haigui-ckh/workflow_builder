import os
import json
from typing import List, Dict, Optional, Any, Tuple

from dotenv import load_dotenv, find_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

# Load environment variables from the nearest .env file, if present.
# This is more robust than relying on CWD-only loading.
try:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()
    alt_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(alt_path):
        load_dotenv(dotenv_path=alt_path, override=False)
except Exception:
    pass


def _first_nonempty(*keys: str) -> str:
    for k in keys:
        v = os.getenv(k, "")
        if v:
            return v
    return ""

def _get_llm_config() -> Tuple[str, str, str]:
    base_url = _first_nonempty("LLM_BASE_URL", "OPENAI_BASE_URL", "DEEPSEEK_BASE_URL")
    api_key = _first_nonempty("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    model = _first_nonempty("LLM_MODEL", "OPENAI_MODEL", "DEEPSEEK_MODEL")
    return base_url, api_key, model

def is_configured() -> bool:
    try:
        from langchain.chat_models import init_chat_model as _init
    except Exception:
        return False
    _, api_key, model = _get_llm_config()
    return bool(api_key and model)

def chat(messages: List[Dict[str, Any]], response_format_json: bool = True, temperature: float = 0.0) -> Optional[str]:
    try:
        base_url, api_key, model = _get_llm_config()
        if base_url and not os.getenv("OPENAI_BASE_URL"):
            os.environ["OPENAI_BASE_URL"] = base_url
        if api_key and not os.getenv("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = api_key
        llm = init_chat_model(model=model, temperature=temperature)
        lc_msgs = []
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system":
                lc_msgs.append(SystemMessage(content=content))
            elif role == "user":
                lc_msgs.append(HumanMessage(content=content))
            else:
                lc_msgs.append(AIMessage(content=content))
        extra = {"response_format": {"type": "json_object"}} if response_format_json else {}
        res = llm.invoke(lc_msgs, **extra)
        return getattr(res, "content", None)
    except Exception:
        import traceback
        traceback.print_exc()
        return None

def build_messages(system_prompt: Optional[str], user_prompt: str) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": user_prompt})
    return msgs

def safe_json_parse(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        # 尝试截取代码块或清理前后缀
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None

def get_llm_config_debug(mask_api_key: bool = True) -> Dict[str, str]:
    base_url, api_key, model = _get_llm_config()
    if mask_api_key and api_key:
        api_key = (api_key[:4] + "..." + api_key[-4:]) if len(api_key) > 8 else "****"
    return {"base_url": base_url, "api_key": api_key, "model": model}
