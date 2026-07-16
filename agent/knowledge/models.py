"""OKF documents and knowledge proposal records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import yaml


class KnowledgeError(ValueError):
    """Raised for invalid or unsafe knowledge operations."""


@dataclass(frozen=True)
class OKFDocument:
    frontmatter: dict[str, Any]
    body: str

    @classmethod
    def parse(cls, text: str) -> "OKFDocument":
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise KnowledgeError("Concept must start with YAML frontmatter.")
        try:
            end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
        except StopIteration as exc:
            raise KnowledgeError("Unterminated YAML frontmatter.") from exc
        try:
            raw = yaml.safe_load("\n".join(lines[1:end])) or {}
        except yaml.YAMLError as exc:
            raise KnowledgeError(f"Invalid YAML frontmatter: {exc}") from exc
        if not isinstance(raw, dict):
            raise KnowledgeError("Frontmatter must be a YAML mapping.")
        if not isinstance(raw.get("type"), str) or not raw["type"].strip():
            raise KnowledgeError("Frontmatter requires a non-empty type.")
        body = "\n".join(lines[end + 1 :]).lstrip("\n")
        return cls(dict(raw), body)

    def validate_created(self) -> None:
        for key in ("type", "title", "description", "timestamp"):
            if not isinstance(self.frontmatter.get(key), str) or not str(
                self.frontmatter[key]
            ).strip():
                raise KnowledgeError(f"Akvan-created concepts require {key!r}.")
        confidence = self.frontmatter.get("confidence")
        if confidence is not None and confidence not in {"low", "medium", "high"}:
            raise KnowledgeError("confidence must be low, medium, or high.")

    def serialize(self) -> str:
        frontmatter = yaml.safe_dump(
            self.frontmatter, sort_keys=False, allow_unicode=True
        ).rstrip()
        body = self.body.rstrip() + "\n"
        return f"---\n{frontmatter}\n---\n\n{body}"

    def digest(self) -> str:
        return hashlib.sha256(self.serialize().encode("utf-8")).hexdigest()


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
