"""국내 SW사업 대가산정 가이드 기반 FP Rule Engine.

LLM 은 이 패키지를 호출하지 않는다. LLM 이 만든 구조화 JSON 을 사람이/서비스가
FPFunction 으로 변환한 뒤 이 패키지가 결정적으로 계산한다.
"""

from .calculator import calculate, calculate_from_complexities, calculate_function
from .complexity import InsufficientData, determine_complexity
from .cost import CostResult, app_complexity_factor, calculate_cost, size_adjustment_factor
from .types import (
    Certainty,
    Complexity,
    Counted,
    FPFunction,
    FPResult,
    FunctionResult,
    FunctionType,
    Method,
)
from .validator import Finding, validate

__all__ = [
    "calculate", "calculate_from_complexities", "calculate_function",
    "determine_complexity", "InsufficientData",
    "calculate_cost", "size_adjustment_factor", "app_complexity_factor", "CostResult",
    "FunctionType", "Complexity", "Method", "Certainty", "Counted",
    "FPFunction", "FPResult", "FunctionResult",
    "validate", "Finding",
]
__version__ = "0.1.0"
