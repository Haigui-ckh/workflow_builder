import os
import json
from typing import List, Dict, Optional, Any

from openai import OpenAI
from dotenv import load_dotenv


load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")


def is_configured() -> bool:
    return bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL)


# 使用 OpenAI 兼容 SDK（DeepSeek API）


def chat(messages: List[Dict[str, Any]], response_format_json: bool = True, temperature: float = 0.0) -> Optional[str]:
    if not is_configured():
        return None
    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        params: Dict[str, Any] = {
            "model": LLM_MODEL,
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