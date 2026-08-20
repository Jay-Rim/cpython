"""보정계수 및 소프트웨어 개발비 산정 (가이드 3~6단계).

반올림 규칙 주의:
  가이드 본문에는 라운딩 규칙이 명시되어 있지 않다. 아래 규칙은 가이드
  2.1.6 적용사례(85FP 예제)의 공표 금액과 자릿수까지 일치하도록 역산한
  것이다(보정후 개발원가=내림, 이윤=반올림). 실제 발주기관이 쓰는 공식
  Excel 과 1원 단위까지 맞춰야 하므로, Phase 0 에서 반드시 해당 기관의
  Excel 산출내역서와 대조하여 이 규칙을 확정해야 한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping, Optional

from .rules import (
    APP_COMPLEXITY_FACTORS,
    DEFAULT_PRICE_YEAR,
    FP_UNIT_PRICE_BY_YEAR,
    PHASE_WEIGHTS,
    PROFIT_RATE_MAX,
    SIZE_ADJ_COEFF,
)


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
    complexity_levels: Mapping[str, int],
    price_year: int = DEFAULT_PRICE_YEAR,
    profit_rate: float = PROFIT_RATE_MAX,
    direct_expense: int = 0,
    phases: Optional[list[str]] = None,
    size_factor_override: Optional[float] = None,
) -> CostResult:
    """총 기능점수 → 소프트웨어 개발비.

    complexity_levels: {"연계복잡성": 2, "성능요구수준": 3, ...} 1~5 수준.
    phases: 분할발주 시 수행 단계 목록(예: ["분석","설계"]). None 이면 전체.
    """
    if not 0 <= profit_rate <= PROFIT_RATE_MAX:
        raise ValueError(f"이윤율은 0~{PROFIT_RATE_MAX} 범위여야 한다 (국가계약법 시행규칙 제8조)")

    unit_price = FP_UNIT_PRICE_BY_YEAR[price_year]
    derivations = [f"기능점수당 단가({price_year}) = {unit_price:,}원 (표 3-21)"]

    phase_weight = 1.0
    if phases:
        missing = [p for p in phases if p not in PHASE_WEIGHTS]
        if missing:
            raise ValueError(f"알 수 없는 단계: {missing}")
        phase_weight = sum(PHASE_WEIGHTS[p] for p in phases)
        derivations.append(f"단계별 가중치 합({'+'.join(phases)}) = {phase_weight:.2f} (표 3-22)")

    base = total_fp * unit_price * phase_weight
    derivations.append(f"보정전 개발원가 = {total_fp:g} × {unit_price:,} × {phase_weight:g}")

    factors: dict[str, float] = {}
    if size_factor_override is not None:
        factors["규모"] = size_factor_override
        derivations.append(f"규모 보정계수(직접지정) = {size_factor_override}")
    else:
        sf, why = size_adjustment_factor(total_fp)
        factors["규모"] = sf
        derivations.append(why)

    for name in ("연계복잡성", "성능요구수준", "운영환경호환성", "보안성수준"):
        value, why = app_complexity_factor(name, complexity_levels[name])
        factors[name] = value
        derivations.append(why)

    adjusted = base
    for value in factors.values():
        adjusted *= value

    base_i = int(base)                      # 내림 (예제 대조 결과)
    adjusted_i = int(adjusted)              # 내림 (예제 대조 결과)
    profit_i = int(
        Decimal(adjusted_i * profit_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )                                        # 반올림 (예제 대조 결과)
    total = adjusted_i + profit_i + direct_expense

    derivations.append(
        "보정후 개발원가 = 보정전 개발원가 × "
        + " × ".join(f"{k}({v})" for k, v in factors.items())
    )
    derivations.append(f"소프트웨어 개발비 = {adjusted_i:,} + {profit_i:,} + {direct_expense:,}")

    return CostResult(
        total_fp=total_fp,
        unit_price=unit_price,
        base_dev_cost=base_i,
        factors=factors,
        adjusted_dev_cost=adjusted_i,
        profit=profit_i,
        direct_expense=direct_expense,
        software_dev_cost=total,
        derivations=tuple(derivations),
    )
