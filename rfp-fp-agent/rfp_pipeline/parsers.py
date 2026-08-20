"""PDF와 Office Open XML 문서를 공통 블록 모델로 변환한다.

파서는 LLM을 호출하지 않는다. 스캔 PDF나 도식처럼 텍스트를 신뢰할 수 없는
입력은 추측하지 않고 warning으로 명시한다.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from .models import (
    DocumentBlock, ParsedDocument, SourceKind, SourceLocation, document_id,
)


class DocumentParseError(RuntimeError):
    pass


class UnsupportedFormatError(DocumentParseError):
    pass


_SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx"}
_LEGACY = {".doc", ".ppt", ".xls"}
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def parse_document(
    path: str | Path,
    *,
    max_file_bytes: int = 100 * 1024 * 1024,
    max_uncompressed_bytes: int = 500 * 1024 * 1024,
) -> ParsedDocument:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size > max_file_bytes:
        raise DocumentParseError(
            f"입력 파일이 허용 크기({max_file_bytes} bytes)를 초과한다: {source.stat().st_size} bytes"
        )
    suffix = source.suffix.lower()
    if suffix in _LEGACY:
        raise UnsupportedFormatError(
            f"구형 Office 형식 {suffix}은 직접 처리하지 않는다. "
            f"LibreOffice로 {suffix}x 형식으로 변환한 뒤 입력해야 한다."
        )
    if suffix not in _SUPPORTED:
        raise UnsupportedFormatError(
            f"지원하지 않는 형식: {suffix or '(확장자 없음)'}. "
            f"지원 형식: {', '.join(sorted(_SUPPORTED))}"
        )

    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    doc_id = document_id(source, digest)
    try:
        if suffix == ".pdf":
            blocks, warnings = _parse_pdf(source)
        elif suffix == ".docx":
            _check_zip_limits(source, max_uncompressed_bytes)
            blocks, warnings = _parse_docx(source)
        elif suffix == ".pptx":
            _check_zip_limits(source, max_uncompressed_bytes)
            blocks, warnings = _parse_pptx(source)
        else:
            _check_zip_limits(source, max_uncompressed_bytes)
            blocks, warnings = _parse_xlsx(source)
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        raise DocumentParseError(f"손상되었거나 지원하지 않는 {suffix} 문서: {source.name}") from exc

    if not blocks:
        warnings.append("추출된 텍스트가 없다. 스캔/OCR 또는 이미지·도식 분석이 필요하다.")
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return ParsedDocument(
        id=doc_id,
        filename=source.name,
        sha256=digest,
        media_type=media_type,
        blocks=tuple(blocks),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _check_zip_limits(path: Path, max_uncompressed_bytes: int) -> None:
    """OOXML zip bomb과 비정상적으로 많은 member를 파싱 전에 거부한다."""
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > 20_000:
            raise DocumentParseError(f"OOXML 내부 파일 수가 비정상적으로 많다: {len(infos)}")
        total = sum(info.file_size for info in infos)
        if total > max_uncompressed_bytes:
            raise DocumentParseError(
                f"OOXML 압축 해제 크기가 허용치({max_uncompressed_bytes} bytes)를 초과한다: {total} bytes"
            )


def _block_id(prefix: str, ordinal: int, item: int) -> str:
    return f"{prefix}-{ordinal:04d}-block-{item:04d}"


def _clean(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _parse_pdf(path: Path) -> tuple[list[DocumentBlock], list[str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentParseError("PDF 처리를 위해 pypdf를 설치해야 한다") from exc

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:  # library-specific crypto errors
            raise DocumentParseError("암호화된 PDF를 열 수 없다") from exc
        if not unlocked:
            raise DocumentParseError("암호화된 PDF를 열 수 없다")

    blocks: list[DocumentBlock] = []
    warnings: list[str] = []
    order = 0
    for page_number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        paragraphs = [_clean(part) for part in re.split(r"\n\s*\n", text) if _clean(part)]
        if not paragraphs and _clean(text):
            paragraphs = [_clean(text)]
        if not paragraphs:
            warnings.append(f"PDF {page_number}쪽에서 텍스트를 추출하지 못했다")
            continue
        for item, paragraph in enumerate(paragraphs, 1):
            order += 1
            blocks.append(DocumentBlock(
                id=_block_id("page", page_number, item), order=order,
                kind="paragraph", text=paragraph,
                location=SourceLocation(SourceKind.PDF_PAGE, page_number, f"{page_number}쪽"),
            ))
    if blocks and sum(len(b.text) for b in blocks) < max(100, len(reader.pages) * 20):
        warnings.append("PDF 텍스트 추출량이 매우 적다. OCR 검토가 필요하다")
    return blocks, warnings


def _xml(zip_file: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zip_file.read(name))


def _text_from(element: ET.Element, tag: str) -> str:
    return _clean("".join(node.text or "" for node in element.iter(tag)))


def _parse_docx(path: Path) -> tuple[list[DocumentBlock], list[str]]:
    blocks: list[DocumentBlock] = []
    warnings: list[str] = []
    with zipfile.ZipFile(path) as zf:
        root = _xml(zf, "word/document.xml")
        body = root.find(f"{{{_W_NS}}}body")
        if body is None:
            return blocks, ["DOCX 본문을 찾지 못했다"]
        current_section: str | None = None
        for child in body:
            local = child.tag.rsplit("}", 1)[-1]
            if local == "p":
                text = _text_from(child, f"{{{_W_NS}}}t")
                if not text:
                    continue
                style_node = child.find(f"{{{_W_NS}}}pPr/{{{_W_NS}}}pStyle")
                style = style_node.get(f"{{{_W_NS}}}val", "") if style_node is not None else ""
                kind = "heading" if style.lower().startswith(("heading", "title")) else "paragraph"
                if kind == "heading":
                    current_section = text
                order = len(blocks) + 1
                blocks.append(DocumentBlock(
                    id=_block_id("docx", 1, order), order=order, kind=kind, text=text,
                    location=SourceLocation(
                        SourceKind.DOCX_SECTION, order, f"문서 블록 {order}", current_section,
                    ),
                ))
            elif local == "tbl":
                rows = []
                for row in child.findall(f"{{{_W_NS}}}tr"):
                    cells = [_text_from(cell, f"{{{_W_NS}}}t") for cell in row.findall(f"{{{_W_NS}}}tc")]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    order = len(blocks) + 1
                    blocks.append(DocumentBlock(
                        id=_block_id("docx", 1, order), order=order, kind="table",
                        text="\n".join(rows),
                        location=SourceLocation(
                            SourceKind.DOCX_SECTION, order, f"문서 표 {order}", current_section,
                        ),
                    ))
    return blocks, warnings


def _numbered_members(zf: zipfile.ZipFile, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    found = []
    for name in zf.namelist():
        match = pattern.fullmatch(name)
        if match:
            found.append((int(match.group(1)), name))
    return sorted(found)


def _parse_pptx(path: Path) -> tuple[list[DocumentBlock], list[str]]:
    blocks: list[DocumentBlock] = []
    warnings: list[str] = []
    pattern = re.compile(r"ppt/slides/slide(\d+)\.xml")
    with zipfile.ZipFile(path) as zf:
        slides = _numbered_members(zf, pattern)
        for slide_number, member in slides:
            root = _xml(zf, member)
            item = 0
            for paragraph in root.iter(f"{{{_A_NS}}}p"):
                text = _text_from(paragraph, f"{{{_A_NS}}}t")
                if not text:
                    continue
                item += 1
                blocks.append(DocumentBlock(
                    id=_block_id("slide", slide_number, item), order=len(blocks) + 1,
                    kind="text", text=text,
                    location=SourceLocation(
                        SourceKind.PPTX_SLIDE, slide_number, f"슬라이드 {slide_number}",
                    ),
                ))
            notes_name = f"ppt/notesSlides/notesSlide{slide_number}.xml"
            if notes_name in zf.namelist():
                notes = _text_from(_xml(zf, notes_name), f"{{{_A_NS}}}t")
                if notes:
                    item += 1
                    blocks.append(DocumentBlock(
                        id=_block_id("slide", slide_number, item), order=len(blocks) + 1,
                        kind="speaker_notes", text=notes,
                        location=SourceLocation(
                            SourceKind.PPTX_SLIDE, slide_number, f"슬라이드 {slide_number} 노트",
                        ),
                    ))
            if item == 0:
                warnings.append(f"PPTX 슬라이드 {slide_number}에 추출 가능한 텍스트가 없다. 도식/이미지 검토가 필요하다")
    return blocks, warnings


def _relationships(zf: zipfile.ZipFile, name: str) -> dict[str, str]:
    root = _xml(zf, name)
    return {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in root.findall(f"{{{_REL_NS}}}Relationship")
    }


def _xlsx_cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    formula = cell.findtext(f"{{{_S_NS}}}f")
    value = cell.findtext(f"{{{_S_NS}}}v")
    if cell_type == "inlineStr":
        value = _text_from(cell, f"{{{_S_NS}}}t")
    elif cell_type == "s" and value is not None:
        try:
            value = shared[int(value)]
        except (ValueError, IndexError):
            value = f"[잘못된 shared string: {value}]"
    elif cell_type == "b" and value is not None:
        value = "TRUE" if value == "1" else "FALSE"
    if formula:
        return f"수식={formula}; 값={value or ''}"
    return _clean(value or "")


def _parse_xlsx(path: Path) -> tuple[list[DocumentBlock], list[str]]:
    blocks: list[DocumentBlock] = []
    warnings: list[str] = []
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared_root = _xml(zf, "xl/sharedStrings.xml")
            shared = [_text_from(si, f"{{{_S_NS}}}t") for si in shared_root.findall(f"{{{_S_NS}}}si")]

        workbook = _xml(zf, "xl/workbook.xml")
        rels = _relationships(zf, "xl/_rels/workbook.xml.rels")
        sheets = workbook.find(f"{{{_S_NS}}}sheets")
        if sheets is None:
            return blocks, ["XLSX 시트를 찾지 못했다"]
        for sheet_ordinal, sheet in enumerate(sheets, 1):
            sheet_name = sheet.attrib.get("name", f"Sheet{sheet_ordinal}")
            rel_id = sheet.attrib.get(f"{{{_OFFICE_REL_NS}}}id")
            target = rels.get(rel_id or "")
            if not target:
                warnings.append(f"XLSX 시트 '{sheet_name}'의 XML 관계를 찾지 못했다")
                continue
            member = str(PurePosixPath("xl") / target).replace("xl/../", "")
            if member not in zf.namelist():
                member = target.lstrip("/")
            root = _xml(zf, member)
            row_item = 0
            for row in root.iter(f"{{{_S_NS}}}row"):
                values: list[tuple[str, str]] = []
                for cell in row.findall(f"{{{_S_NS}}}c"):
                    ref = cell.attrib.get("r", "?")
                    value = _xlsx_cell_value(cell, shared)
                    if value:
                        values.append((ref, value))
                if not values:
                    continue
                row_item += 1
                cell_range = values[0][0] if len(values) == 1 else f"{values[0][0]}:{values[-1][0]}"
                text = " | ".join(f"{ref}={value}" for ref, value in values)
                blocks.append(DocumentBlock(
                    id=_block_id("sheet", sheet_ordinal, row_item), order=len(blocks) + 1,
                    kind="row", text=text,
                    location=SourceLocation(
                        SourceKind.XLSX_SHEET, sheet_ordinal, sheet_name,
                        section=sheet_name, cell_range=cell_range,
                    ),
                ))
    return blocks, warnings
