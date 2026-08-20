"""Rule Engine 골든 테스트.

핵심 테스트는 SW사업 대가산정 가이드 2.1.6 '적용 사례'(SCM 예제)를 그대로
재현하는 것이다. 공표 문서의 85FP / 68,467,488원과 1원 단위까지 일치해야 한다.
이 테스트가 깨지면 기준 테이블 또는 반올림 규칙이 어긋난 것이다.
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fp_engine import (  # noqa: E402
    Certainty,
    Complexity,
    Counted,
    FPFunction,
    FunctionType,
    Method,
    calculate,
    calculate_cost,
    calculate_from_complexities,
    determine_complexity,
    size_adjustment_factor,
    validate,
)
from fp_engine.complexity import InsufficientData  # noqa: E402

C, FT = Complexity, FunctionType

# 가이드 2.1.6 적용사례 - 2단계 표 (정통법)
GUIDE_EXAMPLE = [
    ("고객데이터", FT.ILF, C.AVERAGE, 10),
    ("제품데이터", FT.ILF, C.LOW, 7),
    ("공급데이터", FT.EIF, C.LOW, 5),
    ("고객데이터의 추가", FT.EI, C.HIGH, 6),
    ("고객데이터의 수정", FT.EI, C.AVERAGE, 4),
    ("고객데이터의 삭제", FT.EI, C.LOW, 3),
    ("고객데이터의 조회", FT.EQ, C.LOW, 3),
    ("고객레포트1", FT.EO, C.LOW, 4),
    ("고객레포트2", FT.EO, C.AVERAGE, 5),
    ("고객레포트3", FT.EO, C.LOW, 4),
    ("고객레포트4", FT.EO, C.HIGH, 7),
    ("제품데이터의 추가", FT.EI, C.AVERAGE, 4),
    ("제품데이터의 수정", FT.EI, C.LOW, 3),
    ("제품데이터의 삭제", FT.EI, C.LOW, 3),
    ("제품데이터의 조회", FT.EQ, C.AVERAGE, 4),
    ("제품관련 레포트", FT.EO, C.AVERAGE, 5),
    ("공급자번호 조회", FT.EQ, C.LOW, 3),
    ("공급자 관련 리포트", FT.EO, C.AVERAGE, 5),
]


def test_guide_example_weights_match_official_table():
    result = calculate_from_complexities(
        (name, ftype, complexity) for name, ftype, complexity, _ in GUIDE_EXAMPLE
    )
    for r, (name, _, _, expected_fp) in zip(result.functions, GUIDE_EXAMPLE):
        assert r.fp == expected_fp, f"{name}: {r.fp} != {expected_fp}"


def test_guide_example_total_is_85fp():
    result = calculate_from_complexities(
        (name, ftype, complexity) for name, ftype, complexity, _ in GUIDE_EXAMPLE
    )
    assert result.total_fp == 85
    assert result.data_fp == 22          # 10 + 7 + 5
    assert result.transaction_fp == 63


def test_guide_example_full_cost_chain():
    """가이드 3~6단계: 85FP → 68,467,488원."""
    cost = calculate_cost(
        85,
        complexity_levels={
            "연계복잡성": 2,        # 1개의 타기관 연계 → 0.94
            "성능요구수준": 3,      # 피크타임에 중요 → 1.00
            "운영환경호환성": 1,    # 요구사항 없음 → 0.94
            "보안성수준": 2,        # 암호화, 개인정보보호 → 1.00
        },
        price_year=2020,
        direct_expense=2_000_000,
    )
    assert cost.unit_price == 553_114
    assert cost.base_dev_cost == 47_014_690
    assert cost.factors == {
        "규모": 1.28, "연계복잡성": 0.94,
        "성능요구수준": 1.00, "운영환경호환성": 0.94, "보안성수준": 1.00,
    }
    assert cost.adjusted_dev_cost == 53_173_990
    assert cost.profit == 13_293_498
    assert cost.software_dev_cost == 68_467_488


# --- 복잡도 매트릭스 경계값 (표 3-9/3-10/3-14/3-15/3-16) ---


@pytest.mark.parametrize(
    "ftype,det,axis2,expected",
    [
        # ILF: RET 1 / 2~5 / 6이상, DET 1~19 / 20~50 / 51이상
        (FT.ILF, 19, 1, C.LOW), (FT.ILF, 20, 1, C.LOW), (FT.ILF, 51, 1, C.AVERAGE),
        (FT.ILF, 19, 2, C.LOW), (FT.ILF, 20, 5, C.AVERAGE), (FT.ILF, 51, 5, C.HIGH),
        (FT.ILF, 19, 6, C.AVERAGE), (FT.ILF, 20, 6, C.HIGH), (FT.ILF, 51, 99, C.HIGH),
        # EIF 는 매트릭스 동일, 가중치만 다름
        (FT.EIF, 51, 1, C.AVERAGE), (FT.EIF, 20, 5, C.AVERAGE),
        # EI: FTR 0~1 / 2 / 3이상, DET 1~4 / 5~15 / 16이상
        (FT.EI, 4, 0, C.LOW), (FT.EI, 4, 1, C.LOW), (FT.EI, 16, 1, C.AVERAGE),
        (FT.EI, 5, 2, C.AVERAGE), (FT.EI, 16, 2, C.HIGH), (FT.EI, 4, 3, C.AVERAGE),
        (FT.EI, 5, 3, C.HIGH),
        # EO: FTR 0~1 / 2~3 / 4이상, DET 1~5 / 6~19 / 20이상
        (FT.EO, 5, 1, C.LOW), (FT.EO, 20, 1, C.AVERAGE), (FT.EO, 6, 3, C.AVERAGE),
        (FT.EO, 20, 3, C.HIGH), (FT.EO, 5, 4, C.AVERAGE), (FT.EO, 6, 4, C.HIGH),
        # EQ: FTR 1 / 2~3 / 4이상, DET 1~5 / 6~19 / 20이상
        (FT.EQ, 5, 1, C.LOW), (FT.EQ, 20, 1, C.AVERAGE), (FT.EQ, 6, 2, C.AVERAGE),
        (FT.EQ, 20, 3, C.HIGH), (FT.EQ, 5, 4, C.AVERAGE), (FT.EQ, 6, 4, C.HIGH),
    ],
)
def test_complexity_matrix_boundaries(ftype, det, axis2, expected):
    complexity, derivation = determine_complexity(ftype, det, axis2)
    assert complexity is expected
    assert "표 3-" in derivation


def test_determinism():
    """원칙 2: 같은 입력 → 같은 결과."""
    runs = {determine_complexity(FT.EI, 15, 2)[0] for _ in range(100)}
    assert runs == {C.AVERAGE}


# --- 규모 보정계수: 가이드가 명시한 경계값에 수렴하는지 ---


def test_size_factor_boundaries():
    assert size_adjustment_factor(499)[0] == 1.2800
    assert size_adjustment_factor(3001)[0] == 1.1530
    # 공식 자체가 경계에서 공표값과 일치함을 확인 (가이드 표 3-23 검증)
    assert math.isclose(size_adjustment_factor(500)[0], 1.2800, abs_tol=0.0005)
    assert math.isclose(size_adjustment_factor(3000)[0], 1.1530, abs_tol=0.0005)
    # 중간 구간은 1보다 작아진다 (규모의 경제)
    assert size_adjustment_factor(1000)[0] < 1.0


def test_phase_split_order():
    """분할발주: 분석+설계만 수행하면 0.43 배."""
    full = calculate_cost(1000, complexity_levels=_lvl(), price_year=2020)
    part = calculate_cost(1000, complexity_levels=_lvl(), price_year=2020, phases=["분석", "설계"])
    assert math.isclose(part.base_dev_cost / full.base_dev_cost, 0.43, abs_tol=0.001)


def _lvl():
    return {"연계복잡성": 3, "성능요구수준": 3, "운영환경호환성": 2, "보안성수준": 2}


def test_profit_rate_cap():
    with pytest.raises(ValueError):
        calculate_cost(100, complexity_levels=_lvl(), profit_rate=0.30)


# --- 간이법 / Early FP ---


def _f(fid, name, ftype, det=None, ret=None, ftr=None):
    def c(v):
        return Counted(v, Certainty.MEASURED, "test") if v is not None else Counted(None)
    return FPFunction(fid, name, ftype, det=c(det), ret=c(ret), ftr=c(ftr))


def test_simple_method_uses_average_weights():
    funcs = [
        _f("F1", "고객정보", FT.ILF),
        _f("F2", "공급자정보", FT.EIF),
        _f("F3", "고객등록", FT.EI),
        _f("F4", "매출통계", FT.EO),
        _f("F5", "고객조회", FT.EQ),
    ]
    result = calculate(funcs, Method.SIMPLE)
    assert result.total_fp == pytest.approx(7.5 + 5.4 + 4.0 + 5.2 + 3.9)


def test_detailed_method_raises_on_missing_counts():
    with pytest.raises(InsufficientData):
        calculate([_f("F1", "고객정보", FT.ILF)], Method.DETAILED)


def test_hybrid_fallback_is_traceable():
    funcs = [
        _f("F1", "고객정보", FT.ILF, det=25, ret=3),   # 정통법 가능 → 보통(10)
        _f("F2", "제품정보", FT.ILF),                   # 미확인 → 간이법 7.5
    ]
    result = calculate(funcs, Method.DETAILED, fallback_to_simple=True)
    assert result.total_fp == pytest.approx(17.5)
    assert "간이법 평균복잡도로 대체" in result.functions[1].derivation


def test_excluded_functions_are_reported_not_silently_dropped():
    f = FPFunction("F9", "로그아웃", FT.EQ, excluded=True, exclusion_reason="가이드상 제외")
    result = calculate([f], Method.SIMPLE)
    assert result.total_fp == 0
    assert result.excluded_function_ids == ("F9",)


# --- Validator ---


def test_validator_flags_duplicates_and_misclassification():
    funcs = [
        _f("F1", "고객정보", FT.ILF),
        _f("F2", "고객등록", FT.EI),
        _f("F3", "고객정보", FT.ILF),          # 중복
        _f("F4", "매출통계조회", FT.EQ),        # 통계 → EO 여야 함
        _f("F5", "로그아웃", FT.EQ),            # 제외 권고
    ]
    codes = {f.code for f in validate(funcs)}
    assert "DUP_FUNCTION" in codes
    assert "EQ_SHOULD_BE_EO" in codes
    assert "EXCLUDE_CANDIDATE" in codes


def test_validator_flags_ilf_without_ei():
    funcs = [_f("F1", "공급자정보", FT.ILF), _f("F2", "고객등록", FT.EI)]
    codes = {f.code for f in validate(funcs)}
    assert "ILF_WITHOUT_EI" in codes


def test_validator_flags_det_outlier():
    funcs = [_f("F1", "고객정보", FT.ILF, det=250, ret=2), _f("F2", "고객등록", FT.EI, det=10, ftr=1)]
    codes = {f.code for f in validate(funcs)}
    assert "DET_OUTLIER" in codes


def test_validator_flags_ilf_eif_conflict():
    funcs = [
        _f("F1", "공급자정보", FT.ILF),
        _f("F2", "공급자정보", FT.EIF),
        _f("F3", "공급자등록", FT.EI),
    ]
    codes = {f.code for f in validate(funcs)}
    assert "ILF_EIF_CONFLICT" in codes
