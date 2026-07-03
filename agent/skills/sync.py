"""Manifest-based seeding of bundled skills into the user skills directory."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from agent.config import akvan_home
from agent.skills.paths import (
    BUNDLED_MANIFEST,
    NO_BUNDLED_SKILLS_MARKER,
    bundled_skills_dir,
)
from agent.skills.registry import SKILL_NAME_PATTERN, _split_frontmatter

@dataclass(frozen=True)
class SyncSummary:
    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    removed_manifest: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "added": list(self.added),
            "updated": list(self.updated),
            "skipped": list(self.skipped),
            "unchanged": list(self.unchanged),
            "removed_manifest": list(self.removed_manifest),
        }


def sync_bundled_skills(*, quiet: bool = False) -> SyncSummary:
    home = akvan_home()
    if (home / NO_BUNDLED_SKILLS_MARKER).exists():
        return SyncSummary()

    bundled_dir = bundled_skills_dir()
    target_dir = akvan_home() / "skills"
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / BUNDLED_MANIFEST
    manifest = _read_manifest(manifest_path)

    added: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    unchanged: list[str] = []
    removed_manifest: list[str] = []
    next_manifest: dict[str, str] = {}

    discovered = _discover_bundled_skills(bundled_dir)
    discovered_names = {name for name, _ in discovered}

    for skill_name, skill_src in discovered:
        rel = skill_src.relative_to(bundled_dir)
        dest = target_dir / rel
        bundled_hash = _dir_hash(skill_src)
        recorded_hash = manifest.get(skill_name)

        if dest.exists():
            current_hash = _dir_hash(dest)
            if recorded_hash and current_hash != recorded_hash:
                skipped.append(skill_name)
                next_manifest[skill_name] = recorded_hash
                continue
            if current_hash == bundled_hash:
                unchanged.append(skill_name)
                next_manifest[skill_name] = bundled_hash
                continue
            shutil.copytree(skill_src, dest, dirs_exist_ok=True)
            updated.append(skill_name)
            next_manifest[skill_name] = bundled_hash
            continue

        if skill_name in manifest:
            skipped.append(skill_name)
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_src, dest)
        added.append(skill_name)
        next_manifest[skill_name] = bundled_hash

    for name in manifest:
        if name not in discovered_names:
            removed_manifest.append(name)

    _write_manifest(manifest_path, next_manifest)
    summary = SyncSummary(
        added=tuple(added),
        updated=tuple(updated),
        skipped=tuple(skipped),
        unchanged=tuple(unchanged),
        removed_manifest=tuple(removed_manifest),
    )
    if not quiet:
        _print_summary(summary, bundled_dir=bundled_dir, target_dir=target_dir)
    return summary


def _discover_bundled_skills(bundled_dir: Path) -> list[tuple[str, Path]]:
    if not bundled_dir.is_dir():
        return []
    skills: list[tuple[str, Path]] = []
    for skill_md in sorted(bundled_dir.rglob("SKILL.md")):
        rel = skill_md.relative_to(bundled_dir)
        if len(rel.parts) != 3 or rel.name != "SKILL.md":
            continue
        skill_dir = skill_md.parent
        skill_name = _read_skill_name(skill_md)
        skills.append((skill_name, skill_dir))
    return skills


def _read_skill_name(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
        metadata, _ = _split_frontmatter(text)
        name = metadata.get("name")
        if isinstance(name, str) and SKILL_NAME_PATTERN.fullmatch(name):
            return name
    except (OSError, UnicodeDecodeError, ValueError):
        pass
    return skill_md.parent.name


def _dir_hash(directory: Path) -> str:
    hasher = hashlib.md5()
    for fpath in sorted(directory.rglob("*")):
        if not fpath.is_file():
            continue
        rel = fpath.relative_to(directory).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(fpath.read_bytes())
    return hasher.hexdigest()


def _read_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    manifest: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, hash_value = line.partition(":")
        manifest[name.strip()] = hash_value.strip()
    return manifest


def _write_manifest(path: Path, manifest: dict[str, str]) -> None:
    lines = [f"{name}:{hash_value}" for name, hash_value in sorted(manifest.items())]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _print_summary(
    summary: SyncSummary, *, bundled_dir: Path, target_dir: Path
) -> None:
    print(f"Bundled skills source: {bundled_dir}")
    print(f"User skills target: {target_dir}")
    if summary.added:
        print(f"Added: {', '.join(summary.added)}")
    if summary.updated:
        print(f"Updated: {', '.join(summary.updated)}")
    if summary.skipped:
        print(f"Skipped (customized or deleted): {', '.join(summary.skipped)}")
    if summary.unchanged:
        print(f"Unchanged: {', '.join(summary.unchanged)}")
    if not any(
        (
            summary.added,
            summary.updated,
            summary.skipped,
            summary.unchanged,
        )
    ):
        print("No bundled skills found to sync.")
