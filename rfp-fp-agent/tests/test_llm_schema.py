"""LLM 출력 스키마가 자신이 선언한 규칙을 실제로 강제하는지 검증한다.

스키마에 규칙을 문서로만 써두고 강제하지 않으면, 모순된 산출물이 파이프라인
하류로 그대로 흘러간다. 아래 케이스가 그 회귀를 막는다.
"""

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "llm_extraction.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _sub_validator(ref: str) -> Draft202012Validator:
    """스키마 일부만 떼어 검증한다. 전체 스키마를 리졸브 대상으로 유지해야
    $ref 가 properties/$defs 어느 쪽을 가리켜도 해석된다."""
    sub = {k: v for k, v in SCHEMA.items() if k in ("$schema", "$defs", "properties")}
    sub["$ref"] = ref
    return Draft202012Validator(sub)


def _valid(validator: Draft202012Validator, obj) -> bool:
    return not list(validator.iter_errors(obj))


def test_schema_itself_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


def test_model_fingerprint_is_required():
    """재현성 지문 없는 산출물은 계약 근거로 쓸 수 없다."""
    assert "model_fingerprint" in SCHEMA["required"]


@pytest.mark.parametrize(
    "obj,expected,label",
    [
        ({"value": 99, "certainty": "UNKNOWN"}, False, "UNKNOWN 이 값을 들고 있으면 거부"),
        ({"value": None, "certainty": "UNKNOWN"}, True, "UNKNOWN + null 은 허용"),
        ({"value": 12, "certainty": "MEASURED", "rationale": "r"}, False, "MEASURED 는 items 필수"),
        ({"value": 12, "certainty": "MEASURED", "rationale": "r", "items": ["계약번호"]}, True, "MEASURED 정상"),
        ({"value": 12, "certainty": "MEASURED", "rationale": "r", "items": []}, False, "MEASURED items 비어있으면 거부"),
        ({"value": 12, "certainty": "ESTIMATED", "rationale": "유사기능"}, True, "ESTIMATED 정상"),
        ({"value": 12, "certainty": "ESTIMATED"}, False, "ESTIMATED 는 rationale 필수"),
        ({"value": -1, "certainty": "ESTIMATED", "rationale": "r"}, False, "음수 거부"),
    ],
)
def test_counted_conditional_integrity(obj, expected, label):
    assert _valid(_sub_validator("#/$defs/counted"), obj) is expected, label


def _candidate(**overrides):
    base = {
        "candidate_id": "FC-1",
        "requirement_ids": ["REQ-001"],
        "name": "계약내역 조회",
        "function_type": "EQ",
        "evidence": [{"page": 47, "quote": "계약내역을 조회"}],
        "confidence": 0.8,
        "status": "AI_PROPOSED",
    }
    base.update(overrides)
    return base


def test_function_type_may_be_null_when_information_is_insufficient():
    """정보가 없을 때 유형을 추측하도록 강요하지 않는다."""
    v = _sub_validator("#/properties/function_candidates/items")
    assert _valid(v, _candidate(
        function_type=None, status="INSUFFICIENT_INFO",
        open_questions=["이 요구사항이 조회인지 등록인지 불명확"],
    ))


def test_null_function_type_requires_insufficient_info_and_questions():
    v = _sub_validator("#/properties/function_candidates/items")
    # 유형 null 인데 status 가 확정 제안이면 거부
    assert not _valid(v, _candidate(function_type=None, status="AI_PROPOSED",
                                    open_questions=["q"]))
    # 유형 null 인데 되물을 질문이 없으면 거부
    assert not _valid(v, _candidate(function_type=None, status="INSUFFICIENT_INFO"))


def test_evidence_is_mandatory():
    """근거 없는 기능 후보는 스키마 단계에서 거부된다(설계원칙 3)."""
    v = _sub_validator("#/properties/function_candidates/items")
    assert not _valid(v, _candidate(evidence=[]))
    bad = _candidate()
    del bad["evidence"]
    assert not _valid(v, bad)
