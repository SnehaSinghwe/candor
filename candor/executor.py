"""Optional LOCAL executor (assistant.py --execute). Only runs after the user says yes.
Honest scope: apps are really launched; reminders, calendar events, drafts, Slack messages and emails are written to a local
outbox folder (default ./candor_outbox, override with CANDOR_OUTBOX). Nothing is sent over the network: there are no Slack/Gmail
credentials in this project. Replace `_queue` with a real API call to go live."""
import json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

SAFE_APP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+\-_]{0,39}$")


def outbox():
    d = Path(os.environ.get("CANDOR_OUTBOX", "candor_outbox")); d.mkdir(parents=True, exist_ok=True); return d


def _queue(name, record):
    with open(outbox() / f"{name}.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"queued_at": datetime.now(timezone.utc).isoformat(), **record}, ensure_ascii=False) + "\n")


def _ics(a):
    f = lambda s: datetime.fromisoformat(s).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    att = "".join(f"ATTENDEE:mailto:{x}\n" for x in a.get("attendees", []))
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//candor//EN\nBEGIN:VEVENT\n"
            f"UID:{f(a['start'])}-candor\nDTSTAMP:{f(a['start'])}\nDTSTART:{f(a['start'])}\nDTEND:{f(a['end'])}\n"
            f"SUMMARY:{a['title'].replace(chr(10), ' ')}\n{att}END:VEVENT\nEND:VCALENDAR\n")


def open_app(app):
    if not SAFE_APP.match(app):
        return f"refused: '{app}' is not a plain application name"
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", app], check=True, capture_output=True)
        elif os.name == "nt":
            subprocess.run(["cmd", "/c", "start", "", app], check=True, capture_output=True)
        else:
            subprocess.run(["xdg-open", app], check=True, capture_output=True)
        return f"launched {app}"
    except Exception as e:
        return f"could not launch {app}: {type(e).__name__}"


def execute(action):
    """Run one validated action. Returns a one-line result string."""
    t, a = action["type"], action["args"]
    if t == "app.open":
        return open_app(a["app"])
    if os.environ.get("CANDOR_LIVE") == "1":      # assistant.py --live: real Gmail / Calendar / Slack calls (candor/live.py)
        from . import live
        if t in live.HANDLERS:
            return live.run(action)
    if t == "reminder.create":
        _queue("reminders", a); return f"reminder saved for {a['due']} (candor_outbox/reminders.jsonl)"
    if t == "calendar.create_event":
        p = outbox() / f"event_{datetime.fromisoformat(a['start']).strftime('%Y%m%d_%H%M')}.ics"
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(_ics(a))
        return f"event file written: {p} (import into any calendar)"
    if t == "calendar.update_event":
        _queue("calendar_updates", a); return "calendar change queued (no calendar credentials)"
    if t in ("slack.send_message", "gmail.send", "gmail.draft"):
        _queue({"slack.send_message": "slack", "gmail.send": "email", "gmail.draft": "drafts"}[t], a)
        return f"{t} queued in outbox, NOT sent (no credentials configured)"
    return f"{t}: nothing to execute"
