"""문서 파싱 → 근거 검증 → Rule Engine 입력 변환 통합 테스트."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fp_engine import FunctionType, Method, calculate  # noqa: E402
from rfp_pipeline import Ledger, analyze_document, build_chunks, ingest_extraction, parse_document  # noqa: E402
from rfp_pipeline.ingest import ExtractionSchemaError  # noqa: E402
from rfp_pipeline.parsers import UnsupportedFormatError  # noqa: E402


def _write_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def test_docx_preserves_paragraph_table_order_and_section(tmp_path):
    path = tmp_path / "요구사항.docx"
    _write_zip(path, {"word/document.xml": """
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>계약 관리</w:t></w:r></w:p>
            <w:p><w:r><w:t>고객은 계약을 등록한다.</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>REQ-01</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>계약내역 조회</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
          </w:body>
        </w:document>
    """})
    doc = parse_document(path)
    assert [b.kind for b in doc.blocks] == ["heading", "paragraph", "table"]
    assert doc.blocks[1].location.section == "계약 관리"
    assert "REQ-01 | 계약내역 조회" == doc.blocks[2].text


def test_pptx_preserves_slide_and_speaker_notes(tmp_path):
    path = tmp_path / "제안요청.pptx"
    _write_zip(path, {
        "ppt/slides/slide1.xml": """
          <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
            <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>계약 조회 기능</a:t></a:r></a:p>
            </p:txBody></p:sp></p:spTree></p:cSld></p:sld>
        """,
        "ppt/notesSlides/notesSlide1.xml": """
          <p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
            <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>사용자별 계약 목록</a:t></a:r></a:p>
            </p:txBody></p:sp></p:spTree></p:cSld></p:notes>
        """,
    })
    doc = parse_document(path)
    assert [b.text for b in doc.blocks] == ["계약 조회 기능", "사용자별 계약 목록"]
    assert all(b.location.ordinal == 1 for b in doc.blocks)
    assert doc.blocks[1].kind == "speaker_notes"


def test_xlsx_preserves_sheet_cell_range_and_formula(tmp_path):
    path = tmp_path / "요구목록.xlsx"
    _write_zip(path, {
        "xl/workbook.xml": """
          <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
            <sheets><sheet name="기능요구" sheetId="1" r:id="rId1"/></sheets></workbook>
        """,
        "xl/_rels/workbook.xml.rels": """
          <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Target="worksheets/sheet1.xml"
              Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
          </Relationships>
        """,
        "xl/worksheets/sheet1.xml": """
          <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
            <row r="1"><c r="A1" t="inlineStr"><is><t>REQ-01</t></is></c>
              <c r="B1" t="inlineStr"><is><t>계약조회</t></is></c></row>
            <row r="2"><c r="C2"><f>1+1</f><v>2</v></c></row>
          </sheetData></worksheet>
        """,
    })
    doc = parse_document(path)
    assert doc.blocks[0].location.label == "기능요구"
    assert doc.blocks[0].location.cell_range == "A1:B1"
    assert doc.blocks[0].text == "A1=REQ-01 | B1=계약조회"
    assert doc.blocks[1].text == "C2=수식=1+1; 값=2"


def test_pdf_preserves_page_number(tmp_path):
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    path = tmp_path / "rfp.pdf"
    canvas = reportlab.Canvas(str(path))
    canvas.drawString(72, 720, "Contract inquiry requirement")
    canvas.showPage()
    canvas.drawString(72, 720, "Second page")
    canvas.save()
    doc = parse_document(path)
    assert [b.location.ordinal for b in doc.blocks] == [1, 2]
    assert "Contract inquiry" in doc.blocks[0].text


def test_chunking_is_deterministic_and_keeps_block_ids(tmp_path):
    path = tmp_path / "a.docx"
    _write_zip(path, {"word/document.xml": """
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>고객 계약 조회 요구사항</w:t></w:r></w:p></w:body></w:document>"""})
    doc = parse_document(path)
    first = build_chunks(doc, max_chars=500)
    second = build_chunks(doc, max_chars=500)
    assert first == second
    assert doc.blocks[0].id in first[0].text
    assert first[0].to_llm_payload()["blocks"][0]["location"]["kind"] == "DOCX_SECTION"


def _extraction(doc, quote="계약을 등록한다."):
    block = doc.blocks[1]
    source = {"block_id": block.id, "page": block.location.ordinal, "quote": quote}
    return {
        "document_id": doc.id,
        "chunk_id": "CHUNK-0001-test",
        "model_fingerprint": {"model": "company-standard", "prompt_version": "v1", "temperature": 0},
        "requirements": [{
            "req_id": "REQ-001", "title": "계약 등록", "verbatim": quote,
            "requirement_class": "FUNCTIONAL", "source": source,
        }],
        "function_candidates": [{
            "candidate_id": "FC-001", "requirement_ids": ["REQ-001"], "name": "계약 등록",
            "function_type": "EI", "classification_rationale": "계약정보를 내부에 등록하므로 EI",
            "unit_process_check": {
                "is_self_contained": True, "is_meaningful_to_user": True,
                "leaves_business_consistent": True,
            },
            "evidence": [source], "confidence": 0.8, "status": "AI_PROPOSED",
        }],
    }


def test_ingestion_verifies_quote_and_feeds_rule_engine(tmp_path):
    path = tmp_path / "요구사항.docx"
    _write_zip(path, {"word/document.xml": """
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>계약 관리</w:t></w:r></w:p>
      <w:p><w:r><w:t>고객은 계약을 등록한다.</w:t></w:r></w:p></w:body></w:document>"""})
    doc = parse_document(path)
    ingested = ingest_extraction(_extraction(doc), doc)
    assert not ingested.evidence_issues
    assert ingested.functions[0].function_type is FunctionType.EI
    result = calculate(ingested.functions, Method.SIMPLE)
    assert result.total_fp == 4.0
    assert result.confirmed_fp == 0.0  # 사람 승인 전이므로 잠정 FP


def test_bad_evidence_is_reported_and_candidate_is_not_sized(tmp_path):
    path = tmp_path / "요구사항.docx"
    _write_zip(path, {"word/document.xml": """
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>계약 관리</w:t></w:r></w:p>
      <w:p><w:r><w:t>고객은 계약을 등록한다.</w:t></w:r></w:p></w:body></w:document>"""})
    doc = parse_document(path)
    extraction = _extraction(doc, quote="문서에 존재하지 않는 기능")
    ingested = ingest_extraction(extraction, doc)
    assert {issue.code for issue in ingested.evidence_issues} == {"QUOTE_MISMATCH"}
    assert ingested.functions == ()
    assert ingested.skipped_candidate_ids == ("FC-001",)


def test_requirement_verbatim_must_exist_even_when_short_quote_is_valid(tmp_path):
    path = tmp_path / "요구사항.docx"
    _write_zip(path, {"word/document.xml": """
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>계약 관리</w:t></w:r></w:p>
      <w:p><w:r><w:t>고객은 계약을 등록한다.</w:t></w:r></w:p></w:body></w:document>"""})
    doc = parse_document(path)
    extraction = _extraction(doc)
    extraction["requirements"][0]["verbatim"] = "고객은 계약을 등록하고 자동 승인한다."
    ingested = ingest_extraction(extraction, doc)
    assert {issue.code for issue in ingested.evidence_issues} == {"VERBATIM_MISMATCH"}
    assert ingested.functions == ()


def test_schema_error_is_loud(tmp_path):
    path = tmp_path / "요구사항.docx"
    _write_zip(path, {"word/document.xml": """
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>내용</w:t></w:r></w:p></w:body></w:document>"""})
    doc = parse_document(path)
    with pytest.raises(ExtractionSchemaError):
        ingest_extraction({"document_id": doc.id}, doc)


def test_legacy_office_format_is_rejected_with_conversion_guidance(tmp_path):
    path = tmp_path / "old.ppt"
    path.write_bytes(b"legacy")
    with pytest.raises(UnsupportedFormatError, match="LibreOffice"):
        parse_document(path)


def test_company_llm_adapter_contract_runs_end_to_end(tmp_path):
    path = tmp_path / "요구사항.docx"
    _write_zip(path, {"word/document.xml": """
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>고객은 계약내역을 조회한다.</w:t></w:r></w:p></w:body></w:document>"""})

    class CompanyStandardExtractor:
        def extract(self, chunk, *, json_schema):
            assert json_schema["title"].startswith("RFP FP Agent")
            block = chunk.blocks[0]
            source = {"block_id": block.id, "page": 1, "quote": "계약내역을 조회한다"}
            return {
                "document_id": chunk.document_id,
                "chunk_id": chunk.id,
                "model_fingerprint": {
                    "model": "company-standard", "prompt_version": "v1", "temperature": 0,
                },
                "requirements": [{
                    "req_id": "REQ-001", "title": "계약내역 조회",
                    "verbatim": "고객은 계약내역을 조회한다.",
                    "requirement_class": "FUNCTIONAL", "source": source,
                }],
                "function_candidates": [{
                    "candidate_id": "FC-001", "requirement_ids": ["REQ-001"],
                    "name": "계약내역 조회", "function_type": "EQ",
                    "classification_rationale": "파생 데이터 없이 계약을 조회하므로 EQ",
                    "unit_process_check": {
                        "is_self_contained": True, "is_meaningful_to_user": True,
                        "leaves_business_consistent": True,
                    },
                    "evidence": [source], "confidence": 0.8, "status": "AI_PROPOSED",
                }],
            }

    result = analyze_document(path, CompanyStandardExtractor())
    assert len(result.functions) == 1
    assert result.functions[0].function_type is FunctionType.EQ
    assert not result.evidence_issues


def test_ledger_preserves_review_history_and_approved_fp(tmp_path):
    path = tmp_path / "요구사항.docx"
    _write_zip(path, {"word/document.xml": """
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>계약 관리</w:t></w:r></w:p>
      <w:p><w:r><w:t>고객은 계약을 등록한다.</w:t></w:r></w:p></w:body></w:document>"""})
    doc = parse_document(path)
    extraction = _extraction(doc)

    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        run_id = ledger.save_extraction(doc, extraction)
        assert run_id == 1
        before = calculate(ledger.list_functions(doc.id), Method.SIMPLE)
        assert before.confirmed_fp == 0

        ledger.review_function(
            doc.id, "FC-001", action="MODIFY", reviewer="fp-expert",
            reason="실제 기능은 계약 변경", changes={"name": "계약 변경", "function_type": "EI"},
        )
        ledger.review_function(
            doc.id, "FC-001", action="APPROVE", reviewer="fp-expert",
            reason="원문과 기능 경계 확인 완료",
        )
        functions = ledger.list_functions(doc.id)
        assert functions[0].name == "계약 변경"
        assert functions[0].review_status.value == "APPROVED"
        assert calculate(functions, Method.SIMPLE).confirmed_fp == 4.0
        history = ledger.review_history(doc.id, "FC-001")
        assert [item["action"] for item in history] == ["MODIFY", "APPROVE"]
        assert history[0]["before"]["payload"]["name"] == "계약 등록"


def test_ledger_exclusion_is_traceable_and_not_counted(tmp_path):
    path = tmp_path / "요구사항.docx"
    _write_zip(path, {"word/document.xml": """
      <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>계약 관리</w:t></w:r></w:p>
      <w:p><w:r><w:t>고객은 계약을 등록한다.</w:t></w:r></w:p></w:body></w:document>"""})
    doc = parse_document(path)
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        ledger.save_extraction(doc, _extraction(doc))
        ledger.review_function(
            doc.id, "FC-001", action="EXCLUDE", reviewer="fp-expert",
            reason="단순 기술 제약으로 FP 대상 아님",
        )
        functions = ledger.list_functions(doc.id)
        assert functions[0].excluded
        result = calculate(functions, Method.SIMPLE)
        assert result.total_fp == 0
        assert result.excluded_function_ids == ("FC-001",)


def test_streamlit_review_ui_starts_without_external_services():
    testing = pytest.importorskip("streamlit.testing.v1")
    app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    app = testing.AppTest.from_file(str(app_path), default_timeout=10).run()
    assert not app.exception
    assert app.title[0].value == "RFP FP Estimation Agent"
