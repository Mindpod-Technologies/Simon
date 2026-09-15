"""Configuration for Simon, loaded from environment / .env via pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for Simon.

    All values may be overridden via environment variables or a local
    ``.env`` file (see ``.env.example`` for the full list of keys).
    """

    # LLM (any OpenAI-compatible endpoint)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    # Model router (local multi-model setups, e.g. Ollama): when enabled and
    # llm_model_fast is set, simple turns go to the fast model and complex or
    # memory-miss turns go to llm_model (the "smart" one).
    llm_model_fast: str = ""
    llm_router_enabled: bool = False

    # Frontier tier (optional cloud model, e.g. Kimi K3 via Moonshot AI):
    # active only when llm_frontier_api_key is set. Explicit user requests
    # ("use kimi", "ask the frontier model") route the whole turn here, and
    # local-tier failures escalate here as the last resort before
    # apologising to the user.
    llm_frontier_base_url: str = "https://api.moonshot.ai/v1"
    llm_frontier_model: str = "kimi-k3"
    llm_frontier_api_key: str = ""
    llm_frontier_reasoning_effort: str = "high"

    # Voice
    tts_voice: str = "en-GB-RyanNeural"
    tts_rate: str = "+0%"
    stt_model: str = "base"

    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""

    # Slack (Socket Mode — no public endpoint required)
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_allowed_user_ids: str = ""

    # Microsoft Teams (Bot Framework — requires a public HTTPS endpoint)
    teams_app_id: str = ""
    teams_app_password: str = ""
    teams_allowed_user_ids: str = ""
    teams_port: int = 3978

    # Public base URL used to serve TTS audio links (e.g. for Teams).
    # Leave empty when the host is not publicly reachable.
    simon_public_base_url: str = ""

    # Optional integrations
    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = ""
    smtp_host: str = ""
    smtp_user: str = ""
    smtp_password: str = ""
    # Microsoft Graph mail (M365 tenants with basic auth disabled — the
    # supported path). Entra app registration with Mail.Read + Mail.Send
    # application permissions; see simon/tools/graph_mail.py docstring.
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    simon_mailbox: str = "simon@mindpodtech.com"
    # Headless auth for the claude delegate engine (from `claude setup-token`).
    claude_code_oauth_token: str = ""
    google_calendar_ics: str = ""
    homeassistant_url: str = ""
    homeassistant_token: str = ""

    # Azure management (uses the ambient 'az' CLI login; no credentials here)
    simon_azure_enabled: bool = False
    simon_azure_allow_write: bool = False

    # Sub-agents (Simon can spawn child agents to work in parallel)
    simon_subagents_enabled: bool = True
    simon_subagents_max_concurrent: int = 3
    # Computer use (macOS only; requires Accessibility + Screen Recording permissions)
    simon_computer_use: bool = False
    simon_computer_vision: bool = False  # describe screenshots via the LLM vision API

    # Dedicated browser (Playwright Chromium, persistent profile)
    simon_browser_enabled: bool = True
    simon_browser_headless: bool = True

    # Scheduled proactive behaviour
    simon_hourly_status: bool = True  # hourly status updates 07:17–23:17
    simon_weekly_suggestions: bool = True  # Sat 10:12 automation proposals
    simon_mail_check_enabled: bool = True  # inbox watch (needs GRAPH_* creds)
    simon_mail_check_minutes: int = 5  # inbox poll cadence

    # Background jobs (long-running assignments executed by the JobRunner)
    simon_jobs_enabled: bool = True

    # MCP client (connect to Model Context Protocol servers listed in a
    # Claude-Desktop-style mcp.json; their tools appear as mcp_<server>_<tool>)
    simon_mcp_enabled: bool = True
    # Skills: drop-in SKILL.md instruction packs (repo skills/ = built-in,
    # data/skills/ = customer, survives updates). The load_skill tool and a
    # system-prompt index are only registered when this is true.
    simon_skills_enabled: bool = True
    simon_mcp_config: str = ""  # default: mcp.json in the working directory
    # Comma-separated substrings; when set, only MCP tools whose name
    # contains one of them are exposed to the model. Every exposed tool
    # schema costs prompt tokens on EVERY turn (89 tools ≈ 18k tokens), so
    # curating this list is the single biggest latency lever.
    simon_mcp_tool_allowlist: str = ""
    simon_mcp_call_timeout: int = 120
    simon_mcp_start_timeout: int = 30

    # Safety
    simon_allow_shell: bool = False
    simon_workspace_dir: str = "./workspace"
    # Extra directories the file tools may read/write beyond the workspace
    # (comma-separated, ~ allowed). Empty = workspace only. This is the
    # "access my Mac's files" switch — each entry is read AND write.
    simon_allowed_dirs: str = ""
    # External coding-CLIs (Claude Code / Codex) as delegatable dev engines.
    # Off by default; engines use their own logins, never Simon's secrets.
    simon_dev_delegate_enabled: bool = False

    # Commercial licensing (see COMMERCIAL.md). Empty key + require=false
    # runs as a free "trial" plan, so personal self-hosted use is unaffected.
    simon_license_key: str = ""
    simon_require_license: bool = False

    # Cross-interface identity: map interface-specific user IDs onto one
    # canonical session so a person's conversation follows them across
    # Slack / Telegram / web. Format: "slack:U123=owner,telegram:456=owner".
    simon_identity_map: str = ""
    # The web UI has no login; anonymous browser visitors land on this session
    # (e.g. "owner" to unify the browser with the owner's Slack DM).
    simon_web_default_session: str = ""

    def canonical_session(self, interface: str, raw_id: str) -> str:
        """Map an interface-specific user/session ID to a canonical session."""
        key = f"{interface}:{raw_id}"
        for part in (self.simon_identity_map or "").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == key:
                    return v.strip()
        return raw_id

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()
