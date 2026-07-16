"""Discovery and safe, on-demand reading for SKILL.md packages."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from html import escape
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

try:
    import yaml
except ModuleNotFoundError:  # Used only in unsynced source checkouts.
    class _YamlFallback:
        class YAMLError(ValueError):
            pass

        @staticmethod
        def safe_load(text: str) -> dict[str, str]:
            result: dict[str, str] = {}
            for line in text.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, separator, value = line.partition(":")
                if not separator:
                    raise _YamlFallback.YAMLError("invalid YAML frontmatter")
                result[key.strip()] = value.strip().strip("\"")
            return result

    yaml = _YamlFallback()

from agent.limits import (
    MAX_SKILL_CHARS,
    MAX_SKILL_METADATA_CHARS,
)
from agent.context.config import load_context_config
from agent.skills.models import Skill, SkillError, SkillOrigin
from agent.skills.paths import (
    SKILL_SUPPORT_DIRS,
    project_skills_dir,
    user_skills_dir,
)

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CATEGORY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class SkillRegistry:
    """A frozen, deterministic view of all skills for one agent session."""

    skills: Mapping[str, Skill]
    warnings: tuple[str, ...] = ()

    @classmethod
    def discover(cls, *, user_root: Path, project_root: Path) -> "SkillRegistry":
        found: dict[str, Skill] = {}
        warnings: list[str] = []
        config = load_context_config(project_root=project_root)
        for origin, root in (
            ("user", user_skills_dir(user_root)),
            ("project", project_skills_dir(project_root)),
        ):
            if not root.is_dir():
                continue
            for skill_file in sorted(root.rglob("SKILL.md")):
                if skill_file.is_symlink() or skill_file.parent.is_symlink():
                    warnings.append(f"Skipped symlinked skill {skill_file}.")
                    continue
                if _is_skill_support_path(skill_file, root):
                    continue
                try:
                    category = _category_from_path(skill_file, root)
                except SkillError as exc:
                    warnings.append(f"Skipped {skill_file}: {exc}")
                    continue
                try:
                    skill = _parse_skill(skill_file, origin=origin, category=category)
                except (OSError, UnicodeDecodeError, SkillError, yaml.YAMLError) as exc:
                    warnings.append(f"Skipped {skill_file}: {exc}")
                    continue
                try:
                    main_size, file_count, total_size = _package_metrics(skill_file.parent)
                except OSError as exc:
                    warnings.append(f"Could not inspect skill package {skill_file.parent}: {exc}")
                else:
                    if main_size > config.skill_warn_main_chars:
                        warnings.append(
                            f"Skill {skill.name!r} has a large SKILL.md "
                            f"({main_size:,} bytes); it will be loaded on demand."
                        )
                    if file_count > config.skill_warn_file_count:
                        warnings.append(
                            f"Skill {skill.name!r} contains {file_count:,} files; "
                            "supporting files remain on demand."
                        )
                    if total_size > config.skill_warn_total_bytes:
                        warnings.append(
                            f"Skill {skill.name!r} is a large package "
                            f"({total_size:,} bytes); resources remain on demand."
                        )
                existing = found.get(skill.name)
                if existing is not None:
                    if origin == "project" and existing.origin == "user":
                        found[skill.name] = skill
                        continue
                    warnings.append(
                        f"Skipped duplicate {origin} skill {skill.name!r}."
                    )
                    continue
                found[skill.name] = skill
        ordered = {name: found[name] for name in sorted(found)}
        return cls(MappingProxyType(ordered), tuple(warnings))

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def require(self, name: str) -> Skill:
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"Unknown skill {name!r}.")
        return skill

    def compact_index(self) -> str:
        if not self.skills:
            return "No skills are currently available."
        return "\n".join(
            f"- {skill.name} ({skill.category}): {skill.description} ({skill.origin})"
            for skill in self.skills.values()
        )

    def list_metadata(self) -> str:
        """Return the compact discovery surface exposed to the model."""
        return json.dumps(
            {
                "skills": [
                    {
                        "name": skill.name,
                        "description": skill.description,
                        "category": skill.category,
                        "origin": skill.origin,
                    }
                    for skill in self.skills.values()
                ],
                "count": len(self.skills),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def view(self, name: str, file_path: str | None = None) -> str:
        """Load instructions or one supporting text file on demand."""
        skill = self.require(name)
        if file_path is None:
            if skill.skill_file.is_symlink():
                raise SkillError("Symlinked SKILL.md files are not allowed.")
            content = _read_skill_file(skill.skill_file, label=f"skill {name}")
            _, body = _split_frontmatter(content)
            return (
                f'<skill_instructions name="{skill.name}" category="{skill.category}" '
                f'origin="{skill.origin}">\n'
                f"{body.strip()}\n"
                "</skill_instructions>"
            )

        resource_path = _normalize_resource_path(file_path)
        candidate = skill.root / resource_path
        root = skill.root.resolve()
        if candidate.is_symlink():
            raise SkillError("Symlinked skill resources are not allowed.")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise SkillError(
                f"Resource {resource_path!r} does not exist inside skill {name!r}."
            ) from exc
        if not resolved.is_file():
            raise SkillError(f"Resource {resource_path!r} is not a file.")
        content = _read_skill_file(
            resolved, label=f"skill resource {name}/{resource_path}"
        )
        return (
            f'<skill_resource skill="{skill.name}" path="{escape(resource_path, quote=True)}">\n'
            f"{content}\n"
            "</skill_resource>"
        )


def _category_from_path(skill_file: Path, skills_root: Path) -> str:
    rel = skill_file.relative_to(skills_root)
    parts = rel.parts
    if len(parts) != 3 or parts[-1] != "SKILL.md":
        raise SkillError(
            "skills must use skills/<category>/<name>/SKILL.md layout"
        )
    category = parts[0]
    if not CATEGORY_PATTERN.fullmatch(category):
        raise SkillError("category must use lowercase letters, digits, _ or -")
    return category


def _is_skill_support_path(skill_file: Path, skills_root: Path) -> bool:
    rel = skill_file.relative_to(skills_root)
    parts = rel.parts
    for index, part in enumerate(parts[:-2]):
        if part not in SKILL_SUPPORT_DIRS:
            continue
        skill_root = skills_root.joinpath(*parts[: index + 1])
        if (skill_root / "SKILL.md").exists():
            return True
    return False


def _parse_skill(
    skill_file: Path, *, origin: SkillOrigin, category: str
) -> Skill:
    text = _read_skill_index(skill_file)
    metadata, body = _split_frontmatter(text)
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not SKILL_NAME_PATTERN.fullmatch(name):
        raise SkillError("frontmatter name must use lowercase letters, digits, _ or -")
    if not isinstance(description, str) or not description.strip():
        raise SkillError("frontmatter description is required")
    if not body.strip():
        raise SkillError("skill instructions are empty")

    return Skill(
        name=name,
        description=" ".join(description.split()),
        category=category,
        root=skill_file.parent.resolve(),
        origin=origin,
    )


def _read_skill_index(path: Path) -> str:
    """Read only frontmatter and enough body to validate the skill package."""
    lines: list[str] = []
    frontmatter_closed = False
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if "\x00" in line:
                raise SkillError(f"{path} appears to be binary.")
            lines.append(line)
            if sum(map(len, lines)) > MAX_SKILL_METADATA_CHARS:
                raise SkillError(f"{path} metadata exceeds the skill size limit.")
            if len(lines) > 1 and line.strip() == "---":
                frontmatter_closed = True
                continue
            if frontmatter_closed and line.strip():
                break
    return "".join(lines).lstrip("\ufeff")


def _read_skill_file(path: Path, *, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError as exc:
        raise SkillError(f"{path} is not valid UTF-8.") from exc
    except OSError as exc:
        raise SkillError(f"Could not read {path}: {exc}") from exc
    if "\x00" in text:
        raise SkillError(f"{path} appears to be binary.")
    if len(text) > MAX_SKILL_CHARS:
        raise SkillError(
            f"{label} is {len(text):,} characters, above the "
            f"{MAX_SKILL_CHARS:,}-character safety limit. Split it into "
            "supporting files and load them on demand."
        )
    return text


def _package_metrics(root: Path) -> tuple[int, int, int]:
    """Return main size, regular-file count, and total package bytes."""

    main_size = (root / "SKILL.md").stat().st_size
    count = 0
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        count += 1
        total += path.stat().st_size
    return main_size, count, total


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise SkillError("SKILL.md frontmatter is not closed") from exc
    metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(metadata, dict):
        raise SkillError("SKILL.md frontmatter must be a mapping")
    return metadata, "\n".join(lines[end + 1 :])


def _normalize_resource_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip() or "\\" in path:
        raise SkillError("Skill resource path is invalid.")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise SkillError("Skill resource path must stay inside the skill directory.")
    return candidate.as_posix()
