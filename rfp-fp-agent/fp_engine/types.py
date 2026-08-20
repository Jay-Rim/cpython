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

    확정 총계(confirmed FP)에 들어갈 수 있는 것은 MEASURED 뿐이다.
    ESTIMATED 는 잠정(provisional) 총계로 분리되고, UNKNOWN/NEEDS_REVIEW 는
    정통법 계산 자체가 거부된다. 이 규칙은 calculator.py 가 강제한다.
    """

    MEASURED = "MEASURED"      # RFP/설계산출물에 명시 → 근거 있음
    ESTIMATED = "ESTIMATED"    # 유사 기능/휴리스틱 기반 추정 → 잠정값
    UNKNOWN = "UNKNOWN"        # 판단 불가 → 값을 가질 수 없다
    NEEDS_REVIEW = "NEEDS_REVIEW"  # 값은 있으나 규칙 위반/저신뢰 → 사람 확인 전까지 사용 불가

    @property
    def is_confirmable(self) -> bool:
        """확정 FP 총계에 포함될 수 있는가."""
        return self is Certainty.MEASURED

    @property
    def is_usable(self) -> bool:
        """정통법 복잡도 판정에 값을 쓸 수 있는가 (잠정 포함)."""
        return self in (Certainty.MEASURED, Certainty.ESTIMATED)


class Confirmation(str, Enum):
    """계산 결과 1건의 확정 수준."""

    CONFIRMED = "CONFIRMED"      # 근거 있는 값만으로 산정됨
    PROVISIONAL = "PROVISIONAL"  # 추정값 또는 간이법 대체가 섞임


@dataclass(frozen=True)
class Counted:
    """카운트 값 + 확실성 + 근거를 함께 운반하는 값 객체."""

    value: Optional[int]
    certainty: Certainty = Certainty.UNKNOWN
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.value is not None and self.value < 0:
            raise ValueError(f"count must be >= 0, got {self.value}")
        if self.value is None and self.certainty.is_usable:
            raise ValueError("value=None 인데 certainty가 MEASURED/ESTIMATED 일 수 없다")
        if self.value is not None and self.certainty is Certainty.UNKNOWN:
            raise ValueError(
                f"certainty=UNKNOWN 인데 value={self.value} 가 있다. "
                "판단 불가는 값을 가질 수 없다. 근거 있는 값이면 MEASURED, "
                "추정값이면 ESTIMATED, 확인이 필요하면 NEEDS_REVIEW 를 쓴다."
            )

    @property
    def known(self) -> bool:
        """값이 존재하는가. '쓸 수 있는가'와 다르다 — usable 을 볼 것."""
        return self.value is not None

    @property
    def usable(self) -> bool:
        """정통법 계산에 투입 가능한가 (MEASURED/ESTIMATED 만)."""
        return self.value is not None and self.certainty.is_usable


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
    confirmation: Confirmation = Confirmation.CONFIRMED
    count_certainty: Optional[Certainty] = None  # 투입된 카운트 중 가장 약한 확실성

    @property
    def is_provisional(self) -> bool:
        return self.confirmation is Confirmation.PROVISIONAL


@dataclass(frozen=True)
class FPResult:
    """프로젝트 전체 FP 결과.

    total_fp 하나만 보고 계약 baseline 을 잡으면 안 된다.
    confirmed_fp(근거 있는 값만)와 provisional_fp(추정 포함)를 분리해 제공하며,
    unresolved_function_ids 는 아예 산정되지 못한 기능이다.
    """

    method: Method
    total_fp: float           # confirmed + provisional
    confirmed_fp: float       # MEASURED 카운트만으로 산정된 FP
    provisional_fp: float     # ESTIMATED 또는 간이법 대체가 섞인 FP
    data_fp: float
    transaction_fp: float
    by_type: dict[str, float]
    counts_by_type: dict[str, int]
    functions: tuple[FunctionResult, ...]
    excluded_function_ids: tuple[str, ...] = ()
    unresolved_function_ids: tuple[str, ...] = ()  # 카운트 부족으로 산정 불가

    @property
    def fp_range(self) -> tuple[float, float]:
        """(하한, 상한) = (확정만, 확정+잠정). 미산정 기능은 어느 쪽에도 없다."""
        return (self.confirmed_fp, self.total_fp)

    @property
    def is_fully_confirmed(self) -> bool:
        return not self.provisional_fp and not self.unresolved_function_ids
