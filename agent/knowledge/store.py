"""Private global OKF bundle, search index, and proposal queue."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from agent.knowledge.config import KnowledgeConfig
from agent.knowledge.models import KnowledgeError, OKFDocument, text_digest
from agent.knowledge.paths import (
    knowledge_dir,
    knowledge_state_dir,
)
from agent.storage.permissions import ensure_private_dir, ensure_private_file

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the process-local lock
    fcntl = None  # type: ignore[assignment]


_CONCEPT_PART = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TOKEN = re.compile(r"[\w-]+", re.UNICODE)
_SENSITIVE = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|password|private[_ -]?key|"
    r"client[_ -]?secret|-----BEGIN [A-Z ]+ PRIVATE KEY-----)"
)
_HIGH_RISK = re.compile(r"(?i)\b(private roadmap|publication policy|confidential)\b")


class KnowledgeStore:
    """Own a global OKF bundle and safe proposal lifecycle."""

    def __init__(
        self,
        config: KnowledgeConfig | None = None,
        *,
        root: Path | None = None,
        state_root: Path | None = None,
    ) -> None:
        self.config = config or KnowledgeConfig()
        self.root = root or knowledge_dir()
        self.state_root = state_root or knowledge_state_dir()
        self.proposals_root = self.state_root / "proposals"
        self._thread_lock = threading.RLock()
        self._index: list[dict[str, Any]] = []
        self._signature: tuple[tuple[str, int, int], ...] = ()
        self.ensure_initialized()

    def ensure_initialized(self) -> None:
        ensure_private_dir(self.root)
        ensure_private_dir(self.state_root)
        ensure_private_dir(self.proposals_root)
        index = self.root / "index.md"
        log = self.root / "log.md"
        if not index.exists():
            self._atomic_write(
                index,
                self._system_document(
                    doc_type="index",
                    title="Knowledge",
                    description="Index of global knowledge concepts.",
                    body="# Knowledge\n\nNo concepts yet.",
                ),
            )
        if not log.exists():
            self._atomic_write(
                log,
                self._system_document(
                    doc_type="log",
                    title="Knowledge Update Log",
                    description="Dated history of global knowledge changes.",
                    body="# Knowledge Update Log",
                ),
            )
        self.refresh(force=True)

    @staticmethod
    def _safe_concept_id(concept_id: str) -> str:
        raw = concept_id.strip().replace("\\", "/")
        if raw.endswith(".md"):
            raw = raw[:-3]
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            raise KnowledgeError("Concept ID must be a safe bundle-relative path.")
        if path.name in {"index", "log"}:
            raise KnowledgeError("index.md and log.md are reserved filenames.")
        return path.as_posix()

    @classmethod
    def normalize_concept_id(cls, concept_id: str) -> str:
        raw = cls._safe_concept_id(concept_id)
        path = PurePosixPath(raw)
        if any(not _CONCEPT_PART.fullmatch(part) for part in path.parts):
            raise KnowledgeError(
                "Concept ID parts must use lowercase letters, numbers, '-' or '_'."
            )
        return path.as_posix()

    def _concept_path(self, concept_id: str) -> Path:
        cid = self.normalize_concept_id(concept_id)
        path = (self.root / f"{cid}.md").resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:  # defense in depth
            raise KnowledgeError("Concept path escapes the knowledge bundle.") from exc
        return path

    def _read_concept_path(self, concept_id: str) -> tuple[str, Path]:
        cid = self._safe_concept_id(concept_id)
        path = (self.root / f"{cid}.md").resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise KnowledgeError("Concept path escapes the knowledge bundle.") from exc
        return cid, path

    def _bundle_files(self) -> list[Path]:
        return sorted(
            path
            for path in self.root.rglob("*.md")
            if path.name not in {"index.md", "log.md"}
            and not any(part.startswith(".") for part in path.relative_to(self.root).parts)
        )

    def refresh(self, *, force: bool = False) -> None:
        files = self._bundle_files()
        signature = tuple(
            (str(path.relative_to(self.root)), path.stat().st_mtime_ns, path.stat().st_size)
            for path in files
        )
        if not force and signature == self._signature:
            return
        entries: list[dict[str, Any]] = []
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
                if len(text) > self.config.max_concept_chars:
                    continue
                doc = OKFDocument.parse(text)
            except (OSError, UnicodeDecodeError, KnowledgeError):
                continue
            concept_id = path.relative_to(self.root).with_suffix("").as_posix()
            entries.append(
                {
                    "concept_id": concept_id,
                    "frontmatter": doc.frontmatter,
                    "body": doc.body,
                    "text": " ".join(
                        (
                            concept_id,
                            str(doc.frontmatter.get("type", "")),
                            str(doc.frontmatter.get("title", "")),
                            str(doc.frontmatter.get("description", "")),
                            " ".join(str(v) for v in doc.frontmatter.get("tags", []) or []),
                            doc.body,
                        )
                    ).casefold(),
                }
            )
        self._index = entries
        self._signature = signature

    def list_concepts(self) -> list[dict[str, Any]]:
        self.refresh()
        return [self._summary(entry) for entry in self._index]

    def read(self, concept_id: str) -> dict[str, Any]:
        cid, path = self._read_concept_path(concept_id)
        if not path.is_file():
            raise KnowledgeError(f"Knowledge concept not found: {concept_id}")
        text = path.read_text(encoding="utf-8")
        if len(text) > self.config.max_concept_chars:
            raise KnowledgeError("Knowledge concept exceeds the configured size limit.")
        doc = OKFDocument.parse(text)
        return {
            "concept_id": cid,
            "frontmatter": doc.frontmatter,
            "body": doc.body,
            "hash": text_digest(text),
        }

    def search(
        self, query: str, *, types: list[str] | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        self.refresh()
        tokens = {token.casefold() for token in _TOKEN.findall(query) if token.strip()}
        if not tokens:
            return []
        allowed = {value.casefold() for value in types or []}
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in self._index:
            concept_type = str(entry["frontmatter"].get("type", ""))
            if allowed and concept_type.casefold() not in allowed:
                continue
            haystack = entry["text"]
            score = sum(3 if token in entry["concept_id"] else 1 for token in tokens if token in haystack)
            if score:
                result = self._summary(entry)
                result["excerpt"] = self._excerpt(entry["body"], tokens)
                scored.append((score, result))
        scored.sort(key=lambda item: (-item[0], item[1]["concept_id"]))
        return [result for _, result in scored[: max(1, min(limit, 20))]]

    def propose(
        self,
        *,
        operation: str,
        concept_id: str,
        frontmatter: dict[str, Any],
        body: str,
        evidence: list[dict[str, Any]],
        confidence: str,
        user_messages: list[str],
        conflict: bool = False,
    ) -> dict[str, Any]:
        if operation not in {"create", "update"}:
            raise KnowledgeError("operation must be create or update.")
        cid = self.normalize_concept_id(concept_id)
        existing: dict[str, Any] | None
        try:
            existing = self.read(cid)
        except KnowledgeError:
            existing = None
        if operation == "create" and existing is not None:
            raise KnowledgeError("Concept already exists; propose an update instead.")
        if operation == "update" and existing is None:
            raise KnowledgeError("Concept does not exist; propose a create instead.")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fm = dict(existing["frontmatter"] if existing else {})
        fm.update(frontmatter)
        fm["timestamp"] = now
        fm["confidence"] = confidence
        fm["sources"] = evidence
        doc = OKFDocument(fm, body.strip())
        doc.validate_created()
        serialized = doc.serialize()
        if len(serialized) > self.config.max_concept_chars:
            raise KnowledgeError("Proposed concept exceeds the configured size limit.")

        explicit = self._verified_explicit_evidence(evidence, user_messages)
        risky = bool(_SENSITIVE.search(serialized) or _HIGH_RISK.search(serialized))
        preserves_existing = existing is None or existing["body"].strip() in body.strip()
        auto_apply = bool(
            self.config.auto_save_explicit_facts
            and explicit
            and confidence == "high"
            and not conflict
            and not risky
            and preserves_existing
        )
        proposal = {
            "id": uuid.uuid4().hex[:12],
            "status": "pending",
            "operation": operation,
            "concept_id": cid,
            "frontmatter": fm,
            "body": body.strip(),
            "evidence": evidence,
            "confidence": confidence,
            "conflict": bool(conflict),
            "base_hash": existing["hash"] if existing else None,
            "created_at": now,
        }
        if auto_apply:
            result = self._apply_payload(proposal, automatic=True)
            return {"success": True, "status": "applied", **result}
        self._write_proposal(proposal)
        return {
            "success": True,
            "status": "pending",
            "proposal_id": proposal["id"],
            "concept_id": cid,
            "message": "Knowledge change is waiting for user approval.",
        }

    def manage(self, action: str, proposal_id: str | None = None) -> dict[str, Any]:
        if action == "list":
            proposals = self.list_proposals()
            return {"success": True, "proposals": proposals, "count": len(proposals)}
        if not proposal_id:
            raise KnowledgeError(f"proposal_id is required for {action}.")
        proposal = self._read_proposal(proposal_id)
        if action == "show":
            return {"success": True, "proposal": proposal}
        if action == "approve":
            if proposal.get("status") != "pending":
                raise KnowledgeError("Proposal is no longer pending.")
            result = self._apply_payload(proposal, automatic=False)
            proposal["status"] = "approved"
            proposal["decided_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._write_proposal(proposal)
            return {"success": True, "status": "approved", **result}
        if action == "reject":
            proposal["status"] = "rejected"
            proposal["decided_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._write_proposal(proposal)
            return {"success": True, "status": "rejected", "proposal_id": proposal_id}
        raise KnowledgeError("action must be list, show, approve, or reject.")

    def list_proposals(self) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        for path in sorted(self.proposals_root.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict) and item.get("status") == "pending":
                proposals.append(
                    {
                        "id": item.get("id"),
                        "operation": item.get("operation"),
                        "concept_id": item.get("concept_id"),
                        "confidence": item.get("confidence"),
                        "created_at": item.get("created_at"),
                    }
                )
        return proposals

    def status(self) -> dict[str, Any]:
        concepts = self.list_concepts()
        subjects = sorted({item["concept_id"].partition("/")[0] for item in concepts})
        recent = self._recent_log_entries(5)
        try:
            state = json.loads((self.state_root / "review.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        return {
            "concept_count": len(concepts),
            "subjects": subjects,
            "pending_count": len(self.list_proposals()),
            "recent_updates": recent,
            "last_review_at": state.get("last_review_at") if isinstance(state, dict) else None,
        }

    def _apply_payload(self, proposal: dict[str, Any], *, automatic: bool) -> dict[str, Any]:
        cid = str(proposal["concept_id"])
        path = self._concept_path(cid)
        doc = OKFDocument(dict(proposal["frontmatter"]), str(proposal["body"]))
        doc.validate_created()
        with self._write_lock():
            current_hash = text_digest(path.read_text(encoding="utf-8")) if path.exists() else None
            if current_hash != proposal.get("base_hash"):
                raise KnowledgeError("Concept changed after this proposal was created; review it again.")
            current = path.parent
            while self.root in current.parents or current == self.root:
                ensure_private_dir(current)
                if current == self.root:
                    break
                current = current.parent
            self._atomic_write(path, doc.serialize())
            self._generate_indexes()
            verb = "Auto-update" if automatic else "Approved"
            self._append_log(f"**{verb}**: {proposal['operation']} [{cid}](/{cid}.md).")
        self.refresh(force=True)
        return {
            "concept_id": cid,
            "path": str(path),
            "message": "Knowledge concept updated." if proposal["operation"] == "update" else "Knowledge concept created.",
        }

    def _generate_indexes(self) -> None:
        directories = {self.root}
        for path in self._bundle_files():
            directories.update((path.parent, *path.parent.parents))
        for directory in sorted(
            (d for d in directories if d == self.root or self.root in d.parents),
            key=lambda d: len(d.parts),
            reverse=True,
        ):
            concepts: list[tuple[str, OKFDocument]] = []
            for path in sorted(directory.glob("*.md")):
                if path.name in {"index.md", "log.md"}:
                    continue
                try:
                    concepts.append((path.name, OKFDocument.parse(path.read_text(encoding="utf-8"))))
                except (OSError, KnowledgeError):
                    continue
            subdirs = sorted(
                child for child in directory.iterdir() if child.is_dir() and not child.name.startswith(".")
            )
            title = "Knowledge" if directory == self.root else directory.name.replace("-", " ").title()
            lines = [f"# {title}", ""]
            if subdirs:
                lines.extend(("## Subjects", ""))
                lines.extend(f"- [{d.name}]({d.name}/)" for d in subdirs)
                lines.append("")
            if concepts:
                lines.extend(("## Concepts", ""))
                for filename, doc in concepts:
                    display = str(doc.frontmatter.get("title") or Path(filename).stem)
                    description = str(doc.frontmatter.get("description") or "").strip()
                    suffix = f" — {description}" if description else ""
                    lines.append(f"- [{display}]({filename}){suffix}")
            if not subdirs and not concepts:
                lines.append("No concepts yet.")
            self._atomic_write(
                directory / "index.md",
                self._system_document(
                    doc_type="index",
                    title=title,
                    description=f"Index of knowledge under {directory.relative_to(self.root) or 'the global bundle'}.",
                    body="\n".join(lines).rstrip(),
                ),
            )

    def _append_log(self, entry: str) -> None:
        path = self.root / "log.md"
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        try:
            body = OKFDocument.parse(current).body
        except KnowledgeError:
            body = current.strip() or "# Knowledge Update Log"
        if not body.startswith("# Knowledge Update Log"):
            body = f"# Knowledge Update Log\n\n{body}"
        today = datetime.now(timezone.utc).date().isoformat()
        heading = f"## {today}"
        if heading in body:
            body = body.replace(heading, f"{heading}\n\n- {entry}", 1)
        else:
            header = "# Knowledge Update Log\n"
            rest = body[len(header) :].lstrip() if body.startswith(header) else body
            body = f"{header}\n{heading}\n\n- {entry}\n"
            if rest:
                body += f"\n{rest.rstrip()}\n"
        self._atomic_write(
            path,
            self._system_document(
                doc_type="log",
                title="Knowledge Update Log",
                description="Dated history of global knowledge changes.",
                body=body.rstrip(),
            ),
        )

    def _recent_log_entries(self, limit: int) -> list[str]:
        try:
            text = (self.root / "log.md").read_text(encoding="utf-8")
            lines = OKFDocument.parse(text).body.splitlines()
        except (OSError, KnowledgeError):
            return []
        return [line[2:] for line in lines if line.startswith("- ")][:limit]

    @staticmethod
    def _system_document(
        *, doc_type: str, title: str, description: str, body: str
    ) -> str:
        return OKFDocument(
            {
                "type": doc_type,
                "title": title,
                "description": description,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            body,
        ).serialize()

    @staticmethod
    def _verified_explicit_evidence(
        evidence: list[dict[str, Any]], user_messages: list[str]
    ) -> bool:
        normalized = [" ".join(message.split()).casefold() for message in user_messages]
        for item in evidence:
            if item.get("kind") != "explicit_user":
                continue
            quote = " ".join(str(item.get("quote") or "").split()).casefold()
            if len(quote) >= 8 and any(quote in message for message in normalized):
                return True
        return False

    def _write_proposal(self, proposal: dict[str, Any]) -> None:
        path = self.proposals_root / f"{proposal['id']}.json"
        self._atomic_write(path, json.dumps(proposal, ensure_ascii=False, indent=2) + "\n")

    def _read_proposal(self, proposal_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{12}", proposal_id):
            raise KnowledgeError("Invalid proposal ID.")
        path = self.proposals_root / f"{proposal_id}.json"
        if not path.is_file():
            raise KnowledgeError("Knowledge proposal not found.")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeError("Knowledge proposal could not be read.") from exc
        if not isinstance(value, dict):
            raise KnowledgeError("Knowledge proposal is malformed.")
        return value

    @staticmethod
    def _summary(entry: dict[str, Any]) -> dict[str, Any]:
        fm = entry["frontmatter"]
        return {
            "concept_id": entry["concept_id"],
            "type": fm.get("type"),
            "title": fm.get("title") or entry["concept_id"].rsplit("/", 1)[-1],
            "description": fm.get("description", ""),
            "tags": fm.get("tags", []),
        }

    @staticmethod
    def _excerpt(body: str, tokens: set[str]) -> str:
        compact = " ".join(body.split())
        lowered = compact.casefold()
        positions = [lowered.find(token) for token in tokens if token in lowered]
        start = max(0, (min(positions) if positions else 0) - 80)
        excerpt = compact[start : start + 240]
        return ("…" if start else "") + excerpt + ("…" if start + 240 < len(compact) else "")

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        ensure_private_dir(self.state_root)
        lock_path = self.state_root / "write.lock"
        with self._thread_lock, lock_path.open("a+", encoding="utf-8") as handle:
            ensure_private_file(lock_path)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temp.chmod(0o600)
            os.replace(temp, path)
            path.chmod(0o600)
        finally:
            if temp.exists():
                temp.unlink()
