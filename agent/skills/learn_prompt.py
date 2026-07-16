"""Build the agent prompt for open-ended ``/learn`` requests."""

from __future__ import annotations

import re
from enum import Enum

_AUTHORING_STANDARDS = """\
Follow the Akvan skill-authoring standards exactly:

Frontmatter:
- name: lowercase-hyphenated, <=64 chars.
- description: ONE sentence, <=60 characters, ends with a period. State the capability,
  not the implementation. COUNT characters before saving.
- category: use a sensible folder name (e.g. software-development, creative, github).

Body section order:
1. "# <Human Title>" then a 2-3 sentence intro.
2. "## When to Use" — bullet list of concrete trigger phrases.
3. "## Prerequisites" — env vars, credentials, install steps.
4. "## How to Run" — canonical invocation through Akvan tools.
5. "## Quick Reference" — flat command/endpoint list.
6. "## Procedure" — numbered steps with copy-paste-exact commands.
7. "## Pitfalls" — known limits and gotchas.
8. "## Verification" — one command that proves the skill worked.

Akvan-tool framing:
- Frame scripts as "invoke through the `terminal` tool".
- Reference Akvan tools by name: `terminal`, `read_file`, `write_file`, `patch`,
  `web_extract`, `web_search`, `memory`, `skill_view`, `skill_manage`.
- Do NOT name shell utilities the agent already has wrapped.
- Larger scripts belong under the skill's `scripts/` via skill_manage write_file.

Quality bar:
- Prefer exact commands and APIs from the source — never invent flags or paths.
- Keep it tight: ~100 lines simple, ~200 complex.
- Do not write a router skill that only points at other skills."""

_PAST_SESSION_RE = re.compile(
    r"\b(?:last|previous|prior|earlier|past|old)\s+"
    r"(?:session|chat|conversation)s?\b",
    re.IGNORECASE,
)
_CURRENT_SESSION_RE = re.compile(
    r"\b(?:we just|just did|just ran|this conversation|this chat|above|what we)\b",
    re.IGNORECASE,
)
_EXTERNAL_RE = re.compile(
    r"https?://|~[/\\]|(?:^|[\s'\"])(?:\.{0,2}/)?[\w./-]+\.(?:md|py|sh|yaml|yml|json|toml)\b",
    re.IGNORECASE,
)
_USER_WORKFLOW_RE = re.compile(
    r"\b(?:our|my|this repo|this project|workflow|deploy(?:ment)?|pipeline|"
    r"staging|production|ci/?cd|runbook|playbook)\b",
    re.IGNORECASE,
)
_GENERAL_SUBJECT_RE = re.compile(
    r"\b(?:architecture|football|tailwind(?:\s+css)?|go(?:\s+lang(?:uage)?)?|"
    r"python|javascript|typescript|rust|css|html|design(?:\s+patterns)?|"
    r"algorithms?|machine\s+learning|deep\s+learning)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_TECH_RE = re.compile(
    r"\b(?:kubernetes|k8s|docker|react|vue|angular|terraform|ansible|aws|gcp|azure)\b",
    re.IGNORECASE,
)


class LearnSource(str, Enum):
    CURRENT_SESSION = "current_session"
    PAST_SESSION = "past_session"
    EXTERNAL = "external"
    USER_WORKFLOW = "user_workflow"
    GENERAL_SUBJECT = "general_subject"
    AMBIGUOUS = "ambiguous"


def classify_learn_source(request: str) -> LearnSource:
    """Classify where /learn should gather material from (first match wins)."""
    req = (request or "").strip()
    if not req:
        return LearnSource.CURRENT_SESSION
    if _PAST_SESSION_RE.search(req):
        return LearnSource.PAST_SESSION
    if _EXTERNAL_RE.search(req):
        return LearnSource.EXTERNAL
    if _CURRENT_SESSION_RE.search(req):
        return LearnSource.CURRENT_SESSION
    if _USER_WORKFLOW_RE.search(req):
        return LearnSource.USER_WORKFLOW
    if _GENERAL_SUBJECT_RE.search(req):
        return LearnSource.GENERAL_SUBJECT
    if _AMBIGUOUS_TECH_RE.search(req):
        return LearnSource.AMBIGUOUS
    return LearnSource.AMBIGUOUS


def _has_tool(tools_available: frozenset[str] | None, name: str) -> bool:
    if tools_available is None:
        return True
    return name in tools_available


def _gather_current_session(*, prior_user_turns: int) -> str:
    lines = [
        "## Gather (current session)",
        "",
        "- Use ONLY the current conversation history already in context.",
        "- MUST NOT call `session_search`.",
    ]
    if prior_user_turns == 0:
        lines.extend(
            [
                "- This session has no prior user turns yet.",
                "- Do NOT invent a skill. Tell the user to either:",
                "  - do the work in this chat first, then run `/learn` again;",
                "  - `/resume` a saved session that contains the workflow;",
                "  - name a file path or URL in `/learn …`;",
                "  - say \"from my last session\" to search past chats; or",
                "  - ask about a general subject (e.g. `/learn tailwind css`).",
            ]
        )
    else:
        lines.append(
            "- Filter the conversation to the topic the user described."
        )
    return "\n".join(lines)


def _gather_past_session(*, tools_available: frozenset[str] | None) -> str:
    if not _has_tool(tools_available, "session_search"):
        return (
            "## Gather (past session)\n\n"
            "- `session_search` is not available (session store disabled).\n"
            "- Do NOT create a skill. Tell the user to enable session persistence "
            "or `/resume` the session they want to learn from."
        )
    return "\n".join(
        [
            "## Gather (past session)",
            "",
            "- MUST call `session_search` as your first gather step.",
            "- For \"last/previous session\" wording: call `session_search` with no "
            "arguments (browse mode) to load the most recent past session.",
            "- For a topic plus past-session wording: call `session_search` with "
            "`query=` using topic keywords, then scroll/read as needed.",
            "- MUST NOT rely on the current session as the primary source.",
            "- MUST NOT call `web_search` unless the user explicitly asked for web.",
        ]
    )


def _gather_user_workflow(
    *, prior_user_turns: int, tools_available: frozenset[str] | None
) -> str:
    lines = [
        "## Gather (user workflow)",
        "",
        "- Start with the current conversation history, filtered to the topic.",
    ]
    if prior_user_turns == 0:
        lines.append(
            "- This session has no prior turns — skip current-session gather."
        )
    else:
        lines.append(
            "- MUST NOT call `session_search` when the current session already "
            "has enough material on the topic."
        )
    if _has_tool(tools_available, "session_search"):
        lines.append(
            "- If current-session material is insufficient, call `session_search` "
            "with `query=` using topic keywords from the request."
        )
    else:
        lines.append(
            "- `session_search` is unavailable — if current session is thin, tell "
            "the user to `/resume` the relevant session or add more context."
        )
    lines.append(
        "- MUST NOT use `web_search` unless the user explicitly asked for web."
    )
    return "\n".join(lines)


def _gather_general_subject(*, tools_available: frozenset[str] | None) -> str:
    has_web = _has_tool(tools_available, "web_search") and _has_tool(
        tools_available, "web_extract"
    )
    if not has_web:
        return "\n".join(
            [
                "## Gather (general subject)",
                "",
                "- Web tools are not configured.",
                "- Do NOT use `session_search` or current session history.",
                "- Tell the user to configure web tools, or provide a URL/path "
                "in `/learn …`.",
            ]
        )
    return "\n".join(
        [
            "## Gather (general subject)",
            "",
            "- MUST use `web_search` to find authoritative sources on the topic.",
            "- MUST use `web_extract` on the top relevant results.",
            "- MUST NOT call `session_search`.",
            "- MUST NOT use current session history as a source.",
        ]
    )


def _gather_ambiguous() -> str:
    return "\n".join(
        [
            "## Gather (ambiguous — ask first)",
            "",
            "- The topic could be a general reference or a user-specific workflow.",
            "- MUST ask one short clarifying question before calling `skill_manage`:",
            "  gather from (a) past/current sessions, (b) web, or (c) both?",
            "- Do NOT create a skill until the user answers.",
        ]
    )


def _gather_external() -> str:
    return "\n".join(
        [
            "## Gather (external source)",
            "",
            "- MUST use `read_file` for local file or directory paths in the request.",
            "- MUST use `web_extract` for URLs in the request.",
            "- May supplement from the current session ONLY if the request also "
            "references recent work (e.g. \"we just did\").",
            "- MUST NOT call `session_search` unless the user also referenced a "
            "past session.",
        ]
    )


_GATHER_BUILDERS = {
    LearnSource.CURRENT_SESSION: lambda **kw: _gather_current_session(
        prior_user_turns=kw["prior_user_turns"]
    ),
    LearnSource.PAST_SESSION: lambda **kw: _gather_past_session(
        tools_available=kw["tools_available"]
    ),
    LearnSource.USER_WORKFLOW: lambda **kw: _gather_user_workflow(
        prior_user_turns=kw["prior_user_turns"],
        tools_available=kw["tools_available"],
    ),
    LearnSource.GENERAL_SUBJECT: lambda **kw: _gather_general_subject(
        tools_available=kw["tools_available"]
    ),
    LearnSource.AMBIGUOUS: lambda **_kw: _gather_ambiguous(),
    LearnSource.EXTERNAL: lambda **_kw: _gather_external(),
}


def build_learn_prompt(
    user_request: str,
    *,
    prior_user_turns: int = 0,
    tools_available: frozenset[str] | None = None,
) -> str:
    req = (user_request or "").strip()
    source = classify_learn_source(req)
    if not req and source == LearnSource.CURRENT_SESSION:
        req = (
            "the workflow we just went through in this conversation — review "
            "the steps taken and distill them into a reusable skill"
        )

    gather = _GATHER_BUILDERS[source](
        prior_user_turns=prior_user_turns,
        tools_available=tools_available,
    )

    return (
        "[/learn] The user wants you to learn a reusable skill from the "
        "source(s) they described below, and save it.\n\n"
        f"SOURCE MODE: {source.value}\n\n"
        f"WHAT TO LEARN FROM:\n{req}\n\n"
        f"{gather}\n\n"
        "## Author\n"
        "1. After gathering sufficient material, author ONE SKILL.md and save it "
        "with `skill_manage` (action=\"create\"). Pick a sensible category. "
        "If the procedure needs a script, add it under the skill's `scripts/` "
        "with `skill_manage` write_file.\n\n"
        f"{_AUTHORING_STANDARDS}\n\n"
        "When done, tell the user the skill name, its category, and a "
        "one-line summary of what it captured."
    )
