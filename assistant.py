"""Text assistant (TextOS). `python3 assistant.py` -> type a command; it plans, shows a dry-run, and answers memory questions.
Real execution is intentionally stubbed (dry-run only): each action prints what it would do."""
import json, sys
from datetime import datetime
from candor.actions import Planner
from candor.memory import Memory

def main(data="data"):
    pl, mem = Planner(data), None
    now = sys.argv[1] if len(sys.argv) > 1 else "2026-09-18T18:00:00-07:00"
    print(f"Candor TextOS (dry-run, as_of={now}). Ctrl-D to quit.")
    for line in sys.stdin if not sys.stdin.isatty() else iter(lambda: input("> "), None):
        line = line.strip()
        if not line: continue
        for a in pl.plan(line, now):
            if a["type"] == "memory.ask":
                r = pl.mem.answer({"id": "ask", "question": a["args"]["question"], "as_of": now})
                print("  memory:", r["answer"], "| sources:", ", ".join(r["sources"]))
            else:
                print("  would do:", json.dumps(a, ensure_ascii=False))
if __name__ == "__main__":
    try: main()
    except EOFError: pass
