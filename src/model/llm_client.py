import os
import json
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path
import platform
import subprocess

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
    explicit = os.getenv("LLM_DOTENV_PATH", "")
    if explicit and os.path.exists(explicit):
        load_dotenv(dotenv_path=explicit, override=False)
    else:
        try:
            root_env = Path(__file__).resolve().parents[2] / ".env"
            if root_env.exists():
                load_dotenv(dotenv_path=str(root_env), override=False)
        except Exception:
            pass
except Exception:
    pass


def _first_nonempty(*keys: str) -> str:
    for k in keys:
        v = os.getenv(k, "")
        if v:
            return v
    return ""

def _read_file_json(paths: List[Path]) -> Dict[str, str]:
    for p in paths:
        try:
            if p.exists():
                with p.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return {
                            "base_url": str(data.get("base_url", "")),
                            "api_key": str(data.get("api_key", "")),
                            "model": str(data.get("model", "")),
                        }
        except Exception:
            continue
    return {"base_url": "", "api_key": "", "model": ""}

def _read_file_text(paths: List[Path]) -> str:
    for p in paths:
        try:
            if p.exists():
                t = p.read_text(encoding="utf-8").strip()
                if t:
                    return t
        except Exception:
            continue
    return ""

def _read_keychain_api_key() -> str:
    try:
        if platform.system().lower() != "darwin":
            return ""
        service = os.getenv("LLM_KEYCHAIN_SERVICE", "workflow_builder_llm")
        account = os.getenv("LLM_KEYCHAIN_ACCOUNT", "default")
        cmd = [
            "security",
            "find-generic-password",
            "-s",
            service,
            "-a",
            account,
            "-w",
        ]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout.strip()
        return ""
    except Exception:
        return ""

def _get_llm_config() -> Tuple[str, str, str]:
    base_url = _first_nonempty("LLM_BASE_URL", "OPENAI_BASE_URL", "DEEPSEEK_BASE_URL")
    api_key = _first_nonempty("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    model = _first_nonempty("LLM_MODEL", "OPENAI_MODEL", "DEEPSEEK_MODEL")
    if not api_key or not model:
        home = Path.home()
        cfg = _read_file_json([
            home / ".config" / "workflow_builder" / "llm.json",
            home / ".workflow_builder" / "llm.json",
            home / ".llm_config.json",
        ])
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        model = model or cfg.get("model", "")
    if not api_key:
        api_key = _read_file_text([
            Path.home() / ".workflow_builder" / "llm_key.txt",
            Path.home() / ".llm_key.txt",
        ]) or _read_keychain_api_key()
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
        if base_url:
            os.environ.setdefault("OPENAI_BASE_URL", base_url)
        if api_key:
            os.environ.setdefault("OPENAI_API_KEY", api_key)
            os.environ.setdefault("DEEPSEEK_API_KEY", api_key)
        
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
