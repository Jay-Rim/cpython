"""사내 LLM 구현체를 끼워 넣는 end-to-end 오케스트레이션."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from fp_engine import FPFunction

from .chunking import ExtractionChunk, build_chunks
from .contracts import LLMExtractor
from .evidence import EvidenceIssue
from .ingest import ingest_extraction, load_extraction_schema
from .models import ParsedDocument
from .parsers import parse_document


@dataclass(frozen=True)
class PipelineResult:
    document: ParsedDocument
    chunks: tuple[ExtractionChunk, ...]
    raw_extractions: tuple[Mapping[str, Any], ...]
    functions: tuple[FPFunction, ...]
    evidence_issues: tuple[EvidenceIssue, ...]
    skipped_candidate_ids: tuple[str, ...]


def analyze_document(
    path: str | Path,
    extractor: LLMExtractor,
    *,
    max_chunk_chars: int = 24_000,
) -> PipelineResult:
    """문서를 파싱하고 사내 LLM 추출기를 호출한 뒤 검증된 FP 후보를 반환한다.

    계산은 하지 않는다. 호출자는 사람이 승인한 뒤 ``fp_engine.calculate``를
    사용해야 한다. 청크/문서 ID가 다르거나 후보 ID가 중복되면 조용히 합치지
    않고 실패한다.
    """

    document = parse_document(path)
    chunks = build_chunks(document, max_chars=max_chunk_chars)
    schema = load_extraction_schema()
    raw_results: list[Mapping[str, Any]] = []
    functions: list[FPFunction] = []
    issues: list[EvidenceIssue] = []
    skipped: list[str] = []
    seen_function_ids: set[str] = set()

    for chunk in chunks:
        raw = extractor.extract(chunk, json_schema=schema)
        # ingest_extraction이 스키마를 검증한다. 여기서는 청크 결합 계약만 확인한다.
        ingested = ingest_extraction(raw, document)
        if raw["document_id"] != document.id:
            raise ValueError(
                f"LLM 결과 document_id 불일치: {raw['document_id']!r} != {document.id!r}"
            )
        if raw["chunk_id"] != chunk.id:
            raise ValueError(f"LLM 결과 chunk_id 불일치: {raw['chunk_id']!r} != {chunk.id!r}")
        for function in ingested.functions:
            if function.id in seen_function_ids:
                raise ValueError(f"청크 간 candidate_id 중복: {function.id}")
            seen_function_ids.add(function.id)
            functions.append(function)
        raw_results.append(raw)
        issues.extend(ingested.evidence_issues)
        skipped.extend(ingested.skipped_candidate_ids)

    return PipelineResult(
        document=document,
        chunks=chunks,
        raw_extractions=tuple(raw_results),
        functions=tuple(functions),
        evidence_issues=tuple(issues),
        skipped_candidate_ids=tuple(skipped),
    )
