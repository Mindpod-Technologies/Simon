"""Tests for simon.tools.graph_mail — Microsoft 365 mailbox via Graph."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from simon.tools import ToolRegistry, graph_mail


def _settings(**kw):
    base = dict(graph_tenant_id="tenant-1", graph_client_id="client-1",
                graph_client_secret="secret-1",
                simon_mailbox="simon@mindpodtech.com")
    base.update(kw)
    return SimpleNamespace(**base)


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def _patch_token():
    return mock.patch.object(graph_mail, "_token", return_value="tok")


def test_not_registered_without_credentials():
    reg = ToolRegistry()
    graph_mail.register_graph_mail_tools(
        reg, _settings(graph_client_secret=""))
    assert "send_email" not in reg.names()


def test_registered_with_credentials():
    reg = ToolRegistry()
    graph_mail.register_graph_mail_tools(reg, _settings())
    assert {"read_recent_emails", "read_email", "send_email"} <= set(
        reg.names())


def test_token_fetched_and_cached():
    s = _settings()
    graph_mail._token_cache.update(value=None, exp=0.0)
    calls = []

    def fake_post(url, data, timeout):
        calls.append((url, data))
        return FakeResp(payload={"access_token": "tok-1", "expires_in": 3600})

    with mock.patch.object(graph_mail.requests, "post", fake_post):
        assert graph_mail._token(s) == "tok-1"
        assert graph_mail._token(s) == "tok-1"  # cached, no second call
    assert len(calls) == 1
    assert "tenant-1" in calls[0][0]
    assert calls[0][1]["grant_type"] == "client_credentials"


def test_send_email_payload():
    s = _settings()
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, json=json)
        return FakeResp(status=202)

    with _patch_token(), \
         mock.patch.object(graph_mail.requests, "post", fake_post):
        out = graph_mail.send_email_graph(s, "boss@mindpodtech.com",
                                          "Status", "All green.")
    assert "Email sent to boss@mindpodtech.com" in out
    assert captured["url"].endswith(
        "/users/simon@mindpodtech.com/sendMail")
    msg = captured["json"]["message"]
    assert msg["subject"] == "Status"
    assert msg["toRecipients"][0]["emailAddress"]["address"] == \
        "boss@mindpodtech.com"


def test_send_requires_to_and_subject():
    s = _settings()
    assert "required" in graph_mail.send_email_graph(s, "", "Hi", "body")
    assert "required" in graph_mail.send_email_graph(s, "a@b.c", " ", "body")


def test_read_recent_formats_messages():
    s = _settings()
    payload = {"value": [{
        "id": "msg-1", "subject": "Deploy?",
        "from": {"emailAddress": {"name": "Jaras", "address": "j@x.com"}},
        "receivedDateTime": "2026-09-14T18:00:00Z",
        "bodyPreview": "Can we ship today",
        "isRead": False,
    }]}

    def fake_get(url, headers, params, timeout):
        assert params["$top"] == "5"
        return FakeResp(payload=payload)

    with _patch_token(), \
         mock.patch.object(graph_mail.requests, "get", fake_get):
        out = graph_mail.read_recent_emails_graph(s, 5)
    assert "Deploy?" in out and "[UNREAD]" in out
    assert "j@x.com" in out and "msg-1" in out


def test_read_recent_empty_inbox():
    s = _settings()
    with _patch_token(), mock.patch.object(
            graph_mail.requests, "get",
            lambda *a, **k: FakeResp(payload={"value": []})):
        assert graph_mail.read_recent_emails_graph(s) == "Inbox is empty."


def test_read_email_strips_html():
    s = _settings()
    payload = {
        "subject": "Report", "from": {"emailAddress": {"address": "a@b.c"}},
        "receivedDateTime": "2026-09-14T18:00:00Z",
        "body": {"contentType": "html",
                 "content": "<p>Hello <b>Simon</b></p>"},
    }
    with _patch_token(), mock.patch.object(
            graph_mail.requests, "get",
            lambda *a, **k: FakeResp(payload=payload)):
        out = graph_mail.read_email_graph(s, "msg-9")
    assert "Hello Simon" in out and "<p>" not in out


def test_api_error_reported():
    s = _settings()
    with _patch_token(), mock.patch.object(
            graph_mail.requests, "get",
            lambda *a, **k: FakeResp(status=403, text="Forbidden")):
        out = graph_mail.read_recent_emails_graph(s)
    assert "403" in out


def test_mail_tools_tolerate_model_invented_kwargs(monkeypatch):
    """Models sometimes pass 'address'/'recipient' instead of the schema's
    exact parameter names — the wrappers must cope, not TypeError."""
    import types
    from simon.tools import ToolRegistry
    from simon.tools import graph_mail

    monkeypatch.setattr(graph_mail, "read_recent_emails_graph",
                        lambda s, count=10: f"recent:{count}")
    monkeypatch.setattr(graph_mail, "read_email_graph",
                        lambda s, mid: f"email:{mid}")
    monkeypatch.setattr(graph_mail, "send_email_graph",
                        lambda s, to, subject, body, attachment_path="": f"sent:{to}:{subject}")
    settings = types.SimpleNamespace(
        graph_tenant_id="t", graph_client_id="c", graph_client_secret="s",
        simon_mailbox="simon@x.com")
    reg = ToolRegistry()
    graph_mail.register_graph_mail_tools(reg, settings)

    assert reg.call("read_recent_emails", {"count": 5, "address": "x"}) == "recent:5"
    assert reg.call("read_email", {"address": "mid-9"}) == "email:mid-9"
    assert reg.call("send_email", {"recipient": "a@b.c",
                                   "subject": "s", "body": "b"}) == "sent:a@b.c:s"


def test_send_email_with_workspace_attachment(tmp_path, monkeypatch):
    """The payload must carry a real base64 fileAttachment."""
    import base64
    import types
    from simon.tools import graph_mail

    ws = tmp_path / "workspace"
    (ws / "documents").mkdir(parents=True)
    (ws / "documents" / "report.md").write_text("# Report\ncontent here")
    captured = {}

    class FakeResp:
        status_code = 202
        text = ""

    monkeypatch.setattr(graph_mail, "_token", lambda s: "tok")
    monkeypatch.setattr(graph_mail.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        captured.update(json=json) or FakeResp())
    s = types.SimpleNamespace(simon_workspace_dir=str(ws),
                              simon_mailbox="simon@x.com")
    out = graph_mail.send_email_graph(s, "a@b.c", "Report", "See attached",
                                      attachment_path="documents/report.md")
    assert "attached: report.md" in out
    att = captured["json"]["message"]["attachments"][0]
    assert att["name"] == "report.md"
    assert base64.b64decode(att["contentBytes"]).startswith(b"# Report")


def test_attachment_confined_to_workspace(tmp_path):
    import types
    from simon.tools import graph_mail
    s = types.SimpleNamespace(simon_workspace_dir=str(tmp_path / "ws"),
                              simon_mailbox="simon@x.com")
    out = graph_mail.send_email_graph(s, "a@b.c", "x", "y",
                                      attachment_path="../.env")
    assert "inside the workspace" in out
    out = graph_mail.send_email_graph(s, "a@b.c", "x", "y",
                                      attachment_path="/etc/passwd")
    assert "inside the workspace" in out
    out = graph_mail.send_email_graph(s, "a@b.c", "x", "y",
                                      attachment_path="documents/missing.md")
    assert "no such workspace file" in out
