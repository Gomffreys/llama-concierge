"""
Converts a llama.cpp web UI conversation export (.jsonl) into a readable
.md file.

The export format (see llama.cpp's tools/ui/src/lib/services/
conversation-transfer.service.ts): the first line is a SESSION record
carrying conversation metadata; each following line is a MESSAGE record.
Messages form a branching tree (edit/regenerate creates sibling branches),
so this walks back from the conversation's `currNode` pointer via each
message's `parent` field to reconstruct the single active path — mirroring
the web UI's own `filterByLeafNodeId` — rather than dumping every branch.
"""

import json
import sys
from datetime import datetime

ROLE_HEADINGS = {
    "system": "## ⚙️ System Prompt",
    "user": "### 🧑 You",
    "assistant": "### 🤖 Assistant",
    "tool": "### 🔧 Tool Result",
}

TEXT_ATTACHMENT_TYPES = {"TEXT", "PDF", "context", "MCP_PROMPT", "MCP_RESOURCE"}


def parse_sessions(text):
    """Returns a list of {"conv": {...}, "messages": [...]} dicts."""
    sessions = []
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("type") == "session":
            conv = dict(record)
            conv.pop("type", None)
            conv.pop("harness", None)
            current = {"conv": conv, "messages": []}
            sessions.append(current)
        elif record.get("type") == "message":
            if current is None:
                raise ValueError("Malformed JSONL: message record before any session record")
            current["messages"].append(record["message"])
    return sessions


def resolve_active_path(conv, messages):
    """Walks from conv['currNode'] back to the root, returns the active-branch
    messages (root excluded) sorted system-first then by timestamp — mirrors
    the web UI's filterByLeafNodeId()."""
    by_id = {m["id"]: m for m in messages}

    start = by_id.get(conv.get("currNode"))
    if start is None:
        if not messages:
            return []
        start = max(messages, key=lambda m: m.get("timestamp", 0))

    path = []
    node = start
    while node is not None:
        if node.get("type") != "root":
            path.append(node)
        parent_id = node.get("parent")
        if parent_id is None:
            break
        node = by_id.get(parent_id)

    path.sort(key=lambda m: (0 if m.get("role") == "system" else 1, m.get("timestamp", 0)))
    return path


def format_timestamp(ms):
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return ""


def render_attachment(extra):
    kind = extra.get("type", "")
    name = extra.get("name", "attachment")
    size = extra.get("size")
    size_note = f", {size} bytes" if size else ""

    if kind == "IMAGE":
        src = extra.get("base64Url", "")
        return f"![{name}]({src})"

    if kind in ("AUDIO", "VIDEO"):
        return f"📎 *Attachment: {name} ({kind.lower()}{size_note})*"

    if kind in TEXT_ATTACHMENT_TYPES:
        content = extra.get("content", "")
        label = f"📎 {name}" if name else "📎 Attachment"
        return f"<details>\n<summary>{label}</summary>\n\n```\n{content}\n```\n\n</details>"

    return f"📎 *Attachment: {name} ({kind}{size_note})*"


def render_tool_calls(tool_calls):
    blocks = []
    for call in tool_calls:
        fn = call.get("function", {}) if isinstance(call, dict) else {}
        name = fn.get("name", "unknown")
        args = fn.get("arguments", "")
        try:
            args = json.dumps(json.loads(args), indent=2)
        except (TypeError, ValueError):
            pass
        blocks.append(f"🔧 **Tool call: `{name}`**\n```json\n{args}\n```")
    return "\n\n".join(blocks)


def render_message(message):
    role = message.get("role", "")
    lines = []

    heading = ROLE_HEADINGS.get(role, f"### {role or 'unknown'}")
    if role == "assistant" and message.get("model"):
        heading += f"  _{message['model']}_"
    lines.append(heading)

    ts = format_timestamp(message.get("timestamp"))
    if ts:
        lines.append(f"*{ts}*")
    lines.append("")

    reasoning = message.get("reasoningContent") or message.get("thinking")
    if reasoning:
        lines.append(f"<details>\n<summary>💭 Reasoning</summary>\n\n{reasoning}\n\n</details>")
        lines.append("")

    content = message.get("content", "")
    if content and role == "tool":
        try:
            content = json.dumps(json.loads(content), indent=2)
        except (TypeError, ValueError):
            pass
        lines.append(f"```json\n{content}\n```" if content.lstrip().startswith(("{", "[")) else f"```\n{content}\n```")
        lines.append("")
    elif content:
        lines.append(content)
        lines.append("")

    tool_calls = message.get("toolCalls")
    if tool_calls:
        lines.append(render_tool_calls(tool_calls))
        lines.append("")

    for extra in message.get("extra") or []:
        lines.append(render_attachment(extra))
        lines.append("")

    return "\n".join(lines)


def render_session_markdown(conv, path_messages):
    lines = [f"# {conv.get('name') or 'Untitled Conversation'}", ""]

    meta = []
    last_modified = format_timestamp(conv.get("lastModified"))
    if last_modified:
        meta.append(f"**Last modified:** {last_modified}")
    if conv.get("id"):
        meta.append(f"**Conversation ID:** `{conv['id']}`")
    if conv.get("cwd"):
        meta.append(f"**Working directory:** `{conv['cwd']}`")
    models = sorted({m["model"] for m in path_messages if m.get("model")})
    if models:
        meta.append(f"**Model(s):** {', '.join(models)}")
    if meta:
        lines.append("  \n".join(meta))
        lines.append("")

    lines.append("---")
    lines.append("")

    for message in path_messages:
        lines.append(render_message(message))
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def convert_jsonl_to_markdown(input_path, output_path):
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    sessions = parse_sessions(text)
    if not sessions:
        raise ValueError("No session records found in file")

    rendered = []
    for session in sessions:
        path_messages = resolve_active_path(session["conv"], session["messages"])
        rendered.append(render_session_markdown(session["conv"], path_messages))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(rendered))


def main():
    if len(sys.argv) < 2:
        print("Usage: python jsonl_to_markdown.py <input.jsonl> [output.md]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else input_path.rsplit(".", 1)[0] + ".md"

    convert_jsonl_to_markdown(input_path, output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
