"""Question -> ranked retrieval -> grounded answer. Gemini answer writer optional (GEMINI_API_KEY)."""
import json, re, sys
from pathlib import Path
from .ingest import load, visible, dt
from .retrieve import Index
from . import safety

HOP = re.compile(r"\b(the day|that day|day i|day of|what.s on my calendar)\b", re.I)
CURRENT = re.compile(r"\b(now|current|currently|still|latest|today|going to|is the|are we|status|did|done|landed)\b", re.I)
PAST = re.compile(r"\b(was|were|originally|initially|first|earlier|before|back then|used to|previously|at the time|promised|said)\b", re.I)


class Memory:
    def __init__(self, data_dir="data"):
        self.units, self.deleted, self.edits = load(data_dir)
        self._cache = {}

    def index_at(self, as_of):
        vis = visible(self.units, self.deleted, self.edits, as_of)
        key = tuple(u.id for u in vis) + tuple(u.text[:20] for u in vis if u.meta.get("edited"))
        if key not in self._cache:
            self._cache[key] = Index(vis)
        return self._cache[key]

    def retrieve(self, question, as_of, k=20):
        ix = self.index_at(as_of)
        sc = ix.search(question)
        if HOP.search(question) and sc:
            sc = self.date_hop(ix, question, sc)
        if not sc:
            return ix, []
        from .retrieve import INTENT_SRC
        want = {INTENT_SRC[w] for w in re.findall(r"[a-z]+", question.lower()) if w in INTENT_SRC and w not in ("call", "meeting")}
        if "dictation" in want:   # "what did I dictate": favour dictation records and their sent twins
            for uid, others in ix.links.items():
                if ix.by_id[uid].source == "dictation":
                    for o in [uid, *others]:
                        sc[ix.pos[o]] = sc.get(ix.pos[o], 0) * 1.7
        ranked = sorted(sc, key=sc.get, reverse=True)[:60]
        top = sc[ranked[0]]
        # present-tense questions about a changing fact prefer the newest evidence; questions about the past do not
        cur = bool(CURRENT.search(question)) or not PAST.search(question)
        newest = max(ix.units[i].time for i in ranked[:30])
        span = max(1.0, (newest - min(ix.units[i].time for i in ranked[:30])).total_seconds())
        adj = {}
        for i in ranked:
            u = ix.units[i]
            s = sc[i] / top
            if cur:  # prefer the latest state of a changing fact
                s *= 1 + 0.25 * (1 - (newest - u.time).total_seconds() / span)
            if u.source == "slack" and u.meta.get("bot"):
                s *= 0.8
            if safety.INJ.search(u.text):
                s = 0.0
            adj[i] = s
        order = sorted(adj, key=adj.get, reverse=True)
        # diversity: max 3 per record in the head, then fill
        out, per, rest = [], {}, []
        for i in order:
            r = ix.units[i].record
            if per.get(r, 0) < 3 and len(out) < k:
                out.append(i); per[r] = per.get(r, 0) + 1
            else:
                rest.append(i)
        out += rest[: max(0, k - len(out))]
        return ix, out[:k]

    def date_hop(self, ix, question, sc):
        """Two-hop: 'the day I fly to Denver' -> find the anchor record (email/calendar), take its first
        body date, then pull everything dated that day (calendar events, invites, emails)."""
        from .retrieve import tokens
        from collections import Counter
        m = HOP.search(question)
        anchor = question[m.end():] if m and m.end() < len(question) - 3 else question
        seed = ix.search(anchor)
        seed = {i: v for i, v in seed.items() if ix.units[i].source in ("email", "calendar")}
        if not seed:
            return sc
        best = max(seed, key=seed.get)
        dates = [t for t in tokens(ix.units[best].text) if re.fullmatch(r"d\d+_\d+", t)]
        if not dates:
            return sc
        d = dates[0] if ix.units[best].source == "email" else dates[0]
        extra = ix.own.scores({d: 1.0})
        top = max(sc.values())
        sc[best] = max(sc.get(best, 0), 0.9 * top)
        mx = max(extra.values()) or 1
        for i, v in extra.items():
            if ix.units[i].source in ("calendar", "email", "slack"):
                sc[i] = sc.get(i, 0) + 0.7 * top * v / mx
        return sc

    def answer(self, q):
        as_of = dt(q["as_of"])
        ix, top = self.retrieve(q["question"], as_of)
        units = [ix.units[i] for i in top]
        ans, srcs, abst = safety.compose(q["question"], units, q["as_of"], ix.unseen_terms(q["question"]))
        return {"id": q["id"], "answer": safety.scrub(ans), "sources": srcs,
                "retrieved": [u.id for u in units], "abstained": abst}


def run(questions_path, out_path, data_dir="data"):
    mem = Memory(data_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        for line in open(questions_path, encoding="utf-8"):
            if line.strip():
                f.write(json.dumps(mem.answer(json.loads(line)), ensure_ascii=False) + "\n")
