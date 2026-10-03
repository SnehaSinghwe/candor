# Candor take-home: time-aware memory + TextOS

Run everything: `./run_all.sh` (Python 3.10+). Core needs only the standard library; Gemini needs `pip install -r requirements.txt`. Outputs land in `out/`.

## LLM provider
Google Gemini, via the official `google-genai` SDK. Default model `gemini-3.5-flash-lite`. The LLM only writes the final answer from
already-retrieved evidence; BM25 retrieval, time filtering and safety filters are unchanged and need no key.
(Migrated from an earlier Anthropic-based writer; nothing in the project requires Anthropic now.)

### Environment variables
| variable | meaning |
|---|---|
| `GEMINI_API_KEY` | your Gemini API key (never commit it) |
| `GEMINI_MODEL` | model name, default `gemini-3.5-flash-lite` (model access varies by account) |
| `CANDOR_USER_NAME` | your name, used as the sign-off in written messages (set this in live mode; otherwise the sample user's name is used) |
| `CANDOR_TZ` | your timezone for the assistant, e.g. `Asia/Kolkata` (default `America/Los_Angeles`) |

### Setup
```
pip install -r requirements.txt        # only needed for the Gemini path
cp .env.example .env                   # then edit .env locally and fill in GEMINI_API_KEY=...
```
`.env` is git-ignored. `.env.example` has no secret.

### Running
```
./run_all.sh                                   # train questions + actions + scorers, outputs in out/
./run_all.sh questions.jsonl answers.jsonl     # hidden/other questions
python3 assistant.py                           # text assistant (dry-run)
```

### macOS / Linux / Windows
Works on macOS (Intel and Apple Silicon), Linux and Windows (Git Bash). All paths are relative and all file I/O is explicit UTF-8.
- The core pipeline (retrieval, actions, scorers, no-key fallback) runs on Python 3.9 or newer, which includes the macOS system Python.
- The Gemini SDK `google-genai` needs **Python 3.10+**. On a Mac with only the system Python 3.9, install a newer one first (`brew install python@3.12`).
- Recommended on any OS:
```
python3.12 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # edit and add GEMINI_API_KEY
bash run_all.sh
```
- `.env` is loaded by the Python code, so `python3 run.py ...` and `bash run_all.sh` behave the same.
- Verified on Python 3.9, 3.10, 3.12 and 3.13 (core pipeline, no-key mode: byte-identical memory output on each). Not run on a physical Mac.

### No-key mode
If `GEMINI_API_KEY` is missing, or `google-genai` is not installed, or a Gemini call fails (network, bad key, rate limit, malformed
output), the pipeline does not crash. It prints a `[candor] Gemini ...` line to stderr and uses the extractive fallback for that
question. Retrieval and actions are unaffected. Output JSONL schema is identical in both modes.


## Architecture
1. **Ingest** (`candor/ingest.py`): every source becomes a `Unit` with the most specific citable id (meeting segment, Slack message,
   email, dictation, calendar event, Codex session, ChatGPT message), a delivery time, speaker and label.
2. **Time model**: at query time the corpus is rebuilt as of `as_of`: later units dropped, deleted Slack messages dropped,
   edits replace the old text from the edit time, calendar events exist from `updated`. Index cached per visible set.
3. **Retrieval** (`candor/retrieve.py`): stdlib BM25 over three fields (own text, neighbouring segments / thread parent, metadata
   such as speaker, title, date), prefix stemming, date normalisation (`Sep 30` = `9/30` = `2026-09-30`), small intent synonyms
   ("why" pulls cause words, "sign" pulls proposal/review). Dictation records are linked to their sent email/Slack twin so evidence is shared.
4. **Rerank** (`candor/memory.py`): recency boost for "current/still/did" questions, max 3 hits per record in the head for source diversity,
   source-intent boost ("what did I dictate"), and a two-hop date lookup for "the day I fly to Denver" (anchor record, then its date, then everything that day).
5. **Safety** (`candor/safety.py`): secrets regex-redacted from every output, instruction-like lines in data never retrieved or shown,
   abstain when the question contains rare words that appear nowhere in visible memory.
6. **Answer writer**: with a key, Gemini sees the top 12 records (cleaned, wrapped as untrusted data) with time, source and speaker flags, a strict prompt
   (as_of cutoff, newest wins, who-said-what, conflicts, abstain allowed) and JSON output; cited ids are validated against the retrieved set. Without a key, an extractive fallback.
7. **Actions / assistant** (`candor/agent.py`, `candor/actions.py`, `candor/executor.py`, `candor/voice.py`, `assistant.py`): an LLM-first planner.
   Gemini gets a grounded context (time and timezone, people with Slack ids and emails, channels, the calendar as of `as_of`,
   top memory evidence, chat history) and returns tool calls as JSON, so any wording works and follow-ups ("Patel") work.
   A **code validator** sits between the model and the result: ids and emails must exist, a recipient or attendee must be named by
   the user (blocks prompt-injected recipients), times need a UTC offset, argument-name variants are normalised, secrets are scrubbed,
   and destructive wording (delete, cancel, remove, wipe...) always becomes one `confirm`. A rejected plan gets one repair round-trip
   with the exact errors, then the rule-based planner answers. Extra tool `gmail.draft` writes a draft without sending.
   Modes: text, `--voice` (mic, optional `sounddevice`, Gemini speech-to-text), `--audio file.wav`, `--execute` (asks "run it?", then
   launches apps and writes reminders, `.ics` events and a message outbox locally; Slack and Gmail are queued locally, never sent, unless you
   use `--live`). Dry-run output matches the brief's schema.
   `--live` is an optional extension that makes real Gmail, Google Calendar and Slack calls (see "Live mode" below and `LIVE.md`).

## Key decisions
- **Plain code for anything that must be guaranteed, the LLM only for language.** Time filtering, retrieval, secret redaction, instruction-stripping and the action validator are deterministic Python. Gemini writes the final answer and proposes tool calls, but never decides what the user is allowed to see or what is allowed to run.
- **Rebuild the corpus "as of" each question** instead of filtering after retrieval, so later, edited or deleted records cannot leak into ranking or answers.
- **Stdlib BM25, no vector database.** The dataset is small, results are reproducible byte-for-byte, and the core pipeline needs no key and no network. Cost: weak on questions that share no words with the answer (see below).
- **Always degrade, never crash.** With no key, a bad model name or an API error, the pipeline prints a `[candor] Gemini ...` line and falls back to an extractive answer / rule-based planner, with the same output schema.
- **LLM-first action planner with a validator.** An earlier rule-based planner was brittle on reworded commands, so Gemini plans and code validates (named recipients only, real ids, timezone-aware times, destructive actions always confirmed).
- **Dry-run by default.** Nothing leaves the machine unless you pass `--execute` or `--live`, and each action still asks `run it? [y/N]`.

## Results (train)
| | result |
|---|---|
| Retrieval (top 10 has everything, nothing forbidden) | **96%** (26/27), top-5 exact passage 84% (whole record 88%), MRR 0.69, 0 forbidden records |
| Answers, no-key fallback (measured) | strict 40.7%, lenient 48.1% (judge=none; extractive sentence picker) |
| Answers with Gemini `gemini-3.5-flash-lite` (measured, 27 train questions, `--judge none`, latest run) | strict **85.2%**, lenient 92.6%, 2 unverified, 0 hard failures; citations recall 0.84, precision 0.93. An earlier run scored strict 92.6% / lenient 100%; free-tier Gemini output varies between runs |
| Actions train, rule-based planner (measured, no key) | 12/12 (100%), arg accuracy 100% |
| Actions train, with a key (Gemini planner, rule-based fallback) | 12/12 (100%), arg accuracy 100%. **Caveat:** in this run most Gemini plans were rejected by the validator and the rule-based planner produced the answer, so this is mainly the fallback's score, not Gemini's (see below) |
| Actions, 20 reworded probes (`evals/actions_probe.jsonl`, my own, written by me and tuned against the rule planner, so not independent evidence), same run and same caveat | 20/20 |
| Actions, my 6 extra held-out cases | 6/6 |

## What didn't work / known limits
- **Gemini score is deterministic checks only** (`--judge none`). Two answers (MEM-TR-01, MEM-TR-13) are "unverified" because they also mention the older value; no LLM judge was run. The numbers above were measured on the author's machine with a real key; the extractive no-key fallback scores 40.7% strict on the same set (it was 33.3% before I added sentence selection and newest-first ranking).
- **Model availability varies by account.** In testing `gemini-2.5-flash` and `gemini-3.1-flash` returned 404 and `gemini-3.8-flash` hit a quota limit (429); `gemini-3.5-flash-lite` worked. If a grader's key lacks the model, the pipeline prints a `[candor] Gemini call failed` line and uses the fallback. Set `GEMINI_MODEL` to change it.
- **Gemini planner vs validator (found late, fixed after the outputs were generated).** Debug output showed Gemini often returned action fields beside `"type"` instead of nested under `"args"`, so the validator rejected the plan and the rule-based planner answered. `candor/agent.py` accepts both shapes in a later version, but the Gemini planner was not rescored with it, so the committed action outputs are mostly rule-based. The planner also adds the user as a calendar attendee, which was rejected until then.
- **Free-tier rate limits.** Gemini 429 quota errors made the first full runs fall back to the extractive answer path. Calls now retry with backoff and are paced; if quota stays exhausted, the pipeline falls back and prints a `[candor] Gemini call failed` line. Rerun `bash run_all.sh` if you see these.
- **Windows:** all file reads/writes use explicit UTF-8 (default cp1252 broke Slack ingestion). Run `bash run_all.sh` from Git Bash.
- MEM-TR-21 ("why did the launch slip") fails: the reason is phrased as "geocoding regression" and the question shares no vocabulary with it. A dense or LLM query rewrite would fix it.
- My first version put whole-meeting dates in the query and the planning meeting dominated; date tokens are now down-weighted.
- Abstention is crude (unseen words only). MEM-TR-16 (Harbor + SOC 2) is not abstained offline because "SOC" appears elsewhere.
- The rule-based planner is brittle (about 3 of 14 reworded probe commands were right) and was written against 12 train cases plus 6 of mine. That is why the Gemini planner is primary. `python3 tests_agent.py` tests the validator, repair loop, fallbacks and multi-turn plumbing with a FAKED model; it does not measure Gemini's intelligence.
- Retrieval tuning used the train questions, so expect the hidden score to be lower.

## Reproducing the numbers
The `out/` folder is regenerated by `bash run_all.sh` and is **only meaningful for the model that produced it**: with no working `GEMINI_API_KEY` it contains the weaker extractive fallback (about 41% strict). The Gemini result (85.2% strict, 92.6% lenient, citation recall 0.84, precision 0.93 on train, `--judge none`) comes from a run with `gemini-3.5-flash-lite`. `out/results_summary.txt` records what the last run measured.

## Live mode (optional extension, not needed for grading)
`python3 assistant.py --live` turns the assistant into a real agent: Gmail send/draft, Google Calendar create/update and reminders, and Slack posts, each after `run it? [y/N]`. Every user sets up their own Google Cloud project and OAuth `credentials.json`; see `LIVE.md` and `requirements-live.txt`. "me" resolves to the signed-in Gmail address, addresses you type literally are allowed, sample-data addresses are refused, and `--live --safe` redirects all mail to `CANDOR_LIVE_TO`. `credentials.json`, `token.json` and `.env` are git-ignored. The memory pipeline still answers from the bundled sample dataset, not your real inbox.

## Extra files
`doctor.py` (setup and key check), `tests_agent.py` (offline tests with a faked model), `DEMO.md` (5-minute demo script), `LIVE.md` and `candor/live.py` (optional live mode).

## Tools and cost
Built with AI coding assistants in chat sessions (including Claude). Runtime LLM: Google Gemini (`gemini-3.5-flash-lite`, `google-genai` SDK) for answers, action planning and speech-to-text. Optional live mode uses the Gmail and Google Calendar APIs (free). Gemini free/low tier used; cost not tracked, no other paid services.