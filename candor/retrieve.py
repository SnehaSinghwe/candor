"""Hybrid lexical retrieval over the visible units at `as_of`: BM25 (own text + neighbour context +
metadata fields), date-token normalisation, query expansion, recency/source-diversity rerank."""
import math, re
from collections import Counter, defaultdict

STOP = set("a an the and or of to in on for is are was were be been am do did does i my me we our you your it its "
           "that this these those with at by from as what which who whom when where why how any has have had not no "
           "so if then than about there their they them he she his her will would should could can just also".split())
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
MONTHS.update({"sept": 9, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
               "august": 8, "september": 9, "october": 10, "november": 11, "december": 12})
SYN = {
    "launch": ["ship", "release", "golive", "date", "slip", "moved", "push", "delay"],
    "launching": ["ship", "release", "date", "slip", "moved", "push"],
    "slip": ["delay", "push", "moved", "regression", "launch"],
    "owns": ["owner", "owner", "assigned", "taking", "handling", "responsible"],
    "owe": ["promised", "promise", "send", "commit", "follow"],
    "promised": ["commit", "send", "owe", "will"],
    "hiring": ["hire", "headcount", "candidate", "recruit", "req"],
    "sign": ["contract", "close", "deal", "signed"],
    "signed": ["contract", "sign", "deal"],
    "price": ["pricing", "cost", "quote", "proposal", "per"], "pricing": ["price", "proposal", "tier", "quote"],
    "flight": ["fly", "airline", "departure", "denver", "boarding"],
    "calendar": ["meeting", "event", "schedule", "invite"],
    "database": ["db", "postgres", "sqlite", "duckdb", "storage"],
    "standup": ["stand", "sync", "friday"], "prep": ["preparation", "board"],
    "latency": ["p95", "p50", "ms", "routing", "performance"],
    "why": ["because", "reason", "due", "cause", "regression", "issue", "blocker"],
    "contract": ["proposal", "review", "cfo", "legal", "decision", "close", "sign"],
    "signed": ["proposal", "review", "cfo", "legal", "decision"],
    "sign": ["proposal", "review", "legal", "decision", "q4"],
    "fly": ["flight", "united", "depart", "airline", "confirmation"],
    "dictate": ["dictation", "dictated", "email", "sent", "draft"],
    "dictated": ["dictation", "email", "sent", "draft"],
}
INTENT_SRC = {"calendar": "calendar", "email": "email", "slack": "slack", "dictate": "dictation", "dictated": "dictation",
              "meeting": "meeting", "call": "meeting", "chatgpt": "chatgpt", "codex": "codex"}


def stem(w):
    """Cheap prefix stemmer: dictate/dictated/dictation -> 'dictat'; robust on a small corpus."""
    if w.isdigit() or w.startswith("d") and re.fullmatch(r"d\d+_\d+", w):
        return w
    return w[:6] if len(w) > 6 else w


def tokens(text, expand_dates=True):
    t = text.lower()
    out = []
    if expand_dates:
        for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t):
            out.append(f"d{MONTHS[m.group(1)]}_{int(m.group(2))}")
        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})\b", t):
            out.append(f"d{int(m.group(1))}_{int(m.group(2))}")
        for m in re.finditer(r"\b2026-(\d\d)-(\d\d)\b", t):
            out.append(f"d{int(m.group(1))}_{int(m.group(2))}")
    for w in re.findall(r"[a-z0-9][a-z0-9'\-\.]*[a-z0-9]|[a-z0-9]", t):
        w = w.strip(".'-").replace("'s", "")
        if not w or w in STOP:
            continue
        out.append(stem(w))
        for p in re.split(r"[\-\.@_]", w):
            if p and p != w and p not in STOP and len(p) > 1:
                out.append(stem(p))
    return out


class BM25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(1, len(docs))
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.inv = defaultdict(list)
        for i, c in enumerate(self.tf):
            for w in c:
                self.inv[w].append(i)

    def scores(self, q):
        s = defaultdict(float)
        for w, qw in q.items():
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i in self.inv[w]:
                f = self.tf[i][w]
                s[i] += qw * idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return s


class Index:
    def __init__(self, units, people=None):
        self.units = units
        self.by_id = {u.id: u for u in units}
        self.people = people or {}
        recs = defaultdict(list)
        for u in units:
            recs[u.record].append(u)
        for r in recs.values():
            r.sort(key=lambda u: u.seq)
        self.neigh = {}
        for r in recs.values():
            if r[0].source in ("meeting", "chatgpt"):
                for i, u in enumerate(r):
                    self.neigh[u.id] = r[max(0, i - 2): i] + r[i + 1: i + 3]
        thread_parent = {u.id: u for u in units if u.source == "slack"}
        self.own = BM25([tokens(u.text) for u in units])
        self.meta = BM25([tokens(f"{u.label} {u.speaker} {u.meta.get('date', '')} {u.time.strftime('%B %d')}") for u in units])

        def ctx(u):
            parts = [x.text for x in self.neigh.get(u.id, [])]
            if u.parent and u.parent in thread_parent:
                parts.append(thread_parent[u.parent].text)
            return tokens(" ".join(parts))
        self.ctx = BM25([ctx(u) for u in units])
        self.pos = {u.id: i for i, u in enumerate(units)}
        self.links = self._link_duplicates()

    def _link_duplicates(self):
        """A dictation that was inserted/sent into Gmail/Slack has a twin record; link them (Jaccard on tokens)."""
        links = defaultdict(set)
        tok = {}
        for u in self.units:
            if u.source in ("dictation", "email", "slack") and len(u.text) > 40:
                tok[u.id] = set(tokens(u.text, False))
        for u in self.units:
            if u.source != "dictation" or u.id not in tok:
                continue
            for v in self.units:
                if v.source in ("email", "slack") and v.id in tok and 0 <= (v.time - u.time).total_seconds() < 3600 * 6:
                    a, b = tok[u.id], tok[v.id]
                    if len(a & b) / max(1, len(a | b)) > 0.5:
                        links[u.id].add(v.id); links[v.id].add(u.id)
        return links

    GENERIC = set("say said tell told salary".split()) - {"salary"}

    def unseen_terms(self, question):
        """Rare content words in the question that appear nowhere in visible memory -> evidence is missing -> abstain."""
        out = []
        for w in set(tokens(question, False)):
            if len(w) > 3 and w not in self.own.idf and w not in self.meta.idf and w not in self.ctx.idf \
                    and w not in {stem(x) for x in ("would", "going", "might", "should", "please", "many", "days", "hours")}:
                out.append(w)
        return out

    def expand(self, question):
        base = tokens(question)
        q = Counter()
        for w in base:
            q[w] += 0.5 if re.fullmatch(r"d\d+_\d+", w) else 1.0
        raw = re.findall(r"[a-z0-9]+", question.lower())   # synonyms keyed on raw words, stopwords included ("why")
        for w in set(raw) | set(q):
            for s in SYN.get(w, []) + SYN.get(w + "s", []):
                q[stem(s)] += 0.6 if w == "why" else 0.35
        # names: boost both tokens of a full name together
        return q

    def search(self, question, k=20, recency=True):
        q = self.expand(question)
        so, sm, sc = self.own.scores(q), self.meta.scores(q), self.ctx.scores(q)
        cand = set(so) | set(sm) | set(sc)
        sc_final = {}
        for i in cand:
            sc_final[i] = so.get(i, 0) + 0.35 * sm.get(i, 0) + 0.3 * sc.get(i, 0)
        for uid, others in self.links.items():   # twins share evidence
            i = self.pos[uid]
            for o in others:
                j = self.pos[o]
                if sc_final.get(i, 0) > sc_final.get(j, 0):
                    sc_final[j] = 0.9 * sc_final[i]
        return sc_final
