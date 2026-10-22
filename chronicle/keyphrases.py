"""Corpus-level keyphrase scoring for window titles, page titles, OCR text, and search queries.

Each distinct frame text in a build scope is one document. Candidates are
stopword-delimited n-grams (RAKE) plus capitalized runs; they are scored with
dwell-weighted, position-weighted term frequency times smoothed IDF, boosted by
casing and phrase length (YAKE-like), then kept top-K per scope and per document.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from urllib.parse import parse_qs, urlsplit

PER_DOC = 6
SCOPE_TOP = 30
MIN_SCORE = 0.05
MAX_N = 3
OCR_CHARS = 6000
TITLE_WEIGHT, OCR_WEIGHT, QUERY_WEIGHT = 1.0, 0.4, 2.5

STOP = set("""
a about above after again against all also am an and any are as at be because been before being below between both
but by can could did do does doing down during each either few for from further had has have having he her here hers
him his how i if in into is it its itself just me more most my no nor not now of off on once only or other our ours out
over own same she should so some such than that the their them then there these they this those through to too under
until up very via vs was we were what when where which while who whom why will with would you your yours
still yet already even ok okay yes please thanks let let's need needs today tomorrow yesterday monday tuesday
wednesday thursday friday saturday sunday january february march april may june july august september october
november december
""".split())
GENERIC = set("""
top best list lists news overview home page official site website welcome search results result new latest review
reviews guide login log sign signin signup account dashboard settings menu untitled loading tab tabs view edit file
help share more click here read watch video videos free online get see find learn things stuff space field industry
sector area full using use how-to tips ways untitled-1 inbox company profile pr pull request issue issues
commit commits checks
""".split())
JOIN = {"of", "&", "and", "de", "for", "the"}
SITE_SUFFIXES = {
    "google search", "google", "bing", "duckduckgo", "youtube", "perplexity", "wikipedia", "github", "reddit", "medium",
    "x", "twitter", "linkedin", "google chrome", "chrome", "arc", "safari", "firefox", "mozilla firefox", "microsoft edge",
}
ENGINES = {"google": "google", "bing": "bing", "duckduckgo": "duckduckgo", "youtube": "youtube",
           "perplexity": "perplexity", "yahoo": "yahoo", "brave": "brave"}
QUERY_KEYS = ("q", "query", "search_query", "p")
_ENGINE_TITLE = re.compile(r"^(.+?)\s+[-—–|]\s+(Google Search|Bing|DuckDuckGo|Perplexity|Brave Search)\s*$")

_URL = re.compile(r"https?://\S+|\bwww\.\S+")
_SEGMENT = re.compile(r"\s[-–—|·•:]\s|[\n\r\t|•·;!?()\[\]{}\"“”<>«»]|[.,:](?=\s|$)")
_TITLE_SPLIT = re.compile(r"\s+[-–—|·•]\s+")
_WORD = re.compile(r"&|[A-Za-z0-9_](?:[A-Za-z0-9_'’+./-]*[A-Za-z0-9_+])?")
_YEAR = re.compile(r"(?:19|20)\d\d")
_CODE = re.compile(r".*[_./#].*|[A-Z][A-Z0-9]{1,9}-\d+|[a-z]+(?:[A-Z][a-z0-9]+)+|[A-Z0-9]{7,}")


def search_query(url: str | None) -> tuple[str, str] | None:
    """(engine, query) for a search-engine results URL."""
    if not url:
        return None
    parts = urlsplit(url if "://" in url else f"https://{url}")
    labels = parts.netloc.lower().split(":")[0].split(".")
    engine = next((ENGINES[label] for label in labels if label in ENGINES), None)
    if engine is None:
        return None
    params = parse_qs(parts.query)
    for key in QUERY_KEYS:
        value = " ".join((params.get(key) or [""])[0].split())
        if value:
            return engine, value[:200]
    return None


def title_query(title: str | None) -> tuple[str, str] | None:
    """(engine, query) from a results-page title like 'crusoe funding - Google Search', for frames without a URL."""
    found = _ENGINE_TITLE.match((title or "").strip())
    if not found:
        return None
    return found.group(2).split()[0].lower(), " ".join(found.group(1).split())[:200]


def strip_title(title: str | None, host: str | None = None) -> str:
    """Drop trailing site and browser boilerplate such as ' - Google Search' or ' | Forbes'."""
    title = re.sub(r"^\(\d+\)\s*", "", (title or "").strip())
    brand = (host or "").removeprefix("www.").split(".")[0].lower()
    parts = _TITLE_SPLIT.split(title)
    while len(parts) > 1 and (parts[-1].strip().lower() in SITE_SUFFIXES or (brand and parts[-1].strip().lower() == brand)):
        parts.pop()
    return " | ".join(parts).strip()


def _is_cap(token: str) -> bool:
    return token[:1].isupper() or (token[:1].islower() and any(ch.isupper() for ch in token[1:]))


def _is_acronym(token: str) -> bool:
    return token.isupper() and sum(ch.isalpha() for ch in token) >= 2


def _valid(gram: list[str]) -> bool:
    first, last = gram[0], gram[-1]
    if first.lower() in JOIN or last.lower() in JOIN or first.isdigit():
        return False
    if last.isdigit() and not (len(gram) > 1 and (_is_cap(gram[-2]) or _is_acronym(gram[-2]))):
        return False
    if len(gram) == 1:
        return len(first) >= 3 or _is_acronym(first)
    return True


def candidates(text: str) -> list[tuple[str, str]]:
    """(key, surface) candidate phrases in text, one entry per occurrence."""
    found: list[tuple[str, str]] = []
    for segment in _SEGMENT.split(_URL.sub(" ", text or "")):
        tokens = _WORD.findall(segment)
        chunks: list[list[str]] = []
        current: list[str] = []
        for index, token in enumerate(tokens):
            low = token.lower()
            if _YEAR.fullmatch(token):
                continue
            if low in STOP or low in GENERIC or token == "&" or _CODE.fullmatch(token):
                following = tokens[index + 1] if index + 1 < len(tokens) else ""
                if low in JOIN and current and _is_cap(current[-1]) and following and _is_cap(following) and following.lower() not in GENERIC:
                    current.append(token)
                    continue
                if current:
                    chunks.append(current)
                current = []
                continue
            current.append(token)
        if current:
            chunks.append(current)
        for chunk in chunks:
            grams = {tuple(chunk[i : i + n]) for n in range(1, MAX_N + 1) for i in range(len(chunk) - n + 1)}
            if 3 < len(chunk) <= 5 and all(_is_cap(t) or t.isdigit() or t.lower() in JOIN for t in chunk):
                grams.add(tuple(chunk))
            for gram in sorted(grams, key=lambda g: (chunk.index(g[0]), len(g))):
                if _valid(list(gram)):
                    found.append((" ".join(gram).lower(), " ".join(gram)))
    return found


def _idf(documents: list[list[str]]) -> dict[str, float]:
    """Smoothed IDF, ln((1 + n) / (1 + df)) + 1, matching scikit-learn's TfidfVectorizer."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(analyzer=lambda doc: doc, lowercase=False)
        vectorizer.fit(documents)
        return dict(zip(vectorizer.get_feature_names_out(), (float(v) for v in vectorizer.idf_)))
    except (ImportError, ValueError):
        return _idf_python(documents)


def _idf_python(documents: list[list[str]]) -> dict[str, float]:
    df: Counter[str] = Counter()
    for doc in documents:
        df.update(set(doc))
    total = len(documents)
    return {key: math.log((1 + total) / (1 + count)) + 1 for key, count in df.items()}


class Corpus:
    """Collect frames, then rank keyphrases across the whole scope."""

    def __init__(self, exclude: set[str] | None = None) -> None:
        self.exclude = {" ".join(item.lower().split()) for item in exclude or ()} - {""}
        self._names = [item for item in self.exclude if " " in item]
        self._name_tokens = {token for item in self._names for token in item.split()}
        self.frame_doc: dict[int, str] = {}
        self.frame_query: dict[int, tuple[str, str]] = {}
        self.docs: dict[str, dict] = {}
        self.scores: dict[str, float] = {}
        self.labels: dict[str, str] = {}
        self._doc_top: dict[str, list[str]] = {}

    def add(self, frame_id: int, title: str | None, text: str | None, url: str | None, seconds: float = 0) -> None:
        host = urlsplit(url).netloc.lower() if url and "://" in url else None
        query = search_query(url) or (None if url else title_query(title))
        clean = strip_title(title, host)
        ocr = (text or "").replace(title or "\0", " ")[:OCR_CHARS] if title else (text or "")[:OCR_CHARS]
        ocr = " ".join(_URL.sub(" ", ocr).split())
        key = f"{clean}\n{query[1] if query else ''}\n{ocr}"
        doc = self.docs.get(key)
        if doc is None:
            doc = self.docs[key] = {"title": clean, "ocr": ocr, "query": query[1] if query else "", "seconds": 0.0}
        doc["seconds"] += seconds or 0
        self.frame_doc[frame_id] = key
        if query:
            self.frame_query[frame_id] = query

    def rank(self, per_doc: int = PER_DOC, top: int = SCOPE_TOP) -> None:
        tf: dict[str, Counter[str]] = {}
        sources: dict[str, set[str]] = defaultdict(set)
        surfaces: dict[str, Counter[str]] = defaultdict(Counter)
        token_forms: dict[str, Counter[str]] = defaultdict(Counter)
        for doc_key, doc in self.docs.items():
            counts: Counter[str] = Counter()
            for field, weight in (("title", TITLE_WEIGHT), ("query", QUERY_WEIGHT), ("ocr", OCR_WEIGHT)):
                for key, surface in candidates(doc[field]):
                    if self._excluded(key):
                        continue
                    counts[key] += weight
                    sources[key].add(field)
                    surfaces[key][surface] += 1
                    for token in surface.split():
                        token_forms[token.lower()][token] += 1
            tf[doc_key] = counts
        if not tf:
            return
        idf = _idf([list(counts) for counts in tf.values()])
        df = Counter(key for counts in tf.values() for key in counts)
        total = len(tf)
        self.labels = {key: _label(forms, token_forms) for key, forms in surfaces.items()}
        boost = {key: _casing(label) * _length(label) for key, label in self.labels.items()}
        local: dict[str, dict[str, float]] = {}
        scope: Counter[str] = Counter()
        for doc_key, counts in tf.items():
            dwell = 1 + math.log1p(self.docs[doc_key]["seconds"])
            scored = {}
            for key, count in counts.items():
                if sources[key] == {"ocr"} and total >= 5 and df[key] / total > 0.7:
                    continue
                sublinear = 1 + math.log(count) if count >= 1 else count
                scored[key] = sublinear * idf[key] * dwell * boost[key]
                scope[key] += scored[key]
            local[doc_key] = scored
        kept: list[str] = []
        for key, _ in scope.most_common():
            if len(kept) >= top:
                break
            if any(_contains(other, key) and df[key] <= df[other] for other in kept):
                continue
            kept.append(key)
        best = scope[kept[0]] if kept else 1
        self.scores = {key: round(scope[key] / best, 3) for key in kept if scope[key] / best >= MIN_SCORE}
        for doc_key, scored in local.items():
            ranked = sorted((key for key in scored if key in self.scores), key=lambda k: -scored[k])
            self._doc_top[doc_key] = ranked[:per_doc]

    def _excluded(self, key: str) -> bool:
        """Names already graphed as people or apps, their parts, and phrases that contain them."""
        return (key in self.exclude or set(key.split()) <= self._name_tokens
                or any(_contains(key, name) for name in self._names))

    def topics(self, frame_id: int) -> list[tuple[str, str, float]]:
        """(key, label, score) for the frame's top phrases."""
        keys = self._doc_top.get(self.frame_doc.get(frame_id, ""), [])
        return [(key, self.labels[key], self.scores[key]) for key in keys]

    def query(self, frame_id: int) -> tuple[str, str] | None:
        return self.frame_query.get(frame_id)

    def query_topics(self, query: str) -> list[str]:
        return list(dict.fromkeys(key for key, _ in candidates(query) if key in self.scores))


def _contains(phrase: str, part: str) -> bool:
    return phrase != part and f" {part} " in f" {phrase} "


def _label(forms: Counter[str], token_forms: dict[str, Counter[str]]) -> str:
    surface = max(forms, key=lambda s: (any(ch.isupper() for ch in s), forms[s]))
    tokens = []
    for token in surface.split():
        acronym = next((form for form, _ in token_forms[token.lower()].most_common() if _is_acronym(form)), None)
        tokens.append(acronym if token.islower() and acronym else token)
    return " ".join(tokens)


def _casing(label: str) -> float:
    tokens = [t for t in label.split() if t.lower() not in JOIN]
    if any(_is_acronym(t) for t in tokens):
        return 1.4
    if all(_is_cap(t) or t.isdigit() for t in tokens):
        return 1.25
    return 1.1 if any(_is_cap(t) for t in tokens) else 0.85 if len(tokens) == 1 else 1.0


def _length(label: str) -> float:
    return (1.0, 1.3, 1.45, 1.55, 1.6)[min(len(label.split()), 5) - 1]
