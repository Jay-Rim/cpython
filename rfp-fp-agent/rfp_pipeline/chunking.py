"""원본 블록 경계를 보존하는 결정적 청킹."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import DocumentBlock, ParsedDocument


@dataclass(frozen=True)
class ExtractionChunk:
    id: str
    document_id: str
    blocks: tuple[DocumentBlock, ...]
    text: str

    def to_llm_payload(self) -> dict:
        return {
            "document_id": self.document_id,
            "chunk_id": self.id,
            "blocks": [block.to_dict() for block in self.blocks],
        }


def build_chunks(document: ParsedDocument, *, max_chars: int = 24_000) -> tuple[ExtractionChunk, ...]:
    if max_chars < 500:
        raise ValueError("max_chars must be at least 500")
    groups: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    current_size = 0
    for block in document.blocks:
        size = len(block.text) + 2
        if current and current_size + size > max_chars:
            groups.append(current)
            current = []
            current_size = 0
        current.append(block)
        current_size += size
    if current:
        groups.append(current)

    chunks = []
    for number, group in enumerate(groups, 1):
        text = "\n\n".join(f"[{block.id}] {block.text}" for block in group)
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        chunks.append(ExtractionChunk(
            id=f"CHUNK-{number:04d}-{fingerprint}", document_id=document.id,
            blocks=tuple(group), text=text,
        ))
    return tuple(chunks)

