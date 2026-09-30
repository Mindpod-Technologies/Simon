"""Slack tools: Simon acts on the workspace, not just chats in it.

The bot token (slack_bot_token, the same one the Slack interface uses)
powers a real toolset via the Web API: list/read channels, search, react —
and posting, which is externally visible and therefore approval-gated
("autonomous, not unsupervised").

Note: Slack's search.messages API historically requires a USER token; with
a bot token it may 403 — the tool surfaces the API's error plainly rather
than pretending to search.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import Tool

log = logging.getLogger(__name__)


def _client(settings):
    from slack_sdk import WebClient
    return WebClient(token=settings.slack_bot_token)


def _resolve_channel(client, channel: str) -> Optional[str]:
    """Accept a channel id (C…), or resolve '#name'/name to an id."""
    channel = (channel or "").strip()
    if not channel:
        return None
    if channel.startswith(("C", "G", "D")) and " " not in channel:
        return channel
    wanted = channel.lstrip("#").lower()
    cursor = None
    for _ in range(5):  # page through at most 5×200 channels
        resp = client.conversations_list(limit=200, cursor=cursor)
        for ch in resp.get("channels", []):
            if ch.get("name", "").lower() == wanted:
                return ch["id"]
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    return None


def _slack_list_channels(settings, limit: int = 50) -> str:
    try:
        resp = _client(settings).conversations_list(limit=min(limit, 200))
        channels = resp.get("channels", [])
        if not channels:
            return "No channels visible to the bot."
        return "\n".join(
            f"#{c['name']} ({c['id']}) — {c.get('num_members', '?')} members"
            for c in channels)
    except Exception as exc:  # noqa: BLE001
        return f"Error: slack_list_channels failed: {exc}"


def _slack_read_channel(settings, channel: str, count: int = 10) -> str:
    client = _client(settings)
    cid = _resolve_channel(client, channel)
    if not cid:
        return f"Error: channel '{channel}' not found (is the bot invited?)"
    try:
        resp = client.conversations_history(
            channel=cid, limit=max(1, min(int(count or 10), 50)))
        msgs = resp.get("messages", [])
        if not msgs:
            return f"#{channel.lstrip('#')}: no messages."
        lines = []
        for m in reversed(msgs):
            who = m.get("user") or m.get("bot_id") or "?"
            text = (m.get("text") or "")[:300]
            lines.append(f"[{m.get('ts', '')}] {who}: {text}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error: slack_read_channel failed: {exc}"


def _slack_search(settings, query: str, count: int = 10) -> str:
    try:
        resp = _client(settings).search_messages(
            query=query, count=max(1, min(int(count or 10), 20)))
        matches = (resp.get("messages") or {}).get("matches", [])
        if not matches:
            return f"No Slack messages match '{query}'."
        return "\n\n".join(
            f"#{m.get('channel', {}).get('name', '?')} — "
            f"{m.get('username', '?')}: {(m.get('text') or '')[:300]}"
            for m in matches)
    except Exception as exc:  # noqa: BLE001
        return f"Error: slack_search failed: {exc} (search needs a user token on some plans)"


def _slack_post(settings, channel: str, text: str) -> str:
    client = _client(settings)
    cid = _resolve_channel(client, channel)
    if not cid:
        return f"Error: channel '{channel}' not found (is the bot invited?)"
    try:
        resp = client.chat_postMessage(channel=cid, text=text)
        return f"Posted to #{channel.lstrip('#')}: {resp.get('ts', '')}"
    except Exception as exc:  # noqa: BLE001
        return f"Error: slack_post failed: {exc}"


def _slack_react(settings, channel: str, timestamp: str, emoji: str) -> str:
    client = _client(settings)
    cid = _resolve_channel(client, channel)
    if not cid:
        return f"Error: channel '{channel}' not found."
    try:
        client.reactions_add(channel=cid, timestamp=timestamp,
                             name=emoji.strip(":"))
        return f"Reacted :{emoji.strip(':')}: on {timestamp} in #{channel.lstrip('#')}"
    except Exception as exc:  # noqa: BLE001
        return f"Error: slack_react failed: {exc}"


def register_slack_tools(registry, settings) -> None:
    """Register Slack workspace tools when a bot token is configured."""
    if not getattr(settings, "slack_bot_token", ""):
        return

    registry.register(Tool(
        name="slack_list_channels",
        description="List Slack channels the bot can see.",
        parameters={"type": "object", "properties": {
            "limit": {"type": "integer"}}},
        func=lambda limit=50: _slack_list_channels(settings, limit),
    ))
    registry.register(Tool(
        name="slack_read_channel",
        description="Read recent messages from a Slack channel (name or id).",
        parameters={"type": "object", "properties": {
            "channel": {"type": "string"},
            "count": {"type": "integer"}},
            "required": ["channel"]},
        func=lambda channel, count=10: _slack_read_channel(
            settings, channel, count),
    ))
    registry.register(Tool(
        name="slack_search",
        description="Search Slack messages. May need a user token on some "
                    "plans — the API error will say so.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer"}},
            "required": ["query"]},
        func=lambda query, count=10: _slack_search(settings, query, count),
    ))
    registry.register(Tool(
        name="slack_post",
        description="Post a message to a Slack channel. Externally visible — "
                    "the owner's approval is requested before posting.",
        parameters={"type": "object", "properties": {
            "channel": {"type": "string"},
            "text": {"type": "string"}},
            "required": ["channel", "text"]},
        func=lambda channel, text: _slack_post(settings, channel, text),
    ))
    registry.register(Tool(
        name="slack_react",
        description="Add an emoji reaction to a Slack message (needs the "
                    "message timestamp from slack_read_channel).",
        parameters={"type": "object", "properties": {
            "channel": {"type": "string"},
            "timestamp": {"type": "string"},
            "emoji": {"type": "string"}},
            "required": ["channel", "timestamp", "emoji"]},
        func=lambda channel, timestamp, emoji: _slack_react(
            settings, channel, timestamp, emoji),
    ))
