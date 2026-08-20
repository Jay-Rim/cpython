"""국내 공식 기준 테이블 (SW사업 대가산정 가이드).

출처: 한국소프트웨어산업협회(현 KOSA), 「SW사업 대가산정 가이드」
      Part III. SW사업 구현단계 - 2. 소프트웨어 개발비, 표 3-8 ~ 표 3-25.
      본 파일의 수치는 2020년 개정판 PDF 원문에서 추출·검증하였다.
      https://www.sw.or.kr/site/sw/ex/board/List.do?cbIdx=276

주의(중요):
  - 이 파일은 '기준의 유일한 원천(single source of truth)'이다.
    다른 모듈은 여기서만 숫자를 가져온다. 하드코딩 금지.
  - GUIDE_EDITION 과 FP_UNIT_PRICE 는 매년 개정된다. 개정판을 적용할 때는
    반드시 (1) 원문 표와 diff, (2) tests/golden_cases 재실행을 거친다.
  - 국내 가이드는 IFPUG CPM 의 복잡도 매트릭스/가중치를 그대로 채택하되,
    VAF(GSC 14개)는 사용하지 않는다. 대신 5개 보정계수를 개발원가에 곱한다.
"""

from __future__ import annotations

from typing import Optional

from .types import Complexity, FunctionType

GUIDE_EDITION = "2020-revision (표 3-8 ~ 3-25)"

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
# 3. 단가 / 단계별 가중치 (표 3-21, 3-22)
# ---------------------------------------------------------------------------

# 2020년 개정판 기준. 2024년 개정에서 9.5% 인상되어 605,784원으로 공표되었다.
# 실제 사업 적용 시 '가장 최근에 공표된 단가'를 쓴다 (가이드 3단계 명시).
FP_UNIT_PRICE_BY_YEAR: dict[int, int] = {
    2020: 553_114,
    2024: 605_784,  # 출처: KOSA 공표(2024.05), 언론보도 기준 — 원문 대조 필요
}
DEFAULT_PRICE_YEAR = 2020

# 표 3-22 소프트웨어 개발 단계별 기능점수 가중치 (분할발주 시)
PHASE_WEIGHTS: dict[str, float] = {
    "분석": 0.19,
    "설계": 0.24,
    "구현": 0.32,
    "시험": 0.25,
}

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
