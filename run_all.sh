#!/usr/bin/env bash
# One command: python3 (3.10+, stdlib only). Usage: ./run_all.sh [questions.jsonl] [out.jsonl]
set -e
Q=${1:-evals/memory_train.jsonl}; OUT=${2:-out/memory_train_answers.jsonl}
[ -f .env ] && set -a && . ./.env && set +a
mkdir -p out
python3 run.py "$Q" "$OUT" data
python3 eval_harness/score_retrieval.py --gold evals/memory_train.jsonl --answers "$OUT" 2>/dev/null | tail -4 || true
