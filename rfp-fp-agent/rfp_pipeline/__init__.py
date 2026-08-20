"""RFP 문서 파싱부터 FP Rule Engine 입력 변환까지의 애플리케이션 계층.

LLM 호출 코드는 의도적으로 포함하지 않는다. 사내 표준 LLM 코드는
``LLMExtractor`` 계약을 구현하거나 ``ingest_extraction`` 에 구조화 JSON을 넘긴다.
"""

from .chunking import ExtractionChunk, build_chunks
from .contracts import LLMExtractor
from .evidence import EvidenceIssue, verify_evidence
from .ingest import IngestionResult, ingest_extraction
from .ledger import Ledger
from .models import DocumentBlock, ParsedDocument, SourceKind, SourceLocation
from .parsers import DocumentParseError, UnsupportedFormatError, parse_document
from .pipeline import PipelineResult, analyze_document

__all__ = [
    "DocumentBlock", "DocumentParseError", "EvidenceIssue", "ExtractionChunk",
    "IngestionResult", "LLMExtractor", "Ledger", "ParsedDocument", "PipelineResult", "SourceKind",
    "SourceLocation", "UnsupportedFormatError", "build_chunks",
    "analyze_document", "ingest_extraction", "parse_document", "verify_evidence",
]
