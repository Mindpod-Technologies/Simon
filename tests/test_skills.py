"""Tests for the skills system (simon/skills.py + registry + prompt index)."""

from __future__ import annotations

import textwrap

import pytest

from simon import skills


def _write_skill(root, dirname, name, description, body="# Body\n"):
    d = root / dirname
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        ---

        {body}"""))
    return d


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point both skill dirs at temp locations and clear the cache."""
    monkeypatch.setattr(skills, "BUILTIN_DIR", tmp_path / "builtin")
    monkeypatch.setattr(skills, "CUSTOM_DIR", tmp_path / "custom")
    skills.reset_cache_for_tests()
    yield tmp_path
    skills.reset_cache_for_tests()


def test_parse_frontmatter():
    meta, body = skills._parse_skill_md(
        "---\nname: foo\ndescription: \"does foo\"\n---\n\n# Foo\nbody text\n")
    assert meta == {"name": "foo", "description": "does foo"}
    assert body.startswith("# Foo")


def test_parse_without_frontmatter():
    meta, body = skills._parse_skill_md("# Just markdown\n")
    assert meta == {}
    assert body == "# Just markdown\n"


def test_discover_builtin(tmp_path):
    _write_skill(tmp_path / "builtin", "alpha", "alpha", "first skill")
    found = skills.discover(force=True)
    assert [s.name for s in found] == ["alpha"]
    assert found[0].source == "builtin"


def test_custom_overrides_builtin(tmp_path):
    _write_skill(tmp_path / "builtin", "dup", "dup", "vendor version")
    _write_skill(tmp_path / "custom", "dup2", "dup", "customer version")
    found = skills.discover(force=True)
    assert len(found) == 1
    assert found[0].source == "custom"
    assert found[0].description == "customer version"


def test_name_defaults_to_directory(tmp_path):
    d = tmp_path / "builtin" / "my-dir"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\ndescription: no name key\n---\nbody\n")
    assert skills.discover(force=True)[0].name == "my-dir"


def test_render_index_marks_custom(tmp_path):
    _write_skill(tmp_path / "builtin", "b1", "b1", "builtin one")
    _write_skill(tmp_path / "custom", "c1", "c1", "custom one")
    index = skills.render_index(skills.discover(force=True))
    assert "load_skill" in index
    assert "- b1: builtin one" in index
    assert "- c1: custom one [custom]" in index


def test_load_body_and_unknown(tmp_path):
    _write_skill(tmp_path / "builtin", "r1", "r1", "research", "# Steps\n1. do\n")
    assert "# Steps" in skills.load_body("r1")
    err = skills.load_body("nope")
    assert "Error" in err and "r1" in err


def test_aux_files_listed(tmp_path):
    d = _write_skill(tmp_path / "builtin", "with-aux", "aux", "has files")
    (d / "template.md").write_text("template")
    out = skills.load_body("aux")
    assert "template.md" in out


def test_tool_registered(tmp_path):
    _write_skill(tmp_path / "builtin", "t1", "t1", "tool test")
    from simon.config import Settings
    from simon.tools import build_default_registry

    registry = build_default_registry(
        Settings(llm_base_url="http://localhost:11434/v1",
                 simon_mcp_enabled=False, simon_browser_enabled=False,
                 simon_subagents_enabled=False))
    tool = registry._tools.get("load_skill")
    assert tool is not None
    assert "tool test" in tool.func(name="t1") or "# Body" in tool.func(name="t1")


def test_tool_absent_when_disabled(tmp_path):
    from simon.config import Settings
    from simon.tools import build_default_registry

    registry = build_default_registry(
        Settings(llm_base_url="http://localhost:11434/v1",
                 simon_mcp_enabled=False, simon_browser_enabled=False,
                 simon_subagents_enabled=False, simon_skills_enabled=False))
    assert "load_skill" not in registry._tools
