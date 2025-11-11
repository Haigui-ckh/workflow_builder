import os
import json
from typing import List, Dict, Optional, Any, Tuple

from openai import OpenAI
from dotenv import load_dotenv, find_dotenv


# Load environment variables from the nearest .env file, if present.
# This is more robust than relying on CWD-only loading.
try:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
    else:
        # Fallback: attempt default loader (no-op if .env absent)
        load_dotenv()
except Exception:
    # Proceed even if dotenv loading fails; env may be provided by the OS
    pass


def _get_llm_config() -> Tuple[str, str, str]:
    base_url = os.getenv("LLM_BASE_URL", "")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")
    return base_url, api_key, model


def is_configured() -> bool:
    base_url, api_key, model = _get_llm_config()
    return bool(base_url and api_key and model)


# 使用 OpenAI 兼容 SDK（DeepSeek API）


def chat(messages: List[Dict[str, Any]], response_format_json: bool = True, temperature: float = 0.0) -> Optional[str]:
    if not is_configured():
        return None
    base_url, api_key, model = _get_llm_config()
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        params: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format_json:
            params["response_format"] = {"type": "json_object"}

        result = client.chat.completions.create(**params)
        choices = getattr(result, "choices", [])
        if not choices:
            return None
        content = getattr(choices[0].message, "content", None)
        return content
    except Exception:
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