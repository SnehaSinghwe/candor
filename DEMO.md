# 5-minute demo script (screen-record the terminal)

Setup once: `python doctor.py` should show key set and "Gemini call: OK".

**0:00 - 0:30  What it is.** "Candor memory plus an assistant over two weeks of a VP's work data. Retrieval is BM25 with a time
filter; Gemini only writes the final answer and plans actions; code validates everything."

**0:30 - 2:00  Memory, one command.** Run `bash run_all.sh`. Point at `out/results_summary.txt`: retrieval, answers, actions.
Then open `out/memory_train_answers.jsonl` and show three answers:
- MEM-TR-01 vs MEM-TR-02: same question, different `as_of`, different launch date (time travel).
- MEM-TR-16: abstains ("I don't know").
- MEM-TR-26: planted instruction in the data is not obeyed.

**2:00 - 4:00  Assistant, text.** `python assistant.py` and type, in different wordings:
1. `drop Ben a note on slack saying the NRR fix is done`
2. `push board deck prep back to 3 in the afternoon`
3. `Message Sarah about the pricing proposal` (it asks which Sarah), then `Patel` (follow-up works)
4. `draft me an email for sick leave for tomorrow`
5. `what's our launch date?` (answered from memory with sources)
6. `cancel my 4pm meeting` (asks for confirmation instead of acting)

**4:00 - 4:40  Optional voice / execute.** `python assistant.py --voice` (say "remind me an hour before the board meeting"),
or `python assistant.py --execute` and answer `y`: show the file written in `candor_outbox/`.

**4:40 - 5:00  Honest limits.** Gemini action score and answer score are with `--judge none`; messaging is queued, not sent.
