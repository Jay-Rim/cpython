"""사내 표준 LLM 코드가 구현할 최소 계약."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .chunking import ExtractionChunk


class LLMExtractor(Protocol):
    """네트워크·인증·재시도·모델 선택은 구현체 책임이다."""

    def extract(self, chunk: ExtractionChunk, *, json_schema: Mapping[str, Any]) -> Mapping[str, Any]:
        """스키마에 맞는 요구사항/FP 후보 JSON을 반환한다."""
        ...

