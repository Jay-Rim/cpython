# RFP FP Estimation Agent — 구축계획 및 Rule Engine

국내 SI 사업의 초기 RFP 로부터 기능점수(FP)를 1차 자동 산정하고, FP 전문가가
검토·확정하는 내부 도구의 **구축계획서**와 **동작하는 FP Rule Engine** 이다.

## 무엇이 들어 있나

| 경로 | 내용 |
|---|---|
| [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) | **구축계획서 본문** — Executive Summary, 프로세스 분석, 국내 기준, 오픈소스 평가, 아키텍처, 데이터 모델, MVP, Pilot KPI, 리스크, 6주 개발계획 |
| [`docs/KR_FP_RULES.md`](docs/KR_FP_RULES.md) | 「SW사업 대가산정 가이드」 원문 표 발췌 (구현 대조용) |
| [`fp_engine/`](fp_engine/) | **결정적 FP Rule Engine** — 복잡도 매트릭스, 간이법/정통법, 보정계수, 개발비, 정합성 lint |
| [`schemas/`](schemas/) | LLM Structured Output JSON Schema |
| [`tests/`](tests/) | 68개 테스트. 공식 가이드 예제를 **2개 개정판 모두** 1원 단위로 재현 |

## 핵심 설계 원칙

> **LLM 은 "무엇이 있는가"를 말하고, Rule Engine 은 "그것이 얼마인가"를 말한다.**
> 숫자가 나오는 곳에 LLM 이 있으면 설계 오류다.

`fp_engine` 은 LLM 을 호출하지 않으며, LLM 출력 스키마에는 `fp`·`complexity`·`weight` 필드가 존재하지 않는다.

## 검증

```bash
python3 -m pytest tests/ -q     # 68 passed
```

공식 가이드 2.1.6 적용사례(SCM 예제, 85 FP)를 **두 개정판 모두** 재현한다:

| 개정판 | 단가 | 보정후 개발원가 | 소프트웨어 개발비 |
|---|---|---|---|
| 2020년판 | 553,114원 | 53,173,990원 | **68,467,488원** |
| 2025년판 | 605,784원 | 58,237,457원 | **74,796,821원** |

두 사례를 동시에 만족하는 반올림 규칙은 하나뿐이다: **보정계수를 순차적으로 곱하며 매 단계 원 단위 반올림(ROUND_HALF_EVEN).**

## 빠른 사용

```python
from fp_engine import (
    FPFunction, FunctionType, Method, calculate, calculate_cost, validate,
)

funcs = [
    FPFunction("F1", "고객정보", FunctionType.ILF),
    FPFunction("F2", "고객등록", FunctionType.EI),
    FPFunction("F3", "고객조회", FunctionType.EQ),
]
result = calculate(funcs, Method.SIMPLE)      # 간이법 (RFP 단계 권장)
print(result.total_fp)                        # 7.5 + 4.0 + 3.9 = 15.4
print(result.fp_range)                        # (확정 FP, 확정+잠정 FP)

for finding in validate(funcs):               # 정합성 경고
    print(finding)

cost = calculate_cost(
    result.total_fp,
    edition="2025",                           # 기본값 없음 — 명시 필수
    complexity_levels={"연계복잡성": 3, "성능요구수준": 3, "운영환경호환성": 2, "보안성수준": 2},
)
print(cost.software_dev_cost, cost.derivations)
```

### 미확정 값은 확정 FP 에 섞이지 않는다

```python
from fp_engine import Certainty, Counted

Counted(20, Certainty.UNKNOWN, "지어낸 값")   # ValueError — 판단 불가는 값을 가질 수 없다

# 추정 카운트는 계산은 되지만 잠정 FP 로 분리된다
result.confirmed_fp     # 근거(MEASURED) 있는 값만
result.provisional_fp   # 추정(ESTIMATED)/간이법 대체분
result.unresolved_function_ids  # 정보 부족으로 아예 산정 못한 기능
```

## 상태 및 주의

이 엔진은 **prototype (공식 Excel 검증 대기)** 이다. "완료"가 아니다.

- ✅ 2020년판·2025년판 가이드 **원문 대조 완료** (매트릭스·가중치·보정계수 동일, 단가와 단계별 발주 구분만 변경)
- ❌ **발주기관 공식 Excel 산정 템플릿과는 미대조.** 반올림 규칙은 적용사례 2건에서 역산한 것이며, Excel 의 셀 단위 반올림과 다를 수 있다
- ❌ 사내 과거 사업 확정 산정서와 미대조 (Phase 0 골든셋)
- 개정판(`edition`)은 기본값 없이 필수 인자다. 단가는 매년 바뀌며 85FP 예제에서만 633만원 차이가 난다
