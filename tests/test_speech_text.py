"""speech_text(): markdown replies become plain speakable language.

Regression guard for "Simon reads 'asterisk asterisk' aloud" — the TTS path
must never receive raw markdown, on any channel.
"""

from simon.voice.tts import speech_text, synthesize


def test_bold_and_italic_markers_removed():
    out = speech_text("This is **very** important, *really*.")
    assert "asterisk" not in out.lower()
    assert "**" not in out and "*" not in out
    assert "very" in out and "really" in out


def test_headings_bullets_quotes_stripped():
    out = speech_text("## Plan\n\n- first item\n* second item\n> a quote\n")
    assert "#" not in out
    assert "first item" in out and "second item" in out and "a quote" in out
    for line in out.splitlines():
        assert not line.lstrip().startswith(("- ", "* ", "> "))


def test_links_speak_label_only():
    out = speech_text("See [the report](https://example.com/r) for details.")
    assert out == "See the report for details."


def test_fenced_code_not_read_aloud():
    out = speech_text("Here you go:\n```python\nprint('hello')\n```\nDone.")
    assert "print" not in out
    assert "code snippet" in out
    assert "Done." in out


def test_inline_code_keeps_words():
    assert speech_text("Run `git status` first.") == "Run git status first."


def test_tables_read_as_lists():
    out = speech_text("| Name | Age |\n| --- | --- |\n| Ann | 30 |")
    assert "|" not in out
    assert "Ann , 30" in out or "Ann, 30" in out


def test_html_tags_dropped():
    assert speech_text("Hello<br>world <b>hi</b>") == "Hello\nworld hi" or \
        speech_text("Hello<br>world <b>hi</b>").replace("\n", " ") == \
        "Hello world hi"


def test_empty_and_none():
    assert speech_text("") == ""
    assert speech_text(None) == ""


def test_synthesize_rejects_markup_only_text():
    import asyncio
    import pytest
    with pytest.raises(ValueError):
        asyncio.run(synthesize("***", "/tmp/simon-test-tts-should-not-exist.mp3"))
