"""Context files, workflows, and graphs as text: what agents read and what people download.

Every Markdown file can carry its graph as readable text, so one file can be uploaded to
any LLM on its own. JSON, GraphML, and Cypher serve tools and graph databases.
"""

from __future__ import annotations

import json
import re
from xml.sax.saxutils import escape

from chronicle import contexts, db, graph, workflows

GRAPH_FORMATS = {
    "json": ("application/json", "graph.json"),
    "md": ("text/markdown; charset=utf-8", "graph.md"),
    "graphml": ("application/xml", "graphml"),
    "cypher": ("text/plain; charset=utf-8", "cypher"),
}
MAX_LISTED_EDGES = 40
RECORDING_NODES = 300


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "file"


def file_stem(item: dict) -> str:
    return f"{slug(item['title'])}-{item['id']}"


def graph_text(data: dict, heading: str = "## Knowledge graph") -> str:
    """A graph as text an LLM reads cheaply: what each entity is, then how they connect."""
    if not data["nodes"]:
        return ""
    names = {node["id"]: node["label"] for node in data["nodes"]}
    lines = [heading, "", "Entities:"]
    for node in data["nodes"]:
        actions = (node.get("props") or {}).get("actions") or []
        did = "; ".join(item["text"] for item in actions[:3] if item.get("text"))
        lines.append(f"- {node['label']} ({node['kind']})" + (f": {did}" if did else ""))
    edges = sorted(data["edges"], key=lambda edge: -(edge.get("weight") or 0))[:MAX_LISTED_EDGES]
    if edges:
        lines += ["", "Connections:"]
        lines += [f"- {names.get(edge['src'], edge['src'])} → {edge['rel']} → {names.get(edge['dst'], edge['dst'])}" for edge in edges]
    return "\n".join(lines) + "\n"


def _graphml(data: dict) -> str:
    keys = [("kind", "node"), ("label", "node"), ("seconds", "node"), ("weight", "node"), ("rel", "edge"), ("weight", "edge")]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
    for name, target in keys:
        kind = "double" if name in {"seconds", "weight"} else "string"
        lines.append(f'  <key id="{target[0]}_{name}" for="{target}" attr.name="{name}" attr.type="{kind}"/>')
    lines.append('  <graph id="chronicle" edgedefault="directed">')
    for node in data["nodes"]:
        lines.append(f'    <node id="{escape(node["id"], {chr(34): "&quot;"})}">')
        for name in ("kind", "label"):
            lines.append(f'      <data key="n_{name}">{escape(str(node.get(name) or ""))}</data>')
        for name in ("seconds", "weight"):
            lines.append(f'      <data key="n_{name}">{node.get(name) or 0}</data>')
        lines.append("    </node>")
    for index, edge in enumerate(data["edges"]):
        src = escape(edge["src"], {'"': "&quot;"})
        dst = escape(edge["dst"], {'"': "&quot;"})
        lines.append(f'    <edge id="e{index}" source="{src}" target="{dst}">')
        lines.append(f'      <data key="e_rel">{escape(edge["rel"])}</data>')
        lines.append(f'      <data key="e_weight">{edge.get("weight") or 0}</data>')
        lines.append("    </edge>")
    lines += ["  </graph>", "</graphml>"]
    return "\n".join(lines) + "\n"


def _cypher(data: dict) -> str:
    def text(value) -> str:
        return json.dumps(str(value or ""))

    lines = ["// Chronicle knowledge graph. Run in Neo4j Browser or cypher-shell."]
    for node in data["nodes"]:
        label = re.sub(r"[^A-Za-z]", "", node["kind"].title()) or "Entity"
        lines.append(
            f"MERGE (n:Entity:{label} {{id: {text(node['id'])}}}) "
            f"SET n.label = {text(node['label'])}, n.kind = {text(node['kind'])}, n.seconds = {node.get('seconds') or 0};"
        )
    for edge in data["edges"]:
        rel = re.sub(r"[^A-Z_]", "", edge["rel"].upper()) or "RELATED"
        lines.append(
            f"MATCH (a:Entity {{id: {text(edge['src'])}}}), (b:Entity {{id: {text(edge['dst'])}}}) "
            f"MERGE (a)-[r:{rel}]->(b) SET r.weight = {edge.get('weight') or 0};"
        )
    return "\n".join(lines) + "\n"


def graph_body(data: dict, fmt: str, title: str = "Knowledge graph") -> str:
    if fmt == "json":
        return json.dumps({"chronicle": "knowledge-graph", "title": title, **data}, indent=1, ensure_ascii=False)
    if fmt == "md":
        return graph_text(data, f"# {title}")
    if fmt == "graphml":
        return _graphml(data)
    if fmt == "cypher":
        return _cypher(data)
    raise db.ChronicleError(f"format must be one of {', '.join(GRAPH_FORMATS)}", 400)


def graph_download(kind: str, item_id: int | None, fmt: str = "json") -> tuple[str, str, str]:
    """Return (filename, media type, body) for the graph of a recording, context file, or workflow."""
    if fmt not in GRAPH_FORMATS:
        raise db.ChronicleError(f"format must be one of {', '.join(GRAPH_FORMATS)}", 400)
    if kind == "recording":
        data = graph.slim(graph.export(item_id, limit=RECORDING_NODES))
        title, stem = contexts.window(item_id)["label"], slug(f"recording-{item_id}" if item_id else "all-history")
    elif kind in {"context", "workflow"}:
        if item_id is None:
            raise db.ChronicleError(f"{kind} graph needs an id", 400)
        item = contexts.get(item_id) if kind == "context" else workflows.get_workflow(item_id)
        data, title, stem = item["graph"], item["title"], file_stem(item)
    else:
        raise db.ChronicleError("kind must be recording, context, or workflow", 400)
    media_type, extension = GRAPH_FORMATS[fmt]
    return f"{stem}.{extension}", media_type, graph_body(data, fmt, title)


def front(**values) -> str:
    lines = ["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in values.items() if value not in (None, "")] + ["---", ""]
    return "\n".join(lines)


def references(item: dict, flows: list[dict] | None = None) -> list[dict]:
    """Workflows a context file refers to: those made from it, plus those chosen for it, or from its recording if nobody chose."""
    flows = workflows.list_workflows() if flows is None else flows
    if item["composed"]:
        return [flow for flow in flows if flow["id"] in item["sources"]["workflow_ids"] or item["id"] in flow["sources"]["context_ids"]]
    return [flow for flow in flows if item["id"] in flow["sources"]["context_ids"] or (item["session_id"] and item["session_id"] in flow["sources"]["session_ids"])]


def context_document(item: dict) -> str:
    """A context file on its own, for a small session: instructions, related workflows, and its graph."""
    parts = [front(name=slug(item["title"]), type="context", kind=item["preset"], recording=item["session_id"]), item["markdown"].strip()]
    refs = references(item)
    if refs:
        lines = ["## Workflows", "", "When the task matches one of these, follow it step by step:"]
        lines += [f"- **{flow['title']}** (`get_workflow` with id {flow['id']}): {flow['spec']['trigger']}" for flow in refs]
        parts.append("\n".join(lines))
    text = graph_text(item["graph"])
    if text:
        parts.append(text.strip())
    return "\n\n".join(parts) + "\n"


def workflow_document(item: dict, with_graph: bool = True) -> str:
    """A workflow as a file an agent can follow, with the frontmatter skills use."""
    spec = item["spec"]
    summary = f"{spec['summary']} Use when: {spec['trigger']}"
    lines = [front(name=slug(item["title"]), description=summary, type="workflow", apps=item["apps"]),
             f"# {item['title']}", "", spec["summary"], "", "## When to run it", spec["trigger"], "", "## Result", spec["outcome"]]
    if spec["inputs"]:
        lines += ["", "## Inputs"] + [f"- `{entry['name']}`: {entry['description']}" for entry in spec["inputs"]]
    lines += ["", "## Steps"]
    for number, step in enumerate(spec["steps"], 1):
        where = f" ({step['app']})" if step.get("app") else ""
        lines += ["", f"{number}. **{step['title']}**{where}", f"   {step['detail']}"]
        lines += [f"   - {check}" for check in step.get("checks") or []]
        if step.get("confirm"):
            lines.append("   - Ask the person to confirm before doing this.")
    if spec["guardrails"]:
        lines += ["", "## Confirm with the person first"] + [f"- {entry}" for entry in spec["guardrails"]]
    seen = spec["times_seen"]
    lines += ["", "## Evidence", f"Seen {seen} time{'' if seen == 1 else 's'} (frames {', '.join(spec['frames'])}). {item['evidence']}"]
    text = graph_text(item["graph"]) if with_graph else ""
    return "\n".join(lines) + "\n" + (f"\n{text}" if text else "")
