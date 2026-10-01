"""Normalize every source into Units (the citable ids) with delivery time, speaker, text."""
import json, re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


def dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


@dataclass
class Unit:
    id: str
    record: str
    source: str            # meeting|dictation|slack|email|calendar|codex|chatgpt
    time: datetime
    text: str              # body text (edits applied later)
    speaker: str = ""
    label: str = ""        # title / channel / subject, indexed as metadata
    parent: str = ""       # slack thread parent id
    seq: int = 0           # order inside record
    meta: dict = field(default_factory=dict)


def load(data_dir):
    d = Path(data_dir)
    units, deleted, edits = [], {}, {}
    for f in sorted((d / "native/meetings").glob("*.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        start = dt(m["start"])
        for i, s in enumerate(m["segments"]):
            who = s.get("speaker_name") or ""
            unk = not who
            units.append(Unit(s["seg_id"], m["id"], "meeting", start + timedelta(seconds=s["end_s"]),
                              s["text"], who or s.get("speaker_label") or "Unknown speaker",
                              m["title"], seq=i,
                              meta=dict(conf=s.get("speaker_confidence"), unidentified=unk,
                                        channel=s.get("channel"), date=m["start"][:10],
                                        participants=m.get("participants_known", []))))
    for line in open(d / "native/dictation/dictations.jsonl", encoding="utf-8"):
        x = json.loads(line)
        txt = x["cleaned_text"] or x.get("raw_transcript", "")
        units.append(Unit(x["id"], x["id"], "dictation", dt(x["timestamp"]), txt, "Alex Rivera",
                          f"{x['mode']} {x['target_app']} {x['target_context']} {x['delivery_state']}",
                          meta=dict(state=x["delivery_state"], mode=x["mode"], raw=x.get("raw_transcript", ""))))
    users = {u["id"]: u for u in json.load(open(d / "connectors/slack/users.json", encoding="utf-8"))}
    chans = {c["id"]: c for c in json.load(open(d / "connectors/slack/channels.json", encoding="utf-8"))}
    for line in open(d / "connectors/slack/messages.jsonl",encoding="utf-8"):
        x = json.loads(line)
        t = dt(x["ts"])
        if x.get("subtype") == "message_deleted":
            deleted[x["target_id"]] = t
            continue
        ch = chans.get(x["channel_id"], {})
        chname = ch.get("name", x["channel_id"])
        if x.get("subtype") == "message_changed":
            edits.setdefault(x["target_id"], []).append((t, x["text"]))
            units.append(Unit(x["id"], x["id"], "slack", t, x["text"], "", f"slack {chname} edit",
                              meta=dict(edit_of=x["target_id"], channel=x["channel_id"])))
            continue
        who = users.get(x.get("user"), {}).get("real_name") or x.get("bot_name") or x.get("user") or ""
        units.append(Unit(x["id"], x["id"], "slack", t, x["text"], who,
                          f"slack {chname}{' dm' if ch.get('is_dm') else ''}", parent=x.get("thread_parent_id") or "",
                          meta=dict(channel=x["channel_id"], user=x.get("user"), bot=x.get("subtype") == "bot_message")))
    for line in open(d / "connectors/gmail/messages.jsonl", encoding="utf-8"):
        x = json.loads(line)
        units.append(Unit(x["id"], x["id"], "email", dt(x["date"]), x["body"], x["from"],
                          f"email {x['subject']} to {' '.join(x['to'])} cc {' '.join(x['cc'])}",
                          meta=dict(thread=x.get("thread_id"), subject=x["subject"])))
    for line in open(d / "connectors/google_calendar/events.jsonl", encoding="utf-8"):
        x = json.loads(line)
        st, en = x["start"], x["end"]
        when = f"{st.get('dateTime') or st.get('date')} to {en.get('dateTime') or en.get('date')}"
        att = ", ".join(a["email"] for a in x.get("attendees", []))
        body = f"{x['summary']} | {when} | {x.get('location') or ''} | attendees {att} | {x.get('description') or ''} | status {x['status']}"
        units.append(Unit(x["id"], x["id"], "calendar", dt(x["updated"]), body, x.get("organizer", ""),
                          "calendar " + x["summary"], meta=dict(cancelled=x["status"] == "cancelled",
                                                                start=st.get("dateTime") or st.get("date"))))
    for f in sorted((d / "connectors/codex/sessions").glob("*.jsonl")):
        ev = [json.loads(l) for l in open(f, encoding="utf-8")]
        meta, body = ev[0], ev[1:]
        text = "\n".join(f"{e.get('role', e.get('tool', e['type']))}: {e.get('content') or e.get('input', '')}" for e in body)
        units.append(Unit(meta["id"], meta["id"], "codex", dt(body[-1]["timestamp"] if body else meta["started_at"]),
                          text, "Alex Rivera", f"codex {meta.get('repo')}"))
    for c in json.load(open(d / "connectors/chatgpt/conversations.json", encoding="utf-8")):
        for i, m in enumerate(c["messages"]):
            units.append(Unit(m["id"], c["id"], "chatgpt", dt(m["create_time"]), m["content"],
                              "Alex Rivera" if m["role"] == "user" else "ChatGPT", f"chatgpt {c['title']} {m['role']}", seq=i))
    units.sort(key=lambda u: u.time)
    return units, deleted, edits


def visible(units, deleted, edits, as_of):
    """Units that exist at as_of: delivered, not deleted, edits applied (new text replaces old)."""
    out = []
    for u in units:
        if u.time > as_of or (u.id in deleted and deleted[u.id] <= as_of):
            continue
        newer = [t for tm, t in edits.get(u.id, []) if tm <= as_of]
        if newer:
            u = Unit(**{**u.__dict__, "text": newer[-1], "meta": {**u.meta, "edited": True}})
        out.append(u)
    return out
