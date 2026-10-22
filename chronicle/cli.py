"""chronicle: record, graph, and serve your computer context."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time


def _print(value) -> None:
    if isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, indent=2, default=str))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="chronicle",
        description="Record your screen and system events, build a knowledge graph, and serve it to agents over MCP.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web UI, REST API, and HTTP MCP endpoint")
    serve.add_argument("--host", default=os.environ.get("CHRONICLE_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("CHRONICLE_PORT", "3030")))

    record = sub.add_parser("record", help="record for a preset duration, then build the graph")
    record.add_argument("duration", nargs="?", default="30m", help="e.g. 90s, 15m, 2h, 1h30m")
    record.add_argument("--label")
    record.add_argument("--interval", type=int, default=None, help="seconds between samples")
    record.add_argument("--detach", action="store_true", help="start the session and exit; a running server records it")

    stop = sub.add_parser("stop", help="stop the running recording")
    stop.add_argument("session_id", nargs="?", type=int)

    sub.add_parser("sessions", help="list recordings")

    graph_parser = sub.add_parser("graph", help="build or inspect the knowledge graph")
    graph_sub = graph_parser.add_subparsers(dest="graph_command", required=True)
    build = graph_sub.add_parser("build")
    build.add_argument("--session", type=int)
    build.add_argument("--full", action="store_true", help="re-extract every shard from raw history")
    show = graph_sub.add_parser("show")
    show.add_argument("--session", type=int)
    find = graph_sub.add_parser("find")
    find.add_argument("query", nargs="?")
    find.add_argument("--kind")
    find.add_argument("--session", type=int)
    node = graph_sub.add_parser("node")
    node.add_argument("id")
    node.add_argument("--session", type=int)
    export = graph_sub.add_parser("export")
    export.add_argument("--session", type=int)
    export.add_argument("--limit", type=int, default=500)
    merges = graph_sub.add_parser("merges", help="show which near-duplicate nodes the build merges, with scores")
    merges.add_argument("--session", type=int)
    merges.add_argument("--threshold", type=float)
    merges.add_argument("--json", action="store_true")

    ctx = sub.add_parser("context", help="print the context pack an agent would receive")
    ctx.add_argument("task", nargs="?")
    ctx.add_argument("--minutes", type=int)
    ctx.add_argument("--session", type=int)
    ctx.add_argument("--json", action="store_true")

    mcp = sub.add_parser("mcp", help="run the MCP server on stdio, or set up a client")
    mcp.add_argument("action", nargs="?", choices=["stdio", "config", "install", "clients"], default="stdio")
    mcp.add_argument("client", nargs="?")
    mcp.add_argument("--path", help="config file to write instead of the client's default")
    mcp.add_argument("--dry-run", action="store_true")
    mcp.add_argument("--no-record", action="store_true", help="never capture from this process")

    search_parser = sub.add_parser("search", help="full-text search over history")
    search_parser.add_argument("q")
    search_parser.add_argument("--type", default="all")
    search_parser.add_argument("--limit", type=int, default=10)

    ask_parser = sub.add_parser("ask", help="answer a question from history")
    ask_parser.add_argument("question")

    sub.add_parser("status", help="counts, recording state, and capabilities")
    sub.add_parser("doctor", help="check permissions and optional tools")
    demo = sub.add_parser("demo", help="load a sample workday so you can try everything without recording")
    demo.add_argument("--force", action="store_true", help="wipe existing history first")

    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        os.environ.setdefault("CHRONICLE_PUBLIC_URL", f"http://{'127.0.0.1' if args.host in {'0.0.0.0', '::'} else args.host}:{args.port}")
        print(f"Chronicle on http://{args.host}:{args.port}  ·  MCP at /mcp", file=sys.stderr)
        uvicorn.run("chronicle.main:app", host=args.host, port=args.port, log_level="warning")
        return

    if args.command == "mcp" and args.action == "stdio":
        from chronicle import mcp_server

        mcp_server.serve_stdio(record=not args.no_record)
        return

    from chronicle import db, seed

    seed.ensure()

    try:
        _dispatch(args)
    except db.ChronicleError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        sys.exit(1)


def _dispatch(args) -> None:
    from chronicle import ask, capture, clients, context, db, graph, probe, search, seed, sessions

    if args.command == "record":
        session = sessions.start(args.duration, label=args.label, interval_secs=args.interval)
        print(f"Recording {session['id']} until {session['ends_at'] or 'stopped'}. Ctrl-C stops early.", file=sys.stderr)
        if args.detach:
            _print(session)
            return
        thread, stopper = capture.start_worker()
        interrupted = False

        def _stop(*_):
            nonlocal interrupted
            interrupted = True

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        last = -1
        while not interrupted:
            current = sessions.get(session["id"])
            if current["status"] != "recording":
                break
            sessions.refresh_counts(session["id"])
            if current["frames"] != last:
                last = current["frames"]
                left = f", {current['remaining_secs']}s left" if current["remaining_secs"] is not None else ""
                print(f"  {current['frames']} frames, {current['events']} events{left}", file=sys.stderr)
            error = db.get_setting("last_capture_error")
            if error:
                print(f"  capture: {error}", file=sys.stderr)
            time.sleep(2)
        stopper.set()
        thread.join(timeout=10)
        final = sessions.get(session["id"])
        if final["status"] == "recording":
            final = sessions.stop(session["id"])
        _print({k: final[k] for k in ("id", "status", "elapsed", "frames", "events", "apps", "scope")})
        print(f"\nNext: chronicle context --session {final['id']}", file=sys.stderr)
        return
    if args.command == "stop":
        _print(sessions.stop(args.session_id))
        return
    if args.command == "sessions":
        for item in sessions.list_sessions():
            print(f"{item['id']:>4}  {item['status']:<9} {item['started_at']}  {item['elapsed']:>7}  "
                  f"{item['frames']:>5} frames  {item['label'] or ''}")
        return
    if args.command == "graph":
        session = getattr(args, "session", None)
        if args.graph_command == "build":
            _print(graph.rebuild() if args.full and not session else graph.build(session))
        elif args.graph_command == "show":
            summary = graph.summary(session)
            decay = ""
            if "half_life_hours" in summary:
                half = summary["half_life_hours"]
                decay = f", ranked by weight decayed with a {half:g}h half-life" if half else ", no decay"
            print(f"Graph {summary['scope']}: {sum(summary['counts'].values())} nodes, {summary['edges']} edges{decay}")
            for kind, count in summary["counts"].items():
                names = ", ".join(item["label"] for item in summary["top"].get(kind, [])[:5])
                print(f"  {kind:<11} {count:>4}  {names}")
            if summary["transitions"]:
                print("Transitions:")
                for item in summary["transitions"][:8]:
                    print(f"  {item['from']} -> {item['to']} ({int(item['count'])}x)")
        elif args.graph_command == "find":
            for item in graph.find(args.query, kind=args.kind, session_id=session):
                print(f"{item['id']:<48} {item['kind']:<10} {int(item['seconds']):>6}s  {item['label']}")
        elif args.graph_command == "node":
            _print(graph.node(args.id, session_id=session))
        elif args.graph_command == "merges":
            found = graph.merges(session, threshold=args.threshold)
            if args.json:
                _print(found)
                return
            absorbed = sum(len(cluster["members"]) for cluster in found["clusters"])
            state = "" if found["enabled"] else ", disabled by CHRONICLE_MERGE=0"
            print(f"Merges for {found['scope']} ({found['backend']}, threshold {found['threshold']}{state}): "
                  f"{len(found['clusters'])} clusters, {absorbed} of {found['nodes']} nodes absorbed")
            for cluster in found["clusters"]:
                print(f"  {cluster['kind']:<8} {cluster['label']}  [{cluster['id']}]")
                for member in cluster["members"]:
                    print(f"    <- {member['score']:.2f} {member['reason']:<10} {member['kind']:<8} {member['label']}")
        else:
            _print(graph.export(session, limit=args.limit))
        return
    if args.command == "context":
        pack = context.build(task=args.task, minutes=args.minutes, session_id=args.session)
        if args.json:
            _print({k: v for k, v in pack.items() if k != "markdown"})
        else:
            print(pack["markdown"])
        return
    if args.command == "mcp":
        if args.action == "clients":
            for item in clients.catalog():
                mark = "detected" if item["detected"] else ""
                print(f"{item['id']:<15} {item['transport']:<6} {item['label']:<40} {mark}")
            return
        if not args.client:
            raise db.ChronicleError("name a client: chronicle mcp clients lists them", 400)
        if args.action == "config":
            result = clients.config(args.client)
            print(result["snippet"])
            for step in result.get("steps", []):
                print(f"# {step}", file=sys.stderr)
            return
        _print(clients.install(args.client, path=args.path, dry_run=args.dry_run))
        return
    if args.command == "search":
        found = search.query(q=args.q, content_type=args.type, limit=args.limit)
        for item in found["data"]:
            body = item["content"]
            where = " · ".join(p for p in (body.get("app_name") or body.get("speaker_name"), body.get("window_name")) if p)
            print(f"{body.get('timestamp')}  [{item['type']}] {where}\n    {(body.get('text') or '')[:200]}")
        return
    if args.command == "ask":
        print(ask.prepare(args.question)["answer"])
        return
    if args.command == "status":
        _print({"home": str(db.home()), **db.counts(), "db_bytes": db.db_bytes(), "capture": capture.status()})
        return
    if args.command == "doctor":
        _doctor(probe)
        return
    if args.command == "demo":
        if args.force:
            db.wipe()
            seed.ensure()
        loaded = seed.load_sample()
        print("Loaded the sample workday." if loaded else "History is not empty; use --force to replace it with the sample.")
        print(f"Try: chronicle graph show  ·  chronicle context 'finish the Acme export'", file=sys.stderr)


def _doctor(probe) -> None:
    caps = probe.capabilities()
    print(f"platform: {caps['platform']}")
    try:
        front = probe.frontmost()
        print(f"[ok] front window: {front.app} · {front.window}" + (f" <{front.url}>" if front.url else ""))
    except Exception as exc:  # noqa: BLE001
        print(f"[!!] front window: {exc}")
    idle = probe.idle_seconds()
    print(f"[{'ok' if idle is not None else '--'}] idle time: {idle if idle is not None else 'unavailable'}")
    clip = probe.clipboard()
    print(f"[{'ok' if clip is not None else '--'}] clipboard: {'readable' if clip is not None else 'unavailable'}")
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "probe.jpg"
        ok = probe.screenshot(target)
        size = target.stat().st_size if ok else 0
        print(f"[{'ok' if ok else '!!'}] screenshot: {f'{size // 1024} KB' if ok else 'failed'}")
        if ok and size < 40_000 and sys.platform == "darwin":
            print("     a tiny screenshot usually means Screen Recording permission is missing for this terminal")
    print(f"[{'ok' if caps['ocr'] else '--'}] OCR: {'tesseract' if caps['ocr'] else 'install tesseract for screen text (brew install tesseract)'}")
    if sys.platform == "darwin":
        trusted = caps["accessibility"]
        print(f"[{'ok' if trusted else '!!'}] accessibility: {'granted' if trusted else probe.AX_HINT}")
        result = probe.ax_text(unlock=False, build="wait") if trusted else None
        if result is not None:
            text, meta = result
            built = f", built Swift helper in {meta['compile_ms']} ms" if meta.get("compiled") else ""
            print(f"[{'ok' if meta['status'] == 'ok' else '!!'}] screen text via AX ({meta['helper']}{built}): "
                  f"{meta['status']}, {len(text)} chars from {meta.get('app') or '?'} in {meta['elapsed_ms']} ms")
        elif trusted:
            print("[--] screen text via AX: no helper (needs osascript, or swiftc from the Command Line Tools)")
        print("macOS: grant your terminal (or the MCP host app) Screen Recording and Accessibility in "
              "System Settings > Privacy & Security.")


if __name__ == "__main__":
    main()
