import os
import sys
from pathlib import Path

# 将项目根目录加入模块搜索路径
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main():
    # 确保工具路由指向本地开发服务
    os.environ.setdefault("TOOLS_BASE_URL", "http://127.0.0.1:8000")

    from agent import create_app

    app = create_app()
    thread_id = "demo-thread"

    # 第一次调用：进行拆分并暂停等待确认
    state = app.invoke({
        "user_requirement": "根据用户提出的问题，查询流水线运营知识库，结合知识库内容，生成符合要求的回答",
        "confirmed": False,
        "node_desc_path": "./node_desc.txt",
    }, config={"configurable": {"thread_id": thread_id}})

    print("== 第一次拆分完成，等待确认 ==")
    print("plan_text:\n", state.get("plan_text"))
    print("prompt_for_confirmation:", state.get("prompt_for_confirmation"))
    print("tasks_count:", len(state.get("tasks", [])))

    # 用户确认后继续（如果需要）
    state = app.invoke({"confirmed": True}, config={"configurable": {"thread_id": thread_id}})

    print("== 订阅校验结果 ==")
    print("status:", state.get("status"))
    print("missing_services:", state.get("missing_services"))
    print("subscription_suggestions:", state.get("subscription_suggestions"))

    # 再次调用以继续流程（若仍需暂停订阅或已进入元数据生成）
    state = app.invoke({}, config={"configurable": {"thread_id": thread_id}})
    print("== 当前状态 ==")
    print("status:", state.get("status"))
    if state.get("graph"):
        print("graph nodes:", len(state["graph"].get("nodes", [])))
        print("graph edges:", len(state["graph"].get("edges", [])))


if __name__ == "__main__":
    main()