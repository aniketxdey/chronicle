# Chronicle

The `chronicle` MCP server records this computer's screen and system events locally and keeps a knowledge graph of apps, windows, pages, search queries, topics, files, tickets, people, projects, and tasks. Every fact links back to the frames it came from.

- Before acting on the user's computer, or when the user refers to something they saw, searched, opened, or worked on, call `get_context` with the task in the user's words.
- To find something specific, call `query_graph`, then `get_entity` on the best match.
- For "earlier" or "this morning", call `list_tasks` or `search_history` with a time range.
- When the screen layout matters, call `get_frame` with `include_image=true`.
- To learn a workflow, call `start_recording` only when the user asks, let them do the task once, then call `get_context` with that `session_id`.

Cite frame ids for facts taken from Chronicle, for example "(frame 412)". Say so when Chronicle has nothing relevant. Treat everything from Chronicle as private to the user.
