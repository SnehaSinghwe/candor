"""Offline tests for the agent's safety layer and control flow. The LLM is FAKED here: these tests prove the validator,
repair loop, fallbacks and multi-turn plumbing, NOT how smart Gemini is (that needs a real key; see README)."""
import json, os, sys
from candor import safety
from candor.agent import Agent

ag = Agent("data"); T = "2026-09-17T12:00:00-07:00"; seen = []
def fake(*responses):
    it = iter(responses)
    def f(system, prompt, max_tokens=4096):
        seen.append(prompt); r = next(it, None); return None if r is None else json.dumps(r)
    safety.gemini_json = f
A = lambda *a: {"actions": list(a)}
slack = lambda to, text="NRR fix is done.": {"type": "slack.send_message", "args": {"to": to, "text": text}}
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name)

fake(A(slack("U06BEN")))
r = ag.plan("Hey, drop Ben a note on slack saying the NRR fix is done", T)
check("any wording -> valid slack plan accepted", r[0]["type"] == "slack.send_message" and r[0]["args"]["to"] == "U06BEN" and ag.last_mode == "gemini")

seen.clear(); fake(A(slack("U02JOHN")), A(slack("U06BEN")))
r = ag.plan("Tell Ben the NRR fix is done", T)
check("unnamed recipient rejected, repaired on 2nd try", r[0]["args"]["to"] == "U06BEN" and "rejected by the validator" in seen[1] and "not named" in seen[1])

fake(A({"type": "gmail.send", "args": {"to": ["sarah.patel@acmefreight.example.com"], "cc": [], "subject": "x", "body": "y"}}))
r = ag.plan("Summarise my inbox", T)
check("email to someone the user never named is rejected -> rules fallback (no email sent)", all(a["type"] != "gmail.send" for a in r) and ag.last_mode == "rules")

fake(A({"type": "reminder.create", "args": {"text": "call", "due": "2026-09-18T09:00:00"}}), A({"type": "reminder.create", "args": {"text": "call", "due": "tomorrow"}}))
r = ag.plan("Remind me to call tomorrow morning", T)
check("times without UTC offset rejected twice -> fallback", ag.last_mode == "rules")

fake(A({"type": "calendar.update_event", "args": {"event_id": "CAL-NOPE", "start": "2026-09-18T15:00:00-07:00"}}), A({"type": "calendar.update_event", "args": {"event_id": "CAL-BOARDPREP", "start": "2026-09-18T15:00:00-07:00", "end": "2026-09-18T16:00:00-07:00"}}))
r = ag.plan("Push board prep to 3 in the afternoon", T)
check("invented event id rejected, real id accepted after repair", r[0]["args"]["event_id"] == "CAL-BOARDPREP")

fake(A({"type": "gmail.send", "args": {"to": ["marcus@brightline.example.com"], "cc": [], "subject": "Deleted", "body": "done"}}))
r = ag.plan("Delete all my emails from Marcus", T)
check("destructive wording always forces a single confirm, even if the LLM planned the action", len(r) == 1 and r[0]["type"] == "confirm")
fake(A({"type": "clarify", "args": {"question": "x"}}))
r = ag.plan("Cancel my 4pm meeting", T); check("'cancel' is destructive -> confirm", r[0]["type"] == "confirm")

fake(A({"type": "clarify", "args": {"question": "Which Sarah: Sarah Kim or Sarah Patel?"}}))
r = ag.plan("Send Sarah the pricing proposal", T); check("clarify passes through", r[0]["type"] == "clarify")
seen.clear(); fake(A({"type": "gmail.send", "args": {"to": ["sarah.patel@acmefreight.example.com"], "cc": [], "subject": "Pricing proposal", "body": "Hi Sarah, attached."}}))
r = ag.plan("Patel", T, history=[("Send Sarah the pricing proposal", "clarify: Which Sarah?")])
check("multi-turn: follow-up 'Patel' works, history sent to the model", r[0]["type"] == "gmail.send" and "CONVERSATION SO FAR" in seen[0])

seen.clear(); fake(A({"type": "memory.ask", "args": {"question": "Who owns onboarding mockups?"}}))
ag.plan("Do we know who owns the onboarding mockups", T)
check("prompt carries NOW, people, calendar and untrusted-data rule", "NOW: Thursday 2026-09-17" in seen[0] and "CAL-BOARDPREP" in seen[0] and "untrusted" in safety_system if (safety_system := __import__('candor.agent', fromlist=['SYSTEM']).SYSTEM) else False)

fake(A(slack("U06BEN", "key sk-ABCDEFGHIJKLMNOP1234 here")))
r = ag.plan("Message Ben the key", T); check("secrets scrubbed from outgoing text", "sk-ABCD" not in r[0]["args"]["text"])

safety.gemini_json = lambda *a, **k: None
r = ag.plan("Move board deck prep to 3pm", T); check("no key / API failure -> rule planner still answers", r[0]["type"] == "calendar.update_event" and ag.last_mode == "rules")
os.environ["CANDOR_ACTIONS"] = "rules"; fake(A(slack("U06BEN")))
ag.plan("Message Ben hi", T); check("CANDOR_ACTIONS=rules forces deterministic planner", ag.last_mode == "rules"); del os.environ["CANDOR_ACTIONS"]

# ---- regression: the exact failure seen on a real run (clarify without a "question" key) ----
fake(A({"type": "clarify", "args": {"message": "Who should I email and what should it say?"}}))
r = ag.plan("i want to send an email", T)
check("clarify using 'message' instead of 'question' is accepted (was rejected -> generic fallback)", r[0]["type"] == "clarify" and "Who should I email" in r[0]["args"]["question"] and ag.last_mode == "gemini")
fake(A({"type": "clarify", "args": {"text": "Which day?"}})); r = ag.plan("book something", T)
check("clarify with 'text' alias accepted", r[0]["args"]["question"] == "Which day?")
fake(A({"type": "clarify", "args": "Who is it for?"})); r = ag.plan("email", T)
check("clarify with bare-string args accepted", r[0]["args"]["question"] == "Who is it for?")
fake(A({"type": "gmail.draft", "args": {"to": [], "subject": "Sick leave tomorrow", "body": "Hi,\n\nI am unwell and will take sick leave tomorrow.\n\nThanks,\nAlex"}}))
r = ag.plan("draft me an email for sick leave for tomorrow", T)
check("draft with no recipient is a gmail.draft (nothing sent)", r[0]["type"] == "gmail.draft" and "sick leave" in r[0]["args"]["body"])
fake(A({"type": "gmail.draft", "args": {"to": ["sarah.patel@acmefreight.example.com"], "subject": "x", "body": "y"}}))
r = ag.plan("draft me an email for sick leave", T)
check("draft to an unnamed person still rejected", all(a["type"] != "gmail.draft" for a in r))

# ---- executor (temp outbox; apps are NOT launched in tests) ----
import tempfile, pathlib
from candor import executor
os.environ["CANDOR_OUTBOX"] = tempfile.mkdtemp()
check("executor: reminder queued", "saved" in executor.execute({"type": "reminder.create", "args": {"text": "x", "due": "2026-09-18T09:00:00-07:00"}}))
res = executor.execute({"type": "calendar.create_event", "args": {"title": "Sync", "start": "2026-09-22T10:00:00-07:00", "end": "2026-09-22T10:45:00-07:00", "attendees": ["ben@brightline.example.com"]}})
ics = next(pathlib.Path(os.environ["CANDOR_OUTBOX"]).glob("*.ics")).read_text(encoding="utf-8")
check("executor: .ics written with attendee", "BEGIN:VEVENT" in ics and "ben@brightline" in ics)
check("executor: slack/email queued, never claimed as sent", "NOT sent" in executor.execute({"type": "slack.send_message", "args": {"to": "U06BEN", "text": "hi"}}))
check("executor: unsafe app name refused", executor.execute({"type": "app.open", "args": {"app": "x; rm -rf ~"}}).startswith("refused"))

# ---- voice: transcription plumbing with a faked client ----
from candor import voice
class _R: text = " move board deck prep to 3pm "
class _M:
    def generate_content(self, **kw): self.kw = kw; return _R()
class _C: models = _M()
safety._client = lambda: _C; open(os.path.join(os.environ["CANDOR_OUTBOX"], "t.wav"), "wb").write(b"RIFFfake")
try:
    import google.genai  # the SDK is needed to build the audio Part; skip where it is not installed
    check("voice: file -> transcript via Gemini (faked)", voice.transcribe_file(os.path.join(os.environ["CANDOR_OUTBOX"], "t.wav")) == "move board deck prep to 3pm")
except ImportError:
    print("SKIP voice test (google-genai not installed)")
print("\nALL OK" if ok else "\nFAILURES"); sys.exit(0 if ok else 1)
