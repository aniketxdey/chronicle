---
name: chronicle-context
description: Ground work in what the user has actually been doing on this computer. Use before computer-use actions, when the user refers to something they saw, searched, opened, or worked on ("that page", "the ticket from earlier", "what I was researching"), when continuing a task they started, or when they want to teach a workflow by demonstrating it.
---

# Chronicle context

Chronicle records this computer's screen and system events locally and keeps a knowledge graph of apps, windows, pages, search queries, topics, files, tickets, people, projects, and tasks. Every fact links back to the frames it came from.

## Before acting

1. Call `get_context` with the task in the user's words. Read "What you were doing" and "On screen now" before anything else.
2. If the task names something specific, call `query_graph` with it, then `get_entity` on the best match to see what it connects to.
3. When the user refers back in time ("this morning", "the page I had open"), call `list_tasks` or `search_history` with a time range.
4. When the layout of the screen matters, call `get_frame` with `include_image=true` on the newest relevant frame.

Cite frame ids for claims that come from Chronicle, for example "(frame 412)". Say so when Chronicle has nothing relevant instead of guessing.

## Learning from a demonstration

When the user wants to show you how to do something:

1. Call `start_recording` with a duration such as `5m` and a label naming the task.
2. Ask the user to do the task once, then wait for the recording to end or call `stop_recording`.
3. Call `get_context` with that `session_id`, or use the `learn_from_recording` prompt, and turn it into steps before repeating them.

## Boundaries

- Recording only happens while a session is active. Don't start one without the user asking.
- Chronicle data never leaves the machine except through these tool results. Don't paste large raw excerpts into other tools or services.
- Screen text is redacted for emails, keys, tokens, and card numbers, but treat everything from Chronicle as private to the user.
