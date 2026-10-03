"""Candor assistant: text or voice, multi-turn, dry-run by default.

  python3 assistant.py                       text mode
  python3 assistant.py --voice               press Enter, speak for 6 s (needs: pip install sounddevice, GEMINI_API_KEY)
  python3 assistant.py --audio note.wav      one spoken command from a WAV/MP3 file
  python3 assistant.py --execute             after a plan, ask "run it? [y/N]" and apply local effects (see candor/executor.py)
  python3 assistant.py --live [--safe]       REAL Gmail/Calendar/Slack actions (implies --execute, uses the real clock; see LIVE.md)
  python3 assistant.py --as-of 2026-09-18T18:00:00-07:00

With GEMINI_API_KEY the plan comes from Gemini + a code validator; without a key the rule-based planner is used."""
import argparse, json, sys
from candor.agent import Agent
from candor import voice


def show(ag, acts, now):
    summary = []
    for a in acts:
        if a["type"] == "memory.ask":
            r = ag.planner.mem.answer({"id": "ask", "question": a["args"]["question"], "as_of": now})
            print("  memory:", r["answer"], "| sources:", ", ".join(r["sources"])); summary.append("answered: " + r["answer"][:80])
        elif a["type"] in ("clarify", "confirm"):
            q = a["args"].get("question") or a["args"].get("summary"); print(f"  {a['type']}: {q}"); summary.append(f"{a['type']}: {q}")
        else:
            print(f"  would do [{ag.last_mode}]:", json.dumps(a, ensure_ascii=False)); summary.append("planned " + a["type"])
    return "; ".join(summary)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--voice", action="store_true"); ap.add_argument("--audio")
    ap.add_argument("--execute", action="store_true"); ap.add_argument("--live", action="store_true"); ap.add_argument("--safe", action="store_true"); ap.add_argument("--as-of")
    ap.add_argument("positional", nargs="*"); a = ap.parse_args()
    from datetime import datetime
    SIM = "2026-09-18T18:00:00-07:00"
    if a.live:
        import os; os.environ["CANDOR_LIVE"] = "1"; a.execute = True
        if not a.safe and os.environ.pop("CANDOR_LIVE_TO", None):   # stale test-mode var would redirect all mail; --safe keeps it
            print("  note: ignoring CANDOR_LIVE_TO (use --live --safe to redirect all mail to it)")
        from candor.actions import TZ
        now = a.positional[0] if a.positional else (a.as_of or datetime.now(TZ).isoformat())   # real clock: "tomorrow" must be real
    else:
        now = a.positional[0] if a.positional else (a.as_of or SIM)
    ag, hist = Agent("data"), []
    if a.live:
        try:
            from candor import live
            print(f"  live calendar loaded: {live.load_calendar(ag.planner, datetime.fromisoformat(now))} upcoming events")
        except Exception as e:
            print(f"  could not load Google Calendar ({type(e).__name__}); using sample calendar. Run the setup in LIVE.md.")
    print(f"Candor assistant (as_of={now}, {'LIVE' if a.live else 'EXECUTE (local outbox)' if a.execute else 'dry-run'}). Ctrl-D to quit.")

    def handle(line):
        acts = ag.plan(line, now, hist)
        hist.append((line, show(ag, acts, now)))
        runnable = [x for x in acts if x["type"] not in ("memory.ask", "clarify", "confirm")]
        if a.execute and runnable:
            if input("  run it? [y/N] ").strip().lower() == "y":
                from candor.executor import execute
                for x in runnable:
                    print("  ->", execute(x))

    if a.audio:
        text = voice.transcribe_file(a.audio); print("  heard:", text); text and handle(text); return
    while True:
        try:
            if a.voice:
                input("> press Enter and speak ")
                line = voice.record(); print("  heard:", line)
            else:
                line = input("> ").strip()
        except EOFError:
            break
        except RuntimeError as e:
            print("  ", e); break
        if line:
            handle(line)


if __name__ == "__main__":
    main()