"""Statistical keyphrase scoring: candidates, search queries, corpus ranking, and graph wiring."""

from __future__ import annotations

import pytest

from chronicle import graph, keyphrases


def test_search_query_engines():
    cases = {
        "https://www.google.com/search?q=top+50+startups+in+physical+ai&sca_esv=1": ("google", "top 50 startups in physical ai"),
        "https://www.google.co.uk/search?q=crusoe+energy": ("google", "crusoe energy"),
        "https://www.bing.com/search?q=forbes+ai+50&form=QBLH": ("bing", "forbes ai 50"),
        "https://duckduckgo.com/?q=figure+robotics&ia=web": ("duckduckgo", "figure robotics"),
        "https://www.youtube.com/results?search_query=humanoid+robots": ("youtube", "humanoid robots"),
        "https://www.perplexity.ai/search?q=who+funds+crusoe": ("perplexity", "who funds crusoe"),
        "https://search.yahoo.com/search?p=physical+ai": ("yahoo", "physical ai"),
    }
    for url, expected in cases.items():
        assert keyphrases.search_query(url) == expected, url
    assert keyphrases.search_query("https://github.com/search?q=chronicle") is None
    assert keyphrases.search_query("https://www.google.com/maps") is None
    assert keyphrases.search_query(None) is None


def test_strip_title_boilerplate():
    assert keyphrases.strip_title("physical ai - Google Search") == "physical ai"
    assert keyphrases.strip_title("Crusoe Energy raises $600M | Forbes", "www.forbes.com") == "Crusoe Energy raises $600M"
    assert keyphrases.strip_title("(3) Humanoid robots - YouTube") == "Humanoid robots"
    assert keyphrases.strip_title("Chronicle — autoworker") == "Chronicle | autoworker"


def test_candidates_capitalized_runs_and_ngrams():
    keys = {key for key, _ in keyphrases.candidates("Forbes 2026 AI 50 List | Bank of America backs Crusoe Energy in the physical ai space")}
    assert {"forbes ai 50", "bank of america", "crusoe energy", "physical ai"} <= keys
    assert not keys & {"list", "space", "2026", "of america", "the"}


def test_corpus_ranks_prose_topics():
    corpus = keyphrases.Corpus()
    chrome = "File Edit View History Bookmarks Window Help"
    frames = [
        (1, "top 50 startups in physical ai - Google Search", "https://www.google.com/search?q=top+50+startups+in+physical+ai", 20),
        (2, "Forbes 2026 AI 50 List | Top Artificial Intelligence Companies", "https://www.forbes.com/lists/ai50/", 60),
        (3, "Crusoe Energy | Forbes", "https://www.forbes.com/companies/crusoe/", 40),
        (4, "Crusoe Energy raises funding | Forbes", "https://www.forbes.com/sites/crusoe-raise/", 30),
        (5, "Inbox - Gmail", "https://mail.google.com/", 5),
    ]
    for frame_id, title, url, seconds in frames:
        body = f"{chrome}\nCrusoe Energy builds AI data centers powered by stranded gas." if frame_id in (3, 4) else chrome
        corpus.add(frame_id, title, f"{title}\n{body}", url, seconds)
    corpus.rank()
    labels = {corpus.labels[key] for key in corpus.scores}
    assert {"physical AI", "Forbes AI 50", "Crusoe Energy"} <= labels
    assert not {"Google Search", "Google", "Bookmarks", "History"} & labels
    assert max(corpus.scores.values()) == 1.0
    assert corpus.query(1) == ("google", "top 50 startups in physical ai")
    assert "physical ai" in corpus.query_topics("top 50 startups in physical ai")
    top_three = [label for _, label, _ in corpus.topics(3)][:3]
    assert "Crusoe Energy" in top_three
    assert len(corpus.topics(2)) <= keyphrases.PER_DOC


def test_dwell_time_raises_score():
    corpus = keyphrases.Corpus()
    corpus.add(1, "Figure Robotics humanoid demo", "", None, 300)
    corpus.add(2, "Agility Robotics warehouse pilot", "", None, 2)
    corpus.rank()
    assert corpus.scores["figure robotics"] > corpus.scores["agility robotics"]


def test_excludes_known_people_and_apps():
    corpus = keyphrases.Corpus(exclude={"maya chen", "maya", "Google Chrome"})
    corpus.add(1, "DM Maya Chen about the Acme Export", "Chen approved the Acme Export in Google Chrome", None, 60)
    corpus.rank()
    assert "acme export" in corpus.scores
    assert not {"maya chen", "chen", "dm maya chen", "google chrome", "chrome"} & corpus.scores.keys()


def test_idf_matches_sklearn():
    pytest.importorskip("sklearn")
    docs = [["a b", "c"], ["c"], ["a b", "d", "c"]]
    sklearn_idf = keyphrases._idf(docs)
    python_idf = keyphrases._idf_python(docs)
    assert sklearn_idf.keys() == python_idf.keys()
    assert all(abs(sklearn_idf[key] - python_idf[key]) < 1e-9 for key in docs[2])


def test_graph_builds_query_and_topic_nodes(client):
    session = client.post("/sessions", json={"duration": "15m", "label": "physical ai research"}).json()
    for text, window, url in (
        ("top 50 startups in physical ai space", "top 50 startups in physical ai space - Google Search",
         "https://www.google.com/search?q=top+50+startups+in+physical+ai+space"),
        ("The list includes Crusoe Energy and Figure AI.", "Forbes 2026 AI 50 List | Top Artificial Intelligence Companies",
         "https://www.forbes.com/lists/ai50/"),
        ("Crusoe Energy builds AI data centers.", "Crusoe | Company Overview & News", "https://www.forbes.com/companies/crusoe/"),
    ):
        client.post("/add", json={"text": text, "app_name": "Arc", "window_name": window, "browser_url": url, "session_id": session["id"]})
    client.post(f"/sessions/{session['id']}/stop")
    summary = client.get("/graph/summary", params={"session_id": session["id"]}).json()
    assert summary["counts"]["query"] == 1 and summary["counts"]["topic"] >= 3
    assert all("score" in item for item in summary["top"]["topic"])
    scores = [item["score"] for item in summary["top"]["topic"]]
    assert scores == sorted(scores, reverse=True)

    query = client.get("/graph/node", params={"id": "query:top-50-startups-in-physical-ai-space", "session_id": session["id"]}).json()
    assert query["node"]["props"]["engine"] == "google"
    rels = {(item["rel"], item["direction"], item["id"]) for item in query["neighbors"]}
    assert ("searched", "in", "page:google.com/search?q=top-50-startups-in-physical-ai-space") in rels
    assert ("about", "out", "topic:physical-ai") in rels

    found = client.get("/graph/search", params={"q": "crusoe", "session_id": session["id"]}).json()["data"]
    assert found[0]["kind"] == "topic" and found[0]["label"].startswith("Crusoe")
    assert 0 < found[0]["props"]["score"] <= 1
    crusoe = next(item for item in found if item["id"] == "topic:crusoe")
    assert "Crusoe Energy" in crusoe["props"].get("aliases", [])

    pack = client.get("/context", params={"session_id": session["id"], "task": "physical ai startups"}).json()
    assert "## Topics and searches" in pack["markdown"]
    assert "top 50 startups in physical ai space" in pack["searches"]
    assert any(item["label"] == "physical AI" for item in pack["topics"])
    assert graph.extract("no code tokens here") == []


def test_brave_and_title_queries():
    assert keyphrases.search_query("https://search.brave.com/search?q=crusoe+energy") == ("brave", "crusoe energy")
    assert keyphrases.title_query("crusoe funding round - Google Search") == ("google", "crusoe funding round")
    assert keyphrases.title_query("Crusoe | Company Overview & News") is None
