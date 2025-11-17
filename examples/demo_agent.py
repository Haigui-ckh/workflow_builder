import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def print_state(s):
    print("status:", s.get("status"))
    p = s.get("prompt_for_confirmation")
    if p:
        print("prompt_for_confirmation:", p)
    t = s.get("tasks")
    if t is not None:
        print("tasks_count:", len(t))
    plan = s.get("plan_text")
    if plan:
        print("plan_text:\n", plan)
    miss = s.get("missing_services")
    if miss is not None:
        print("missing_services:", miss)
    sug = s.get("subscription_suggestions")
    if sug is not None:
        print("subscription_suggestions:", sug)
    g = s.get("graph")
    if g:
        print("graph nodes:", len(g.get("nodes", [])))
        print("graph edges:", len(g.get("edges", [])))

def main():
    os.environ.setdefault("TOOLS_BASE_URL", "http://127.0.0.1:8000")
    from src.agent import create_app
    from langgraph.types import Command
    app = create_app()
    thread_id = "demo-thread"

    user_requirement = "输入流水线id, 查询流水线今天前10条运行结果及运行描述，分别将每一条的结果写入到数据库中，然后使用大模型分析运行描述，输出优化方案，最后汇总输出"
    state = app.invoke({
        "user_requirement": user_requirement,
        "confirmed": False,
        "node_desc_path": "../templates/node/node_desc.txt",
    }, config={"configurable": {"thread_id": thread_id}})
    print_state(state)

    while True:
        try:
            cmd = input("input> ").strip()
        except EOFError:
            break
        if not cmd:
            continue
        if cmd.lower() in ("exit", "quit"): 
            break
        if cmd.lower() in ("confirm", "continue", "yes", "y"):
            state = app.invoke(Command(resume={"confirmed": True}), config={"configurable": {"thread_id": thread_id}})
            print_state(state)
            continue
        if cmd.lower().startswith("feedback:"):
            fb = cmd.split(":", 1)[1].strip()
            state = app.invoke(Command(resume={"user_feedback": fb}), config={"configurable": {"thread_id": thread_id}})
            print_state(state)
            continue
        if cmd.lower() in ("resume", "next"):
            state = app.invoke(Command(resume={}), config={"configurable": {"thread_id": thread_id}})
            print_state(state)
            continue
        print("unknown command")

if __name__ == "__main__":
    main()