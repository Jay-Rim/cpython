"""RFP FP Agent 최소 검토 UI.

실행: streamlit run streamlit_app.py
LLM 호출은 포함하지 않는다. 사내 표준 코드가 만든 JSON을 업로드하거나
``rfp_pipeline.analyze_document``를 별도 서비스에서 호출한다.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import streamlit as st

from fp_engine import Method, calculate, validate
from rfp_pipeline import Ledger, ingest_extraction, parse_document


st.set_page_config(page_title="RFP FP Estimation Agent", layout="wide")
st.title("RFP FP Estimation Agent")
st.caption("문서 파싱 → 사내 LLM 구조화 JSON → 근거 검증 → 전문가 검토 → 결정적 FP 계산")


def _ledger_path() -> Path:
    configured = os.environ.get("RFP_FP_LEDGER")
    path = Path(configured) if configured else Path("data/fp-ledger.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _parse_upload(uploaded):
    suffix = Path(uploaded.name).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(uploaded.getvalue())
        temporary = Path(handle.name)
    try:
        return parse_document(temporary)
    finally:
        temporary.unlink(missing_ok=True)


with st.sidebar:
    st.header("1. 문서 입력")
    uploaded_document = st.file_uploader(
        "RFP 문서", type=["pdf", "docx", "pptx", "xlsx"],
    )
    if st.button("문서 파싱", disabled=uploaded_document is None, use_container_width=True):
        try:
            st.session_state.document = _parse_upload(uploaded_document)
            st.session_state.pop("extraction", None)
            st.success("문서 파싱 완료")
        except Exception as exc:
            st.exception(exc)

    st.header("2. 사내 LLM 결과")
    uploaded_json = st.file_uploader("Structured Output JSON", type=["json"])
    if st.button("JSON 검증·저장", disabled=uploaded_json is None, use_container_width=True):
        document = st.session_state.get("document")
        if document is None:
            st.error("RFP 문서를 먼저 파싱해야 합니다.")
        else:
            try:
                extraction = json.loads(uploaded_json.getvalue().decode("utf-8"))
                ingested = ingest_extraction(extraction, document)
                with Ledger(_ledger_path()) as ledger:
                    ledger.save_extraction(document, extraction)
                st.session_state.extraction = extraction
                st.session_state.ingestion = ingested
                st.success("스키마와 근거를 검증하고 원장에 저장했습니다.")
            except Exception as exc:
                st.exception(exc)


document = st.session_state.get("document")
if document is None:
    st.info("왼쪽에서 PDF/PPTX/XLSX/DOCX RFP를 업로드하고 파싱하세요.")
    st.stop()

summary, source, review = st.tabs(["문서 요약", "원문 블록", "FP 후보 검토"])

with summary:
    a, b, c = st.columns(3)
    a.metric("문서", document.filename)
    b.metric("원문 블록", len(document.blocks))
    c.metric("SHA-256", document.sha256[:12])
    for warning in document.warnings:
        st.warning(warning)

with source:
    st.dataframe([
        {
            "block_id": block.id,
            "종류": block.kind,
            "위치": block.location.label,
            "절": block.location.section,
            "셀": block.location.cell_range,
            "원문": block.text,
        }
        for block in document.blocks
    ], use_container_width=True, hide_index=True)

with review:
    extraction = st.session_state.get("extraction")
    if extraction is None:
        st.info("사내 LLM Structured Output JSON을 업로드하면 후보 검토를 시작할 수 있습니다.")
        st.stop()

    with Ledger(_ledger_path()) as ledger:
        functions = ledger.list_functions(document.id)
        if functions:
            result = calculate(functions, Method.SIMPLE)
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("전체 Early FP", f"{result.total_fp:g}")
            f2.metric("확정 FP", f"{result.confirmed_fp:g}")
            f3.metric("잠정 FP", f"{result.provisional_fp:g}")
            f4.metric("미산정", len(result.unresolved_function_ids))

            st.dataframe([
                {
                    "ID": function.id,
                    "기능명": function.name,
                    "유형": function.function_type.value,
                    "상태": function.review_status.value,
                    "제외": function.excluded,
                    "Requirement": ", ".join(function.requirement_ids),
                }
                for function in functions
            ], use_container_width=True, hide_index=True)

            findings = validate(functions)
            if findings:
                st.subheader("정합성 경고")
                st.dataframe([
                    {"코드": finding.code, "심각도": finding.severity, "메시지": finding.message}
                    for finding in findings
                ], use_container_width=True, hide_index=True)

            st.subheader("검토 처리")
            selected = st.selectbox("기능", [function.id for function in functions])
            action = st.radio("처리", ["APPROVE", "MODIFY", "EXCLUDE"], horizontal=True)
            reviewer = st.text_input("검토자")
            reason = st.text_area("검토 사유")
            changes = None
            if action == "MODIFY":
                target = next(function for function in functions if function.id == selected)
                name = st.text_input("수정 기능명", value=target.name)
                function_type = st.selectbox(
                    "수정 기능유형", ["ILF", "EIF", "EI", "EO", "EQ"],
                    index=["ILF", "EIF", "EI", "EO", "EQ"].index(target.function_type.value),
                )
                changes = {"name": name, "function_type": function_type}
            if st.button("검토 결과 저장", type="primary"):
                try:
                    ledger.review_function(
                        document.id, selected, action=action, reviewer=reviewer,
                        reason=reason, changes=changes,
                    )
                    st.success("검토 이력을 저장했습니다. 화면을 갱신합니다.")
                    st.rerun()
                except Exception as exc:
                    st.exception(exc)
        else:
            st.warning("계산 가능한 기능 후보가 없습니다. 미확정 유형이나 근거 오류를 확인하세요.")

    ingestion = st.session_state.get("ingestion")
    if ingestion and ingestion.evidence_issues:
        st.subheader("근거 검증 실패")
        st.dataframe([
            {"코드": issue.code, "대상": issue.owner_id, "메시지": issue.message}
            for issue in ingestion.evidence_issues
        ], use_container_width=True, hide_index=True)

