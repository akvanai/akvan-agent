"""Filesystem locations for global knowledge and its private runtime state."""

from pathlib import Path

from agent.config import akvan_home


def knowledge_dir() -> Path:
    return akvan_home() / "knowledge"


def knowledge_state_dir() -> Path:
    return akvan_home() / "knowledge-state"


def knowledge_proposals_dir() -> Path:
    return knowledge_state_dir() / "proposals"


def knowledge_review_state_file() -> Path:
    return knowledge_state_dir() / "review.json"
