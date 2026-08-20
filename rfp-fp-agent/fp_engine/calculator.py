"""기능점수 계산 — 간이법/정통법. 입력이 같으면 결과가 항상 같다(원칙 2)."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .complexity import InsufficientData, determine_complexity
from .rules import AVERAGE_WEIGHTS, DETAILED_WEIGHTS
from .types import (
    Complexity,
    FPFunction,
    FPResult,
    FunctionResult,
    FunctionType,
    Method,
)


def _round_fp(value: float) -> float:
    """간이법은 소수 가중치를 쓰므로 부동소수 오차만 제거한다."""
    return round(value + 0.0, 6)


def calculate_function(func: FPFunction, method: Method) -> FunctionResult:
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
        )

    det, axis2 = func.sizing_counts
    complexity, derivation = determine_complexity(
        func.function_type, det.value, axis2.value
    )
    weight = float(DETAILED_WEIGHTS[func.function_type][complexity])
    return FunctionResult(
        function_id=func.id,
        function_type=func.function_type,
        method=method,
        complexity=complexity,
        weight=weight,
        fp=weight,
        derivation=f"{derivation}; 가중치={weight:g} (표 3-20)",
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
            )
        )
    return _aggregate(results, Method.DETAILED, ())


def calculate(
    functions: Iterable[FPFunction],
    method: Method,
    *,
    fallback_to_simple: bool = False,
) -> FPResult:
    """FP 총계를 계산한다.

    fallback_to_simple=True 이면 정통법 산정 중 DET/FTR 미확인 기능만
    간이법 가중치로 대체한다(Early FP 하이브리드). 대체된 기능은
    derivation 에 명시되어 추적 가능하다.
    """
    results: list[FunctionResult] = []
    excluded: list[str] = []

    for func in functions:
        if func.excluded:
            excluded.append(func.id)
            continue
        try:
            results.append(calculate_function(func, method))
        except InsufficientData:
            if method is Method.DETAILED and fallback_to_simple:
                simple = calculate_function(func, Method.SIMPLE)
                results.append(
                    FunctionResult(
                        simple.function_id, simple.function_type, Method.SIMPLE,
                        None, simple.weight, simple.fp,
                        derivation=(
                            "정통법 산정 불가(DET/FTR 미확인) → 간이법 평균복잡도로 "
                            f"대체: {simple.weight}"
                        ),
                    )
                )
            else:
                raise

    return _aggregate(results, method, tuple(excluded))


def _aggregate(
    results: list[FunctionResult], method: Method, excluded: tuple[str, ...]
) -> FPResult:
    by_type: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for r in results:
        by_type[r.function_type.value] += r.fp
        counts[r.function_type.value] += 1

    data_fp = by_type["ILF"] + by_type["EIF"]
    txn_fp = by_type["EI"] + by_type["EO"] + by_type["EQ"]
    return FPResult(
        method=method,
        total_fp=_round_fp(data_fp + txn_fp),
        data_fp=_round_fp(data_fp),
        transaction_fp=_round_fp(txn_fp),
        by_type={k: _round_fp(v) for k, v in sorted(by_type.items())},
        counts_by_type=dict(sorted(counts.items())),
        functions=tuple(results),
        excluded_function_ids=excluded,
    )
