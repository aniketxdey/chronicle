"""Tasks: consecutive activity grouped by topic, TextTiling-style.

Frames are bucketed into time slices, each a TF-IDF vector over window title, URL host/path,
screen text, and extracted entities. Boundaries go where the cosine similarity of adjacent
windows dips deepest; idle gaps always split; short segments merge into their most similar
neighbor. Segment centroids are then clustered (cosine, average linkage) into topics so that
separate stints on the same thing share a cluster.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from chronicle.timeutil import utc_iso

try:
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.feature_extraction.text import TfidfTransformer
except ImportError:  # pragma: no cover - optional
    DictVectorizer = TfidfTransformer = None
try:
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage
except ImportError:  # pragma: no cover - optional
    np = fcluster = linkage = None

SLICE_SECS = 30
WINDOW = 2
IDLE_SECS = 120
MIN_SECS = 60
MIN_DEPTH = 0.1
SPLIT_SIM = 0.25
CLUSTER_DISTANCE = 0.7
TEXT_CAP = 4000
INVOLVED_CAP = 12
PHRASE_WORDS = 3

_URL = re.compile(r"https?://\S+")
_BREAK = re.compile(r"[|\n\t·•—–:;,()\[\]{}<>\"“”!?…]+|\s[-/]\s|\.\s")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_]*(?:[.'’+#&-][A-Za-z0-9_]+)*")
_STOP = set(
    """a about above after again all also am an and any are as at be been before being below between both but by can
    could did do does doing down during each few for from further had has have having he her here hers him his how i if
    in into is it its itself just me more most my no nor not now of off on once only or other our ours out over own same
    she should so some such than that the their them then there these they this those through to too under until up very
    was we were what when where which while who whom why will with would you your yours via vs per get got let us
    http https www com org net io html htm php aspx index google search results result page tab tabs new untitled home
    overview news window view sign log login menu settings edit file help open close loading list lists top inc llc
    """.split()
)

_MEETING_APPS = {"zoom", "zoom.us", "facetime", "webex", "microsoft teams", "around", "tuple"}
_MEETING_HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "whereby.com")
_CHAT_APPS = {"slack", "mail", "gmail", "messages", "discord", "telegram", "whatsapp", "signal", "outlook", "microsoft outlook", "superhuman", "spark"}
_CHAT_HOSTS = ("mail.google.com", "slack.com", "discord.com", "web.whatsapp.com", "outlook.live.com", "outlook.office.com", "web.telegram.org", "messenger.com")
_WRITE_APPS = {"notion", "pages", "microsoft word", "word", "obsidian", "bear", "notes", "textedit", "ulysses", "craft", "typora", "scrivener"}
_WRITE_HOSTS = ("docs.google.com", "notion.so", "overleaf.com", "coda.io", "quip.com", "dropbox.com/paper")
_CODE_APPS = {
    "cursor", "code", "visual studio code", "xcode", "terminal", "iterm2", "warp", "ghostty", "pycharm", "intellij idea",
    "webstorm", "zed", "sublime text", "nova", "android studio", "kitty", "alacritty", "wezterm",
}
_CODE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com", "localhost", "127.0.0.1", "pypi.org", "npmjs.com", "docs.python.org")
_RESEARCH_HOSTS = (
    "google.", "bing.com", "duckduckgo.com", "perplexity.ai", "wikipedia.org", "arxiv.org", "chatgpt.com", "claude.ai",
    "kagi.com", "news.ycombinator.com", "semanticscholar.org",
)
_BROWSERS = {"arc", "safari", "google chrome", "chrome", "firefox", "brave browser", "microsoft edge", "orion", "vivaldi", "opera", "dia", "zen"}


@dataclass
class Item:
    """One frame of activity."""

    start: datetime
    seconds: float
    app: str
    window: str = ""
    url: str | None = None
    text: str = ""
    terms: list[str] = field(default_factory=list)
    involved: list[str] = field(default_factory=list)
    ref: dict | None = None
    group: object = None
    end: datetime | None = None


@dataclass
class Task:
    items: list[Item]
    vector: dict[str, float]
    start: datetime
    end: datetime
    verb: str = ""
    terms: list[str] = field(default_factory=list)
    cluster: int = 0
    topic: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def label(self) -> str:
        return f"{self.verb} {' · '.join(self.terms[:2]) or self.apps()[0]}"

    def apps(self) -> list[str]:
        dwell: Counter = Counter()
        for item in self.items:
            dwell[item.app] += _dwell(item)
        return [app for app, _ in dwell.most_common()]


def _dwell(item: Item) -> float:
    return max((item.end - item.start).total_seconds() if item.end else item.seconds or 0, 1.0)


def _noise(word: str) -> bool:
    if len(word) < 2 or len(word) > 32 or sum(c.isdigit() for c in word) >= 6:
        return True
    return bool(re.fullmatch(r"[\d.,'’-]+", word)) and (len(word) > 3 or not word.isdigit())


def _terms(item: Item, surface: dict[str, Counter]) -> Counter:
    counts: Counter = Counter()
    window = (item.window or "").strip()
    lines = [window] + [line for line in _URL.sub("\n", (item.text or "")[:TEXT_CAP]).splitlines() if line.strip() != window]
    app_words = {word.lower() for word in _WORD.findall(item.app or "")}
    for line in lines:
        for chunk in _BREAK.split(line):
            previous = None
            for raw in _WORD.findall(chunk):
                word = raw.lower()
                if _noise(word):
                    continue
                if word in _STOP or word in app_words:
                    previous = None
                    continue
                surface.setdefault(word, Counter())[raw] += 1
                counts[word] += 1
                if previous:
                    counts[f"{previous} {word}"] += 1
                previous = word
    if item.url:
        parts = urlsplit(item.url if "://" in item.url else f"https://{item.url}")
        host = (parts.hostname or "").split(".")[:-1]
        for word in host + re.split(r"[^A-Za-z0-9]+", parts.path):
            word = word.lower()
            if word and not _noise(word) and word not in _STOP:
                surface.setdefault(word, Counter())
                counts[word] += 1
    for label in item.terms:
        words = _WORD.findall(label)
        if words:
            for raw in words:
                surface.setdefault(raw.lower(), Counter())[raw] += 1
            counts[" ".join(raw.lower() for raw in words)] += 1
    return counts


def _tfidf(docs: list[Counter]) -> list[dict[str, float]]:
    """Sublinear-tf, smooth-idf, L2-normalized TF-IDF; the pure-Python path matches scikit-learn's."""
    if DictVectorizer is not None and any(docs):
        vectorizer = DictVectorizer()
        matrix = TfidfTransformer(sublinear_tf=True).fit_transform(vectorizer.fit_transform(docs)).tocsr()
        names = vectorizer.get_feature_names_out()
        return [
            {str(names[j]): float(v) for j, v in zip(matrix.indices[lo:hi], matrix.data[lo:hi])}
            for lo, hi in zip(matrix.indptr[:-1], matrix.indptr[1:])
        ]
    total = len(docs)
    df = Counter(term for doc in docs for term in doc)
    vectors = []
    for doc in docs:
        weights = {t: (1 + math.log(c)) * (math.log((1 + total) / (1 + df[t])) + 1) for t, c in doc.items() if c > 0}
        norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
        vectors.append({t: w / norm for t, w in weights.items()})
    return vectors


def _sum(vectors: list[dict[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for vector in vectors:
        for term, weight in vector.items():
            out[term] = out.get(term, 0.0) + weight
    return out


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(b) < len(a):
        a, b = b, a
    dot = sum(w * b.get(t, 0.0) for t, w in a.items())
    norm = math.sqrt(sum(w * w for w in a.values())) * math.sqrt(sum(w * w for w in b.values()))
    return dot / norm if norm else 0.0


def boundaries(vectors: list[dict[str, float]], window: int = WINDOW, min_depth: float = MIN_DEPTH) -> list[int]:
    """Indices i where a new segment starts at slice i (TextTiling depth scoring)."""
    if len(vectors) < 2:
        return []
    sims = [_cos(_sum(vectors[max(0, i - window):i]), _sum(vectors[i:i + window])) for i in range(1, len(vectors))]
    if len(sims) == 1:
        return [1] if sims[0] < SPLIT_SIM else []
    depths = []
    for gap, value in enumerate(sims):
        left = right = value
        j = gap
        while j > 0 and sims[j - 1] >= left:
            j -= 1
            left = sims[j]
        j = gap
        while j < len(sims) - 1 and sims[j + 1] >= right:
            j += 1
            right = sims[j]
        depths.append(left + right - 2 * value)
    mean = sum(depths) / len(depths)
    spread = math.sqrt(sum((d - mean) ** 2 for d in depths) / len(depths))
    cutoff = max(mean - spread / 2, min_depth)
    return [
        gap + 1 for gap, depth in enumerate(depths)
        if depth > cutoff and (gap == 0 or depth >= depths[gap - 1]) and (gap == len(depths) - 1 or depth > depths[gap + 1])
    ]


def _cluster(vectors: list[dict[str, float]], distance: float) -> list[int]:
    count = len(vectors)
    if count < 2:
        return [1] * count
    matrix = [[0.0] * count for _ in range(count)]
    for i in range(count):
        for j in range(i + 1, count):
            matrix[i][j] = matrix[j][i] = min(1.0, max(0.0, 1 - _cos(vectors[i], vectors[j])))
    if linkage is not None:
        condensed = np.asarray([matrix[i][j] for i in range(count) for j in range(i + 1, count)])
        raw = [int(label) for label in fcluster(linkage(condensed, method="average"), t=distance, criterion="distance")]
    else:
        groups = [[i] for i in range(count)]
        while len(groups) > 1:
            best, pair = math.inf, (0, 1)
            for a in range(len(groups)):
                for b in range(a + 1, len(groups)):
                    value = sum(matrix[i][j] for i in groups[a] for j in groups[b]) / (len(groups[a]) * len(groups[b]))
                    if value < best:
                        best, pair = value, (a, b)
            if best > distance:
                break
            groups[pair[0]] += groups.pop(pair[1])
        raw = [0] * count
        for label, members in enumerate(groups):
            for i in members:
                raw[i] = label
    order: dict[int, int] = {}
    return [order.setdefault(label, len(order) + 1) for label in raw]


def _category(item: Item) -> str:
    app = (item.app or "").lower()
    where = ""
    if item.url:
        parts = urlsplit(item.url if "://" in item.url else f"https://{item.url}")
        where = f".{parts.hostname or ''}{parts.path}"

    def on(hosts: tuple[str, ...]) -> bool:
        return bool(where) and any(f".{host}" in where for host in hosts)

    if app in _MEETING_APPS or on(_MEETING_HOSTS):
        return "meeting"
    if app in _CHAT_APPS or on(_CHAT_HOSTS):
        return "communicating"
    if app in _WRITE_APPS or on(_WRITE_HOSTS):
        return "writing"
    if app in _CODE_APPS or on(_CODE_HOSTS):
        return "coding"
    if where:
        query = urlsplit(item.url).query
        return "researching" if on(_RESEARCH_HOSTS) or "/search" in where or "q=" in query else "browsing"
    return "browsing" if app in _BROWSERS else "working on"


def verb(items: list[Item]) -> str:
    totals: Counter = Counter()
    for item in items:
        totals[_category(item)] += _dwell(item)
    if totals["researching"]:
        totals["researching"] += totals.pop("browsing", 0)
    return totals.most_common(1)[0][0] if totals else "working on"


def _display(word: str, surface: dict[str, Counter]) -> str:
    forms = surface.get(word)
    if not forms:
        return word
    cased = [(n, form) for form, n in forms.items() if form != form.lower()]
    if cased and max(cased)[0] * 3 >= sum(forms.values()):
        return max(cased)[1]
    return forms.most_common(1)[0][0]


def phrases(vector: dict[str, float], surface: dict[str, Counter], limit: int = 3, pool: int = 14) -> list[str]:
    """Top terms as phrases: overlapping bigrams chain ('forbes ai' + 'ai 50' -> 'forbes ai 50')."""
    chosen: list[list[str]] = []
    for term, _ in sorted(vector.items(), key=lambda kv: (-kv[1], kv[0]))[:pool]:
        words = term.split()
        if all(w.isdigit() for w in words) or any(set(words) <= set(p) for p in chosen):
            continue
        if len(words) != 2:
            chosen.append(words)
            continue
        first, second = words
        used = {w for p in chosen for w in p}
        left = next((p for p in chosen if p[-1] == first and second not in p), None)
        right = next((p for p in chosen if p[0] == second and first not in p and p is not left), None)
        if left is not None and right is not None:
            if len(left) + len(right) <= PHRASE_WORDS:
                left.extend(right)
                chosen.remove(right)
        elif left is not None:
            if len(left) < PHRASE_WORDS and second not in used:
                left.append(second)
        elif right is not None:
            if len(right) < PHRASE_WORDS and first not in used:
                right.insert(0, first)
        elif not used & set(words):
            chosen.append(words)
    unique = [p for i, p in enumerate(chosen) if not any(set(p) < set(q) or (set(p) == set(q) and j < i) for j, q in enumerate(chosen) if j != i)]
    return [" ".join(_display(w, surface) for w in p) for p in unique[:limit]]


def _set_ends(items: list[Item], idle_secs: float) -> None:
    for item, following in zip(items, [*items[1:], None]):
        stop = item.start.timestamp() + (item.seconds or 0)
        if following is not None and following.group == item.group:
            nxt = following.start.timestamp()
            if not item.seconds and nxt - item.start.timestamp() <= idle_secs:
                stop = nxt
            stop = min(stop, max(nxt, item.start.timestamp()))
        item.end = datetime.fromtimestamp(max(stop, item.start.timestamp()), item.start.tzinfo)


def _idle(previous: Item, item: Item, idle_secs: float) -> bool:
    if previous.group != item.group:
        return True
    return (item.start - previous.start).total_seconds() - (previous.seconds or 0) > idle_secs


def segment(
    items: list[Item],
    *,
    slice_secs: float = SLICE_SECS,
    window: int = WINDOW,
    idle_secs: float = IDLE_SECS,
    min_secs: float = MIN_SECS,
    min_depth: float = MIN_DEPTH,
    cluster_distance: float = CLUSTER_DISTANCE,
) -> list[Task]:
    items = sorted(items, key=lambda item: item.start)
    if not items:
        return []
    _set_ends(items, idle_secs)
    surface: dict[str, Counter] = {}
    slices: list[list[Item]] = []
    runs: list[list[int]] = []
    for index, item in enumerate(items):
        hard = index == 0 or _idle(items[index - 1], item, idle_secs)
        if hard or (item.start - slices[-1][0].start).total_seconds() >= slice_secs:
            slices.append([])
            if hard:
                runs.append([])
            runs[-1].append(len(slices) - 1)
        slices[-1].append(item)
    vectors = _tfidf([sum((_terms(item, surface) for item in chunk), Counter()) for chunk in slices])

    def span(seg: list[int]) -> float:
        return (max(i.end for s in seg for i in slices[s]) - slices[seg[0]][0].start).total_seconds()

    def centroid(seg: list[int]) -> dict[str, float]:
        return {t: w / len(seg) for t, w in _sum([vectors[s] for s in seg]).items()}

    spans: list[tuple[int, list[int]]] = []
    for run_index, run in enumerate(runs):
        cuts = boundaries([vectors[s] for s in run], window, min_depth)
        segs = [run[a:b] for a, b in zip([0, *cuts], [*cuts, len(run)])]
        while len(segs) > 1:
            lengths = [span(seg) for seg in segs]
            index = min(range(len(segs)), key=lengths.__getitem__)
            if lengths[index] >= min_secs:
                break
            own = centroid(segs[index])
            target = max((j for j in (index - 1, index + 1) if 0 <= j < len(segs)), key=lambda j: (_cos(own, centroid(segs[j])), -j))
            lo, hi = sorted((index, target))
            segs[lo:hi + 1] = [segs[lo] + segs[hi]]
        spans += [(run_index, seg) for seg in segs]

    labels = _cluster([centroid(seg) for _, seg in spans], cluster_distance)
    # Adjacent segments of one run that land in the same topic were split by noise (e.g. a short interruption).
    merged: list[tuple[int, int, list[int]]] = []
    for (run_index, seg), label in zip(spans, labels):
        if merged and merged[-1][0] == run_index and merged[-1][1] == label:
            merged[-1][2].extend(seg)
        else:
            merged.append((run_index, label, list(seg)))

    found = []
    for _, label, seg in merged:
        members = [item for s in seg for item in slices[s]]
        vector = centroid(seg)
        found.append(Task(
            items=members, vector=vector, start=members[0].start, end=max(item.end for item in members),
            verb=verb(members), terms=phrases(vector, surface), cluster=label,
        ))
    for label in {task.cluster for task in found}:
        members = [task for task in found if task.cluster == label]
        topic = " · ".join(phrases(_sum([task.vector for task in members]), surface, limit=2)) or members[0].apps()[0]
        for task in members:
            task.topic = topic
    return found


def attach(graph, items: list[Item], session_node: str | None = None) -> list[Task]:
    """Add task and topic_cluster nodes to a graph builder (see chronicle.graph._Builder)."""
    found = segment(items)
    previous = None
    for index, task in enumerate(found, 1):
        first = task.items[0].ref
        # Ids carry the shard scope so tasks from different recordings stay distinct in the all-history graph.
        topic_id = graph.node("topic_cluster", f"{graph.scope}.{task.cluster}", task.topic, ref=first, seconds=task.duration,
                              props={"cluster_id": task.cluster})
        task_id = graph.node("task", f"{graph.scope}.{index}", task.label, props={
            "start": utc_iso(task.start), "end": utc_iso(task.end), "duration_secs": round(task.duration),
            "verb": task.verb, "top_terms": task.terms, "cluster_id": task.cluster, "topic": topic_id,
            "frame_count": len(task.items), "apps": task.apps()[:4],
        })
        node = graph.nodes[task_id]
        node["weight"], node["seconds"] = len(task.items), task.duration
        for item in task.items:
            if item.ref:
                graph._touch(node, item.ref)
        graph.edge(task_id, topic_id, "part_of", ref=first, weight=task.duration or 1)
        dwell: Counter = Counter()
        for item in task.items:
            for node_id in dict.fromkeys(item.involved):
                dwell[node_id] += _dwell(item)
        for node_id, seconds in dwell.most_common(INVOLVED_CAP):
            graph.edge(task_id, node_id, "involved", ref=first, weight=seconds)
        if previous:
            graph.edge(previous, task_id, "then", ref=first)
        if session_node:
            graph.edge(session_node, task_id, "contains", ref=first, weight=task.duration or 1)
        previous = task_id
    return found
