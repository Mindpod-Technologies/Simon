"""Filesystem guard: TCC-blocked file calls must time out, not hang."""
from __future__ import annotations

import threading

from simon.tools import builtin


def test_fs_guard_returns_on_time():
    assert builtin._fs_guard(lambda: "ok", "/tmp/x") == "ok"


def test_fs_guard_times_out_with_actionable_message(monkeypatch):
    monkeypatch.setattr(builtin, "_FS_TIMEOUT", 0.2)
    started = threading.Event()

    def stuck():
        started.set()
        threading.Event().wait(30)  # released at interpreter exit
        return "never"

    result = builtin._fs_guard(stuck, "/Users/x/Desktop")
    assert started.is_set()
    assert result.startswith("Error: macOS is blocking access")
    assert "Privacy & Security" in result


def test_list_files_workspace_still_works(tmp_path):
    class S:
        simon_workspace_dir = str(tmp_path)
        simon_allowed_dirs = ""

    (tmp_path / "hello.txt").write_text("hi")
    out = builtin._list_files(S(), ".")
    assert "hello.txt" in out
