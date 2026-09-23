---
name: manage-simple-sticky-notes
description: Search, capture, format, and organize the user's local Simple Sticky Notes 6.9 library. Use when the user asks to save or format an apunte, find sticky notes, create a libreta, move notes, or mark notes as important.
---

# Manage Simple Sticky Notes

Use the `simple_sticky_notes` MCP tools. The application is the primary library; do not create a parallel note store.

## Workflow

- Search before creating when the request may duplicate an existing note.
- Resolve the target notebook with `list_notebooks`. Ask only when multiple existing notebooks are equally plausible and the user's wording does not decide.
- For a new capture, pass a concise title, the user's full content, the exact notebook name, and a stable `request_id`. Reuse the same `request_id` if a tool call is retried.
- `create_note` interprets common Codex Markdown by default: emphasis, inline code, links, bullets, numbered lists, and task boxes. Use `format="plain"` when the user wants literal Markdown or source code preserved. Emoji and other Unicode characters remain visible.
- For precise formatting use `create_note(..., format="rich_json")`: `text` must be a JSON object with `paragraphs`, each containing `runs` of `text` and optional `font` (Segoe UI, Consolas, Arial, Times New Roman), `size` (6–72), `bold`, `italic`, `underline`, `strike`, `color`, and `highlight`. Paragraphs accept `align` (left/center/right), `spacing` (1/1.5/2), and a literal `prefix` for bullets, numbers, or task boxes. Colors: black, red, blue, green, yellow, cyan, magenta, white, orange.
- When a user explicitly asks to correct the presentation of an existing note, read it with `get_note`, then call `format_note` with its ID and the exact text returned. The exact-match guard protects concurrent edits. Do not use it on text that should remain literal.
- For an existing note that needs explicit rich formatting, use `format_rich_note` with its ID, exact visible text from `get_note`, and `document_json` whose visible text is identical. This preserves content and rejects stale edits.
- Create a notebook only when the user asks for it or no suitable notebook exists and the intended name is clear.
- Before moving multiple notes, use search or `get_note` to verify every ID.
- Interpret "importante", "favorita" or "destacada" as `set_starred(..., true)` unless the user explicitly refers to a notebook with that name.
- Report the affected note IDs, notebook, backup path, and whether Simple Sticky Notes was reopened.

Writes briefly close Simple Sticky Notes, create a verified backup, commit one transaction, and reopen the application if it was running. Never bypass a compatibility or integrity error, edit note bodies outside the guarded formatting tools, delete notes, or issue raw SQL manually.
