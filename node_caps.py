from pathlib import Path
from typing import Dict, Any, Optional


def parse_node_capabilities(node_desc_path: Optional[str] = None) -> Dict[str, Any]:
    path = Path(node_desc_path or (Path.cwd() / "node_desc.txt"))
    if not path.exists():
        # 基础能力映射（兜底）
        return {
            "Prompt节点": {"category": "即时可用能力"},
            "脚本节点": {"category": "即时可用能力"},
            "循环节点": {"category": "即时可用能力"},
            "RAG节点": {"category": "三方服务生态", "resource": "RAG"},
            "API节点": {"category": "三方服务生态", "resource": "API"},
            "AI能力节点": {"category": "三方服务生态", "resource": "AI能力"},
            "MCP节点": {"category": "三方服务生态", "resource": "MCP"},
        }
    content = path.read_text(encoding="utf-8")
    caps: Dict[str, Any] = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("-- ") and "节点" in line:
            name = line.replace("-- ", "").split("：")[0]
            # 粗略判断来源
            resource = None
            if "RAG" in name:
                resource = "RAG"
            elif "API" in name:
                resource = "API"
            elif "MCP" in name:
                resource = "MCP"
            elif "AI能力" in name:
                resource = "AI能力"
            category = "三方服务生态" if resource else "即时可用能力"
            caps[name] = {"category": category}
            if resource:
                caps[name]["resource"] = resource
    # 回填基础能力，避免遗漏
    caps.setdefault("Prompt节点", {"category": "即时可用能力"})
    caps.setdefault("脚本节点", {"category": "即时可用能力"})
    caps.setdefault("循环节点", {"category": "即时可用能力"})
    caps.setdefault("RAG节点", {"category": "三方服务生态", "resource": "RAG"})
    caps.setdefault("API节点", {"category": "三方服务生态", "resource": "API"})
    caps.setdefault("AI能力节点", {"category": "三方服务生态", "resource": "AI能力"})
    caps.setdefault("MCP节点", {"category": "三方服务生态", "resource": "MCP"})
    return caps