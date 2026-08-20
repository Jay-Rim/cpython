"""LLM 인용이 실제 입력 문서에 있는지 검증한다."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models import DocumentBlock, ParsedDocument


@dataclass(frozen=True)
class EvidenceIssue:
    code: str
    owner_id: str
    message: str


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip()


def _candidate_blocks(source: Mapping[str, Any], document: ParsedDocument) -> Iterable[DocumentBlock]:
    block_id = source.get("block_id")
    if block_id:
        block = document.block_index().get(str(block_id))
        return (block,) if block else ()
    page = source.get("page")
    return tuple(block for block in document.blocks if block.location.ordinal == page)


def _verify_source(source: Mapping[str, Any], owner_id: str, document: ParsedDocument) -> EvidenceIssue | None:
    quote = source.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return EvidenceIssue("EMPTY_QUOTE", owner_id, "인용문이 비어 있다")
    blocks = tuple(_candidate_blocks(source, document))
    if not blocks:
        return EvidenceIssue("SOURCE_NOT_FOUND", owner_id, "block_id/page에 해당하는 원문 블록이 없다")
    needle = _normalized(quote)
    if not any(needle in _normalized(block.text) for block in blocks):
        return EvidenceIssue("QUOTE_MISMATCH", owner_id, f"원문에서 인용문을 찾지 못했다: {quote!r}")
    return None


def verify_evidence(extraction: Mapping[str, Any], document: ParsedDocument) -> tuple[EvidenceIssue, ...]:
    issues: list[EvidenceIssue] = []
    if extraction.get("document_id") != document.id:
        issues.append(EvidenceIssue(
            "DOCUMENT_MISMATCH", str(extraction.get("document_id")),
            f"결과 document_id가 입력 문서 {document.id}와 다르다",
        ))
        return tuple(issues)
    for requirement in extraction.get("requirements", []):
        source = requirement.get("source", {})
        owner = requirement.get("req_id", "?")
        issue = _verify_source(source, owner, document)
        if issue:
            issues.append(issue)
            continue
        verbatim = _normalized(str(requirement.get("verbatim", "")))
        if not verbatim or not any(
            verbatim in _normalized(block.text)
            for block in _candidate_blocks(source, document)
        ):
            issues.append(EvidenceIssue(
                "VERBATIM_MISMATCH", owner,
                "요구사항 verbatim이 지정된 원문 블록에 문자 그대로 존재하지 않는다",
            ))
    for candidate in extraction.get("function_candidates", []):
        owner = candidate.get("candidate_id", "?")
        for source in candidate.get("evidence", []):
            issue = _verify_source(source, owner, document)
            if issue:
                issues.append(issue)
    return tuple(issues)
