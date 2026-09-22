"""Optional LLM vision helper shared by the computer-use and browser tools.

Used only when SIMON_COMPUTER_VISION=true. Sends an image to the configured
OpenAI-compatible endpoint as a base64 data URL and returns a short text
description. Never raises: returns None when vision is disabled, the openai
SDK is unavailable, or the request fails.
"""
from __future__ import annotations

import base64
import logging

log = logging.getLogger(__name__)


def describe_image(path: str, settings, prompt: str | None = None) -> str | None:
    """Describe the image at ``path`` via the LLM's vision API, or None.

    Gated on ``settings.simon_computer_vision``. All failures are logged and
    swallowed so callers can fall back to returning the plain file path.
    """
    if not getattr(settings, "simon_computer_vision", False):
        return None
    try:
        from openai import OpenAI  # lazy: openai is a core dep but keep import soft
    except Exception as exc:  # noqa: BLE001
        log.warning("vision unavailable (openai import failed): %s", exc)
        return None
    try:
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        client = OpenAI(base_url=settings.llm_base_url,
                        api_key=settings.llm_api_key or "simon-no-key")
        resp = client.chat.completions.create(
            model=(getattr(settings, "llm_vision_model", "")
                   or settings.llm_model),
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt or (
                            "Describe this image concisely and factually: what is "
                            "on screen, key text, and anything actionable."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }],
            # No max_tokens: qwen3.5-vision returns EMPTY content when capped
            # (verified 2026-09-22). Descriptions are naturally short.
            # keep_alive 0: vision runs occasionally atop the chat models —
            # don't keep it resident or total VRAM blows past the box.
            extra_body={"keep_alive": "0"},
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as exc:  # noqa: BLE001 - vision must never break the tool
        log.warning("vision description failed: %s", exc)
        return None


# Private alias kept for internal callers per original design notes.
_describe_image = describe_image
