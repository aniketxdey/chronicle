#!/usr/bin/env python3
"""Run the configured model over the sample workday in a throwaway folder and score the graph it builds.

Usage: python scripts/eval_graph.py [--home DIR]    (a saved --home is scored as is, without calling a model)
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile

EXPECTED = ["PR 882", "BILL-142", "export.ts", "Priya Shah", "Maya Chen", "Jordan Hale", "Acme"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="score this existing Chronicle folder instead of running the model")
    args = parser.parse_args()
    os.environ["CHRONICLE_HOME"] = args.home or tempfile.mkdtemp(prefix="chronicle-eval-")
    os.environ["CHRONICLE_DISABLE_JOBS"] = os.environ["CHRONICLE_DISABLE_CAPTURE"] = "1"

    from chronicle import db, graph, llm, seed, tuner

    if not args.home:
        db.init()
        seed.ensure()
        seed.load_sample()
        result = tuner.tune(None)
        print(json.dumps({key: result.get(key) for key in ("status", "error", "model", "calls", "applied")}))
        print("rejected:", len(result["rejected"]))
    report = tuner.quality(None)
    labels = [node["label"].lower() for node in graph.find(None, limit=500)]
    report["expected_found"] = [name for name in EXPECTED if any(name.lower() in label for label in labels)]
    print(json.dumps(report, indent=2))
    if not args.home:
        nodes, _ = tuner._load("all")
        for node in sorted(nodes.values(), key=lambda item: -len(item["props"].get("actions", []))):
            for item in node["props"].get("actions", []):
                print(f"{node['label'][:32]:32} [{item.get('status', '-'):9}] {item['text']}")
    print("model:", llm.status()["model"])


if __name__ == "__main__":
    main()
