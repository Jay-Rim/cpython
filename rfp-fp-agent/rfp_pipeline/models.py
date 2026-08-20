"""문서 형식과 무관한 추적성 중심 중간 표현."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SourceKind(str, Enum):
    PDF_PAGE = "PDF_PAGE"
    DOCX_SECTION = "DOCX_SECTION"
    PPTX_SLIDE = "PPTX_SLIDE"
    XLSX_SHEET = "XLSX_SHEET"


@dataclass(frozen=True)
class SourceLocation:
    """원본에서 블록을 다시 찾기 위한 위치.

    ordinal은 모든 형식에서 1부터 시작한다. PDF는 페이지, PPTX는 슬라이드,
    XLSX는 시트 순번, DOCX는 문서 내 블록 순번이다.
    """

    kind: SourceKind
    ordinal: int
    label: str
    section: str | None = None
    cell_range: str | None = None
    bbox: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 1:
            raise ValueError("source ordinal must start at 1")


@dataclass(frozen=True)
class DocumentBlock:
    id: str
    order: int
    kind: str
    text: str
    location: SourceLocation
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("block id is required")
        if self.order < 1:
            raise ValueError("block order must start at 1")
        if not self.text.strip():
            raise ValueError("empty document blocks are not allowed")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["location"]["kind"] = self.location.kind.value
        return result


@dataclass(frozen=True)
class ParsedDocument:
    id: str
    filename: str
    sha256: str
    media_type: str
    blocks: tuple[DocumentBlock, ...]
    warnings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)

    def block_index(self) -> dict[str, DocumentBlock]:
        return {block.id: block for block in self.blocks}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "warnings": list(self.warnings),
            "blocks": [block.to_dict() for block in self.blocks],
        }


def document_id(path: Path, digest: str) -> str:
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "-" for c in path.stem)
    return f"DOC-{safe_stem[:40]}-{digest[:12]}"

