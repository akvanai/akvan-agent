"""Bundled skill sync tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.skills.paths import BUNDLED_MANIFEST, NO_BUNDLED_SKILLS_MARKER
from agent.skills.sync import sync_bundled_skills


def write_bundled_skill(
    bundled_root: Path,
    category: str,
    name: str,
    description: str,
    body: str,
) -> Path:
    skill_root = bundled_root / category / name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_root


def test_sync_copies_new_skill_and_preserves_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundled"
    home = tmp_path / "home"
    home.mkdir()
    write_bundled_skill(bundled, "creative", "claude-design", "Design", "BODY")

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setenv("AKVAN_BUNDLED_SKILLS", str(bundled))

    summary = sync_bundled_skills(quiet=True)

    target = home / "skills" / "creative" / "claude-design" / "SKILL.md"
    assert target.is_file()
    assert "claude-design" in summary.added
    assert "BODY" in target.read_text(encoding="utf-8")
    assert (home / "skills" / BUNDLED_MANIFEST).is_file()


def test_sync_skips_customized_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "bundled"
    home = tmp_path / "home"
    home.mkdir()
    write_bundled_skill(bundled, "creative", "claude-design", "Design", "BUNDLED")

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setenv("AKVAN_BUNDLED_SKILLS", str(bundled))

    first = sync_bundled_skills(quiet=True)
    assert first.added == ("claude-design",)

    target = home / "skills" / "creative" / "claude-design" / "SKILL.md"
    target.write_text(target.read_text(encoding="utf-8").replace("BUNDLED", "CUSTOM"), encoding="utf-8")

    write_bundled_skill(bundled, "creative", "claude-design", "Design", "BUNDLED v2")
    second = sync_bundled_skills(quiet=True)

    assert "claude-design" in second.skipped
    assert "CUSTOM" in target.read_text(encoding="utf-8")


def test_sync_does_not_readd_deleted_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "bundled"
    home = tmp_path / "home"
    home.mkdir()
    write_bundled_skill(bundled, "creative", "claude-design", "Design", "BODY")

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setenv("AKVAN_BUNDLED_SKILLS", str(bundled))

    sync_bundled_skills(quiet=True)
    target_dir = home / "skills" / "creative" / "claude-design"
    assert target_dir.is_dir()
    for child in target_dir.iterdir():
        if child.is_file():
            child.unlink()
        else:
            import shutil

            shutil.rmtree(child)
    target_dir.rmdir()

    second = sync_bundled_skills(quiet=True)
    assert "claude-design" in second.skipped
    assert not target_dir.exists()


def test_sync_removes_obsolete_unmodified_bundled_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundled"
    home = tmp_path / "home"
    home.mkdir()
    write_bundled_skill(bundled, "social", "x-account", "Old X tools", "OLD BODY")

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setenv("AKVAN_BUNDLED_SKILLS", str(bundled))

    first = sync_bundled_skills(quiet=True)
    assert first.added == ("x-account",)
    target = home / "skills" / "social" / "x-account"
    assert target.is_dir()

    # Remove from bundled package (simulates shipping without x-account).
    import shutil

    shutil.rmtree(bundled / "social" / "x-account")
    write_bundled_skill(bundled, "browser", "auth-profiles", "Browser auth", "NEW")

    second = sync_bundled_skills(quiet=True)

    assert "x-account" in second.removed
    assert "auth-profiles" in second.added
    assert not target.exists()
    assert (home / "skills" / "browser" / "auth-profiles" / "SKILL.md").is_file()


def test_sync_keeps_customized_obsolete_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundled"
    home = tmp_path / "home"
    home.mkdir()
    write_bundled_skill(bundled, "social", "legacy-tool", "Old tools", "OLD BODY")

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setenv("AKVAN_BUNDLED_SKILLS", str(bundled))
    sync_bundled_skills(quiet=True)

    target = home / "skills" / "social" / "legacy-tool" / "SKILL.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nCUSTOM\n", encoding="utf-8")

    import shutil

    shutil.rmtree(bundled / "social" / "legacy-tool")
    third = sync_bundled_skills(quiet=True)

    assert "legacy-tool" in third.skipped
    assert "legacy-tool" not in third.removed
    assert target.is_file()
    assert "CUSTOM" in target.read_text(encoding="utf-8")


def test_sync_force_removes_retired_skill_even_without_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundled"
    home = tmp_path / "home"
    home.mkdir()
    write_bundled_skill(bundled, "browser", "auth-profiles", "Browser auth", "NEW")
    # Leftover retired skill not listed in manifest.
    leftover = home / "skills" / "social" / "x-account"
    leftover.mkdir(parents=True)
    (leftover / "SKILL.md").write_text(
        "---\nname: x-account\ndescription: old\n---\n\nold\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setenv("AKVAN_BUNDLED_SKILLS", str(bundled))
    summary = sync_bundled_skills(quiet=True)

    assert "x-account" in summary.removed
    assert not leftover.exists()


def test_sync_respects_opt_out_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "bundled"
    home = tmp_path / "home"
    home.mkdir()
    write_bundled_skill(bundled, "creative", "claude-design", "Design", "BODY")
    (home / NO_BUNDLED_SKILLS_MARKER).write_text("", encoding="utf-8")

    monkeypatch.setenv("AKVAN_HOME", str(home))
    monkeypatch.setenv("AKVAN_BUNDLED_SKILLS", str(bundled))

    summary = sync_bundled_skills(quiet=True)
    assert summary.added == ()
    assert not (home / "skills").exists()
