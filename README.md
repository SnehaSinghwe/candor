# Candor take-home: time-aware memory + TextOS

Run everything: `./run_all.sh` (Python 3.10+, standard library only, no install). Outputs land in `out/`.
Hidden questions: `./run_all.sh questions.jsonl answers.jsonl`. Text assistant: `python3 assistant.py`.
Optional `ANTHROPIC_API_KEY` (see `.env.example`) turns on the LLM answer writer. Retrieval never needs a key.

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
6. **Answer writer**: with a key, an LLM sees the top 12 records with time, source and speaker-identified flags, a strict prompt
   (newest wins, first vs second hand, abstain allowed) and JSON output. Without a key, an extractive fallback.
7. **Actions** (`candor/actions.py`): rule-based dry-run planner grounded in Slack ids, email contacts and visible calendar events.
   Destructive means `confirm`, two candidate people means `clarify`, questions become `memory.ask`, compound commands are split and
   values like "the corrected NRR" are fetched from memory.

## Results (train)
| | result |
|---|---|
| Retrieval (top 10 has everything, nothing forbidden) | **96%** (26/27), top-5 exact 76%, MRR 0.69, 0 forbidden records |
| Answers, rules only, no LLM | strict 33% (extractive fallback; LLM writer not measured, see below) |
| Actions train | 12/12 (100%), arg accuracy 100% |
| Actions, my 6 extra held-out cases | 6/6 |

## What didn't work / known limits
- **No API key was available while building**, so the LLM answer writer and LLM judge are untested. Answer quality above is the weak extractive fallback.
- MEM-TR-21 ("why did the launch slip") fails: the reason is phrased as "geocoding regression" and the question shares no vocabulary with it. A dense or LLM query rewrite would fix it.
- My first version put whole-meeting dates in the query and the planning meeting dominated; date tokens are now down-weighted.
- Abstention is crude (unseen words only). MEM-TR-16 (Harbor + SOC 2) is not abstained offline because "SOC" appears elsewhere.
- Action rules were written against 12 train cases plus 6 of mine; the hidden set will expose gaps. An LLM fallback for unparsed commands is the obvious next step.
- Retrieval tuning used the train questions, so expect the hidden score to be lower.

## Tools and cost
Built with Claude (coding assistant) in a chat session. No paid APIs called. Cost: ₹0.
