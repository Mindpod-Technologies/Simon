"""Guard test: every skill shipped in the repo's skills/ directory must
parse cleanly and carry the metadata the system prompt depends on."""

from __future__ import annotations

from pathlib import Path

from simon import skills

REPO_SKILLS = Path(skills.__file__).resolve().parent.parent / "skills"


def _real_builtin_skills():
    # Bypass the cache and any test monkeypatching: scan the real directory.
    return skills._scan_dir(REPO_SKILLS, "builtin")


def test_builtin_skills_exist():
    found = _real_builtin_skills()
    assert len(found) >= 6, f"expected the shipped library, got {[s.name for s in found]}"


def test_every_shipped_skill_is_wellformed():
    for skill in _real_builtin_skills():
        assert skill.name and " " not in skill.name, skill.path
        assert skill.description and skill.description != "(no description)", skill.path
        # A procedure is a procedure: at least a few steps of substance.
        assert len(skill.body) > 400, f"{skill.name} body looks like a stub"
        assert skill.body.count("\n1.") >= 1, f"{skill.name} has no numbered procedure"


def test_shipped_skill_names_are_unique():
    names = [s.name for s in _real_builtin_skills()]
    assert len(names) == len(set(names)), names
