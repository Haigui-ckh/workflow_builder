from pathlib import Path
from typing import Dict, Any, Optional


def parse_node_capabilities(node_desc_path: Optional[str] = None) -> Dict[str, Any]:
    """读取节点描述文件原文并作为提示注入，同时返回标准能力映射供匹配使用。"""
    path = Path(node_desc_path or (Path.cwd() / "node_desc.txt"))
    # 基础能力映射（用于类型匹配与资源判断）
    base_caps: Dict[str, Any] = {
        "Prompt节点": {"category": "即时可用能力"},
        "脚本节点": {"category": "即时可用能力"},
        "循环节点": {"category": "即时可用能力"},
        "RAG节点": {"category": "三方服务生态", "resource": "RAG"},
        "API节点": {"category": "三方服务生态", "resource": "API"},
        "AI能力节点": {"category": "三方服务生态", "resource": "AI能力"},
        "MCP节点": {"category": "三方服务生态", "resource": "MCP"},
    }
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        # 将原文注入到特殊键，供提示词使用；匹配逻辑仍使用标准能力映射
        base_caps["__raw_text__"] = raw
    return base_caps