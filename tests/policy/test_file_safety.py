from __future__ import annotations

from pathlib import Path

import pytest

from agent.policy.file_safety import get_read_block_error


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def akvan_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "akvan"
    home.mkdir()
    monkeypatch.setenv("AKVAN_HOME", str(home))
    return home


def test_allows_normal_project_file(isolated_home: Path) -> None:
    project = isolated_home / "project"
    project.mkdir()
    target = project / "main.py"
    target.write_text("print('ok')\n", encoding="utf-8")
    assert get_read_block_error(target) is None


def test_allows_read_outside_project_root(isolated_home: Path) -> None:
    outside = isolated_home / "other-project" / "README.md"
    outside.parent.mkdir()
    outside.write_text("# Other\n", encoding="utf-8")
    assert get_read_block_error(outside) is None


def test_blocks_ssh_private_key(isolated_home: Path) -> None:
    ssh_dir = isolated_home / ".ssh"
    ssh_dir.mkdir(mode=0o700)
    key = ssh_dir / "id_ed25519"
    key.write_text("secret-key\n", encoding="utf-8")
    error = get_read_block_error(key)
    assert error is not None
    assert "credential" in error.lower()


def test_blocks_project_env_file(isolated_home: Path) -> None:
    env_file = isolated_home / "project" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("API_KEY=secret\n", encoding="utf-8")
    error = get_read_block_error(env_file)
    assert error is not None
    assert ".env" in error


def test_blocks_akvan_env(akvan_home: Path, isolated_home: Path) -> None:
    env_file = akvan_home / ".env"
    env_file.write_text("PROVIDER_KEY=secret\n", encoding="utf-8")
    error = get_read_block_error(env_file)
    assert error is not None
    assert "Akvan credential" in error


def test_allows_akvan_skills(akvan_home: Path) -> None:
    skill_md = akvan_home / "skills" / "demo" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("---\nname: demo\ndescription: test\n---\nbody\n", encoding="utf-8")
    assert get_read_block_error(skill_md) is None


def test_blocks_akvan_approvals_json(akvan_home: Path) -> None:
    path = akvan_home / "approvals.json"
    path.write_text("{}\n", encoding="utf-8")
    error = get_read_block_error(path)
    assert error is not None
    assert "Akvan credential" in error
