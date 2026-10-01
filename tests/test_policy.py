"""Policy pack: guardrails as data — verdicts, wildcards, when-clauses."""

from __future__ import annotations

from simon import policy


def _pack(tmp_path, rules):
    path = tmp_path / "policy.yml"
    import yaml
    path.write_text(yaml.safe_dump({"rules": rules}))
    policy.reload()
    return str(path)


def test_approve_verdict(tmp_path, monkeypatch):
    p = _pack(tmp_path, [{"tool": "send_email", "verdict": "approve",
                          "reason": "external email"}])
    monkeypatch.setenv("SIMON_POLICY_FILE", p)
    assert policy.evaluate("send_email", {})["verdict"] == "approve"
    assert policy.evaluate("web_search", {}) is None


def test_deny_verdict_with_reason(tmp_path, monkeypatch):
    p = _pack(tmp_path, [{"tool": "mcp_stripe_*", "verdict": "deny",
                          "reason": "payments off"}])
    monkeypatch.setenv("SIMON_POLICY_FILE", p)
    r = policy.evaluate("mcp_stripe_create_charge", {})
    assert r["verdict"] == "deny" and "payments" in r["reason"]


def test_when_clause_arg_regex(tmp_path, monkeypatch):
    p = _pack(tmp_path, [
        {"tool": "run_shell", "verdict": "approve",
         "when": {"command": "rm|kill"}, "reason": "destructive"},
    ])
    monkeypatch.setenv("SIMON_POLICY_FILE", p)
    assert policy.evaluate("run_shell", {"command": "rm -rf /tmp"})[
        "verdict"] == "approve"
    assert policy.evaluate("run_shell", {"command": "ls"}) is None


def test_first_match_wins(tmp_path, monkeypatch):
    p = _pack(tmp_path, [
        {"tool": "mcp_github_read", "verdict": "allow"},
        {"tool": "mcp_*", "verdict": "approve", "reason": "catch-all"},
    ])
    monkeypatch.setenv("SIMON_POLICY_FILE", p)
    assert policy.evaluate("mcp_github_read", {})["verdict"] == "allow"
    assert policy.evaluate("mcp_github_push", {})["verdict"] == "approve"


def test_missing_pack_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMON_POLICY_FILE", str(tmp_path / "nope.yml"))
    policy.reload()
    assert policy.evaluate("send_email", {}) is None
    policy.reload()


def test_default_pack_loads_and_gates():
    """The bundled policy.yml gates the core sensitive tools."""
    import os
    os.environ.pop("SIMON_POLICY_FILE", None)
    policy.reload()
    assert policy.evaluate("send_email", {})["verdict"] == "approve"
    assert policy.evaluate("delegate_dev", {})["verdict"] == "approve"
    assert policy.evaluate("mcp_stripe_create_charge", {})["verdict"] == "deny"
    assert policy.evaluate("web_search", {}) is None
    assert policy.evaluate("run_shell", {"command": "rm -rf /tmp/x"})[
        "verdict"] == "approve"
    assert policy.evaluate("run_shell", {"command": "ls"}) is None
    policy.reload()
