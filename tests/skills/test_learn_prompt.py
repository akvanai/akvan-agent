"""Tests for learn prompt builder."""

from __future__ import annotations

import pytest

from agent.skills.learn_prompt import (
    LearnSource,
    _AUTHORING_STANDARDS,
    build_learn_prompt,
    classify_learn_source,
)


@pytest.mark.parametrize(
    ("user_request", "expected"),
    [
        ("", LearnSource.CURRENT_SESSION),
        ("the deploy flow we just did", LearnSource.CURRENT_SESSION),
        ("what we ran above", LearnSource.CURRENT_SESSION),
        ("the deploy workflow from my last session", LearnSource.PAST_SESSION),
        ("from previous conversation", LearnSource.PAST_SESSION),
        ("the REST client in ~/sdk", LearnSource.EXTERNAL),
        ("https://docs.example.com/deploy", LearnSource.EXTERNAL),
        ("our deploy pipeline", LearnSource.USER_WORKFLOW),
        ("deploy staging", LearnSource.USER_WORKFLOW),
        ("tailwind css", LearnSource.GENERAL_SUBJECT),
        ("Go lang", LearnSource.GENERAL_SUBJECT),
        ("football tactics", LearnSource.GENERAL_SUBJECT),
        ("kubernetes", LearnSource.AMBIGUOUS),
        ("docker setup", LearnSource.AMBIGUOUS),
    ],
)
def test_classify_learn_source(user_request: str, expected: LearnSource) -> None:
    assert classify_learn_source(user_request) == expected


def test_build_learn_prompt_includes_request() -> None:
    prompt = build_learn_prompt("the REST client in ~/sdk")
    assert "REST client" in prompt
    assert "skill_manage" in prompt
    assert _AUTHORING_STANDARDS in prompt
    assert "SOURCE MODE: external" in prompt


def test_build_learn_prompt_default_conversation() -> None:
    prompt = build_learn_prompt("")
    assert "conversation" in prompt.lower()
    assert "SOURCE MODE: current_session" in prompt
    assert "MUST NOT call `session_search`" in prompt


def test_build_learn_prompt_empty_session_guard() -> None:
    prompt = build_learn_prompt("", prior_user_turns=0)
    assert "no prior user turns" in prompt
    assert "MUST NOT call `session_search`" in prompt


def test_build_learn_prompt_past_session_requires_session_search() -> None:
    prompt = build_learn_prompt(
        "deploy workflow from my last session",
        tools_available=frozenset({"skill_manage", "session_search"}),
    )
    assert "SOURCE MODE: past_session" in prompt
    assert "MUST call `session_search`" in prompt
    assert "MUST NOT rely on the current session" in prompt


def test_build_learn_prompt_past_session_without_store() -> None:
    prompt = build_learn_prompt(
        "from my last session",
        tools_available=frozenset({"skill_manage"}),
    )
    assert "session_search` is not available" in prompt


def test_build_learn_prompt_general_subject_uses_web() -> None:
    prompt = build_learn_prompt(
        "tailwind css",
        tools_available=frozenset({"web_search", "web_extract", "skill_manage"}),
    )
    assert "SOURCE MODE: general_subject" in prompt
    assert "MUST use `web_search`" in prompt
    assert "MUST NOT call `session_search`" in prompt


def test_build_learn_prompt_ambiguous_asks_user() -> None:
    prompt = build_learn_prompt("kubernetes")
    assert "SOURCE MODE: ambiguous" in prompt
    assert "ask one short clarifying question" in prompt
    assert "Do NOT create a skill until the user answers" in prompt


def test_build_learn_prompt_user_workflow() -> None:
    prompt = build_learn_prompt(
        "deploy staging",
        prior_user_turns=3,
        tools_available=frozenset({"session_search", "skill_manage"}),
    )
    assert "SOURCE MODE: user_workflow" in prompt
    assert "MUST NOT use `web_search`" in prompt
