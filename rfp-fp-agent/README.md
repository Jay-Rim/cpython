# RFP FP Estimation Agent — 구축계획 및 Rule Engine

국내 SI 사업의 초기 RFP 로부터 기능점수(FP)를 1차 자동 산정하고, FP 전문가가
검토·확정하는 내부 도구의 **구축계획서**와 **동작하는 FP Rule Engine** 이다.

## 무엇이 들어 있나

| 경로 | 내용 |
|---|---|
| [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) | **구축계획서 본문** — Executive Summary, 프로세스 분석, 국내 기준, 오픈소스 평가, 아키텍처, 데이터 모델, MVP, Pilot KPI, 리스크, 6주 개발계획 |
| [`docs/KR_FP_RULES.md`](docs/KR_FP_RULES.md) | 「SW사업 대가산정 가이드」 원문 표 발췌 (구현 대조용) |
| [`fp_engine/`](fp_engine/) | **결정적 FP Rule Engine** — 복잡도 매트릭스, 간이법/정통법, 보정계수, 개발비, 정합성 lint |
| [`rfp_pipeline/`](rfp_pipeline/) | **문서 파이프라인** — PDF/PPTX/XLSX/DOCX 파싱, 원문 위치 보존, 결정적 청킹, LLM 계약, Evidence 검증, Rule Engine 입력 변환 |
| [`schemas/`](schemas/) | LLM Structured Output JSON Schema |
| [`tests/`](tests/) | 107개 테스트. 공식 가이드 예제를 **2개 개정판 모두** 1원 단위로 재현 |

## 핵심 설계 원칙

> **LLM 은 "무엇이 있는가"를 말하고, Rule Engine 은 "그것이 얼마인가"를 말한다.**
> 숫자가 나오는 곳에 LLM 이 있으면 설계 오류다.

`fp_engine` 은 LLM 을 호출하지 않으며, LLM 출력 스키마에는 `fp`·`complexity`·`weight` 필드가 존재하지 않는다.

## 검증

```bash
python3 -m pytest tests/ -q     # 107 passed
```

## RFP 문서 파이프라인

설치:

```bash
python -m pip install -r requirements.txt
```

최소 검토 UI까지 실행하려면:

```bash
python -m pip install -r requirements-ui.txt
streamlit run streamlit_app.py
```

UI는 문서를 외부로 전송하지 않는다. 문서를 로컬에서 파싱하고 사내 표준 LLM이
생성한 JSON을 업로드받아 근거 검증, 승인·수정·제외, 확정/잠정 Early FP 확인을
지원한다. SQLite 위치는 `RFP_FP_LEDGER` 환경변수로 지정할 수 있다.

LLM 없이 파싱 결과와 사내 LLM 전달용 청크를 확인할 수 있다.

```bash
python -m rfp_pipeline ./sample.pptx -o parsed.json
```

지원 입력은 `.pdf`, `.docx`, `.pptx`, `.xlsx`이다. 구형 `.doc/.ppt/.xls`는
LibreOffice로 OOXML 형식으로 변환해야 한다. PDF 텍스트가 없거나 PPT 슬라이드가
이미지·도식만 포함하면 파서가 내용을 추측하지 않고 `warnings`에 남긴다.

사내 표준 LLM 코드는 다음 계약만 구현한다. 인증, 재시도, 모델 선택과 네트워크
정책은 이 저장소의 책임이 아니다.

```python
from rfp_pipeline import analyze_document

class CompanyLLMExtractor:
    def extract(self, chunk, *, json_schema):
        # 사내 표준 코드 호출. 반드시 chunk.document_id/chunk.id를 그대로 반환한다.
        return company_llm.structured_output(
            input=chunk.to_llm_payload(),
            schema=json_schema,
        )

pipeline = analyze_document("RFP.xlsx", CompanyLLMExtractor())

# 허위 인용 후보는 functions에서 제외되고 evidence_issues에 남는다.
assert not pipeline.evidence_issues

# AI 후보는 사람 승인 전까지 잠정 FP다.
from fp_engine import Method, calculate
early_fp = calculate(pipeline.functions, Method.SIMPLE)
print(early_fp.provisional_fp)
```

검토 결과와 변경 전후 값은 SQLite에 저장할 수 있다.

```python
from rfp_pipeline import Ledger

with Ledger("fp-ledger.sqlite3") as ledger:
    ledger.save_extraction(pipeline.document, pipeline.raw_extractions[0])
    ledger.review_function(
        pipeline.document.id,
        "FC-001",
        action="APPROVE",
        reviewer="fp-expert",
        reason="원문과 기능 경계 확인 완료",
    )
    approved = ledger.list_functions(pipeline.document.id)
```

`APPROVE`, `MODIFY`, `EXCLUDE`의 변경 전후 payload와 검토자·사유·시각이
append-only `review` 이력에 남는다. 재분석으로 후보가 갱신돼도 기존 사람의
검토 상태와 review 이력은 삭제하지 않는다.

각 원문 블록에는 안정적인 `block_id`가 부여된다. 위치는 PDF 쪽, PPTX 슬라이드,
XLSX 시트·셀 범위, DOCX 문서 블록·절로 저장된다. LLM 인용은 Unicode/공백만
정규화한 뒤 실제 블록에 존재하는지 검사하며, 의미상 유사하다는 이유만으로
통과시키지 않는다.

공식 가이드 2.1.6 적용사례(SCM 예제, 85 FP)를 **두 개정판 모두** 재현한다:

| 개정판 | 단가 | 보정후 개발원가 | 소프트웨어 개발비 |
|---|---|---|---|
| 2020년판 | 553,114원 | 53,173,990원 | **68,467,488원** |
| 2025년판 | 605,784원 | 58,237,457원 | **74,796,821원** |

두 사례를 동시에 만족하는 반올림 규칙은 하나뿐이다: **보정계수를 순차적으로 곱하며 매 단계 원 단위 반올림(ROUND_HALF_EVEN).**

## 빠른 사용

```python
from fp_engine import (
    FPFunction, FunctionType, Method, ReviewStatus,
    calculate, calculate_cost, validate,
)

funcs = [
    FPFunction("F1", "고객정보", FunctionType.ILF, ReviewStatus.AI_PROPOSED),
    FPFunction("F2", "고객등록", FunctionType.EI, ReviewStatus.AI_PROPOSED),
    FPFunction("F3", "고객조회", FunctionType.EQ, ReviewStatus.AI_PROPOSED),
]
result = calculate(funcs, Method.SIMPLE)      # 간이법 (RFP 단계 권장)
print(result.total_fp)                        # 7.5 + 4.0 + 3.9 = 15.4
print(result.confirmed_fp)                    # 0.0 (아직 사람 미승인)
print(result.provisional_fp)                  # 15.4

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
