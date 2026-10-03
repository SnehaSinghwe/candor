"""LIVE backends for assistant.py --live. Real network calls; only reached after the user answers "y" to "run it?".

  gmail.send / gmail.draft       -> Gmail API   (users.messages.send / users.drafts.create)
  calendar.create_event / update -> Google Calendar API (events.insert / events.patch)
  reminder.create                -> Google Calendar event with a popup + email reminder at the due time
  slack.send_message             -> Slack Web API chat.postMessage (needs SLACK_BOT_TOKEN)
  app.open                       -> already real in executor.py

One-time setup is in LIVE.md. Env vars (all optional):
  CANDOR_LIVE_TO         redirect EVERY outgoing email to this address (safe testing; the original recipients are noted in the body)
  CANDOR_GOOGLE_CREDENTIALS / CANDOR_GOOGLE_TOKEN   paths, default credentials.json / token.json
  CANDOR_SLACK_CHANNEL   post every Slack message to this real channel id (the sample data's ids are fake)
"""
import base64, json, os, urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.compose",      # send + drafts, nothing that reads your inbox
          "https://www.googleapis.com/auth/calendar.events"]
FAKE_DOMAIN = "example.com"       # the sample data uses *.example.com: mail there can only bounce
_svc = {}


def _creds():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    cred_path = Path(os.environ.get("CANDOR_GOOGLE_CREDENTIALS", "credentials.json"))
    tok_path = Path(os.environ.get("CANDOR_GOOGLE_TOKEN", "token.json"))
    creds = Credentials.from_authorized_user_file(str(tok_path), SCOPES) if tok_path.exists() else None
    if creds and not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:          # e.g. the 7-day expiry of tokens for apps still in "Testing"
            creds = None
    if not creds or not creds.valid:
        if not cred_path.exists():
            raise RuntimeError(f"{cred_path} not found. Follow LIVE.md step 1-3 to download your OAuth client file.")
        creds = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES).run_local_server(port=0)
    tok_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _service(name, version):
    if name not in _svc:
        from googleapiclient.discovery import build
        _svc[name] = build(name, version, credentials=_creds(), cache_discovery=False)
    return _svc[name]


def _real(addr):
    return not addr.lower().endswith("." + FAKE_DOMAIN) and not addr.lower().endswith("@" + FAKE_DOMAIN)


def _outbox():
    d = Path(os.environ.get("CANDOR_OUTBOX", "candor_outbox")); d.mkdir(parents=True, exist_ok=True); return d


def _past(iso):
    return datetime.fromisoformat(iso) < datetime.now(timezone.utc) - timedelta(minutes=1)


# ---- Gmail -----------------------------------------------------------------------------------------
def _mime(to, cc, subject, body):
    m = EmailMessage()
    m["To"] = ", ".join(to)
    if cc:
        m["Cc"] = ", ".join(cc)
    m["Subject"] = subject
    m.set_content(body)
    return {"raw": base64.urlsafe_b64encode(m.as_bytes()).decode()}


SELF_SAMPLE = "alex@brightline.example.com"   # the sample data's "me" (Alex Rivera); in live mode "me" is the signed-in Gmail account


def _my_address():
    return _service("gmail", "v1").users().getProfile(userId="me").execute()["emailAddress"]


def _route(a):
    """Apply the safety rules for outgoing mail. Returns (to, cc, body) or raises ValueError."""
    to, cc, body = list(a["to"]), list(a.get("cc") or []), a["body"]
    redirect = os.environ.get("CANDOR_LIVE_TO", "").strip()
    if not redirect and SELF_SAMPLE in [x.lower() for x in to + cc]:   # "email me": sample "me" -> your real Gmail address
        me = _my_address()
        to = [me if x.lower() == SELF_SAMPLE else x for x in to]
        cc = [me if x.lower() == SELF_SAMPLE else x for x in cc]
    if redirect:
        if [x.lower() for x in to + cc] != [SELF_SAMPLE]:   # only add the note when redirecting someone else
            body = f"[TEST MODE: originally to {', '.join(to + cc) or 'nobody'}]\n\n{body}"
        return [redirect], [], body
    fake = [x for x in to + cc if not _real(x)]
    if fake:
        raise ValueError(f"{', '.join(fake)} is a sample-data address and would bounce. Set CANDOR_LIVE_TO=you@gmail.com to test, "
                         "or use a real address.")
    return to, cc, body


def gmail_send(a):
    to, cc, body = _route(a)
    sent = _service("gmail", "v1").users().messages().send(userId="me", body=_mime(to, cc, a["subject"], body)).execute()
    rec = {"id": sent["id"], "thread_id": sent["threadId"], "date": datetime.now(timezone.utc).isoformat(), "from": "me",
           "to": to, "cc": cc, "subject": a["subject"], "body": body, "labels": ["SENT"], "attachments": []}
    with open(_outbox() / "sent.jsonl", "a", encoding="utf-8", newline="\n") as f:   # same shape as data/connectors/gmail/messages.jsonl
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return f"email SENT to {', '.join(to)} (gmail id {sent['id']})"


def gmail_draft(a):
    to, cc, body = _route({**a, "to": a.get("to") or [], "cc": []}) if a.get("to") else ([], [], a["body"])
    d = _service("gmail", "v1").users().drafts().create(userId="me", body={"message": _mime(to, cc, a["subject"], body)}).execute()
    return f"draft saved in Gmail (draft id {d['id']}), nothing sent"


# ---- Calendar --------------------------------------------------------------------------------------
def calendar_create(a):
    if _past(a["start"]):
        return "refused: that start time is already in the past (start with --as-of unset so 'tomorrow' means the real tomorrow)"
    real = [x for x in a.get("attendees", []) if _real(x)]
    skipped = [x for x in a.get("attendees", []) if not _real(x)]
    body = {"summary": a["title"], "start": {"dateTime": a["start"]}, "end": {"dateTime": a["end"]},
            "attendees": [{"email": x} for x in real]}
    if skipped:
        body["description"] = "Sample-data attendees not invited: " + ", ".join(skipped)
    ev = _service("calendar", "v3").events().insert(calendarId="primary", body=body, sendUpdates="all" if real else "none").execute()
    return f"event created: {ev.get('htmlLink')}" + (f" ({len(skipped)} sample attendee(s) skipped)" if skipped else "")


def calendar_update(a):
    patch = {k: a[k] for k in ("title",) if k in a}
    if "title" in patch:
        patch = {"summary": patch["title"]}
    for k in ("start", "end"):
        if k in a:
            patch[k] = {"dateTime": a[k]}
    ev = _service("calendar", "v3").events().patch(calendarId="primary", eventId=a["event_id"], body=patch, sendUpdates="none").execute()
    return f"event updated: {ev.get('htmlLink')}"


def reminder_create(a):
    if _past(a["due"]):
        return "refused: that reminder time is already in the past"
    due = datetime.fromisoformat(a["due"])
    body = {"summary": "Reminder: " + a["text"], "start": {"dateTime": due.isoformat()},
            "end": {"dateTime": (due + timedelta(minutes=10)).isoformat()},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}, {"method": "email", "minutes": 0}]}}
    ev = _service("calendar", "v3").events().insert(calendarId="primary", body=body, sendUpdates="none").execute()
    return f"reminder set for {a['due']} (Google Calendar popup + email): {ev.get('htmlLink')}"


def load_calendar(planner, now):
    """Replace the sample calendar with the real one (next 30 days) so 'move X to 3pm' targets real event ids."""
    items = _service("calendar", "v3").events().list(
        calendarId="primary", timeMin=now.astimezone(timezone.utc).isoformat(), singleEvents=True, orderBy="startTime",
        timeMax=(now + timedelta(days=30)).astimezone(timezone.utc).isoformat(), maxResults=100).execute().get("items", [])
    evs = []
    for e in items:
        if "dateTime" not in e.get("start", {}):
            continue            # skip all-day events
        evs.append({"id": e["id"], "summary": e.get("summary", "(no title)"), "start": e["start"], "end": e["end"],
                    "status": e.get("status", "confirmed"), "updated": e.get("updated", now.isoformat()),
                    "attendees": [{"email": x["email"]} for x in e.get("attendees", []) if "email" in x]})
    planner.events = evs
    return len(evs)


# ---- Slack -----------------------------------------------------------------------------------------
def slack_send(a):
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        return "not sent: set SLACK_BOT_TOKEN (a bot token with chat:write)"
    channel, text = os.environ.get("CANDOR_SLACK_CHANNEL") or a["to"], a["text"]
    if channel != a["to"]:
        text = f"[test mode, originally to {a['to']}] {text}"
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", method="POST",
                                 data=json.dumps({"channel": channel, "text": text}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    r = json.load(urllib.request.urlopen(req, timeout=15))
    return f"slack message posted to {channel}" if r.get("ok") else f"slack error: {r.get('error')} (sample ids like U06BEN don't exist in a real workspace; set CANDOR_SLACK_CHANNEL)"


HANDLERS = {"gmail.send": gmail_send, "gmail.draft": gmail_draft, "calendar.create_event": calendar_create,
            "calendar.update_event": calendar_update, "reminder.create": reminder_create, "slack.send_message": slack_send}


def run(action):
    try:
        return HANDLERS[action["type"]](action["args"])
    except ValueError as e:
        return f"refused: {e}"
    except Exception as e:           # network, auth, quota: report, never crash the REPL
        return f"{action['type']} failed: {type(e).__name__}: {str(e)[:200]}"