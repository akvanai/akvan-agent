"""Structured event logging for user-visible activity."""

from __future__ import annotations

import logging

from agent.logging_setup import truncate_summary
from agent.skills.provenance import get_current_write_origin

memory_logger = logging.getLogger("akvan.memory")
skills_logger = logging.getLogger("akvan.skills")
review_logger = logging.getLogger("akvan.review")
session_logger = logging.getLogger("akvan.session")
gateway_logger = logging.getLogger("akvan.gateway")


def log_memory(action: str, target: str, summary: str) -> None:
    memory_logger.info(
        "%s target=%s origin=%s summary=%s",
        action,
        target,
        get_current_write_origin(),
        truncate_summary(summary),
    )


def log_skill(action: str, name: str, summary: str = "") -> None:
    skills_logger.info(
        "%s name=%s origin=%s%s",
        action,
        name,
        get_current_write_origin(),
        f" summary={truncate_summary(summary)}" if summary else "",
    )


def log_review(message: str, *, level: int = logging.INFO) -> None:
    review_logger.log(level, "%s", message)


def log_session(message: str, *, level: int = logging.INFO) -> None:
    session_logger.log(level, "%s", message)


def log_gateway(message: str, *, level: int = logging.INFO) -> None:
    gateway_logger.log(level, "%s", message)
