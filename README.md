# Candor take-home: time-aware memory + TextOS

Run everything: `./run_all.sh` (Python 3.10+). Core needs only the standard library; Gemini needs `pip install -r requirements.txt`. Outputs land in `out/`.

## LLM provider
Google Gemini, via the official `google-genai` SDK. Default model `gemini-2.5-flash`. The LLM only writes the final answer from
already-retrieved evidence; BM25 retrieval, time filtering and safety filters are unchanged and need no key.
(Migrated from an earlier Anthropic-based writer; nothing in the project requires Anthropic now.)

### Environment variables
| variable | meaning |
|---|---|
| `GEMINI_API_KEY` | your Gemini API key (never commit it) |
| `GEMINI_MODEL` | model name, default `gemini-2.5-flash` |

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
7. **Actions** (`candor/actions.py`): rule-based dry-run planner grounded in Slack ids, email contacts and visible calendar events.
   Destructive means `confirm`, two candidate people means `clarify`, questions become `memory.ask`, compound commands are split and
   values like "the corrected NRR" are fetched from memory.

## Results (train)
| | result |
|---|---|
| Retrieval (top 10 has everything, nothing forbidden) | **96%** (26/27), top-5 exact 76%, MRR 0.69, 0 forbidden records |
| Answers, no-key fallback (measured) | strict 33.3%, lenient 37.0% (judge=none) |
| Answers with Gemini | **not measured**: no Gemini key was available when this was written |
| Actions train | 12/12 (100%), arg accuracy 100% |
| Actions, my 6 extra held-out cases | 6/6 |

## What didn't work / known limits
- **No Gemini key was available while building.** The Gemini call itself has not been run against the real API. Tested only: SDK import and config construction, mocked responses (valid, malformed, abstain, uncited, API exception), env-var handling, and the failure path with a dummy key. Answer scores above are the extractive fallback, not Gemini.
- MEM-TR-21 ("why did the launch slip") fails: the reason is phrased as "geocoding regression" and the question shares no vocabulary with it. A dense or LLM query rewrite would fix it.
- My first version put whole-meeting dates in the query and the planning meeting dominated; date tokens are now down-weighted.
- Abstention is crude (unseen words only). MEM-TR-16 (Harbor + SOC 2) is not abstained offline because "SOC" appears elsewhere.
- Action rules were written against 12 train cases plus 6 of mine; the hidden set will expose gaps. An LLM fallback for unparsed commands is the obvious next step.
- Retrieval tuning used the train questions, so expect the hidden score to be lower.

## Tools and cost
Built with an AI coding assistant in a chat session. Answer writer: Google Gemini (`gemini-2.5-flash`, `google-genai` SDK), not yet run for reported scores. No paid API calls were made while building.
