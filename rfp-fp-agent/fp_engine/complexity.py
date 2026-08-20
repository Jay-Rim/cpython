"""복잡도 판정 — 순수 결정적 함수. LLM 개입 없음."""

from __future__ import annotations

from typing import Optional

from .rules import COMPLEXITY_MATRIX, bucket_index
from .types import Complexity, FunctionType


class InsufficientData(Exception):
    """정통법 복잡도 판정에 필요한 카운트가 없거나 사용할 수 없다."""

    @property
    def reason(self) -> str:
        return str(self)


def determine_complexity(
    function_type: FunctionType, det: Optional[int], axis2: Optional[int]
) -> tuple[Complexity, str]:
    """정통법 복잡도를 판정하고 (복잡도, 근거문자열)을 돌려준다.

    axis2 는 데이터기능이면 RET, 트랜잭션기능이면 FTR.
    """
    spec = COMPLEXITY_MATRIX[function_type]
    if det is None or axis2 is None:
        raise InsufficientData(
            f"{function_type.value}: DET={det}, {spec['axis2_name']}={axis2} "
            "→ 정통법 복잡도 판정 불가 (간이법 적용 또는 사람 확인 필요)"
        )

    row = bucket_index(axis2, spec["axis2_bounds"])
    col = bucket_index(det, spec["det_bounds"])
    complexity = spec["matrix"][row][col]

    derivation = (
        f"{spec['source']}: {spec['axis2_name']}={axis2}(행{row + 1}), "
        f"DET={det}(열{col + 1}) → {complexity.value}"
    )
    return complexity, derivation
