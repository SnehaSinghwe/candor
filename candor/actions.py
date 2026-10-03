"""Dry-run action planner: natural-language command -> list of tool calls. Rule-based (offline, deterministic),
grounded in Slack ids / gmail contacts / calendar events visible at as_of. Destructive -> confirm, ambiguous -> clarify,
question -> memory.ask."""
import json, re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .ingest import dt
from .memory import Memory

import os
TZ = ZoneInfo(os.environ.get("CANDOR_TZ", "America/Los_Angeles"))   # brief: times are America/Los_Angeles
DESTRUCTIVE = re.compile(r"\b(delete|remove|erase|wipe|purge|trash|cancel|drop all|clear (all|my))\b", re.I)
QUESTION = re.compile(r"^(what|when|who|where|why|how|did|do|does|is|are|was|were|which|has|have|can you tell)\b|\?\s*$", re.I)
VERB = r"(?:message|tell|ping|dm|slack|email|mail|remind|book|schedule|move|reschedule|open|thank|notify)"
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
DOW = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class Planner:
    def __init__(self, data_dir="data"):
        d = Path(data_dir)
        self.tz = TZ
        self.mem = Memory(data_dir)
        self.users = [u for u in json.load(open(d / "connectors/slack/users.json", encoding="utf-8")) if u.get("email")]
        self.chans = json.load(open(d / "connectors/slack/channels.json", encoding="utf-8"))
        self.contacts = {u["real_name"]: u["email"] for u in self.users}
        for line in open(d / "connectors/gmail/messages.jsonl", encoding="utf-8"):
            x = json.loads(line)
            for f in [x["from"], *x["to"], *x["cc"]]:
                m = re.match(r"\s*(.+?)\s*<(.+?)>", f)
                if m and not m.group(2).startswith(("no-reply", "digest")):
                    self.contacts.setdefault(m.group(1), m.group(2))
        self.slack_ids = {u["real_name"]: u["id"] for u in self.users}
        self.events = [json.loads(l) for l in open(d / "connectors/google_calendar/events.jsonl", encoding="utf-8")]

    # ---- resolution -------------------------------------------------------------------------------
    def people(self, first):
        f = first.lower()
        return sorted({n for n in self.contacts if n.lower().split()[0] == f and n != "Alex Rivera"})

    def channel(self, text):
        t = text.lower()
        for c in self.chans:
            if not c["is_dm"] and (c["name"] in t or c["name"].replace("-", " ") in t):
                return c
        return None

    def find_event(self, phrase, as_of):
        words = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if w not in ("the", "my", "a", "meeting", "call", "prep")] or \
                re.findall(r"[a-z0-9]+", phrase.lower())
        best, bs = None, 0
        for e in self.events:
            if dt(e["updated"]) > as_of or e["status"] == "cancelled":
                continue
            s = sum(w in e["summary"].lower() for w in words) / max(1, len(words))
            if "prep" in phrase.lower() and "prep" in e["summary"].lower():
                s += 0.5
            if s > bs or (s == bs and best and e["start"].get("dateTime", "") > best["start"].get("dateTime", "") and s > 0
                          and dt(e["start"].get("dateTime") or e["start"]["date"] + "T00:00:00-07:00") >= as_of):
                best, bs = e, s
        return best if bs >= 0.5 else None

    # ---- time -------------------------------------------------------------------------------------
    def parse_time(self, text, as_of, base_date=None):
        t = text.lower()
        now = as_of.astimezone(TZ)
        date = base_date
        if "tomorrow" in t:
            date = (now + timedelta(days=1)).date()
        elif "today" in t or "tonight" in t:
            date = now.date()
        elif m := re.search(r"\b(?:on |the )?(\d{1,2})(?:st|nd|rd|th)\b", t):
            day = int(m.group(1)); y, mo = now.year, now.month
            if day < now.day:
                mo += 1
            date = datetime(y + (mo > 12), (mo - 1) % 12 + 1, day).date()
        elif m := re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})\b", t):
            date = datetime(now.year, MONTHS[m.group(1)], int(m.group(2))).date()
        else:
            for i, d in enumerate(DOW):
                if re.search(rf"\b(next |on )?{d}\b", t):
                    date = (now + timedelta(days=(i - now.weekday() - 1) % 7 + 1)).date(); break
        tm = None
        if m := re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d\d))?\s*(am|pm)\b", t) or re.search(r"\bat\s+(\d{1,2})(?::(\d\d))?()\b", t) \
                or re.search(r"\bto\s+(\d{1,2})(?::(\d\d))?()\s*$", t):
            h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
            if ap == "pm" and h < 12: h += 12
            if ap == "am" and h == 12: h = 0
            if not ap and 1 <= h <= 6: h += 12
            tm = (h, mi)
        return date, tm

    def iso(self, date, tm):
        return datetime(date.year, date.month, date.day, tm[0], tm[1], tzinfo=TZ).isoformat()

    # ---- planning ---------------------------------------------------------------------------------
    NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12}

    def normalise(self, c):
        """Wording variations -> the canonical phrasing the rules know. (The Gemini planner needs none of this.)"""
        c = re.sub(r"^(hey|hi|ok|okay|so|please|can you|could you|would you|can u|i need to|i want to|i'd like to|i would like to|go ahead and|just)[,\s]+", "", c.strip(), flags=re.I)
        c = re.sub(r"^(please|can you|could you|just)[,\s]+", "", c, flags=re.I)
        for w, n in self.NUMW.items():
            c = re.sub(rf"\b{w}\s*(am|pm|o'?clock)\b", rf"{n}\1", c, flags=re.I)
            c = re.sub(rf"\bhalf past {w}\b", f"{n}:30", c, flags=re.I)
        c = re.sub(r"\bhalf past (\d{1,2})\b", r"\1:30", c, flags=re.I)
        c = re.sub(r"\b(\d{1,2})\s*o'?clock\b", r"\1", c, flags=re.I)
        c = re.sub(r"\bnoon\b", "12pm", c, flags=re.I)
        c = re.sub(r"\bmidnight\b", "11:59pm", c, flags=re.I)
        c = re.sub(r"\b(\d{1,2})(?::(\d\d))? in the (afternoon|evening)\b", lambda m: f"{m.group(1)}{':' + m.group(2) if m.group(2) else ''}pm", c, flags=re.I)
        c = re.sub(r"\b(\d{1,2})(?::(\d\d))? in the morning\b", lambda m: f"{m.group(1)}{':' + m.group(2) if m.group(2) else ''}am", c, flags=re.I)
        c = re.sub(r"^(reschedule|move)\s+(.+?)\s+for\s+", r"move \2 to ", c, flags=re.I)
        c = re.sub(r"^(push|shift|bump)\s+(.+?)\s+(?:back |forward )?(to|until)\s+", r"move \2 to ", c, flags=re.I)
        c = re.sub(r"^(drop|send|shoot)\s+(\w+)\s+(?:a |an )?(?:note|message|ping|dm)(?:\s+on slack)?\s*(?:saying|that|:)?\s*", r"message \2 ", c, flags=re.I)
        c = re.sub(r"^send\s+(\w+)\s+a slack(?: message)?\s*(?:saying|that|:)?\s*", r"message \1 ", c, flags=re.I)
        c = re.sub(r"^(?:i )?(?:need to )?tell\s+(?:the\s+)?(.+? channel)\s+(?:that\s+)?(?:we|i)\s+", r"tell the \1 ", c, flags=re.I)
        return c

    def plan(self, command, as_of):
        as_of = dt(as_of)
        c = self.normalise(command)
        if DESTRUCTIVE.search(c):
            return [{"type": "confirm", "args": {"summary": f"This will {c[0].lower() + c[1:]}. It may not be undoable. Proceed?"}}]
        parts = [p for p in re.split(rf"\s+(?:and|then)\s+(?={VERB}\b)", c, flags=re.I) if p.strip()]
        acts = []
        for p in parts:
            acts += self.plan_one(p.strip(" ."), as_of, c)
        return acts

    def plan_one(self, c, as_of, full):
        low = c.lower()
        if m := re.match(r"open\s+(.+)", c, re.I):
            return [{"type": "app.open", "args": {"app": m.group(1).strip()}}]
        if re.match(r"remind", low):
            return self.remind(c, as_of)
        if re.match(r"(book|schedule|set up|create)\b", low) and re.search(r"\b(meeting|minutes?|hour|with|call)\b", low):
            return self.book(c, as_of)
        if re.match(r"(move|reschedule|push|shift)\b", low):
            return self.move(c, as_of)
        if re.match(r"(draft|write|compose)\b.*\b(email|mail)\b", low) or re.match(r"(send|write)\s+(an?\s+)?(email|mail)\b", low) \
                or re.match(r"(email|mail)\s*$", low):
            return [{"type": "clarify", "args": {"question": "Who is the email for, and what should it say? (Drafting text needs the Gemini key; add GEMINI_API_KEY.)"}}]
        if re.match(r"(email|mail)\b", low):
            return self.email(c, as_of, full)
        if re.match(r"(message|tell|ping|dm|slack|thank|notify|let)\b", low):
            return self.slack(c, as_of, full)
        if QUESTION.search(c) or re.match(r"(show|find|look up|check)\b", low):
            return [{"type": "memory.ask", "args": {"question": c.rstrip("?") + "?"}}]
        return [{"type": "clarify", "args": {"question": "What would you like me to do? I could message, email, schedule, remind or look something up."}}]

    def remind(self, c, as_of):
        text = re.sub(r"^remind me\s*(to\s+)?", "", c, flags=re.I)
        m = re.search(r"(?:an? |\d+ )?(hour|minutes?|min|day)s?\s+before\s+(?:the\s+)?(.+?)(?:\s+to\s+(.+))?$", text, re.I)
        if m:
            n = re.match(r"(\d+)", text[m.start():]); n = int(n.group(1)) if n else 1
            unit = m.group(1).lower()
            ev = self.find_event(m.group(2), as_of)
            body = m.group(3) or text[:m.start()].strip()
            if ev:
                start = dt(ev["start"]["dateTime"]).astimezone(TZ)
                due = start - (timedelta(hours=n) if unit.startswith("hour") else timedelta(days=n) if unit == "day" else timedelta(minutes=n))
                return [{"type": "reminder.create", "args": {"text": body or f"Before {ev['summary']}", "due": due.isoformat()}}]
        m2 = re.search(r"\bin\s+(\d+|an?)\s+(hour|minute|min|day)s?\b", text, re.I)
        if m2:
            n = 1 if m2.group(1).lower() in ("a", "an") else int(m2.group(1)); u = m2.group(2).lower()
            due = as_of.astimezone(TZ) + (timedelta(hours=n) if u == "hour" else timedelta(days=n) if u == "day" else timedelta(minutes=n))
            return [{"type": "reminder.create", "args": {"text": text[:m2.start()].strip(" ,") or text, "due": due.isoformat()}}]
        date, tm = self.parse_time(text, as_of)
        date = date or as_of.astimezone(TZ).date()
        if not tm:
            tm = {"morning": (9, 0), "afternoon": (14, 0), "evening": (18, 0), "tonight": (20, 0)}.get(
                next((w for w in ("morning", "afternoon", "evening", "tonight") if w in text.lower()), ""), (9, 0))
        TIME = r"\b(?:tomorrow(?:\s+(?:morning|afternoon|evening|night))?|today|tonight|this (?:morning|afternoon|evening)|(?:on\s+)?(?:the\s+)?\d{1,2}(?:st|nd|rd|th)|(?:on|next)\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day|at\s+\d{1,2}(?::\d\d)?\s*(?:am|pm)?)\b"
        body = re.sub(r"\s+", " ", re.sub(TIME, " ", text, flags=re.I)).strip(" ,")
        body = re.sub(r"^to\s+", "", body, flags=re.I)
        return [{"type": "reminder.create", "args": {"text": body, "due": self.iso(date, tm)}}]

    def book(self, c, as_of):
        low = c.lower()
        dur = 30
        if m := re.search(r"(\d+)\s*(minute|min|hour|hr)", low):
            dur = int(m.group(1)) * (60 if m.group(2).startswith(("hour", "hr")) else 1)
        att = []
        for m in re.finditer(r"with\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", c):
            ppl = self.people(m.group(1).split()[0])
            att += [self.contacts[p] for p in ppl[:1]]
        date, tm = self.parse_time(c, as_of)
        if not (date and tm):
            return [{"type": "clarify", "args": {"question": "What day and time should I book it?"}}]
        title = re.search(r"about\s+(.+)$", c, re.I)
        title = title.group(1) if title else re.sub(r"^(book|schedule)\s+", "", c, flags=re.I)
        start = datetime(date.year, date.month, date.day, tm[0], tm[1], tzinfo=TZ)
        return [{"type": "calendar.create_event", "args": {"title": title, "start": start.isoformat(),
                                                        "end": (start + timedelta(minutes=dur)).isoformat(), "attendees": att}}]

    def move(self, c, as_of):
        m = re.match(r"(?:move|reschedule|push|shift)\s+(.+?)\s+(?:to|until)\s+(.+)$", c, re.I)
        if not m:
            return [{"type": "clarify", "args": {"question": "Which event, and to when?"}}]
        ev = self.find_event(m.group(1), as_of)
        if not ev:
            return [{"type": "clarify", "args": {"question": f"I couldn't find an event matching '{m.group(1)}'. Which one?"}}]
        s0, e0 = dt(ev["start"]["dateTime"]).astimezone(TZ), dt(ev["end"]["dateTime"]).astimezone(TZ)
        date, tm = self.parse_time(m.group(2), as_of, base_date=s0.date())
        date = date or s0.date()
        if not tm:
            return [{"type": "clarify", "args": {"question": "What time should I move it to?"}}]
        start = datetime(date.year, date.month, date.day, tm[0], tm[1], tzinfo=TZ)
        return [{"type": "calendar.update_event", "args": {"event_id": ev["id"], "start": start.isoformat(),
                                                        "end": (start + (e0 - s0)).isoformat()}}]

    def target(self, name_text):
        m = re.search(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b", name_text)
        return m.group(1) if m else None

    def content_from_memory(self, phrase, as_of):
        """Fetch the fact a command refers to ('the corrected NRR'): search memory, prefer correction-style records,
        return the sentence that carries the number."""
        noun = re.sub(r"^(corrected|latest|updated|new|current)\s+", "", phrase, flags=re.I)
        ix, top = self.mem.retrieve(f"{noun} {phrase} fix wrong actually not double-counted", as_of, k=6)
        for i in top:
            u = ix.units[i]
            for sent in re.split(r"(?<=[.!?])\s+|\n+", u.text):
                if re.search(r"\d", sent) and any(w.lower() in sent.lower() for w in noun.split()):
                    return sent.strip()[:240]
        return phrase
    def slack(self, c, as_of, full):
        m = re.match(r"(?:message|tell|ping|dm|slack|let|notify|post to)\s+(?:the\s+)?(.+?\s+channel)\s*(?:that\s+|:\s*|,\s*)?(.+)$", c, re.I) or \
            re.match(r"(?:message|tell|ping|dm|slack|let|notify)\s+(?:the\s+)?(.+?)(?:\s+on slack)?(?:\s+(?:that|about|to)\s+|:\s*|,\s*)(.+)$", c, re.I)
        thanks = re.match(r"thank\s+(\w+)(?:\s+on slack)?", c, re.I)
        if not m and not thanks:   # "message Ben the NRR fix is done": a known first name followed directly by the text
            m3 = re.match(r"(?:message|tell|ping|dm|slack|let|notify)\s+(\w+)\s+(.+)$", c, re.I)
            if m3 and self.people(m3.group(1)):
                m = m3
        if thanks:
            who, body = thanks.group(1), "Thank you!"
            if m2 := re.search(r"for\s+(.+)$", c, re.I): body = f"Thanks for {m2.group(1)}!"
        elif m:
            who, body = m.group(1), m.group(2)
        else:
            return [{"type": "clarify", "args": {"question": "Who should I message, and what should it say?"}}]
        ch = self.channel(who + " " + c) if re.search(r"channel|#", c, re.I) or self.channel(who) else None
        body = re.sub(r"^(?:that\s+)?", "", body).strip()
        body = body[0].upper() + body[1:] + ("" if body.endswith((".", "!", "?")) else ".")
        if ch:
            return [{"type": "slack.send_message", "args": {"to": ch["id"], "text": body}}]
        first = who.split()[0]
        cands = [p for p in self.people(first) if p in self.slack_ids]
        allc = self.people(first)
        if len(allc) > 1 and not (len(cands) == 1 and re.search(r"on slack", full, re.I)):
            return [{"type": "clarify", "args": {"question": f"Which {first}: " + " or ".join(allc) + "?"}}]
        if not cands:
            return [{"type": "clarify", "args": {"question": f"I couldn't find {first} on Slack. Who do you mean?"}}]
        return [{"type": "slack.send_message", "args": {"to": self.slack_ids[cands[0]], "text": body}}]

    def email(self, c, as_of, full):
        m = re.match(r"(?:email|mail)\s+((?-i:[A-Z][a-z]+(?:\s[A-Z][a-z]+)?))\s*(?:,|:)?\s*(.*)$", c, re.I)
        if not m:
            return [{"type": "clarify", "args": {"question": "Who should I email?"}}]
        name, rest = m.group(1), m.group(2)
        ppl = self.people(name.split()[0]) if len(name.split()) == 1 else [n for n in self.contacts if n.lower() == name.lower()]
        if not ppl:
            return [{"type": "clarify", "args": {"question": f"I don't have an email for {name}. What's the address?"}}]
        if len(ppl) > 1:
            return [{"type": "clarify", "args": {"question": f"Which {name}: " + " or ".join(ppl) + "?"}}]
        person = ppl[0]
        rest = re.sub(r"^(and\s+)?", "", rest).strip()
        if m2 := re.match(r"ask (?:if|whether) (?:she|he|they)(?:'s| has| have)?\s*(?:had|got)?\s*(.*)", rest, re.I):
            body = f"Have you had {m2.group(1)}?".replace("Have you had a chance", "Have you had a chance")
            subj = "Quick follow-up"
        elif m2 := re.match(r"(?:the\s+)?(corrected .+|latest .+|updated .+)", rest, re.I):
            val = self.content_from_memory(m2.group(1), as_of)
            body = f"Here is the {m2.group(1)}: {val}"
            subj = m2.group(1).capitalize()
        else:
            rest = re.sub(r"^(that|about|to say|saying)\s+", "", rest, flags=re.I)
            body, subj = rest[:1].upper() + rest[1:], (rest[:50] or "Note")
        body = f"Hi {person.split()[0]},\n\n{body}\n\nThanks,\nAlex"
        return [{"type": "gmail.send", "args": {"to": [self.contacts[person]], "cc": [], "subject": subj, "body": body}}]


def run(path, out, data_dir="data"):
    pl = Planner(data_dir)
    with open(out, "w", encoding="utf-8") as f:
        for line in open(path, encoding="utf-8"):
            if line.strip():
                q = json.loads(line)
                f.write(json.dumps({"id": q["id"], "actions": pl.plan(q["command"], q["as_of"])}, ensure_ascii=False) + "\n")
