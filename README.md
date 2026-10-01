# Candor — Time-Aware Memory + TextOS

A take-home implementation of a **time-aware memory system and TextOS action planner**. Candor ingests heterogeneous records, reconstructs memory as of a query time, retrieves and reranks relevant evidence, generates grounded answers with Google Gemini, and converts natural-language action requests into safe dry-run plans.

## Quick Start

**Requirements:** Python 3.10+

Core retrieval, memory, and action logic uses the Python standard library. The Gemini answer-writer requires the official `google-genai` SDK.

```bash
pip install -r requirements.txt
```

Create a local `.env` file from the example:

```bash
cp .env.example .env
```

Then set:

```env
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
```

**Never commit `.env` or an API key.** `.env` is git-ignored.

### Run the full evaluation

On macOS/Linux or Git Bash:

```bash
./run_all.sh
```

Or run the components directly:

```bash
python3 run.py evals/memory_train.jsonl out/memory_train_answers.jsonl data
python3 run_actions.py evals/actions_train.jsonl out/actions_train_predictions.jsonl data
```

The generated evaluation outputs are written to `out/`.

For another question set:

```bash
./run_all.sh questions.jsonl answers.jsonl
```

## LLM Provider

Candor uses **Google Gemini** through the official `google-genai` SDK.

The default answer-writing model is:

```text
gemini-3.5-flash-lite
```

The LLM is deliberately limited to **answer generation**. Retrieval, time filtering, safety filtering, citation validation, and action planning remain deterministic and do not require an API key.

The project was migrated from an earlier Anthropic-based answer writer; Anthropic is not required by the current implementation.

### Environment variables

| Variable         | Purpose                                                |
| ---------------- | ------------------------------------------------------ |
| `GEMINI_API_KEY` | Gemini API key; keep this local and never commit it    |
| `GEMINI_MODEL`   | Gemini model name; defaults to `gemini-3.5-flash-lite` |

Model availability can vary by Google account/project.

## No-Key / Failure Fallback

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

### 1. Ingestion

`candor/ingest.py`

Every source is normalized into a `Unit` containing a citable identifier, delivery time, speaker, label, and source-specific metadata.

Supported source types include meeting segments, Slack messages, emails, dictation, calendar events, Codex sessions, and ChatGPT messages.

### 2. Time-Aware Memory

At query time, Candor reconstructs the corpus as it existed at the requested `as_of` timestamp.

This includes:

* dropping records that did not yet exist,
* removing deleted Slack messages,
* replacing edited content from the edit time onward,
* handling calendar events from their update time,
* caching indexes per visible corpus.

### 3. Retrieval

`candor/retrieve.py`

Candor uses a standard-library BM25 retriever over:

* record text,
* neighbouring segments / thread context,
* metadata such as speaker, title, and date.

Additional retrieval behavior includes:

* prefix stemming,
* date normalization,
* lightweight intent synonyms,
* linking dictation records to their corresponding sent messages.

### 4. Reranking

`candor/memory.py`

Retrieved records are reranked using:

* recency boosts for current-state questions,
* source diversity,
* source-intent matching,
* multi-hop date lookup.

This allows queries such as identifying the day associated with another remembered event.

### 5. Safety

`candor/safety.py`

The safety layer:

* redacts detected secrets from generated output,
* prevents instruction-like data from being treated as executable instructions,
* validates citations against retrieved evidence,
* supports abstention when evidence is insufficient.

### 6. Gemini Answer Writer

When Gemini is available, the model receives only the retrieved evidence needed for the question.

The answer-writing prompt explicitly handles:

* `as_of` cutoffs,
* newer-vs-older information,
* speaker attribution,
* conflicting records,
* abstention,
* citation requirements.

The resulting citation IDs are validated against the retrieved evidence before the answer is emitted.

### 7. TextOS Actions

`candor/actions.py`

The action planner is deterministic and operates in dry-run mode.

It supports actions such as:

* Slack messages,
* emails,
* reminders,
* calendar scheduling,
* opening applications,
* memory questions,
* compound commands.

Safety behavior includes:

* confirmation for destructive actions,
* clarification when multiple people match,
* splitting compound commands,
* resolving remembered values such as "the corrected NRR" through memory.

## Evaluation Results

Results below were measured locally on the provided training evaluation set.

### Memory

| Metric                         |            Result |
| ------------------------------ | ----------------: |
| Retrieval score                | **96.0% (26/27)** |
| Top-5 exact passage retrieval  |           **76%** |
| Top-10 exact passage retrieval |           **96%** |
| Top-20 exact passage retrieval |           **96%** |
| MRR                            |        **0.6884** |
| Forbidden records retrieved    |             **0** |
| Gemini strict answer accuracy  |         **92.6%** |
| Gemini lenient answer accuracy |          **100%** |
| Hard failures                  |             **0** |
| Unverified answers             |             **2** |
| Citation recall                |           **90%** |
| Citation precision             |           **95%** |

The Gemini evaluation covered **27 training questions** using:

```text
gemini-3.5-flash-lite
```

Both expected abstention cases were handled correctly.

The two unverified answers (`MEM-TR-01` and `MEM-TR-13`) were flagged by the deterministic scorer because they included an additional older date. No LLM judge was used for those cases.

### No-Key Fallback

The deterministic extractive fallback was also measured:

* Strict accuracy: **33.3%**
* Lenient accuracy: **37.0%**

This demonstrates that the Gemini path improves answer quality while the system remains operational without an API key.

### Actions

| Metric            |    Result |
| ----------------- | --------: |
| Action cases      | **12/12** |
| Pass rate         |  **100%** |
| Argument accuracy |  **100%** |

All 12 provided action-training cases passed with the expected arguments.

## Known Limitations

### Retrieval vocabulary

`MEM-TR-21` is the main retrieval weakness in the training set. The question asks why a launch slipped, while the underlying evidence uses the phrase "geocoding regression", giving the query and evidence little lexical overlap.

A denser semantic retriever or LLM-assisted query rewrite could address this class of failure.

### Deterministic evaluation

The reported Gemini answer score uses the deterministic evaluator:

```text
--judge none
```

Two answers remain marked `unverified` because they require semantic judgment rather than simple rule-based verification.

### Model availability

Gemini model availability depends on the Google account/project and current quota. The implementation allows the model to be changed through:

```env
GEMINI_MODEL=...
```

If Gemini is unavailable, Candor falls back to the extractive answer path instead of terminating the evaluation.

### Windows

File reads and writes use explicit UTF-8 handling.

The `run_all.sh` script is a Bash script and should be run from Git Bash, macOS, or Linux. On Windows PowerShell, the individual Python commands can be run directly.

### Training-set tuning

Retrieval tuning was performed against the provided training questions. Performance on a hidden evaluation set may therefore be lower.

### Action coverage

The action planner is rule-based and was developed against the provided action cases plus additional local cases. A more general semantic action parser would improve coverage for unseen command variations.

## Project Structure

```text
candor/
├── actions.py
├── ingest.py
├── memory.py
├── retrieve.py
└── safety.py

data/
evals/
eval_harness/
examples/

run.py
run_actions.py
run_all.sh
requirements.txt
.env.example
.gitignore
README.md
```

## Reproducibility

The main evaluation can be reproduced with:

```bash
pip install -r requirements.txt
```

Set `GEMINI_API_KEY` locally if Gemini-backed answer generation is desired, then:

```bash
python3 run.py evals/memory_train.jsonl out/memory_train_answers.jsonl data
python3 eval_harness/score_retrieval.py \
  --gold evals/memory_train.jsonl \
  --answers out/memory_train_answers.jsonl

python3 eval_harness/score_memory.py \
  --gold evals/memory_train.jsonl \
  --answers out/memory_train_answers.jsonl \
  --judge none

python3 run_actions.py \
  evals/actions_train.jsonl \
  out/actions_train_predictions.jsonl \
  data

python3 eval_harness/score_actions.py \
  --gold evals/actions_train.jsonl \
  --predictions out/actions_train_predictions.jsonl
```

## Tools and AI Assistance

The project was developed with assistance from an AI coding assistant during a chat-based development session.

The answer-writing component uses Google Gemini through the official `google-genai` SDK.

Gemini was used on the free/low-cost tier during development; API cost was not separately tracked.

The API key used for local evaluation is **not included in this repository**.
