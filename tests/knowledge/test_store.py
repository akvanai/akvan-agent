from __future__ import annotations

from pathlib import Path

import pytest

from agent.knowledge.config import KnowledgeConfig
from agent.knowledge.models import KnowledgeError, OKFDocument
from agent.knowledge.store import KnowledgeStore


@pytest.fixture
def knowledge(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(
        KnowledgeConfig(),
        root=tmp_path / "knowledge",
        state_root=tmp_path / "knowledge-state",
    )


def _frontmatter(**extra):
    return {
        "type": "Brand Identity",
        "title": "Brand Identity",
        "description": "Official brand details.",
        **extra,
    }


def test_empty_bundle_and_permissive_external_document(knowledge: KnowledgeStore) -> None:
    assert (knowledge.root / "index.md").is_file()
    assert (knowledge.root / "log.md").is_file()
    index = OKFDocument.parse((knowledge.root / "index.md").read_text())
    log = OKFDocument.parse((knowledge.root / "log.md").read_text())
    assert index.frontmatter["type"] == "index"
    assert log.frontmatter["type"] == "log"
    external = knowledge.root / "External Concept.md"
    external.write_text("---\ntype: Reference\ncustom: kept\n---\n\nUseful body.\n", encoding="utf-8")

    loaded = knowledge.read("External Concept")
    assert loaded["frontmatter"]["custom"] == "kept"
    assert loaded["body"].strip() == "Useful body."


def test_explicit_safe_fact_auto_applies_and_is_searchable(knowledge: KnowledgeStore) -> None:
    result = knowledge.propose(
        operation="create",
        concept_id="brand/identity",
        frontmatter=_frontmatter(tags=["brand", "color"]),
        body="# Colors\n\nThe official accent is `#FF9F1C`.",
        evidence=[{"kind": "explicit_user", "quote": "official accent is #FF9F1C"}],
        confidence="high",
        user_messages=["Our official accent is #FF9F1C."],
    )

    assert result["status"] == "applied"
    assert knowledge.search("FF9F1C")[0]["concept_id"] == "brand/identity"
    assert "Brand Identity" in (knowledge.root / "brand" / "index.md").read_text()
    assert "brand/identity" in (knowledge.root / "log.md").read_text()


def test_inference_waits_for_approval_and_detects_drift(knowledge: KnowledgeStore) -> None:
    created = knowledge.propose(
        operation="create",
        concept_id="products/akvan",
        frontmatter={
            "type": "Product",
            "title": "Akvan",
            "description": "The Akvan product.",
        },
        body="# Positioning\n\nAgent harness.",
        evidence=[{"kind": "inference", "quote": "sounds like an agent harness"}],
        confidence="medium",
        user_messages=["Tell me about Akvan."],
    )
    assert created["status"] == "pending"
    proposal_id = created["proposal_id"]

    path = knowledge.root / "products" / "akvan.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntype: Product\ntitle: Other\ndescription: Changed.\ntimestamp: now\n---\n\nChanged.\n",
        encoding="utf-8",
    )
    with pytest.raises(KnowledgeError, match="changed"):
        knowledge.manage("approve", proposal_id)


def test_risky_fact_and_unsafe_paths_do_not_auto_apply(knowledge: KnowledgeStore) -> None:
    result = knowledge.propose(
        operation="create",
        concept_id="roadmap/private",
        frontmatter={
            "type": "Roadmap",
            "title": "Private roadmap",
            "description": "Confidential plans.",
        },
        body="# Private roadmap\n\nDo not publish.",
        evidence=[{"kind": "explicit_user", "quote": "This private roadmap is confidential"}],
        confidence="high",
        user_messages=["This private roadmap is confidential."],
    )
    assert result["status"] == "pending"
    with pytest.raises(KnowledgeError):
        knowledge.read("../secret")
    with pytest.raises(KnowledgeError):
        knowledge.propose(
            operation="create",
            concept_id="Bad Path",
            frontmatter=_frontmatter(),
            body="Body",
            evidence=[],
            confidence="low",
            user_messages=[],
        )


def test_update_preserves_unknown_frontmatter(knowledge: KnowledgeStore) -> None:
    initial = knowledge.propose(
        operation="create",
        concept_id="brand/voice",
        frontmatter={
            "type": "Brand Voice",
            "title": "Voice",
            "description": "Writing voice.",
            "custom": "preserve-me",
        },
        body="# Voice\n\nDirect.",
        evidence=[{"kind": "explicit_user", "quote": "Our voice is direct"}],
        confidence="high",
        user_messages=["Our voice is direct."],
    )
    assert initial["status"] == "applied"
    update = knowledge.propose(
        operation="update",
        concept_id="brand/voice",
        frontmatter={"description": "Writing voice and tone."},
        body="# Voice\n\nDirect.\n\nAvoid hype.",
        evidence=[{"kind": "explicit_user", "quote": "Avoid hype in our voice"}],
        confidence="high",
        user_messages=["Avoid hype in our voice."],
    )
    assert update["status"] == "applied"
    assert knowledge.read("brand/voice")["frontmatter"]["custom"] == "preserve-me"
