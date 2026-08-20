"""기능점수 계산 — 간이법/정통법. 입력이 같으면 결과가 항상 같다(원칙 2).

확실성(certainty) 강제 규칙 — 설계원칙 4의 실제 집행 지점:

  MEASURED     → 확정 FP(confirmed_fp)에 포함
  ESTIMATED    → 잠정 FP(provisional_fp)로 분리. 확정 총계에 섞이지 않는다
  UNKNOWN      → 값 자체가 존재할 수 없다(types.Counted 가 거부)
  NEEDS_REVIEW → 값이 있어도 사용 거부. InsufficientData

즉 "근거 없음/검토 필요" 값이 확정 FP 총계에 들어가는 경로는 없다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .complexity import InsufficientData, determine_complexity
from .rules import AVERAGE_WEIGHTS, DETAILED_WEIGHTS
from .types import (
    Certainty,
    Complexity,
    Confirmation,
    Counted,
    FPFunction,
    FPResult,
    FunctionResult,
    FunctionType,
    Method,
)


def _round_fp(value: float) -> float:
    """간이법은 소수 가중치를 쓰므로 부동소수 오차만 제거한다."""
    return round(value + 0.0, 6)


def _weakest(*counts: Counted) -> Certainty:
    """투입된 카운트 중 가장 약한 확실성. 확정 여부는 이 값으로 결정된다."""
    order = {
        Certainty.MEASURED: 0,
        Certainty.ESTIMATED: 1,
        Certainty.NEEDS_REVIEW: 2,
        Certainty.UNKNOWN: 3,
    }
    return max((c.certainty for c in counts), key=lambda c: order[c])


def calculate_function(func: FPFunction, method: Method) -> FunctionResult:
    """기능 1건의 FP. 간이법은 카운트를 쓰지 않으므로 확실성 판정 대상이 아니다."""
    if method is Method.SIMPLE:
        weight = AVERAGE_WEIGHTS[func.function_type]
        return FunctionResult(
            function_id=func.id,
            function_type=func.function_type,
            method=method,
            complexity=None,
            weight=weight,
            fp=weight,
            derivation=f"간이법 평균복잡도 가중치({func.function_type.value})={weight}",
            confirmation=Confirmation.CONFIRMED,
            count_certainty=None,
        )

    det, axis2 = func.sizing_counts
    axis_name = "RET" if func.function_type.is_data_function else "FTR"

    # ── 확실성 게이트: 값이 있어도 쓸 수 없는 경우를 먼저 걸러낸다 ──
    for count, label in ((det, "DET"), (axis2, axis_name)):
        if count.value is not None and not count.certainty.is_usable:
            raise InsufficientData(
                f"{func.function_type.value} '{func.name}': {label}={count.value} 이지만 "
                f"certainty={count.certainty.value} 이므로 정통법 산정에 사용할 수 없다. "
                "사람 확인 후 MEASURED 로 승격하거나 간이법으로 산정해야 한다."
            )

    complexity, derivation = determine_complexity(
        func.function_type, det.value, axis2.value
    )
    weight = float(DETAILED_WEIGHTS[func.function_type][complexity])
    certainty = _weakest(det, axis2)
    confirmation = (
        Confirmation.CONFIRMED
        if certainty.is_confirmable
        else Confirmation.PROVISIONAL
    )
    note = "" if confirmation is Confirmation.CONFIRMED else " [잠정: 추정 카운트 포함]"

    return FunctionResult(
        function_id=func.id,
        function_type=func.function_type,
        method=method,
        complexity=complexity,
        weight=weight,
        fp=weight,
        derivation=f"{derivation}; 가중치={weight:g} (표 3-20){note}",
        confirmation=confirmation,
        count_certainty=certainty,
    )


def calculate_from_complexities(
    items: Iterable[tuple[str, FunctionType, Complexity]],
) -> FPResult:
    """복잡도가 이미 확정된 경우(예: 공식 산정서 검증)의 정통법 계산."""
    results = []
    for fid, ftype, complexity in items:
        weight = float(DETAILED_WEIGHTS[ftype][complexity])
        results.append(
            FunctionResult(
                fid, ftype, Method.DETAILED, complexity, weight, weight,
                derivation=f"복잡도 {complexity.value} 직접 지정; 가중치={weight:g}",
                confirmation=Confirmation.CONFIRMED,
                count_certainty=Certainty.MEASURED,
            )
        )
    return _aggregate(results, Method.DETAILED, (), ())


def calculate(
    functions: Iterable[FPFunction],
    method: Method,
    *,
    fallback_to_simple: bool = False,
    skip_unresolved: bool = False,
) -> FPResult:
    """FP 총계를 계산한다.

    fallback_to_simple=True : 정통법 산정 불가 기능을 간이법 가중치로 대체한다
        (Early FP 하이브리드). 대체분은 **잠정 FP** 로 분류되며 derivation 에 명시된다.
    skip_unresolved=True    : 산정 불가 기능을 총계에서 제외하고
        unresolved_function_ids 로 보고한다.

    둘 다 False 이면 InsufficientData 를 그대로 올린다 — 조용히 넘어가지 않는다.
    """
    results: list[FunctionResult] = []
    excluded: list[str] = []
    unresolved: list[str] = []

    for func in functions:
        if func.excluded:
            excluded.append(func.id)
            continue
        try:
            results.append(calculate_function(func, method))
        except InsufficientData as exc:
            if method is Method.DETAILED and fallback_to_simple:
                weight = AVERAGE_WEIGHTS[func.function_type]
                results.append(
                    FunctionResult(
                        func.id, func.function_type, Method.SIMPLE,
                        None, weight, weight,
                        derivation=(
                            f"정통법 산정 불가({exc.reason}) → 간이법 평균복잡도로 "
                            f"대체: {weight}"
                        ),
                        confirmation=Confirmation.PROVISIONAL,
                        count_certainty=_weakest(*func.sizing_counts),
                    )
                )
            elif skip_unresolved:
                unresolved.append(func.id)
            else:
                raise

    return _aggregate(results, method, tuple(excluded), tuple(unresolved))


def _aggregate(
    results: list[FunctionResult],
    method: Method,
    excluded: tuple[str, ...],
    unresolved: tuple[str, ...],
) -> FPResult:
    by_type: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    confirmed = 0.0
    provisional = 0.0
    for r in results:
        by_type[r.function_type.value] += r.fp
        counts[r.function_type.value] += 1
        if r.is_provisional:
            provisional += r.fp
        else:
            confirmed += r.fp

    data_fp = by_type["ILF"] + by_type["EIF"]
    txn_fp = by_type["EI"] + by_type["EO"] + by_type["EQ"]
    return FPResult(
        method=method,
        total_fp=_round_fp(data_fp + txn_fp),
        confirmed_fp=_round_fp(confirmed),
        provisional_fp=_round_fp(provisional),
        data_fp=_round_fp(data_fp),
        transaction_fp=_round_fp(txn_fp),
        by_type={k: _round_fp(v) for k, v in sorted(by_type.items())},
        counts_by_type=dict(sorted(counts.items())),
        functions=tuple(results),
        excluded_function_ids=excluded,
        unresolved_function_ids=unresolved,
    )
