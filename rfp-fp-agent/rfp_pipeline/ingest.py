"""구조화된 LLM 결과를 검증하고 FP Rule Engine 입력으로 변환한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from fp_engine import Certainty, Counted, FPFunction, FunctionType, ReviewStatus

from .evidence import EvidenceIssue, verify_evidence
from .models import ParsedDocument


class ExtractionSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class IngestionResult:
    functions: tuple[FPFunction, ...]
    evidence_issues: tuple[EvidenceIssue, ...]
    skipped_candidate_ids: tuple[str, ...]


def load_extraction_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "schemas" / "llm_extraction.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate_extraction_schema(extraction: Mapping[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise RuntimeError("구조화 출력 검증을 위해 jsonschema를 설치해야 한다") from exc
    errors = sorted(Draft202012Validator(load_extraction_schema()).iter_errors(extraction), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors[:10])
        raise ExtractionSchemaError(f"LLM 구조화 출력이 스키마에 맞지 않는다: {detail}")


def _counted(raw: Mapping[str, Any] | None) -> Counted:
    if not raw:
        return Counted(None, Certainty.UNKNOWN, "미확인")
    return Counted(
        raw.get("value"), Certainty(raw["certainty"]), raw.get("rationale", ""),
    )


def ingest_extraction(
    extraction: Mapping[str, Any], document: ParsedDocument, *, reject_bad_evidence: bool = True,
) -> IngestionResult:
    validate_extraction_schema(extraction)
    issues = verify_evidence(extraction, document)
    reject_all = any(issue.code == "DOCUMENT_MISMATCH" for issue in issues)
    requirement_ids = {requirement["req_id"] for requirement in extraction["requirements"]}
    bad_ids = {issue.owner_id for issue in issues}
    bad_requirement_ids = bad_ids & requirement_ids
    functions: list[FPFunction] = []
    skipped: list[str] = []
    for candidate in extraction["function_candidates"]:
        candidate_id = candidate["candidate_id"]
        function_type = candidate["function_type"]
        status = candidate["status"]
        if function_type is None or status == "INSUFFICIENT_INFO":
            skipped.append(candidate_id)
            continue
        if reject_bad_evidence and (
            reject_all
            or candidate_id in bad_ids
            or bad_requirement_ids.intersection(candidate["requirement_ids"])
        ):
            skipped.append(candidate_id)
            continue
        excluded = status == "OUT_OF_SCOPE_CANDIDATE"
        counts = candidate.get("counts", {})
        functions.append(FPFunction(
            id=candidate_id,
            name=candidate["name"],
            function_type=FunctionType(function_type),
            review_status=(
                ReviewStatus.NEED_REVIEW if status == "NEED_REVIEW"
                else ReviewStatus.AI_PROPOSED
            ),
            det=_counted(counts.get("det")),
            ret=_counted(counts.get("ret")),
            ftr=_counted(counts.get("ftr")),
            requirement_ids=tuple(candidate["requirement_ids"]),
            excluded=excluded,
            exclusion_reason="LLM이 FP 범위 밖 후보로 제안" if excluded else "",
        ))
    return IngestionResult(tuple(functions), issues, tuple(skipped))
