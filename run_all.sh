#!/usr/bin/env bash
# One command. Python 3.10+, stdlib only. Usage: ./run_all.sh [questions.jsonl] [out.jsonl]
set -e
Q=${1:-evals/memory_train.jsonl}; OUT=${2:-out/memory_train_answers.jsonl}
if [ -f .env ]; then set -a; . <(tr -d '\r' < .env); set +a; fi
mkdir -p out
python3 run.py "$Q" "$OUT" data
python3 run_actions.py evals/actions_train.jsonl out/actions_train_predictions.jsonl data
if [ "$Q" = "evals/memory_train.jsonl" ]; then
  python3 eval_harness/score_retrieval.py --gold evals/memory_train.jsonl --answers "$OUT" | tail -4
  python3 eval_harness/score_memory.py --gold evals/memory_train.jsonl --answers "$OUT" --judge none | tail -2
  python3 eval_harness/score_actions.py --gold evals/actions_train.jsonl --predictions out/actions_train_predictions.jsonl | tail -1
fi
