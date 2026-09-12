"""Home Assistant integration for Simon (optional).

Registered by build_default_registry() only when HOMEASSISTANT_URL and
HOMEASSISTANT_TOKEN are configured. Uses the Home Assistant REST API.
"""
from __future__ import annotations

import logging

import requests

from .base import Tool

log = logging.getLogger(__name__)

_TIMEOUT = 10


def _headers(settings) -> dict:
    return {
        "Authorization": f"Bearer {settings.homeassistant_token}",
        "Content-Type": "application/json",
    }


def _base(settings) -> str:
    return settings.homeassistant_url.rstrip("/")


def register_homeassistant_tools(registry, settings) -> None:
    """Register Home Assistant tools on the given registry."""

    def ha_states(entity_id: str = "") -> str:
        """Get the state of one entity, or a compact list of all entities."""
        try:
            if entity_id:
                r = requests.get(
                    f"{_base(settings)}/api/states/{entity_id}",
                    headers=_headers(settings), timeout=_TIMEOUT,
                )
                r.raise_for_status()
                data = r.json()
                attrs = ", ".join(
                    f"{k}={v}" for k, v in list(data.get("attributes", {}).items())[:8]
                )
                return f"{data['entity_id']}: {data['state']} ({attrs})"
            r = requests.get(
                f"{_base(settings)}/api/states",
                headers=_headers(settings), timeout=_TIMEOUT,
            )
            r.raise_for_status()
            lines = [
                f"{e['entity_id']}: {e['state']}" for e in r.json()[:50]
            ]
            return "\n".join(lines) if lines else "No entities found."
        except Exception as exc:  # noqa: BLE001
            return f"Error: Home Assistant request failed: {exc}"

    def ha_call_service(domain: str, service: str, entity_id: str) -> str:
        """Call a service, e.g. domain=light service=turn_on entity_id=light.lounge."""
        try:
            r = requests.post(
                f"{_base(settings)}/api/services/{domain}/{service}",
                headers=_headers(settings),
                json={"entity_id": entity_id},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            return f"Done, sir — {domain}.{service} called on {entity_id}."
        except Exception as exc:  # noqa: BLE001
            return f"Error: Home Assistant service call failed: {exc}"

    registry.register(Tool(
        name="ha_get_state",
        description=(
            "Get the state of a Home Assistant entity, or list entities if "
            "entity_id is omitted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "e.g. light.lounge (optional)",
                },
            },
        },
        func=ha_states,
    ))
    registry.register(Tool(
        name="ha_call_service",
        description=(
            "Control a smart-home device via Home Assistant, e.g. turn a "
            "light or switch on/off, set a scene, lock a door."
        ),
        parameters={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "e.g. light, switch, lock"},
                "service": {"type": "string", "description": "e.g. turn_on, turn_off, toggle"},
                "entity_id": {"type": "string", "description": "e.g. light.lounge"},
            },
            "required": ["domain", "service", "entity_id"],
        },
        func=ha_call_service,
    ))
    log.info("Home Assistant tools registered.")
