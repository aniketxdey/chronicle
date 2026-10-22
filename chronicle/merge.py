"""Merge near-duplicate graph nodes by string similarity, without an LLM.

Labels are normalized, embedded with character and word TF-IDF, and clustered with
average linkage inside groups of compatible kinds. Hard rules force a pair together
(same normalized URL, file path suffix, entity-name prefix) or keep it apart
(different URLs, apps, search queries, or incompatible kinds).
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

try:
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    from sklearn.feature_extraction.text import TfidfVectorizer

    BACKEND = "tfidf"
except ImportError:  # pragma: no cover
    BACKEND = "difflib"

# Crafted pairs: true duplicates score >= 0.72, distinct look-alikes ("Weekly sync notes/agenda") <= 0.67.
THRESHOLD = 0.7
CHAR_WEIGHT = 0.5
IDF_MIN_DOCS = 50
GROUP_CAP = {"tfidf": 1500, "difflib": 400}

ENTITY_KINDS = {"topic", "company", "org", "organization", "customer", "project", "product", "entity", "concept"}
_PAIRS = {frozenset(("page", "window")), frozenset(("company", "org")), frozenset(("company", "organization")),
          frozenset(("org", "organization"))} | {frozenset(("topic", kind)) for kind in ENTITY_KINDS - {"topic"}}
_GROUP = {"page": "web", "window": "web", "file": "file", "identifier": "identifier", "person": "person",
          **{kind: "entity" for kind in ENTITY_KINDS}}
_KIND_RANK = {"page": 1}

_BROWSERS = {"google chrome", "chrome", "arc", "safari", "firefox", "mozilla firefox", "microsoft edge", "edge",
             "brave", "opera", "chromium"}
_SEARCH_SUFFIX = {"google search", "bing", "duckduckgo", "search", "yahoo search", "kagi search"}
_SEARCH_HOSTS = re.compile(r"^(?:google\.[a-z.]+|bing\.com|duckduckgo\.com|search\.yahoo\.com|kagi\.com)/search")
_QUERY_KEYS = {"q", "query", "search", "id", "v", "p", "page", "tab", "k", "s"}
_SEPARATORS = re.compile(r"\s+[|\-–—·•»:]\s+")
_COUNTER = re.compile(r"^(?:\(\d+\+?\)|\[\d+\]|[●•*])\s*|\s*(?:\(\d+\+?\)|\[\d+\])$")
_CORPORATE = {"inc", "incorporated", "corp", "corporation", "co", "company", "llc", "ltd", "limited", "plc", "gmbh",
              "ag", "sa", "pbc", "labs"}


def enabled() -> bool:
    return os.environ.get("CHRONICLE_MERGE", "1").strip().lower() not in {"0", "false", "no", "off"}


def words(text: str) -> str:
    text = re.sub(r"['’]", "", (text or "").casefold())
    return re.sub(r"[^\w]+", " ", text).strip()


def normalize_url(url: str | None) -> str | None:
    """Host, path, fragment, and identity-bearing query keys; tracking params dropped."""
    if not url:
        return None
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = re.sub(r"^(?:www\d*|m|mobile)\.", "", parts.netloc.lower().rsplit("@", 1)[-1].split(":")[0])
    if not host:
        return None
    query = sorted((k.lower(), v.strip().casefold()) for k, v in parse_qsl(parts.query) if k.lower() in _QUERY_KEYS)
    path = parts.path.rstrip("/").lower()
    return f"{host}{path}" + (f"?{urlencode(query)}" if query else "") + (f"#{parts.fragment.lower()}" if parts.fragment else "")


def segments(label: str, drop: set[str] = frozenset()) -> tuple[list[str], bool]:
    """Title segments with counters and trailing boilerplate (app, site, browser, search engine) removed."""
    parts = [_COUNTER.sub("", part.strip()).strip() for part in _SEPARATORS.split((label or "").strip())]
    parts = [part for part in parts if part]
    search = False
    while len(parts) > 1 and parts[-1].casefold() in drop | _SEARCH_SUFFIX | _BROWSERS:
        search = search or parts[-1].casefold() in _SEARCH_SUFFIX
        parts.pop()
    return parts, search


@dataclass
class _Item:
    id: str
    kind: str
    label: str
    weight: float
    app: str | None
    url: str | None
    search: bool
    core: str
    head: tuple[str, ...]
    segments: int
    path: str
    ident: str


def _item(node: dict) -> _Item:
    props = node.get("props") or {}
    kind, label = node["kind"], node["label"] or ""
    app = props.get("app")
    url = props.get("url") or (node["id"].split(":", 1)[1] if kind == "page" else None)
    url = normalize_url(url) if kind in ("page", "window") else None
    host = url.split("/", 1)[0] if url else ""
    drop = {value.casefold() for value in (app, host, host.split(".")[0]) if value}
    parts, search = segments(label, drop)
    search = search or bool(url and _SEARCH_HOSTS.match(url) and "q=" in url)
    core = words(" ".join(parts))
    head = tuple(words(parts[0]).split()) if parts else ()
    if _GROUP.get(kind) == "entity":
        while len(head) > 1 and head[-1] in _CORPORATE:
            head = head[:-1]
        if len(parts) == 1:
            core = " ".join(head)
    return _Item(
        id=node["id"], kind=kind, label=label, weight=float(node.get("weight") or 0), app=app, url=url, search=search,
        core=core, head=head, segments=len(parts),
        path=label.strip().replace("\\", "/").removeprefix("./").lower(),
        ident=re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", label.strip()).lower().replace("-", "_"),
    )


def _suffix(left: str, right: str) -> bool:
    return bool(left and right) and (left == right or left.endswith("/" + right) or right.endswith("/" + left))


def rule(a: _Item, b: _Item) -> tuple[float | None, str]:
    """1.0 forces a merge, 0.0 forbids one, None defers to similarity."""
    if a.kind != b.kind and frozenset((a.kind, b.kind)) not in _PAIRS:
        return 0.0, "kind"
    group = _GROUP[a.kind]
    if group == "file":
        return (1.0, "path") if _suffix(a.path, b.path) else (0.0, "path")
    if group == "identifier":
        return (1.0, "identifier") if a.ident == b.ident else (0.0, "identifier")
    if group == "web":
        if "page" in (a.kind, b.kind) or (a.url and b.url):
            return (1.0, "url") if a.url and a.url == b.url else (0.0, "url")
        if a.app != b.app:
            return 0.0, "app"
    if a.search or b.search:
        return (1.0, "query") if a.core and a.core == b.core else (0.0, "query")
    if a.core and a.core.replace(" ", "") == b.core.replace(" ", ""):
        return 1.0, "label"
    numbers = [{token for token in item.core.split() if any(ch.isdigit() for ch in token)} for item in (a, b)]
    if all(numbers) and not (numbers[0] <= numbers[1] or numbers[1] <= numbers[0]):
        return 0.0, "number"
    if group in ("entity", "person") and a.head and b.head:
        if a.head == b.head:
            return 1.0, "head"
        short, long = sorted((a.head, b.head), key=len)
        if (long[: len(short)] == short and len(long) <= 3 and len(" ".join(short)) >= 2
                and all(token.isalpha() for token in long[len(short):])):
            return 1.0, "prefix"
        if short[0] == long[0] and len(short) > 1:
            return 0.0, "prefix"
    return None, ""


def _similarity(items: list[_Item], corpus: list[str], backend: str) -> list[list[float]]:
    cores = [item.core for item in items]
    if backend == "tfidf":
        blocks = []
        # IDF from a handful of labels down-weights every shared token and deflates true duplicates.
        idf = sum(1 for text in corpus if text) >= IDF_MIN_DOCS
        for vectorizer in (TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True, use_idf=idf),
                           TfidfVectorizer(analyzer="word", token_pattern=r"(?u)\b\w+\b", sublinear_tf=True, use_idf=idf)):
            try:
                vectorizer.fit([text for text in corpus if text] or ["_"])
                matrix = vectorizer.transform(cores)
                blocks.append((matrix @ matrix.T).toarray())
            except ValueError:
                blocks.append(np.zeros((len(items), len(items))))
        return (CHAR_WEIGHT * blocks[0] + (1 - CHAR_WEIGHT) * blocks[1]).tolist()
    tokens = [set(core.split()) for core in cores]
    size = len(items)
    sim = [[1.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1, size):
            union = tokens[i] | tokens[j]
            jaccard = len(tokens[i] & tokens[j]) / len(union) if union else 0.0
            ratio = difflib.SequenceMatcher(None, cores[i], cores[j]).ratio() if cores[i] and cores[j] else 0.0
            sim[i][j] = sim[j][i] = CHAR_WEIGHT * ratio + (1 - CHAR_WEIGHT) * jaccard
    return sim


def _matrix(items: list[_Item], corpus: list[str], backend: str):
    sim = _similarity(items, corpus, backend)
    reasons: dict[tuple[int, int], str] = {}
    cannot: set[tuple[int, int]] = set()
    for i in range(len(items)):
        sim[i][i] = 1.0
        for j in range(i + 1, len(items)):
            value, why = rule(items[i], items[j])
            if value is None:
                continue
            sim[i][j] = sim[j][i] = value
            reasons[(i, j)] = why
            if value == 0.0:
                cannot.add((i, j))
    return sim, reasons, cannot


def _linked(i: int, j: int, cannot: set[tuple[int, int]]) -> bool:
    return (min(i, j), max(i, j)) in cannot


def _mean(left: list[int], right: list[int], sim) -> float:
    return sum(sim[i][j] for i in left for j in right) / (len(left) * len(right))


def _split(members: list[int], items: list[_Item], sim, cannot, threshold: float) -> list[list[int]]:
    """Enforce cannot-links inside a linkage cluster by greedy assignment, heaviest first."""
    parts: list[list[int]] = []
    for index in sorted(members, key=lambda i: -items[i].weight):
        options = [part for part in parts if not any(_linked(index, other, cannot) for other in part)]
        best = max(options, key=lambda part: _mean([index], part, sim), default=None)
        if best is not None and _mean([index], best, sim) >= threshold:
            best.append(index)
        else:
            parts.append([index])
    return parts


def _cluster(items: list[_Item], sim, cannot, threshold: float, backend: str) -> list[list[int]]:
    size = len(items)
    if backend == "tfidf":
        distance = np.clip(1.0 - np.asarray(sim), 0.0, 1.0)
        np.fill_diagonal(distance, 0.0)
        labels = fcluster(linkage(squareform(distance, checks=False), method="average"), t=1 - threshold, criterion="distance")
        groups: dict[int, list[int]] = {}
        for index, label in enumerate(labels):
            groups.setdefault(int(label), []).append(index)
        raw = list(groups.values())
    else:
        clusters = {i: [i] for i in range(size)}
        owner = list(range(size))
        pairs = sorted(((sim[i][j], i, j) for i in range(size) for j in range(i + 1, size) if sim[i][j] >= threshold), reverse=True)
        for _, i, j in pairs:
            a, b = owner[i], owner[j]
            if a == b or any(_linked(x, y, cannot) for x in clusters[a] for y in clusters[b]):
                continue
            if _mean(clusters[a], clusters[b], sim) < threshold:
                continue
            for index in clusters[b]:
                owner[index] = a
            clusters[a] += clusters.pop(b)
        raw = list(clusters.values())
    return [part for members in raw if len(members) > 1 for part in _split(members, items, sim, cannot, threshold)]


def plan(nodes: dict[str, dict], threshold: float = THRESHOLD, backend: str | None = None) -> list[dict]:
    """Clusters of node ids to merge, each with a canonical node and per-member scores."""
    backend = backend or BACKEND
    items = [_item(node) for node in nodes.values() if node["kind"] in _GROUP]
    corpus = [item.core for item in items]
    by_group: dict[str, list[_Item]] = {}
    for item in items:
        by_group.setdefault(_GROUP[item.kind], []).append(item)
    found = []
    for group in by_group.values():
        group = sorted(group, key=lambda item: (-item.weight, item.id))[: GROUP_CAP[backend]]
        if len(group) < 2:
            continue
        sim, reasons, cannot = _matrix(group, corpus, backend)
        for members in _cluster(group, sim, cannot, threshold, backend):
            if len(members) < 2:
                continue
            canon = max(members, key=lambda i: (group[i].weight, _KIND_RANK.get(group[i].kind, 0), len(group[i].head),
                                                 -group[i].segments, -len(group[i].label)))
            others = [i for i in members if i != canon]
            found.append({
                "id": group[canon].id, "kind": group[canon].kind, "label": group[canon].label,
                "members": [
                    {
                        "id": group[i].id, "kind": group[i].kind, "label": group[i].label, "weight": group[i].weight,
                        "score": round(float(sim[i][canon]), 3),
                        "reason": reasons.get((min(i, canon), max(i, canon))) or "similarity",
                    }
                    for i in sorted(others, key=lambda i: -sim[i][canon])
                ],
            })
    return sorted(found, key=lambda cluster: (cluster["kind"], cluster["id"]))


def _fold(target: dict, source: dict, cap: int) -> None:
    for key, pick in (("first_seen", min), ("last_seen", max)):
        values = [value for value in (target.get(key), source.get(key)) if value]
        target[key] = pick(values) if values else None
    evidence = target["evidence"]
    for ref in source.get("evidence") or []:
        if len(evidence) >= cap:
            break
        if ref not in evidence:
            evidence.append(ref)


def apply(nodes: dict[str, dict], edges: dict[tuple, dict], clusters: list[dict], cap: int,
          undirected: frozenset[str] = frozenset({"co_occurs"})) -> int:
    """Fold each cluster into its canonical node in place and rewire edges. Returns nodes absorbed."""
    remap: dict[str, str] = {}
    for cluster in clusters:
        target = nodes[cluster["id"]]
        aliases: list[str] = []
        for member in cluster["members"]:
            source = nodes.pop(member["id"], None)
            if source is None:
                continue
            remap[source["id"]] = target["id"]
            # Cross-kind duplicates (a tab's window and page) describe the same frames, so their time is not additive.
            combine = (lambda x, y: x + y) if source["kind"] == target["kind"] else max
            target["weight"] = combine(target["weight"], source["weight"])
            target["seconds"] = combine(target["seconds"], source["seconds"])
            _fold(target, source, cap)
            for key, value in (source.get("props") or {}).items():
                target["props"].setdefault(key, value)
            if source["label"] != target["label"] and source["label"] not in aliases:
                aliases.append(source["label"])
        target["props"]["aliases"] = aliases
        target["props"]["merged"] = [member["id"] for member in cluster["members"]]
    if not remap:
        return 0
    rewired: dict[tuple, dict] = {}
    for edge in edges.values():
        src, dst = remap.get(edge["src"], edge["src"]), remap.get(edge["dst"], edge["dst"])
        if src == dst:
            continue
        if edge["rel"] in undirected and dst < src:
            src, dst = dst, src
        key = (src, dst, edge["rel"])
        item = rewired.get(key)
        if item is None:
            rewired[key] = {**edge, "src": src, "dst": dst, "evidence": list(edge["evidence"])}
        else:
            item["weight"] += edge["weight"]
            _fold(item, edge, cap)
    edges.clear()
    edges.update(rewired)
    return len(remap)
