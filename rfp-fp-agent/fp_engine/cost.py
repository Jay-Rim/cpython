"""보정계수 및 소프트웨어 개발비 산정 (가이드 3~6단계).

## 개정판 명시가 필수인 이유

`calculate_cost` 는 `edition` 을 기본값 없이 요구한다. 단가는 개정판마다 바뀌며
(2020: 553,114원 → 2025: 605,784원), 85FP 예제 기준 약 633만원 차이가 난다.
기본값을 두면 호출자가 무심코 과거 단가로 계약금액을 산출하게 되므로 두지 않는다.

## 반올림 규칙 (역산 결과, 공식 Excel 미대조)

보정계수를 순차적으로 곱하며 **매 단계 원 단위 반올림(ROUND_HALF_EVEN)** 한다.
이 규칙만이 2020년판·2025년판 적용사례를 동시에 재현한다.
`rules.ROUNDING_NOTE` 및 `tests/test_fp_engine.py` 참조.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Mapping, Optional

from .rules import (
    APP_COMPLEXITY_FACTORS,
    PROFIT_RATE_MAX,
    SIZE_ADJ_COEFF,
    RulePack,
    get_rule_pack,
)

APP_FACTOR_ORDER = ("연계복잡성", "성능요구수준", "운영환경호환성", "보안성수준")


def _won(value: Decimal) -> Decimal:
    """원 단위 반올림 (ROUND_HALF_EVEN)."""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)


def size_adjustment_factor(total_fp: float) -> tuple[float, str]:
    """규모 보정계수 (표 3-23).

    500FP 미만 1.2800, 3,000FP 초과 1.1530, 그 사이는 공식 적용.
    (공식은 500FP/3,000FP 경계에서 각각 1.2800/1.1530 에 수렴한다 — 검증 완료)
    """
    c = SIZE_ADJ_COEFF
    if total_fp < c["floor_fp"]:
        return c["floor_value"], f"{c['source']}: {total_fp}FP < 500FP → {c['floor_value']}"
    if total_fp > c["cap_fp"]:
        return c["cap_value"], f"{c['source']}: {total_fp}FP > 3,000FP → {c['cap_value']}"
    value = c["a"] * (math.log(total_fp) - c["ln_center"]) ** 2 + c["b"]
    value = round(value, 4)
    return value, f"{c['source']}: 0.4057×(ln({total_fp})-7.1978)²+0.8878 = {value}"


def app_complexity_factor(name: str, level: int) -> tuple[float, str]:
    """애플리케이션 복잡도 보정계수 1종 (표 3-24). level 은 1~5."""
    table = APP_COMPLEXITY_FACTORS[name]
    if level not in table:
        raise ValueError(f"{name}: level 은 1~5 이어야 한다 (got {level})")
    value, desc = table[level]
    return value, f"표 3-24 {name} 수준{level}({desc}) → {value}"


@dataclass(frozen=True)
class CostResult:
    edition: str
    total_fp: float
    unit_price: int
    base_dev_cost: int          # 보정전 개발원가
    factors: dict[str, float]
    adjusted_dev_cost: int      # 보정후 개발원가
    profit: int
    direct_expense: int
    software_dev_cost: int      # 소프트웨어 개발비 (부가세 별도)
    derivations: tuple[str, ...]


def calculate_cost(
    total_fp: float,
    *,
    edition: str,
    complexity_levels: Mapping[str, int],
    profit_rate: float = PROFIT_RATE_MAX,
    direct_expense: int = 0,
    phases: Optional[list[str]] = None,
    size_factor_override: Optional[float] = None,
) -> CostResult:
    """총 기능점수 → 소프트웨어 개발비.

    edition: 적용할 가이드 개정판("2020"/"2025"). **기본값 없음 — 명시 필수.**
    complexity_levels: {"연계복잡성": 2, "성능요구수준": 3, ...} 1~5 수준.
    phases: 분할발주 시 수행 단계(예: ["분석","설계"] 또는 ["설계사업"]).
    """
    pack: RulePack = get_rule_pack(edition)

    if not math.isfinite(total_fp) or total_fp <= 0:
        raise ValueError(f"total_fp는 0보다 큰 유한수여야 한다 (got {total_fp})")
    if isinstance(direct_expense, bool) or not isinstance(direct_expense, int) or direct_expense < 0:
        raise ValueError(f"직접경비는 0 이상의 정수여야 한다 (got {direct_expense})")
    if not math.isfinite(profit_rate):
        raise ValueError(f"이윤율은 유한수여야 한다 (got {profit_rate})")
    if not 0 <= profit_rate <= PROFIT_RATE_MAX:
        raise ValueError(
            f"이윤율은 0~{PROFIT_RATE_MAX} 범위여야 한다 (국가계약법 시행규칙 제8조)"
        )
    missing = [n for n in APP_FACTOR_ORDER if n not in complexity_levels]
    if missing:
        raise ValueError(f"보정계수 수준이 지정되지 않았다: {missing}")

    derivations = [
        f"적용 개정판: {pack.edition} ({pack.verified_against})",
        f"기능점수당 단가 = {pack.fp_unit_price:,}원 (표 3-21)",
    ]

    phase_weight = Decimal("1")
    if phases:
        table = {**pack.phase_weights, **pack.split_order_weights}
        unknown = [p for p in phases if p not in table]
        if unknown:
            raise ValueError(f"{pack.edition}년판에 없는 단계: {unknown}. 사용 가능: {sorted(table)}")
        if len(phases) != len(set(phases)):
            raise ValueError(f"단계는 중복 지정할 수 없다: {phases}")
        development_phases = set(phases) & set(pack.phase_weights)
        split_contract_phases = set(phases) & set(pack.split_order_weights)
        if development_phases and split_contract_phases:
            raise ValueError(
                "개발 단계(분석/설계/구현/시험)와 분할발주 구분"
                "(설계사업/구축사업)을 한 계산에서 혼합할 수 없다"
            )
        phase_weight = sum((Decimal(str(table[p])) for p in phases), Decimal("0"))
        if not Decimal("0") < phase_weight <= Decimal("1"):
            raise ValueError(f"단계별 가중치 합은 0 초과 1 이하여야 한다 (got {phase_weight})")
        derivations.append(
            f"단계별 가중치 합({'+'.join(phases)}) = {phase_weight} (표 3-22)"
        )

    base = _won(Decimal(str(total_fp)) * pack.fp_unit_price * phase_weight)
    derivations.append(
        f"보정전 개발원가 = {total_fp:g} × {pack.fp_unit_price:,} × {phase_weight} = {int(base):,}"
    )

    factors: dict[str, float] = {}
    if size_factor_override is not None:
        if not math.isfinite(size_factor_override) or size_factor_override <= 0:
            raise ValueError(
                f"규모 보정계수 직접지정값은 0보다 큰 유한수여야 한다 "
                f"(got {size_factor_override})"
            )
        factors["규모"] = size_factor_override
        derivations.append(f"규모 보정계수(직접지정) = {size_factor_override}")
    else:
        sf, why = size_adjustment_factor(total_fp)
        factors["규모"] = sf
        derivations.append(why)

    for name in APP_FACTOR_ORDER:
        value, why = app_complexity_factor(name, complexity_levels[name])
        factors[name] = value
        derivations.append(why)

    # 순차 곱 + 매 단계 원 단위 반올림 (rules.ROUNDING_NOTE 참조)
    adjusted = base
    for value in factors.values():
        adjusted = _won(adjusted * Decimal(str(value)))

    profit = _won(adjusted * Decimal(str(profit_rate)))
    total = int(adjusted) + int(profit) + direct_expense

    derivations.append(
        "보정후 개발원가 = 보정전 개발원가 × "
        + " × ".join(f"{k}({v})" for k, v in factors.items())
        + f" = {int(adjusted):,} (각 단계 원 단위 반올림)"
    )
    derivations.append(
        f"소프트웨어 개발비 = {int(adjusted):,} + 이윤 {int(profit):,} + 직접경비 {direct_expense:,}"
    )

    return CostResult(
        edition=pack.edition,
        total_fp=total_fp,
        unit_price=pack.fp_unit_price,
        base_dev_cost=int(base),
        factors=factors,
        adjusted_dev_cost=int(adjusted),
        profit=int(profit),
        direct_expense=direct_expense,
        software_dev_cost=total,
        derivations=tuple(derivations),
    )
