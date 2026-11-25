# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Render an answer for chat fronts: HTML fragment and Adaptive Card 1.5.

Display rules (README, Dual-path answering): an AI-generated answer ends with the soft footer in small red
type (`ai_generated` / `footer_ja` in the JSON); SQL is never rendered here; hints become buttons / chips.
"""
from __future__ import annotations

import html


def to_html(a: dict) -> str:
    body = "<br>".join(html.escape(line) for line in a["answer_ja"].split("\n"))
    parts = [f'<div class="lexigate-answer" data-path="{html.escape(a["path"])}" data-ai-generated="{str(bool(a.get("ai_generated"))).lower()}">',
             f"<p>{body}</p>"]
    if a.get("hints"):
        parts.append('<ul class="lexigate-hints">' + "".join(f"<li>{html.escape(h)}</li>" for h in a["hints"]) + "</ul>")
    if a.get("ai_generated") and a.get("footer_ja"):
        parts.append(f'<p class="lexigate-ai-footer" style="color:#c00;font-size:small">{html.escape(a["footer_ja"])}</p>')
    if a.get("data_label"):
        parts.append(f'<p class="lexigate-label" style="color:#666;font-size:small">{html.escape(a["data_label"])}</p>')
    parts.append("</div>")
    return "".join(parts)


def to_adaptive_card(a: dict) -> dict:
    body: list[dict] = [{"type": "TextBlock", "text": a["answer_ja"], "wrap": True}]
    if a.get("ai_generated") and a.get("footer_ja"):
        body.append({"type": "TextBlock", "text": a["footer_ja"], "wrap": True, "size": "Small",
                     "color": "Attention", "spacing": "Medium", "id": "aiGeneratedFooter"})
    if a.get("data_label"):
        body.append({"type": "TextBlock", "text": a["data_label"], "size": "Small", "isSubtle": True, "wrap": True})
    actions = [{"type": "Action.Submit", "title": h, "data": {"question": h, "session_id": a.get("session_id")}}
               for h in a.get("hints") or []]
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
            "body": body, "actions": actions,
            "metadata": {"lexigate": {"path": a["path"], "ai_generated": bool(a.get("ai_generated")), "query_id": a.get("query_id")}}}
