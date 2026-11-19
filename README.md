# 🏗️ ArchiMate, "나의 첫 간편 클라우드 서비스"

> 혼자서도 관리할 수 있는 서비스로 바꿔 주는 AI 클라우드 설계 플랫폼입니다!

몇 가지 질문에 답하면 팀 예산·트래픽·역량에 맞는 **AWS 아키텍처 3안(최소비용 / 균형 / 확장형)**, **월 예상 비용**, **배포·운영 절차**를 한국어로 제공합니다.
이 레포는 ArchiMate의 **백엔드**이며, 프론트엔드와 인프라(AWS CDK)는 별도 레포에서 관리합니다.

## 🎥 배포·시연 영상 링크

| 구분 | URL |
| :---: | :--- |
| **프론트엔드** | - |
| **백엔드** | - |
| **인프라** | - |

---

## ⚙️ 백엔드 핵심 기능 (Key Feature)

> API 명세는 **초안**입니다. 백엔드 2인 합의 후 확정합니다.

### 1. 가이드형 요구사항 인터뷰 `AI 생성`

* **기능 소개**

> 빈 프롬프트 대신 고정 질문 7개(서비스 유형, 동시 사용자, 월 예산, AWS 경험, 저장 데이터, 트래픽 패턴, 가용성) + 자유 서술로 요구사항을 빠짐없이 수집합니다.
> 답변은 `Requirements` 스키마로 검증된 뒤 3안 생성의 입력이 됩니다.

* **API 명세**

> `GET /api/v1/interview/questions` → 질문 목록 ([예시](contracts/examples/interview_questions.json))
> `POST /api/v1/interviews` → 답변 제출 ([요청 예시](contracts/examples/interview_answers.json), [스키마](contracts/requirements.schema.json))

### 2. AI 아키텍처 3안 생성 `AI 생성`

* **기능 소개**

> LLM이 요구사항을 **최소비용 / 균형 / 확장형** 3가지 아키텍처 JSON으로 변환합니다. 3안은 병렬로 생성합니다.
> - 출력은 JSON 스키마로 강제하고, 스키마·의미 검증(노드 연결, 그룹 계층, 비용 계산 필수 항목)에 실패하면 오류 내용을 피드백으로 붙여 자동 재시도합니다.
> - 노드마다 **왜 이 서비스인가 / 대안 / 주의점**을 한국어로 담고, 안마다 운영 난이도(1~5)와 성장 단계 로드맵을 제공합니다.
> - LLM은 **가격을 추정하지 않고** 월 사용량(`usage`)만 출력합니다. 금액은 비용 계산 기능이 공식 단가로 계산합니다.
> - 일부 안만 실패하면 성공한 안을 반환하고, 실패한 안은 `failures`에 담습니다.

* **API 명세**

> `POST /api/v1/interviews/{interviewId}/designs` → 3안 생성 요청 (동기/비동기 방식 협의 중)
> `GET /api/v1/designs/{designId}` → 3안 결과 조회 ([응답 예시](contracts/examples/design_set.json), [스키마](contracts/design_set.schema.json))

### 3. 월 비용 계산·Free Tier 판별 `API·비용`

* **기능 소개**

> 3안의 노드별 사용량에 AWS 공식 단가를 적용해 월 예상 비용과 Free Tier 적용 여부를 계산합니다. (주요 서비스 8종: Lambda, S3, DynamoDB, RDS, EC2, CloudFront, API Gateway, ALB)

* **API 명세**

> 작성 예정

### 4. 배포·운영 절차 안내 `AI 생성`

* **기능 소개**

> 사용자가 고른 안에 대해 배포 순서, 비용 알람 설정, 장애 대응 체크리스트, 정기 점검 항목을 한국어로 생성합니다.
> 3안을 미리 모두 생성하지 않고 **선택한 안만** 생성해 대기 시간과 LLM 호출을 줄입니다.

* **API 명세**

> `POST /api/v1/designs/{designId}/operations-guide` → body `{ "tier": "minimal_cost" }` ([응답 예시](contracts/examples/operations_guide_minimal_cost.json), [스키마](contracts/operations_guide.schema.json))

---

## 🖼️ 전체 서비스 UI

> {ArchiMate 메인 페이지 화면(1875x1044)}

---

## ⚙️ 시스템 아키텍처 (백엔드 파트)

> {시스템 아키텍처 백엔드 부분 이미지(1875x1044)}

```text
사용자 → CloudFront + S3 (React)
       → API Gateway
            ├─ ④ 인터뷰·API Lambda ──────────→ DynamoDB (인터뷰·설계 결과)
            ├─ ⑤ 설계 생성 Lambda ──→ Amazon Bedrock (Claude)
            │        └─ JSON 스키마 검증·재시도 → DynamoDB
            └─ ⑦ 비용 계산 Lambda ──→ AWS Price List API (단가)
공통: CloudWatch 로그·알람 / 배포: GitHub Actions → AWS CDK (인프라 레포)
```

---

## ⛏️ 기술 스택

| 구분 | 기술 스택 |
| :---: | :--- |
| **프레임워크** | AWS Lambda (Python), FastAPI (로컬·중간 배포) |
| **프로그래밍 언어** | Python 3.12 |
| **데이터베이스** | Amazon DynamoDB |
| **ORM / ODM** | Pydantic v2 (스키마·검증), DynamoDB 접근 방식은 API·비용 파트에서 결정 |
| **인증 / 보안** | IAM 최소 권한, 시크릿은 환경 변수로 주입 (`.env` 커밋 금지) |
| **API 문서화** | JSON Schema + 예시 (`contracts/`), FastAPI OpenAPI |
| **배포 / 인프라** | API Gateway, Lambda, DynamoDB, CloudWatch / AWS CDK (별도 인프라 레포) |
| **기타** | LLM: Amazon Bedrock (Claude, 최종) · Google Gemini API (개발용 무료 티어) / 도구: pip, Ruff, pytest |

---

## 🏗️ 아키텍처 구조

> 한 패키지 안에서 **폴더 단위로 담당을 나누고**, 아키텍처 JSON 스키마(`schemas/`)를 경계로 두 사람이 독립적으로 개발합니다.
> 🧠 = 백엔드(AI 생성), 💰 = 백엔드(API·비용), 🤝 = 공동 (둘 다 리뷰)

```text
ArchiMate-Backend/
├── src/archimate/
│   ├── schemas/                  🤝 팀 계약: 인터뷰 답변, 아키텍처 JSON, 운영 절차 (Pydantic)
│   ├── llm/                      🧠 AI 생성 로직 (실행 환경과 무관)
│   │   ├── __init__.py               공개 API (다른 영역은 여기만 import)
│   │   ├── interview.py              고정 인터뷰 질문, 답변 검증
│   │   ├── prompts/                  시스템 프롬프트(.md), 프롬프트 조립
│   │   ├── providers/                LLM 제공자: mock / gemini / claude(bedrock) + 데모 고정 응답
│   │   ├── pipeline.py               3안 병렬 생성, 검증 → 피드백 → 재시도
│   │   ├── validation.py             의미 검증 (노드 연결, 그룹 계층, 필수 사용량)
│   │   └── settings.py · errors.py   LLM 설정, 예외
│   ├── api/                      💰 인터뷰 세션·결과 조회, 3안 비교 가공, 로컬 FastAPI 앱
│   ├── pricing/                  💰 Price List API 연동, 월 비용·Free Tier 계산
│   ├── storage/                  💰 DynamoDB 데이터 모델·리포지토리
│   └── handlers/                 Lambda 진입점 (이벤트 파싱·응답 변환만)
│       ├── interview_api.py          💰 ④ 인터뷰·API Lambda
│       ├── design_generation.py      🧠 ⑤ 설계 생성 Lambda
│       └── cost_calculation.py       💰 ⑦ 비용 계산 Lambda
├── contracts/                    🤝 프론트 공유용 JSON Schema + examples/ (자동 생성)
├── tests/                        llm/ 🧠 · api/ pricing/ storage/ 💰 · handlers/ · contracts/ 🤝
├── scripts/
│   ├── llm/try_generate.py       🧠 생성 파이프라인 수동 실행
│   └── export_contracts.py       🤝 contracts/ 생성
├── evals/llm/                    🧠 대표 시나리오 5종 평가
├── data/pricing/                 💰 AWS 가격 데이터
├── .github/CODEOWNERS            영역별 자동 리뷰어 지정
├── .env.example                  환경 변수 템플릿
└── pyproject.toml                의존성·린트·테스트 설정
```

**의존 방향**: `handlers → (llm | api | pricing | storage) → schemas`
`llm`은 다른 영역을 import 하지 않습니다. 생성 결과 저장처럼 여러 영역을 조합하는 일은 `handlers`에서 합니다.

---

## 🚀 로컬 실행 방법

### 1. 프로젝트 클론 및 환경 설정

> Python 3.12와 pip를 사용합니다. (`.python-version` 참고)

```shell
git clone https://github.com/KNU-AWS-ILT/ArchiMate-Backend.git
cd ArchiMate-Backend
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. 의존성 설치 및 환경 변수 설정

> 기본값 `LLM_PROVIDER=mock`은 고정 응답을 돌려주므로 API 키 없이 비용 0원으로 실행됩니다.
> 실제 생성을 보려면 [Google AI Studio](https://aistudio.google.com/apikey)에서 키를 발급받아 `GEMINI_API_KEY`를 넣고 `LLM_PROVIDER=gemini`로 바꿉니다.

```shell
pip install -e ".[dev]"
cp .env.example .env
```

### 3. 서버 실행

> API 서버는 API·비용 파트 구현 후 추가됩니다. 현재는 AI 생성 파이프라인을 단독으로 실행할 수 있습니다. (결과는 `out/`에 저장)

```shell
python scripts/llm/try_generate.py --ops                   # 데모 시나리오 3안 + 운영 절차
python scripts/llm/try_generate.py --tier minimal_cost     # 1안만 생성 (무료 한도 절약)
python scripts/export_contracts.py                         # 스키마 변경 시 contracts/ 갱신
ruff check . && ruff format . && pytest                    # 린트·포맷·테스트
```

---

## 📋 API 구성

> 초안 — 백엔드 2인 합의 후 확정

| HTTP Method | 경로 | 주요 기능 |
| :---: | :--- | :--- |
| `GET` | `/api/v1/interview/questions` | 가이드형 인터뷰 질문 목록 |
| `POST` | `/api/v1/interviews` | 인터뷰 답변 제출 (세션 생성) |
| `POST` | `/api/v1/interviews/{interviewId}/designs` | 3안 생성 요청 |
| `GET` | `/api/v1/designs/{designId}` | 3안 + 월 비용 결과 조회 |
| `POST` | `/api/v1/designs/{designId}/operations-guide` | 선택한 안의 배포·운영 절차 생성 |

---

## 🗄️ 데이터베이스 구조

> Amazon DynamoDB — API·비용 파트에서 설계 예정
> 저장 대상: 인터뷰 세션(답변), 설계 결과(3안 `DesignSet` + 월 비용), 생성한 운영 절차

---

## 🔐 환경 변수 설정

> 전체 목록과 설명은 [`.env.example`](.env.example) 참고. AI 생성 파트는 `LLM_` 접두사를 씁니다.

| 환경 변수 | 설명 |
| :--- | :--- |
| `LLM_PROVIDER` | `mock`(기본, 고정 응답) / `gemini`(개발용 무료) / `bedrock`(최종) / `anthropic` |
| `LLM_MODEL` | 사용할 모델 ID. 비우면 제공자별 기본값 (gemini: `gemini-3.8-flash`) |
| `LLM_MAX_VALIDATION_RETRIES` | 검증 실패 시 재시도 횟수 (기본 2) |
| `LLM_REQUEST_TIMEOUT_S` | LLM 1회 호출 타임아웃(초, 기본 50) |
| `LLM_CLAUDE_EFFORT` | Claude 추론 깊이 `low` / `medium` / `high` (기본 low) |
| `GEMINI_API_KEY` | Google AI Studio API 키 (`LLM_PROVIDER=gemini`일 때) |
| `AWS_REGION` | Bedrock 리전 (`LLM_PROVIDER=bedrock`일 때) |

---

## 📌 참고 사항

> **협업 규칙**
> - 자기 영역 폴더만 수정하고, 🤝 공동 영역(`schemas/`, `contracts/`, `pyproject.toml`, `.env.example`)은 두 사람 모두 리뷰합니다.
> - 스키마를 바꾸면 `scripts/export_contracts.py`를 실행해 `contracts/` 변경도 같은 PR에 포함하고 프론트에 공유합니다.
> - 의존성 추가는 별도 PR로 올립니다. (`pyproject.toml` 충돌 방지)
>
> **브랜치·커밋**
> - 브랜치: `feat/#이슈번호-설명`
> - 커밋: `type(scope): subject` — type은 `feat`, `fix`, `docs`, `refactor`, `test`, `chore` 등, scope는 영역
> - 예: `feat(llm): Add retry feedback`, `feat(pricing): Add Lambda cost calculation`
>
> **비용·데이터 주의**
> - 개발 기간에는 Mock과 Gemini 무료 티어만 사용합니다. (Bedrock은 이후 연결)
> - Gemini 무료 티어 입력은 Google 제품 개선에 쓰일 수 있으므로 **실사용자 데이터를 넣지 않습니다.**
> - `.env`와 API 키는 절대 커밋하지 않습니다.
