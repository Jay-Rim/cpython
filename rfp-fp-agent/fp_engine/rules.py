"""국내 공식 기준 테이블 (SW사업 대가산정 가이드).

출처: 한국인공지능·소프트웨어산업협회(KOSA), 「SW사업 대가산정 가이드」
      Part III. SW사업 구현단계 - 2. 소프트웨어 개발비, 표 3-8 ~ 3-25.

대조 이력:
  - 2020년 개정판 PDF 원문에서 최초 추출
  - **2025년 개정판(KOSA 공식 배포본) 원문과 전량 대조 완료 (2026-08-20)**
    · 복잡도 매트릭스(표 3-9/3-10/3-14/3-15/3-16): 동일
    · 정통법 가중치, 간이법 평균복잡도 가중치: 동일
    · 규모 보정계수 공식, 애플리케이션 복잡도 보정계수 4종: 동일
    · **기능점수당 단가: 553,114원 → 605,784원 (변경)**
    · **단계별 발주 구분(설계사업/구축사업) 신설 (변경)**

주의(중요):
  - 이 파일은 '기준의 유일한 원천(single source of truth)'이다.
  - 개정판별 차이는 RULE_PACKS 로 분리한다. 호출자는 반드시 edition 을 명시해야
    하며, 기본값은 제공하지 않는다 — 과거 단가로 조용히 계산되는 사고를 막는다.
  - 국내 가이드는 IFPUG CPM 의 복잡도 매트릭스/가중치를 그대로 채택하되,
    VAF(GSC 14개)는 사용하지 않는다. 대신 5개 보정계수를 개발원가에 곱한다.
  - 아직 **공식 Excel 산정 템플릿과는 대조하지 않았다.** 반올림 규칙은 두 개정판의
    적용사례로부터 역산한 것이다(ROUNDING_NOTE 참조).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .types import Complexity, FunctionType

SUPPORTED_EDITIONS = ("2020", "2025")
LATEST_EDITION = "2025"

# ---------------------------------------------------------------------------
# 1. 정통법 복잡도 매트릭스
#    표 3-9(ILF), 3-10(EIF), 3-14(EI), 3-15(EO), 3-16(EQ)
#    각 항목: (하한, 상한(None=무한), ...) 형태의 구간 경계로 표현한다.
# ---------------------------------------------------------------------------

L, A, H = Complexity.LOW, Complexity.AVERAGE, Complexity.HIGH

# 구조: {유형: (2축_구간경계, DET_구간경계, 행렬)}
#   2축_구간경계: 데이터기능=RET, 트랜잭션기능=FTR
#   경계값은 '해당 구간의 상한(inclusive)' 리스트이며 마지막은 None(무한대).
COMPLEXITY_MATRIX: dict[FunctionType, dict] = {
    # 표 3-9 ILF: RET 1 / 2~5 / 6이상,  DET 1~19 / 20~50 / 51이상
    FunctionType.ILF: {
        "axis2_name": "RET",
        "axis2_bounds": [1, 5, None],
        "det_bounds": [19, 50, None],
        "matrix": [
            [L, L, A],
            [L, A, H],
            [A, H, H],
        ],
        "source": "표 3-9 내부논리파일(ILF) 복잡도 및 기능점수 가중치",
    },
    # 표 3-10 EIF: 동일 구간
    FunctionType.EIF: {
        "axis2_name": "RET",
        "axis2_bounds": [1, 5, None],
        "det_bounds": [19, 50, None],
        "matrix": [
            [L, L, A],
            [L, A, H],
            [A, H, H],
        ],
        "source": "표 3-10 외부연계파일(EIF) 복잡도 및 기능점수 가중치",
    },
    # 표 3-14 EI: FTR 0~1 / 2 / 3이상,  DET 1~4 / 5~15 / 16이상
    FunctionType.EI: {
        "axis2_name": "FTR",
        "axis2_bounds": [1, 2, None],
        "det_bounds": [4, 15, None],
        "matrix": [
            [L, L, A],
            [L, A, H],
            [A, H, H],
        ],
        "source": "표 3-14 외부입력(EI) 복잡도 및 기능점수 가중치",
    },
    # 표 3-15 EO: FTR 0~1 / 2~3 / 4이상,  DET 1~5 / 6~19 / 20이상
    FunctionType.EO: {
        "axis2_name": "FTR",
        "axis2_bounds": [1, 3, None],
        "det_bounds": [5, 19, None],
        "matrix": [
            [L, L, A],
            [L, A, H],
            [A, H, H],
        ],
        "source": "표 3-15 외부출력(EO) 복잡도 및 기능점수 가중치",
    },
    # 표 3-16 EQ: FTR 1 / 2~3 / 4이상,  DET 1~5 / 6~19 / 20이상
    FunctionType.EQ: {
        "axis2_name": "FTR",
        "axis2_bounds": [1, 3, None],
        "det_bounds": [5, 19, None],
        "matrix": [
            [L, L, A],
            [L, A, H],
            [A, H, H],
        ],
        "source": "표 3-16 외부조회(EQ) 복잡도 및 기능점수 가중치",
    },
}

# ---------------------------------------------------------------------------
# 2. 정통법 가중치 (표 3-20) / 간이법 평균복잡도 가중치 (표 3-19)
# ---------------------------------------------------------------------------

DETAILED_WEIGHTS: dict[FunctionType, dict[Complexity, int]] = {
    FunctionType.ILF: {L: 7, A: 10, H: 15},
    FunctionType.EIF: {L: 5, A: 7, H: 10},
    FunctionType.EI: {L: 3, A: 4, H: 6},
    FunctionType.EO: {L: 4, A: 5, H: 7},
    FunctionType.EQ: {L: 3, A: 4, H: 6},
}

AVERAGE_WEIGHTS: dict[FunctionType, float] = {
    FunctionType.ILF: 7.5,
    FunctionType.EIF: 5.4,
    FunctionType.EI: 4.0,
    FunctionType.EO: 5.2,
    FunctionType.EQ: 3.9,
}

# ---------------------------------------------------------------------------
# 3. 개정판별 규칙 팩 (표 3-21 단가, 표 3-22 단계별 가중치)
#    매트릭스·가중치·보정계수는 2020/2025 가 동일하므로 공유한다(대조 완료).
#    개정판 간 차이가 나는 항목만 여기서 분기한다.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RulePack:
    """특정 개정판의 금액 관련 기준."""

    edition: str
    fp_unit_price: int
    phase_weights: dict[str, float]
    split_order_weights: dict[str, float]  # 단계별 발주 구분 (2025 신설)
    verified_against: str
    example_total_fp: int
    example_software_dev_cost: int  # 적용사례 공표 금액 (골든 테스트용)


RULE_PACKS: dict[str, RulePack] = {
    "2020": RulePack(
        edition="2020",
        fp_unit_price=553_114,
        phase_weights={"분석": 0.19, "설계": 0.24, "구현": 0.32, "시험": 0.25},
        split_order_weights={},  # 2020년판에는 없음
        verified_against="2020년 개정판 PDF 원문 (표 3-21, 3-22, 2.1.6 적용사례)",
        example_total_fp=85,
        example_software_dev_cost=68_467_488,
    ),
    "2025": RulePack(
        edition="2025",
        fp_unit_price=605_784,
        phase_weights={"분석": 0.19, "설계": 0.24, "구현": 0.32, "시험": 0.25},
        split_order_weights={"설계사업": 0.281, "구축사업": 0.719},
        verified_against="2025년 개정판 KOSA 배포본 PDF 원문 (표 3-21, 3-22, 2.1.6 적용사례)",
        example_total_fp=85,
        example_software_dev_cost=74_796_821,
    ),
}


def get_rule_pack(edition: str) -> RulePack:
    if edition not in RULE_PACKS:
        raise ValueError(
            f"알 수 없는 개정판 '{edition}'. 지원: {sorted(RULE_PACKS)}. "
            "가이드는 '가장 최근에 공표된 단가'를 적용하도록 규정한다."
        )
    return RULE_PACKS[edition]


# 반올림 규칙 — 가이드 본문에 명시가 없어 적용사례에서 역산한 것.
ROUNDING_NOTE = """보정계수를 하나씩 순차적으로 곱하며 매 단계 원 단위 반올림(ROUND_HALF_EVEN).
2020년판 예제(53,173,990원)와 2025년판 예제(58,237,457원)를 동시에 만족하는 유일한 규칙이다.
결합 곱 후 1회 반올림이나 절사(floor)로는 두 예제를 동시에 재현할 수 없다.
※ 공식 Excel 산정 템플릿과는 아직 대조하지 않았다. 도입 전 반드시 대조할 것."""

PROFIT_RATE_MAX = 0.25  # 국가계약법 시행규칙 제8조: 개발원가의 25% 초과 불가

# ---------------------------------------------------------------------------
# 4. 보정계수 (표 3-23 규모, 표 3-24 애플리케이션 복잡도)
#    국내 가이드는 VAF/GSC(14개)를 사용하지 않는다.
# ---------------------------------------------------------------------------

SIZE_ADJ_COEFF = {
    "a": 0.4057,
    "ln_center": 7.1978,
    "b": 0.8878,
    "floor_fp": 500,      # 500FP 미만
    "floor_value": 1.2800,
    "cap_fp": 3000,       # 3,000FP 초과
    "cap_value": 1.1530,
    "source": "표 3-23 규모 보정계수",
}

APP_COMPLEXITY_FACTORS: dict[str, dict[int, tuple[float, str]]] = {
    "연계복잡성": {
        1: (0.88, "타기관 연계 없음"),
        2: (0.94, "1~2개의 타 기관 연계"),
        3: (1.00, "3~5개의 타 기관 연계"),
        4: (1.06, "6~10개의 타 기관 연계"),
        5: (1.12, "10개를 초과하는 타 기관 연계"),
    },
    "성능요구수준": {
        1: (0.91, "응답성능에 대한 특별한 요구사항 없음"),
        2: (0.95, "요구사항 있으나 특별한 조치 불필요"),
        3: (1.00, "피크타임에 중요하며 처리시한 명시"),
        4: (1.05, "모든 업무시간에 중요하며 처리시한 명시"),
        5: (1.09, "설계단계부터 성능분석/성능분석도구 요구"),
    },
    "운영환경호환성": {
        1: (0.94, "호환성 요구사항 없음"),
        2: (1.00, "동일 HW/SW 환경"),
        3: (1.06, "유사 HW/SW 환경"),
        4: (1.13, "이질적 HW/SW 환경"),
        5: (1.19, "4 + 운영절차 문서화 및 사전 모의훈련 요구"),
    },
    "보안성수준": {
        1: (0.97, "보안 요구사항 1가지"),
        2: (1.00, "보안 요구사항 2가지"),
        3: (1.03, "보안 요구사항 3가지"),
        4: (1.06, "보안 요구사항 4가지"),
        5: (1.08, "보안 요구사항 5가지 이상"),
    },
}

# ---------------------------------------------------------------------------
# 5. 단위프로세스 식별 권고 사례 (가이드 2.1.6 적용사례 표)
#    → LLM 프롬프트의 few-shot 및 Rule Engine 의 사후 검증(lint)에 사용.
#    'expected' 가 None 이면 FP 산정 대상에서 제외 권고.
# ---------------------------------------------------------------------------

TERM_CLASSIFICATION_HINTS: dict[str, dict] = {
    "데이터적재": {"expected": "EI", "not": ["EO", "EQ"]},
    "업로드": {"expected": "EI", "not": ["EO", "EQ"]},
    "설정": {"expected": "EI", "not": ["EO", "EQ"], "why": "ILF를 변경"},
    "발송": {"expected": "EQ", "not": ["EO"], "why": "단순 발송"},
    "전송": {"expected": "EQ|EO", "not": ["EI"]},
    "그래프": {"expected": "EO", "not": ["EQ"]},
    "다운로드": {"expected": "EQ", "not": ["EI", "EO"]},
    "로그인": {"expected": "EQ", "not": ["EI", "EO"], "why": "암호검증 후 로그인"},
    "로그아웃": {"expected": None, "why": "단순 Log-out은 기능 제외"},
    "사용자인증": {"expected": "EQ", "not": ["EI", "EO"]},
    "통계": {"expected": "EO", "not": ["EQ"]},
    "코드": {"expected": None, "not": ["ILF"], "why": "코드데이터 제외"},
    "임시": {"expected": None, "not": ["ILF"], "why": "임시파일 제외"},
    "이력": {"expected": None, "not": ["ILF"], "why": "이력정보 제외"},
    "첨부": {"expected": None, "not": ["ILF"], "why": "단위프로세스 미완결"},
    "로그": {"expected": None, "not": ["ILF"], "why": "로그 데이터 제외"},
}

# 논리파일 식별에서 제외되어야 할 물리파일 유형 (가이드 Step1 제외목록)
DATA_FUNCTION_EXCLUSIONS: tuple[str, ...] = (
    "임시파일",
    "물리적 복사 파일",
    "정렬 파일(Sort File)",
    "화면/보고서 출력용 추출파일",
    "기술적 이유로 도입된 코드파일",
    "인덱스 파일",
    "조인(join) 파일",
    "키로만 구성된 관계파일",
    "일반적인 백업파일",
)

# 기능점수 방식 대신 투입공수 방식이 허용되는 예외 사업유형 (가이드 2.1.5)
EFFORT_BASED_EXCEPTIONS: tuple[str, ...] = (
    "홈페이지 디자인/웹 접근성 개선/동영상 등 콘텐츠 관련 정보화사업",
    "R&D 성격의 소프트웨어개발 사업",
    "식별 기능규모 대비 내부처리 복잡도가 현저히 높은 사업",
    "데이터 튜닝/최적화, 테스트 등 기능점수 산정 불가 사업",
    "소프트웨어개발 관련 예산이 5천만원 미만인 사업",
)


def bucket_index(value: int, bounds: list[Optional[int]]) -> int:
    """구간 경계 리스트에서 value가 속한 인덱스를 돌려준다."""
    for i, upper in enumerate(bounds):
        if upper is None or value <= upper:
            return i
    return len(bounds) - 1
