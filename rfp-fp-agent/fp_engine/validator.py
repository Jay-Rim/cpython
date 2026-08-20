"""FP 산정 결과 정합성 검증 (lint).

LLM 이 만들어낸 기능 목록에서 '사람 리뷰어가 실제로 잡아내는' 오류 패턴을
규칙으로 잡는다. 검증은 FP 숫자를 바꾸지 않는다 — 경고만 만들어 리뷰
큐에 올린다(원칙 4, 7).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from .rules import TERM_CLASSIFICATION_HINTS
from .types import Certainty, FPFunction, FunctionType

Severity = str  # "ERROR" | "WARN" | "INFO"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    function_ids: tuple[str, ...] = ()

    def __str__(self) -> str:  # pragma: no cover - 편의용
        return f"[{self.severity}] {self.code}: {self.message} {list(self.function_ids)}"


def _normalize(name: str) -> str:
    return re.sub(r"[\s\-_()·/]", "", name).lower()


def validate(functions: Sequence[FPFunction]) -> list[Finding]:
    findings: list[Finding] = []
    active = [f for f in functions if not f.excluded]

    findings += _check_duplicates(active)
    findings += _check_term_hints(active)
    findings += _check_counts(active)
    findings += _check_data_txn_balance(active)
    findings += _check_eq_vs_eo(active)
    findings += _check_orphan_ftr(active)
    return findings


def _check_duplicates(functions: Iterable[FPFunction]) -> list[Finding]:
    """동일 기능 중복 산정 / ILF·EIF 중복 등록."""
    out: list[Finding] = []
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for f in functions:
        seen[(_normalize(f.name), f.function_type.value)].append(f.id)
    for (name, ftype), ids in seen.items():
        if len(ids) > 1:
            out.append(
                Finding(
                    "DUP_FUNCTION", "ERROR",
                    f"동일 이름·유형({ftype})의 기능이 {len(ids)}건 중복 산정되었다: {name}",
                    tuple(ids),
                )
            )

    # 같은 논리파일이 ILF 와 EIF 로 동시에 잡힌 경우
    data_by_name: dict[str, set[str]] = defaultdict(set)
    for f in functions:
        if f.function_type.is_data_function:
            data_by_name[_normalize(f.name)].add(f.function_type.value)
    for name, types in data_by_name.items():
        if len(types) > 1:
            ids = tuple(f.id for f in functions if _normalize(f.name) == name)
            out.append(
                Finding(
                    "ILF_EIF_CONFLICT", "ERROR",
                    f"'{name}' 이 ILF 와 EIF 로 동시에 식별되었다. 유지 주체를 확인해야 한다.",
                    ids,
                )
            )
    return out


def _check_term_hints(functions: Iterable[FPFunction]) -> list[Finding]:
    """가이드 2.1.6 '단위프로세스 식별 권고 사례' 대조."""
    out: list[Finding] = []
    for f in functions:
        norm = _normalize(f.name)
        for term, hint in TERM_CLASSIFICATION_HINTS.items():
            if term not in norm:
                continue
            expected = hint.get("expected")
            if expected is None:
                out.append(
                    Finding(
                        "EXCLUDE_CANDIDATE", "WARN",
                        f"'{f.name}' 은 '{term}' 을 포함한다 → 가이드상 산정 제외 권고"
                        f"({hint.get('why', '')})",
                        (f.id,),
                    )
                )
            elif f.function_type.value in hint.get("not", []):
                out.append(
                    Finding(
                        "TERM_TYPE_MISMATCH", "WARN",
                        f"'{f.name}' 은 '{term}' 을 포함하므로 {expected} 산정이 타당하나 "
                        f"{f.function_type.value} 로 판정되었다",
                        (f.id,),
                    )
                )
    return out


def _check_counts(functions: Iterable[FPFunction]) -> list[Finding]:
    """카운트 누락/이상치. DET 과대추정은 복잡도를 직접 끌어올리므로 중요."""
    out: list[Finding] = []
    for f in functions:
        det, axis2 = f.sizing_counts
        axis_name = "RET" if f.function_type.is_data_function else "FTR"

        if not det.known or not axis2.known:
            out.append(
                Finding(
                    "MISSING_COUNT", "INFO",
                    f"{f.name}: DET/{axis_name} 미확인 → 정통법 불가, 간이법 대체 또는 확인 필요",
                    (f.id,),
                )
            )
        if det.certainty is Certainty.ESTIMATED or axis2.certainty is Certainty.ESTIMATED:
            out.append(
                Finding("ESTIMATED_COUNT", "INFO", f"{f.name}: 추정 카운트 포함 → 리뷰 대상", (f.id,))
            )
        if det.known and det.value is not None and det.value > 100:
            out.append(
                Finding(
                    "DET_OUTLIER", "WARN",
                    f"{f.name}: DET={det.value} 는 과대추정 가능성이 높다"
                    "(리터럴/네비게이션/페이지변수는 DET 제외)",
                    (f.id,),
                )
            )
        if not f.function_type.is_data_function and axis2.known and (axis2.value or 0) > 10:
            out.append(
                Finding("FTR_OUTLIER", "WARN", f"{f.name}: FTR={axis2.value} 는 단위프로세스 분해 누락 의심", (f.id,))
            )
        if f.function_type is FunctionType.EQ and axis2.known and axis2.value == 0:
            out.append(
                Finding("EQ_FTR_ZERO", "ERROR", f"{f.name}: EQ 의 FTR 은 최소 1 이어야 한다(표 3-16)", (f.id,))
            )
    return out


def _check_data_txn_balance(functions: Sequence[FPFunction]) -> list[Finding]:
    """ILF 인데 이를 유지하는 EI 가 하나도 없으면 ILF/EIF 오분류 가능성이 높다."""
    out: list[Finding] = []
    ilfs = [f for f in functions if f.function_type is FunctionType.ILF]
    ei_names = " ".join(_normalize(f.name) for f in functions if f.function_type is FunctionType.EI)
    for f in ilfs:
        stem = _normalize(f.name).replace("정보", "").replace("데이터", "")
        if stem and stem not in ei_names:
            out.append(
                Finding(
                    "ILF_WITHOUT_EI", "WARN",
                    f"ILF '{f.name}' 를 유지(등록/수정/삭제)하는 EI 가 식별되지 않았다 "
                    "→ 실제로는 EIF 이거나 트랜잭션 누락",
                    (f.id,),
                )
            )
    counts = Counter(f.function_type for f in functions)
    if counts[FunctionType.ILF] and not counts[FunctionType.EI]:
        out.append(Finding("NO_EI_AT_ALL", "ERROR", "ILF 는 있으나 EI 가 전혀 없다. 분해 실패 가능성.", ()))
    return out


def _check_eq_vs_eo(functions: Iterable[FPFunction]) -> list[Finding]:
    """EQ/EO 오분류: 집계·계산·파생데이터 어휘가 있으면 EO 여야 한다."""
    derived = ("집계", "통계", "산출", "계산", "합계", "평균", "차트", "그래프", "리포트", "보고서")
    out: list[Finding] = []
    for f in functions:
        if f.function_type is not FunctionType.EQ:
            continue
        hit = [w for w in derived if w in f.name]
        if hit:
            out.append(
                Finding(
                    "EQ_SHOULD_BE_EO", "WARN",
                    f"'{f.name}' 에 파생데이터 어휘{hit}가 있다 → EO 검토 필요"
                    "(EO는 계산/공식/파생데이터 포함)",
                    (f.id,),
                )
            )
    return out


def _check_orphan_ftr(functions: Sequence[FPFunction]) -> list[Finding]:
    """트랜잭션은 있는데 참조할 데이터기능이 하나도 없는 경우."""
    if not functions:
        return []
    has_data = any(f.function_type.is_data_function for f in functions)
    has_txn = any(not f.function_type.is_data_function for f in functions)
    if has_txn and not has_data:
        return [Finding("NO_DATA_FUNCTION", "ERROR", "트랜잭션 기능만 있고 데이터 기능(ILF/EIF)이 없다.", ())]
    return []
