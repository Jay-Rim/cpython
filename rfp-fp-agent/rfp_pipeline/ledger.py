"""문서, LLM 산출물, 사람 검토 이력을 보존하는 SQLite 원장."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from fp_engine import Certainty, Counted, FPFunction, FunctionType, ReviewStatus

from .ingest import validate_extraction_schema
from .models import ParsedDocument


_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS document (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES document(id),
    chunk_id TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    temperature REAL NOT NULL,
    seed INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, chunk_id, model, prompt_version, created_at)
);
CREATE TABLE IF NOT EXISTS requirement (
    document_id TEXT NOT NULL REFERENCES document(id),
    id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(document_id, id)
);
CREATE TABLE IF NOT EXISTS fp_function (
    document_id TEXT NOT NULL REFERENCES document(id),
    id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    review_status TEXT NOT NULL,
    excluded INTEGER NOT NULL DEFAULT 0,
    exclusion_reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(document_id, id)
);
CREATE TABLE IF NOT EXISTS review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    function_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id, function_id) REFERENCES fp_function(document_id, id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _counted(raw: Mapping[str, Any] | None) -> Counted:
    if not raw:
        return Counted(None, Certainty.UNKNOWN, "미확인")
    return Counted(raw.get("value"), Certainty(raw["certainty"]), raw.get("rationale", ""))


class Ledger:
    """SQLite 기반 append-audit 원장.

    최신 후보 상태는 ``fp_function``에, 모든 사람 변경 전후 값은 ``review``에
    별도로 남는다. 같은 파일을 다시 저장해도 기존 review 행은 삭제하지 않는다.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(_SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def save_document(self, document: ParsedDocument) -> None:
        now = _now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO document(id, filename, sha256, media_type, parsed_json, created_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     filename=excluded.filename, sha256=excluded.sha256,
                     media_type=excluded.media_type, parsed_json=excluded.parsed_json""",
                (
                    document.id, document.filename, document.sha256, document.media_type,
                    _dump(document.to_dict()), now,
                ),
            )

    def save_extraction(self, document: ParsedDocument, extraction: Mapping[str, Any]) -> int:
        validate_extraction_schema(extraction)
        if extraction["document_id"] != document.id:
            raise ValueError("extraction document_id가 저장 대상 문서와 다르다")
        self.save_document(document)
        now = _now()
        fingerprint = extraction["model_fingerprint"]
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO extraction_run(
                     document_id, chunk_id, model, prompt_version, temperature, seed,
                     payload_json, created_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document.id, extraction["chunk_id"], fingerprint["model"],
                    fingerprint["prompt_version"], fingerprint["temperature"],
                    fingerprint.get("seed"), _dump(extraction), now,
                ),
            )
            for requirement in extraction["requirements"]:
                self.connection.execute(
                    """INSERT INTO requirement(document_id, id, payload_json, updated_at)
                       VALUES(?, ?, ?, ?)
                       ON CONFLICT(document_id, id) DO UPDATE SET
                         payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                    (document.id, requirement["req_id"], _dump(requirement), now),
                )
            for candidate in extraction["function_candidates"]:
                current = self.connection.execute(
                    "SELECT review_status, excluded, exclusion_reason FROM fp_function WHERE document_id=? AND id=?",
                    (document.id, candidate["candidate_id"]),
                ).fetchone()
                review_status = current["review_status"] if current else (
                    "NEED_REVIEW" if candidate["status"] == "NEED_REVIEW" else "AI_PROPOSED"
                )
                excluded = current["excluded"] if current else int(candidate["status"] == "OUT_OF_SCOPE_CANDIDATE")
                reason = current["exclusion_reason"] if current else (
                    "LLM이 FP 범위 밖 후보로 제안" if excluded else ""
                )
                self.connection.execute(
                    """INSERT INTO fp_function(
                         document_id, id, payload_json, review_status, excluded,
                         exclusion_reason, updated_at
                       ) VALUES(?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(document_id, id) DO UPDATE SET
                         payload_json=excluded.payload_json,
                         review_status=fp_function.review_status,
                         excluded=fp_function.excluded,
                         exclusion_reason=fp_function.exclusion_reason,
                         updated_at=excluded.updated_at""",
                    (
                        document.id, candidate["candidate_id"], _dump(candidate), review_status,
                        excluded, reason, now,
                    ),
                )
        return int(cursor.lastrowid)

    def review_function(
        self,
        document_id: str,
        function_id: str,
        *,
        action: str,
        reviewer: str,
        reason: str,
        changes: Mapping[str, Any] | None = None,
    ) -> None:
        if action not in {"APPROVE", "MODIFY", "EXCLUDE"}:
            raise ValueError("action은 APPROVE/MODIFY/EXCLUDE 중 하나여야 한다")
        if not reviewer.strip() or not reason.strip():
            raise ValueError("reviewer와 reason은 필수다")
        row = self.connection.execute(
            "SELECT * FROM fp_function WHERE document_id=? AND id=?", (document_id, function_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"기능을 찾을 수 없다: {document_id}/{function_id}")

        payload = json.loads(row["payload_json"])
        before = {
            "payload": json.loads(_dump(payload)), "review_status": row["review_status"],
            "excluded": bool(row["excluded"]), "exclusion_reason": row["exclusion_reason"],
        }
        review_status = row["review_status"]
        excluded = bool(row["excluded"])
        exclusion_reason = row["exclusion_reason"]
        if action == "APPROVE":
            review_status = ReviewStatus.APPROVED.value
            excluded = False
            exclusion_reason = ""
        elif action == "EXCLUDE":
            review_status = ReviewStatus.MODIFIED.value
            excluded = True
            exclusion_reason = reason
        else:
            allowed = {"name", "function_type", "counts", "requirement_ids"}
            unknown = set(changes or ()) - allowed
            if unknown:
                raise ValueError(f"수정할 수 없는 필드: {', '.join(sorted(unknown))}")
            if not changes:
                raise ValueError("MODIFY action에는 changes가 필요하다")
            payload.update(changes)
            if not isinstance(payload.get("name"), str) or not payload["name"].strip():
                raise ValueError("기능명은 비어 있을 수 없다")
            try:
                FunctionType(payload.get("function_type"))
            except (TypeError, ValueError) as exc:
                raise ValueError("function_type은 ILF/EIF/EI/EO/EQ 중 하나여야 한다") from exc
            if not isinstance(payload.get("requirement_ids"), list) or not payload["requirement_ids"]:
                raise ValueError("requirement_ids는 한 개 이상이어야 한다")
            for raw_count in payload.get("counts", {}).values():
                _counted(raw_count)
            review_status = ReviewStatus.MODIFIED.value

        after = {
            "payload": payload, "review_status": review_status,
            "excluded": excluded, "exclusion_reason": exclusion_reason,
        }
        now = _now()
        with self.connection:
            self.connection.execute(
                """UPDATE fp_function SET payload_json=?, review_status=?, excluded=?,
                   exclusion_reason=?, updated_at=? WHERE document_id=? AND id=?""",
                (
                    _dump(payload), review_status, int(excluded), exclusion_reason, now,
                    document_id, function_id,
                ),
            )
            self.connection.execute(
                """INSERT INTO review(
                     document_id, function_id, action, reviewer, reason,
                     before_json, after_json, created_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id, function_id, action, reviewer, reason,
                    _dump(before), _dump(after), now,
                ),
            )

    def list_functions(self, document_id: str) -> tuple[FPFunction, ...]:
        rows = self.connection.execute(
            "SELECT * FROM fp_function WHERE document_id=? ORDER BY id", (document_id,),
        ).fetchall()
        functions = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("function_type") is None:
                continue
            counts = payload.get("counts", {})
            functions.append(FPFunction(
                id=payload["candidate_id"], name=payload["name"],
                function_type=FunctionType(payload["function_type"]),
                review_status=ReviewStatus(row["review_status"]),
                det=_counted(counts.get("det")), ret=_counted(counts.get("ret")),
                ftr=_counted(counts.get("ftr")),
                requirement_ids=tuple(payload["requirement_ids"]),
                excluded=bool(row["excluded"]), exclusion_reason=row["exclusion_reason"],
            ))
        return tuple(functions)

    def review_history(self, document_id: str, function_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            """SELECT action, reviewer, reason, before_json, after_json, created_at
               FROM review WHERE document_id=? AND function_id=? ORDER BY id""",
            (document_id, function_id),
        ).fetchall()
        return tuple({
            "action": row["action"], "reviewer": row["reviewer"], "reason": row["reason"],
            "before": json.loads(row["before_json"]), "after": json.loads(row["after_json"]),
            "created_at": row["created_at"],
        } for row in rows)
