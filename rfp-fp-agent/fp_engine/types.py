"""FP 산정 도메인 타입.

이 모듈은 값(value) 정의만 담는다. 계산 로직은 complexity.py / calculator.py,
기준 테이블은 rules.py 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FunctionType(str, Enum):
    """기능 유형 (SW사업 대가산정 가이드 / IFPUG CPM)."""

    ILF = "ILF"  # 내부논리파일
    EIF = "EIF"  # 외부연계파일
    EI = "EI"    # 외부입력
    EO = "EO"    # 외부출력
    EQ = "EQ"    # 외부조회

    @property
    def is_data_function(self) -> bool:
        return self in (FunctionType.ILF, FunctionType.EIF)


class Complexity(str, Enum):
    LOW = "LOW"          # 낮음
    AVERAGE = "AVERAGE"  # 보통
    HIGH = "HIGH"        # 높음


class Method(str, Enum):
    """산정 방법."""

    SIMPLE = "SIMPLE"      # 간이법 (평균복잡도)
    DETAILED = "DETAILED"  # 정통법 (개별복잡도)


class Certainty(str, Enum):
    """DET/RET/FTR 값의 확실성 상태.

    설계원칙 4: AI가 모르는 것을 확정값처럼 만들지 않는다.
    """

    MEASURED = "MEASURED"      # RFP/설계산출물에 명시 → 근거 있음
    ESTIMATED = "ESTIMATED"    # 유사 기능/휴리스틱 기반 추정
    UNKNOWN = "UNKNOWN"        # 판단 불가 → 간이법으로만 산정 가능
    NEEDS_REVIEW = "NEEDS_REVIEW"  # 규칙 위반 또는 저신뢰 → 사람 확인 필수


@dataclass(frozen=True)
class Counted:
    """카운트 값 + 확실성 + 근거를 함께 운반하는 값 객체."""

    value: Optional[int]
    certainty: Certainty = Certainty.UNKNOWN
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.value is not None and self.value < 0:
            raise ValueError(f"count must be >= 0, got {self.value}")
        if self.value is None and self.certainty in (Certainty.MEASURED, Certainty.ESTIMATED):
            raise ValueError("value=None 인데 certainty가 MEASURED/ESTIMATED 일 수 없다")

    @property
    def known(self) -> bool:
        return self.value is not None


UNKNOWN_COUNT = Counted(None, Certainty.UNKNOWN, "미확인")


@dataclass
class FPFunction:
    """FP 산정 대상 단위 기능 1건.

    det/ret/ftr 는 정통법에서만 필요하다. 간이법은 유형과 개수만 쓴다.
    """

    id: str
    name: str
    function_type: FunctionType
    det: Counted = UNKNOWN_COUNT
    ret: Counted = UNKNOWN_COUNT   # 데이터기능(ILF/EIF)에서만 사용
    ftr: Counted = UNKNOWN_COUNT   # 트랜잭션기능(EI/EO/EQ)에서만 사용
    requirement_ids: tuple[str, ...] = ()
    excluded: bool = False
    exclusion_reason: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sizing_counts(self) -> tuple[Counted, Counted]:
        """(DET, RET or FTR) — 복잡도 판정에 쓰이는 두 축."""
        second = self.ret if self.function_type.is_data_function else self.ftr
        return self.det, second


@dataclass(frozen=True)
class FunctionResult:
    """Rule Engine 이 계산한 기능 1건의 결과 (deterministic)."""

    function_id: str
    function_type: FunctionType
    method: Method
    complexity: Optional[Complexity]  # 간이법은 None
    weight: float
    fp: float
    derivation: str  # 어떤 표/어떤 셀에서 나왔는지 사람이 읽을 수 있는 근거


@dataclass(frozen=True)
class FPResult:
    """프로젝트 전체 FP 결과."""

    method: Method
    total_fp: float
    data_fp: float
    transaction_fp: float
    by_type: dict[str, float]
    counts_by_type: dict[str, int]
    functions: tuple[FunctionResult, ...]
    excluded_function_ids: tuple[str, ...] = ()
