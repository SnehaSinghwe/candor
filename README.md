# Candor — Time-Aware Memory + TextOS

A take-home implementation of a **time-aware memory system and TextOS action planner**. Candor ingests heterogeneous records, reconstructs memory as of a query time, retrieves and reranks relevant evidence, generates grounded answers with Google Gemini, and converts natural-language action requests into safe dry-run plans.

## LLM provider
Google Gemini, via the official `google-genai` SDK. Default model `gemini-3.5-flash-lite`. The LLM only writes the final answer from
already-retrieved evidence; BM25 retrieval, time filtering and safety filters are unchanged and need no key.
(Migrated from an earlier Anthropic-based writer; nothing in the project requires Anthropic now.)

### Environment variables
| variable | meaning |
|---|---|
| `GEMINI_API_KEY` | your Gemini API key (never commit it) |
| `GEMINI_MODEL` | model name, default `gemini-3.5-flash-lite` (model access varies by account) |

| Variable         | Purpose                                                |
| ---------------- | ------------------------------------------------------ |
| `GEMINI_API_KEY` | Gemini API key; keep this local and never commit it    |
| `GEMINI_MODEL`   | Gemini model name; defaults to `gemini-3.5-flash-lite` |

Model availability can vary by Google account/project.

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

Candor is designed to remain runnable when Gemini is unavailable.

If:

* `GEMINI_API_KEY` is missing,
* `google-genai` is not installed,
* the selected model is unavailable,
* a request fails,
* a rate limit is reached, or
* Gemini returns malformed output,

the affected question falls back to the deterministic extractive answer path rather than crashing the pipeline.

Retrieval and action planning continue to work independently.

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
   launches apps and writes reminders, `.ics` events and a message outbox locally; Slack and Gmail are queued, never sent, since no
   credentials exist). Dry-run output matches the brief's schema.

## Results (train)
| | result |
|---|---|
| Retrieval (top 10 has everything, nothing forbidden) | **96%** (26/27), top-5 exact 76%, MRR 0.69, 0 forbidden records |
| Answers, no-key fallback (measured) | strict 40.7%, lenient 48.1% (judge=none; extractive sentence picker) |
| Answers with Gemini `gemini-3.5-flash-lite` (measured, 27 train questions, `--judge none`) | strict **92.6%**, lenient 100%, 2 unverified, 0 hard failures; citations recall 0.84, precision 0.93; both expected abstentions correct |
| Actions train, rule-based planner (measured, no key) | 12/12 (100%), arg accuracy 100% |
| Actions train, Gemini planner | **not measured by the author yet**: run `bash run_all.sh` with a key (writes `out/results_summary.txt`) |
| Actions, 20 reworded probes (`evals/actions_probe.jsonl`, my own, written by me and tuned against the rule planner, so not independent evidence), rule planner only | 20/20; Gemini planner not measured yet |
| Actions, my 6 extra held-out cases | 6/6 |

## What didn't work / known limits
- **Gemini score is deterministic checks only** (`--judge none`). Two answers (MEM-TR-01, MEM-TR-13) are "unverified" because they also mention the older value; no LLM judge was run. The numbers above were measured on the author's machine with a real key; the extractive no-key fallback scores 40.7% strict on the same set (it was 33.3% before I added sentence selection and newest-first ranking).
- **Model availability varies by account.** In testing `gemini-2.5-flash` and `gemini-3.1-flash` returned 404 and `gemini-3.8-flash` hit a quota limit (429); `gemini-3.5-flash-lite` worked. If a grader's key lacks the model, the pipeline prints a `[candor] Gemini call failed` line and uses the fallback. Set `GEMINI_MODEL` to change it.
- **Windows:** all file reads/writes use explicit UTF-8 (default cp1252 broke Slack ingestion). Run `bash run_all.sh` from Git Bash.
- MEM-TR-21 ("why did the launch slip") fails: the reason is phrased as "geocoding regression" and the question shares no vocabulary with it. A dense or LLM query rewrite would fix it.
- My first version put whole-meeting dates in the query and the planning meeting dominated; date tokens are now down-weighted.
- Abstention is crude (unseen words only). MEM-TR-16 (Harbor + SOC 2) is not abstained offline because "SOC" appears elsewhere.
- The rule-based planner is brittle (about 3 of 14 reworded probe commands were right) and was written against 12 train cases plus 6 of mine. That is why the Gemini planner is primary. `python3 tests_agent.py` tests the validator, repair loop, fallbacks and multi-turn plumbing with a FAKED model; it does not measure Gemini's intelligence.
- Retrieval tuning used the train questions, so expect the hidden score to be lower.

## Reproducing the numbers
The `out/` folder is regenerated by `bash run_all.sh` and is **only meaningful for the model that produced it**: with no working `GEMINI_API_KEY` it contains the weaker extractive fallback (about 41% strict). The Gemini result (92.6% strict, citation recall 0.84, precision 0.93 on train, `--judge none`) comes from a run with `gemini-3.5-flash-lite`. `out/results_summary.txt` records what the last run measured.

## Extra files
`doctor.py` (setup and key check), `tests_agent.py` (offline tests with a faked model), `DEMO.md` (5-minute demo script), `SUBMISSION.md` (reply template).

## Tools and cost
Built with an AI coding assistant in a chat session. Answer writer: Google Gemini (`gemini-3.5-flash-lite`, `google-genai` SDK). Gemini free/low tier used; cost not tracked.
