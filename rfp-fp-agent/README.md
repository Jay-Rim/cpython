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
| [`tests/`](tests/) | 45개 테스트. 공식 가이드 예제를 1원 단위로 재현 |

## 핵심 설계 원칙

> **LLM 은 "무엇이 있는가"를 말하고, Rule Engine 은 "그것이 얼마인가"를 말한다.**
> 숫자가 나오는 곳에 LLM 이 있으면 설계 오류다.

`fp_engine` 은 LLM 을 호출하지 않으며, LLM 출력 스키마에는 `fp`·`complexity`·`weight` 필드가 존재하지 않는다.

## 검증

```bash
python3 -m pytest tests/ -q     # 45 passed
```

공식 가이드 2.1.6 적용사례(SCM 예제) 재현:
85 FP → 보정전 47,014,690원 → 보정후 53,173,990원 → **소프트웨어 개발비 68,467,488원** (공표값과 일치)

## 빠른 사용

```python
from fp_engine import FPFunction, FunctionType, Method, calculate, calculate_cost, validate

funcs = [
    FPFunction("F1", "고객정보", FunctionType.ILF),
    FPFunction("F2", "고객등록", FunctionType.EI),
    FPFunction("F3", "고객조회", FunctionType.EQ),
]
result = calculate(funcs, Method.SIMPLE)      # 간이법 (RFP 단계 권장)
print(result.total_fp)                        # 7.5 + 4.0 + 3.9 = 15.4

for finding in validate(funcs):               # 정합성 경고
    print(finding)

cost = calculate_cost(
    result.total_fp,
    complexity_levels={"연계복잡성": 3, "성능요구수준": 3, "운영환경호환성": 2, "보안성수준": 2},
)
print(cost.software_dev_cost, cost.derivations)
```

## 주의

- `fp_engine/rules.py` 의 수치는 **2020년 개정판** 원문 기준이다. 사내 도입 전 **최신 개정판(2025년) 대조가 필수**다.
- 반올림 규칙은 가이드에 명시가 없어 공식 예제에서 역산했다. 발주기관 Excel 과 대조해 확정해야 한다.
