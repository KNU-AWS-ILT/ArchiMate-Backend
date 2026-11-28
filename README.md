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

> API 명세는 **초안**입니다. 프론트 명세(A~D)와 고도화 아키텍처 문서를 기준으로 맞췄고, 백엔드 2인·프론트 합의 후 확정합니다. (#6, #7)
> JSON 필드명은 **camelCase**, 열거형 값은 소문자 snake_case, 에러 코드는 UPPER_SNAKE_CASE입니다.

### 1. 가이드형 요구사항 인터뷰 `AI 생성`

* **기능 소개**

> 빈 프롬프트 대신 고정 질문 7개(서비스 유형, 동시 사용자, 월 예산, AWS 경험, 저장 데이터, 트래픽 패턴, 가용성) + 자유 서술로 요구사항을 빠짐없이 수집합니다.
> 질문 목록은 고정이라 API 없이 프론트가 [질문 예시](contracts/examples/interview_questions.json)를 상수로 씁니다. 답변은 3안 생성 요청의 `answers`로 보내고 `Requirements` 스키마로 검증합니다.

### 2. AI 아키텍처 3안 생성 + 운영 절차 `AI 생성`

* **기능 소개**

> LLM이 요구사항을 **최소비용 / 균형 / 확장형** 3가지 아키텍처 JSON으로 변환하고, 안마다 **배포·운영 절차**까지 만듭니다. 3안은 병렬로 생성하고 **끝난 안부터** 저장해 화면에 보여 줍니다.
> - 출력은 JSON 스키마로 강제하고, 스키마·의미 검증(노드 연결, 그룹 계층, 비용 계산 필수 항목)에 실패하면 오류 내용을 피드백으로 붙여 자동 재시도합니다.
> - 노드마다 역할과 **선택 이유 한 줄**을 담고, 안마다 운영 난이도(쉬움·보통·어려움), 장단점, 성장 로드맵(사용자 1만 → 10만 명)을 제공합니다.
> - LLM은 **가격을 추정하지 않고** 월 사용량(`usage`)만 출력합니다. 금액은 비용 계산 기능이 공식 단가로 계산합니다.
> - 운영 절차는 초보자 기준으로 콘솔 경로를 안내하고, 예산 알람(AWS Budgets, 달러 금액 또는 예산 0원이면 제로 지출 예산)을 가장 먼저 설정하게 합니다.
> - 요청 수·전송량은 요구사항에서 계산한 기준값을 세 안이 함께 써서 월 비용을 공정하게 비교합니다. 인스턴스 유형은 단가가 있는 것만 고릅니다.
> - 자유 서술이 선택 항목과 충돌하면(예: "동시 접속 10만 명" vs "100명 이하") 선택 항목 기준으로 설계하고 그 사실을 단점에 알립니다. 예산 0원이면 요금이 생기는 서비스를 설명에 밝힙니다.
> - 전체 기한(기본 150초) 안에서 일시 오류(429·5xx)는 지수 백오프로 재시도하고, 한도 초과·과부하가 계속되면 대체 모델로 한 번 더 시도합니다.

* **API 명세**

> `POST /api/v1/designs` → `202 { jobId }` ([요청 예시](contracts/examples/create_design_request.json), [응답 예시](contracts/examples/design_job_accepted.json))
> `GET /api/v1/designs/jobs/{jobId}` → 진행 상태 + 끝난 안 ([생성 중](contracts/examples/design_job_running.json), [완료](contracts/examples/design_job_completed.json), [일부 실패](contracts/examples/design_job_partial_failed.json), [전부 실패](contracts/examples/design_job_failed.json), [스키마](contracts/design_job.schema.json))
> `GET /api/v1/designs/{designId}/guide` → 운영 절차 ([응답 예시](contracts/examples/operations_guide_minimal_cost.json), [스키마](contracts/operations_guide.schema.json))

### 3. 노드 설명 `AI 생성`

* **기능 소개**

> 다이어그램에서 노드를 클릭하면 소형 모델이 **왜 이 서비스인가 / 대안 / 주의점**을 사용자 상황에 맞춰 설명합니다. 설명은 캐시해 두 번째부터 바로 보여 줍니다. 설명에는 요금 숫자를 넣지 않습니다.

* **API 명세**

> `GET /api/v1/designs/{designId}/nodes/{nodeId}/explanation` ([응답 예시](contracts/examples/node_explanation_app_db.json), [스키마](contracts/node_explanation.schema.json))

### 4. 월 비용 계산·Free Tier 판별 `API·비용`

* **기능 소개**

> 3안의 노드별 사용량에 AWS 공식 단가를 적용해 월 예상 비용과 Free Tier 적용 여부를 계산합니다. (계산 대상 11종: Lambda, S3, DynamoDB, RDS, EC2, CloudFront, API Gateway, ALB, NAT Gateway, ElastiCache, ECS Fargate. 그 밖의 서비스는 금액 없이 사유만 표시합니다)
> 단가는 매일 새벽 Price List API에서 동기화해 두고, 3안이 생성될 때마다 계산해 `monthlyCost`에 채웁니다.

* **API 명세**

> 별도 API 없음. 3안 조회 응답의 `designs[].monthlyCost`, 노드 설명의 `monthlyCost`로 내려갑니다. (형식: Issue #10 C안)
> - 화면 표시는 KRW(원 단위), USD 원금액·환율·기준일을 함께 제공. `total`은 `items[].amount` 합과 항상 같습니다.
> - 금액은 '항상 무료' 한도(Lambda·CloudFront·DynamoDB)를 뺀 값이고, 가입 크레딧은 반영하지 않습니다.
> - 계산 대상이 아닌 서비스·사용량이 없는 노드는 `amount: null` + `note`. 예산 초과는 `overBudget`.
> - 개발 중(로컬·Railway) 단가는 `src/archimate/pricing/snapshots/`의 서울 리전 스냅샷을 씁니다. 스냅샷의 단가 33개는 AWS 공개 Price List와 같은 값입니다(2026-11-25 확인).
> - AWS 배포에서는 단가 동기화 Lambda가 매일 Price List Query API(`pricing:GetProducts`)로 단가를 받아 prices 테이블에 씁니다(#55). 하나라도 못 받거나 스냅샷과 3배 넘게 다르면 쓰지 않고 전날 단가를 그대로 둡니다. 무료 한도 수량과 환율은 스냅샷 값입니다.

### 공통: 에러 응답

> `{ code, message, retryable, requestId }` ([예시](contracts/examples/error_response.json), [스키마](contracts/error_response.schema.json))
> 3안 생성은 비동기라 실패가 HTTP 에러가 아니라 작업 상태 `failed`와 `error`로 전달됩니다.

---

## 🖼️ 전체 서비스 UI

> {ArchiMate 메인 페이지 화면(1875x1044)}

---

## ⚙️ 시스템 아키텍처 (백엔드 파트)

> {시스템 아키텍처 백엔드 부분 이미지(1875x1044)}

> 고도화 아키텍처 문서 기준 (서울 리전, VPC 없음)

```text
사용자 → CloudFront ─┬─ /      → S3 (React)
                     └─ /api/* → API Gateway (Cognito 토큰 확인)
                                   ├─ 설계 생성 Lambda ─→ Bedrock 설계용 모델 (3안 병렬 + 운영 절차)
                                   │     접수 → jobId 응답 → 비동기 생성 → 월 비용 계산 → DynamoDB(designs)
                                   └─ 노드 설명 Lambda ─→ Bedrock 소형 모델 → DynamoDB(explanations, 30일 캐시)
EventBridge(매일) → 단가 동기화 Lambda → Price List API → DynamoDB(prices)
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
│   ├── schemas/                  🤝 팀 계약: 인터뷰 답변, 아키텍처 JSON, 운영 절차, 노드 설명, API 응답 (Pydantic)
│   ├── llm/                      🧠 AI 생성 로직 (실행 환경과 무관)
│   │   ├── __init__.py               공개 API (다른 영역은 여기만 import)
│   │   ├── interview.py              고정 인터뷰 질문, 답변 검증
│   │   ├── prompts/                  시스템 프롬프트(.md), 프롬프트 조립
│   │   ├── providers/                LLM 제공자: mock / gemini / claude(bedrock) + 데모 고정 응답
│   │   ├── pipeline.py               3안 병렬 생성(+운영 절차), 노드 설명, 검증 → 피드백 → 재시도, 기한·일시 오류 재시도·대체 모델
│   │   ├── validation.py             의미 검증 (노드 연결, 그룹 계층, 필수 사용량·기본값, 인스턴스 유형, 예산 안내)
│   │   ├── conflicts.py              자유 서술과 선택 항목의 충돌 찾기
│   │   ├── features.py               요구사항에 적힌 기능의 필수 서비스 찾기 (로그인 → Cognito 등)
│   │   └── settings.py · errors.py   LLM 설정, 예외
│   ├── api/                      💰 생성 작업·결과 조회, 3안 비교 가공, 로컬 FastAPI 앱
│   ├── pricing/                  💰 월 비용·Free Tier 계산, 단가 스냅샷(snapshots/)
│   ├── storage/                  💰 DynamoDB 데이터 모델·리포지토리 (designs, prices, explanations)
│   └── handlers/                 Lambda 진입점 (이벤트 파싱·응답 변환만)
│       ├── generate.py               🧠 설계 생성 Lambda (접수·비동기 생성·조회)
│       ├── explain.py                🧠 노드 설명 Lambda
│       └── sync_prices.py            💰 단가 동기화 Lambda
├── contracts/                    🤝 프론트 공유용 JSON Schema + examples/ (자동 생성)
├── tests/                        llm/ 🧠 · api/ pricing/ storage/ 💰 · handlers/ · contracts/ 🤝
├── scripts/
│   ├── llm/try_generate.py       🧠 생성 파이프라인 수동 실행
│   ├── export_contracts.py       🤝 contracts/ 생성 (JSON Schema, 예시)
│   └── export_openapi.py         🤝 contracts/openapi.json 생성
├── evals/llm/                    🧠 대표 시나리오 5종 평가
├── .github/CODEOWNERS            영역별 자동 리뷰어 지정
├── .env.example                  환경 변수 템플릿
└── pyproject.toml                의존성·린트·테스트 설정
```

**의존 방향**: `handlers → (llm | api | pricing | storage) → schemas`
`llm`은 다른 영역을 import 하지 않습니다. 예외로 단가 스냅샷(`pricing.load_price_book`)만 읽어 인스턴스 유형 목록과 환율(예산 알람 달러 금액)에 씁니다. 생성 결과 저장처럼 여러 영역을 조합하는 일은 `handlers`·`api`에서 합니다.

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

> API 서버를 띄우면 Swagger UI(<http://localhost:8000/docs>)에서 요청을 바로 보내 볼 수 있습니다.
> 저장소는 메모리라서 서버를 끄면 데이터가 사라집니다. 개발 중 사용자는 `X-User-Id` 헤더로 구분합니다(없으면 `local-user`).

```shell
uvicorn archimate.api.app:app --reload --app-dir src     # API 서버 (Swagger: /docs, ReDoc: /redoc)
python scripts/export_openapi.py                           # 라우트·스키마 변경 시 contracts/openapi.json 갱신
```

AI 생성 파이프라인만 단독으로 실행할 수도 있습니다. (결과는 `out/`에 저장)

```shell
python scripts/llm/try_generate.py                         # 데모 시나리오 3안 + 운영 절차
python scripts/llm/try_generate.py --tier minimal_cost     # 1안만 생성 (무료 한도 절약)
python scripts/llm/try_generate.py --explain app_db        # 첫 번째 안의 노드 설명까지
python evals/llm/run_eval.py --runs 3 --interval 20        # 대표 시나리오 5종 평가 → out/eval/ (성공률·응답 시간)
python scripts/export_contracts.py                         # 스키마 변경 시 contracts/ 갱신
python scripts/verify_prices.py                            # 단가 스냅샷을 AWS 공개 Price List와 대조 (인증 불필요)
python evals/pricing/run_cost_check.py                     # 비용 오차 ±20% 검증 표 (Calculator 값은 evals/pricing/cases.json에 입력)
ruff check . && ruff format . && pytest                    # 린트·포맷·테스트
```

### 개발 서버 배포 (Railway)

> 프론트가 실제 API로 연동해 볼 개발 서버입니다. 모킹 LLM이라 비용이 들지 않습니다. 결제 수단 등록·유료 플랜 업그레이드는 하지 않습니다(코딩 컨벤션 1장).

`railway.json`에 시작 명령(`uvicorn ... --port $PORT`)과 헬스 체크(`/api/v1/health`)가 들어 있어서, Railway에서 이 저장소를 연결하면 별도 설정 없이 배포됩니다.
Railway 빌더(Railpack)는 `pyproject.toml`만으로는 의존성을 설치하지 않아서, 루트의 `requirements.txt`로 설치합니다. `pyproject.toml`의 기본 의존성을 바꾸면 `requirements.txt`도 같이 고칩니다(테스트가 확인합니다). (#58)

1. Railway에서 New Project → Deploy from GitHub repo → `ArchiMate-Backend` 선택
2. Variables에 아래 값을 넣습니다. API 키는 넣지 않습니다.

   | 변수 | 값 |
   | :--- | :--- |
   | `LLM_PROVIDER` | `mock` |
   | `API_CORS_ORIGINS` | `["http://localhost:3000","https://프론트-배포-주소"]` |
   | `MOCK_DELAY_S` | `2` (프론트가 진행 화면을 볼 수 있게 queued → running → 안이 하나씩 채워지는 흐름을 재현) |

3. Settings → Networking → Generate Domain 으로 주소를 만들고 `https://<주소>/docs`에서 확인합니다.

주의: 저장소가 메모리라서 재배포·재시작하면 작업 결과가 사라집니다. 사용자는 `X-User-Id` 헤더로 구분합니다(Cognito는 AWS 배포에서 적용).

생성 작업이 같은 프로세스의 메모리에서 돌기 때문에 워커 수(--workers)나 인스턴스 수를 2개 이상으로 늘리면 조회가 404가 될 수 있습니다. 개발 서버는 워커 1개, 인스턴스 1개로 유지하세요.

### Lambda 배포 패키지 (AWS)

Lambda(Python 3.12, ARM64)는 `src/`만으로는 실행되지 않고 의존성이 함께 있어야 합니다. 배포용 최소 의존성은 `requirements-lambda.txt`에 있고(`google-genai`·`uvicorn`처럼 Lambda에서 쓰지 않는 패키지는 뺐습니다), 아래 스크립트가 의존성과 `archimate/`를 한 폴더에 모읍니다.

```shell
python scripts/build_lambda.py                       # build/lambda (ARM64용, 약 42MB)
python scripts/build_lambda.py --platform host        # 이 PC용 (import 확인용)
```

- 인프라(CDK)는 `build/lambda` 폴더를 그대로 Lambda 코드로 묶으면 됩니다. 핸들러는 `archimate.handlers.generate.handler` 등 그대로입니다.
- ARM64용 wheel(`pydantic-core`, `jiter`)을 받을 뿐 실행하지 않아서 어떤 PC에서도 만들 수 있습니다.
- 코드에서 `requirements-lambda.txt`에 없는 외부 패키지를 모듈 맨 위에서 import하면 `tests/test_lambda_requirements.py`가 실패합니다.
- 버전은 `constraints-lambda.txt`가 하위 의존성까지 `==`로 고정합니다. 배포일에 빌드해도 확인한 버전이 들어가고, 의존성을 올릴 때는 새로 빌드해 확인한 뒤 이 파일도 같이 고칩니다.

---

## 📋 API 구성

> 초안 — 백엔드 2인·프론트 합의 후 확정 (#7). 요청·응답 모양은 `src/archimate/schemas/api.py`

| HTTP Method | 경로 | 주요 기능 |
| :---: | :--- | :--- |
| `POST` | `/api/v1/designs` | 3안 생성 요청 → `202 { jobId }` |
| `GET` | `/api/v1/designs/jobs/{jobId}` | 생성 진행 상태와 끝난 안 조회 (2~3초마다) |
| `GET` | `/api/v1/designs/{designId}/guide` | 선택한 안의 배포·운영 절차 |
| `GET` | `/api/v1/designs/{designId}/nodes/{nodeId}/explanation` | 노드 설명 (왜 이 서비스 / 대안 / 주의점) |

---

## 🗄️ 데이터베이스 구조

> Amazon DynamoDB (온디맨드). 키·테이블 환경 변수·TTL은 [#15](https://github.com/KNU-AWS-ILT/ArchiMate-Backend/issues/15)에서 확정했고, 구현은 `src/archimate/storage/dynamodb.py`

| 테이블 | 키 | 내용 | 보존 |
| :--- | :--- | :--- | :--- |
| `designs` | jobId | 인터뷰 답변, 진행 상태, 3안 JSON(운영 절차 포함), 월 비용. `userId` 속성으로 소유자 확인 | TTL 30일 (`expiresAt`) |
| `prices` | service / regionUsageType (값 예: `ap-northeast-2#Requests`) | 서비스별 단가 | 매일 덮어쓰기 (TTL 없음) |
| `explanations` | designId / nodeId | 노드 설명 캐시 | TTL 30일 (`expiresAt`) |

---

## 🔐 환경 변수 설정

> 전체 목록과 설명은 [`.env.example`](.env.example) 참고. AI 생성 파트는 `LLM_` 접두사를 씁니다.

| 환경 변수 | 설명 |
| :--- | :--- |
| `LLM_PROVIDER` | `mock`(기본, 고정 응답) / `gemini`(개발용 무료) / `bedrock`(Claude, 유료 플랜) / `bedrock_converse`(Claude 외 Bedrock 모델, 무료 플랜 크레딧) / `anthropic` |
| `LLM_MODEL` | 설계용 모델 ID. 비우면 제공자별 기본값 (gemini: `gemini-3.5-flash-lite`, 품질 확인은 `gemini-3.8-flash` / bedrock_converse: `openai.gpt-oss-120b-1:0`, #15 시험에서 데모 시나리오 3안을 통과한 유일한 모델) |
| `LLM_EXPLAIN_MODEL` | 노드 설명용 소형 모델 ID. 비우면 제공자별 기본값 (gemini: `gemini-3.5-flash-lite` / bedrock_converse: `amazon.nova-pro-v1:0`) |
| `LLM_MAX_VALIDATION_RETRIES` | 검증 실패 시 재시도 횟수 (기본 2) |
| `LLM_REQUEST_TIMEOUT_S` | LLM 1회 호출 타임아웃(초, 기본 50) |
| `LLM_GENERATION_DEADLINE_S` | 설계 생성 전체 기한(초, 기본 150). 기한 안에 못 끝낸 안은 `LLM_TIMEOUT` 실패 |
| `LLM_EXPLAIN_DEADLINE_S` | 노드 설명 전체 기한(초, 기본 20) |
| `LLM_TIERS` | 생성할 안, 쉼표 구분 (기본 3안 전체). 1안으로 줄일 때 `minimal_cost` |
| `LLM_MAX_TRANSIENT_RETRIES` | 일시 오류(429·5xx·네트워크) 재시도 횟수 (기본 3, 지수 백오프) |
| `LLM_RETRY_BASE_DELAY_S` | 재시도 첫 대기 시간(초, 기본 1). 1초 → 2초 → 4초 + 무작위 |
| `LLM_FALLBACK_MODEL` | 한도 초과(429)·과부하(503, 529)가 재시도 후에도 계속될 때 1회 시도할 대체 모델 (기본 없음, 설계·운영 절차만) |
| `LLM_MAX_OUTPUT_TOKENS` | LLM 1회 출력 최대 토큰 (기본 16000) |
| `LLM_CLAUDE_EFFORT` | Claude 추론 깊이 `low` / `medium` / `high` (기본 low) |
| `LLM_PROMPT_CACHE` | Claude 경로에서 시스템 프롬프트 캐시 (기본 `false`. Bedrock 실제 호출로 확인한 뒤 켠다) |
| `GEMINI_API_KEY` | Google AI Studio API 키 (`LLM_PROVIDER=gemini`일 때) |
| `AWS_REGION` | Bedrock 리전 (기본 `ap-northeast-2` 서울). Lambda에서는 직접 넣지 않고 Lambda가 넣는 값(배포 계정은 `ap-southeast-2` 시드니)을 쓴다 |
| `API_DOCS_ENABLED` | Swagger UI·`/openapi.json` 노출 여부 (기본 `true`, AWS 배포에서는 `false` 권장) |
| `DESIGNS_TABLE` | (AWS Lambda) 설계 결과 테이블 이름. 설계 생성·노드 설명 Lambda |
| `EXPLANATIONS_TABLE` | (AWS Lambda) 노드 설명 캐시 테이블 이름. 노드 설명 Lambda |
| `PRICES_TABLE` | (AWS Lambda) 단가 테이블 이름. 단가 동기화 Lambda가 쓰고 설계 생성 Lambda가 읽음. 없으면 패키지 스냅샷 단가 |
| `PRICE_FETCHER` | (단가 동기화 Lambda) `price_list`(기본, AWS Price List API, IAM `pricing:GetProducts` 필요) / `snapshot`(패키지 스냅샷을 그대로 채움) (#55) |
| `API_CORS_ORIGINS` | 브라우저 호출을 허용할 프론트 주소 JSON 배열 (기본 `["http://localhost:3000"]`) |
| `MOCK_DELAY_S` | 모킹 지연(초, 기본 0). 예: `2`면 queued 2초 → running → 안이 2초 간격으로 하나씩 채워짐. `LLM_PROVIDER=mock`일 때만 적용 |
| `API_DAILY_GENERATION_LIMIT` | 사용자별 하루 설계 생성 횟수 (기본 `5`, `0`이면 제한 없음). 한국 시간 자정에 초기화. 넘으면 `429 RATE_LIMITED`. `LLM_PROVIDER=mock`일 때는 적용하지 않음 (#52) |

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
> - 개발 기간에는 Mock과 Gemini 무료 티어만 사용합니다. AWS 배포는 `LLM_PROVIDER=bedrock`(모델 ID는 비우면 `anthropic.claude-opus-5-5`, 설명용 `anthropic.claude-haiku-5-5`)이고, Lambda 역할에는 `bedrock-mantle:CreateInference` 권한이 필요합니다. (#15)
> - 배포 계정(무료 플랜, 시드니)은 Claude를 쓸 수 없어서 `LLM_PROVIDER=bedrock_converse`로 Qwen·Amazon Nova 등을 씁니다. Converse API의 도구 호출로 JSON을 강제하고, Lambda 역할에는 해당 모델의 `bedrock:InvokeModel` 권한이 필요합니다. 호출 비용은 크레딧에서 빠지고, 크레딧을 다 쓰면 계정이 닫히므로 `API_DAILY_GENERATION_LIMIT`과 함께 씁니다. (#15)
> - Gemini 무료 티어 입력은 Google 제품 개선에 쓰일 수 있으므로 **실사용자 데이터를 넣지 않습니다.**
> - `.env`와 API 키는 절대 커밋하지 않습니다.
