#!/usr/bin/env bash
# One command. Core: Python 3.9+, stdlib only (Gemini SDK needs 3.10+). Usage: bash run_all.sh [questions.jsonl] [out.jsonl]
# .env is read by the Python code itself (candor/safety.py), so no shell sourcing is needed (works in macOS bash 3.2 and with CRLF files)
set -e
export CANDOR_TZ=America/Los_Angeles
PY=${PYTHON:-python3}
Q=${1:-evals/memory_train.jsonl}; OUT=${2:-out/memory_train_answers.jsonl}
mkdir -p out
$PY tests_agent.py 2>&1 | tail -1
$PY run.py "$Q" "$OUT" data
$PY run_actions.py evals/actions_train.jsonl out/actions_train_predictions.jsonl data
if [ "$Q" = "evals/memory_train.jsonl" ]; then
  $PY run_actions.py evals/actions_probe.jsonl out/actions_probe_predictions.jsonl data
  {
    echo "== retrieval (memory_train) =="; $PY eval_harness/score_retrieval.py --gold evals/memory_train.jsonl --answers "$OUT" | tail -4
    echo "== answers (memory_train, --judge none) =="; $PY eval_harness/score_memory.py --gold evals/memory_train.jsonl --answers "$OUT" --judge none | tail -2
    echo "== actions_train =="; $PY eval_harness/score_actions.py --gold evals/actions_train.jsonl --predictions out/actions_train_predictions.jsonl | tail -1
    echo "== actions_probe (20 reworded commands, my own set) =="; $PY eval_harness/score_actions.py --gold evals/actions_probe.jsonl --predictions out/actions_probe_predictions.jsonl | grep -E "FAIL|pass rate"
  } 2>&1 | tee out/results_summary.txt
  echo "(scores also saved to out/results_summary.txt; model used: ${GEMINI_MODEL:-default})"
fi
