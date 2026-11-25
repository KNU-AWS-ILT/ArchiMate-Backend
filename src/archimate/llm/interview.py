"""가이드형 인터뷰 질문(고정) — 질문 id는 Requirements의 JSON 필드명(camelCase)과 같다.

질문 목록은 API 없이 contracts/examples/interview_questions.json을 프론트가 상수로 쓴다(Issue #7 A).
프론트가 보낸 answers는 build_requirements()로 검증한다.
문구 수정은 PM과 협의(시나리오 5종과 맞물림).
"""

from enum import StrEnum
from typing import Any, Literal

from archimate.schemas.base import CamelModel
from archimate.schemas.requirements import (
    AvailabilityNeed,
    AwsExperience,
    DataStorageNeed,
    Requirements,
    ServiceType,
    TrafficPattern,
    UserScale,
)


class InterviewOption(CamelModel):
    value: str
    label: str
    description: str | None = None


class InterviewQuestion(CamelModel):
    id: str
    text: str
    help: str | None = None
    input_type: Literal["single", "multi", "number", "text"]
    options: list[InterviewOption] = []
    required: bool = True
    unit: str | None = None
    placeholder: str | None = None


def _opts(
    enum: type[StrEnum], labels: dict[StrEnum, tuple[str, str | None]]
) -> list[InterviewOption]:
    assert set(labels) == set(enum), f"{enum.__name__}의 모든 값에 라벨이 필요합니다"
    return [
        InterviewOption(value=v.value, label=lab, description=d) for v, (lab, d) in labels.items()
    ]


INTERVIEW_QUESTIONS: list[InterviewQuestion] = [
    InterviewQuestion(
        id="serviceType",
        text="어떤 서비스인가요?",
        input_type="single",
        options=_opts(
            ServiceType,
            {
                ServiceType.WEB_APP: ("웹 서비스", "화면과 API가 있는 일반 웹 서비스"),
                ServiceType.API_ONLY: ("앱/서비스용 API", "모바일 앱 등에서 쓰는 API 서버"),
                ServiceType.STATIC_SITE: ("정적 사이트", "소개 페이지, 랜딩 페이지, 포트폴리오"),
                ServiceType.AI_CHATBOT: ("AI 서비스", "LLM API를 호출하는 챗봇·생성형 서비스"),
                ServiceType.DATA_BATCH: (
                    "데이터 수집·처리",
                    "정해진 시간마다 데이터를 모으고 가공",
                ),
                ServiceType.OTHER: ("기타", "아래 자유 서술에 적어 주세요"),
            },
        ),
    ),
    InterviewQuestion(
        id="concurrentUsers",
        text="동시에 몇 명 정도 접속할까요?",
        help="가장 붐비는 순간을 기준으로 골라 주세요.",
        input_type="single",
        options=_opts(
            UserScale,
            {
                UserScale.UNDER_100: ("100명 이하", None),
                UserScale.UNDER_1K: ("101~1,000명", None),
                UserScale.UNDER_10K: ("1,001~1만 명", None),
                UserScale.OVER_10K: ("1만 명 초과", None),
            },
        ),
    ),
    InterviewQuestion(
        id="monthlyBudget",
        text="한 달 인프라 예산은 얼마인가요?",
        help="0원이면 AWS Free Tier 안에서만 설계합니다.",
        input_type="number",
        unit="KRW",
        placeholder="50000",
    ),
    InterviewQuestion(
        id="awsExperience",
        text="AWS를 얼마나 다뤄 봤나요?",
        input_type="single",
        options=_opts(
            AwsExperience,
            {
                AwsExperience.NONE: ("처음이에요", "AWS 콘솔에 들어가 본 적이 없음"),
                AwsExperience.BEGINNER: ("조금 써 봤어요", "튜토리얼로 몇 개 만들어 봄"),
                AwsExperience.INTERMEDIATE: ("배포해 봤어요", "직접 서비스를 올리고 운영해 봄"),
                AwsExperience.ADVANCED: ("익숙해요", "인프라를 담당해 본 경험이 있음"),
            },
        ),
    ),
    InterviewQuestion(
        id="dataStorage",
        text="어떤 데이터를 저장하나요?",
        help="해당하는 것을 모두 골라 주세요.",
        input_type="multi",
        options=_opts(
            DataStorageNeed,
            {
                DataStorageNeed.RELATIONAL: (
                    "회원·주문처럼 서로 연결된 데이터",
                    "표 여러 개를 엮어서 조회",
                ),
                DataStorageNeed.KEY_VALUE: ("단순한 목록·기록 데이터", "ID로 바로 찾는 데이터"),
                DataStorageNeed.FILE_STORAGE: ("이미지·파일 업로드", None),
                DataStorageNeed.NONE: ("저장할 데이터 없음", None),
            },
        ),
    ),
    InterviewQuestion(
        id="trafficPattern",
        required=False,  # 답하지 않으면 스키마 기본값 사용
        text="접속은 언제 몰리나요?",
        input_type="single",
        options=_opts(
            TrafficPattern,
            {
                TrafficPattern.STEADY: ("하루 종일 고르게", None),
                TrafficPattern.BUSINESS_HOURS: ("주로 낮 시간", None),
                TrafficPattern.SPIKY: ("특정 기간에 몰림", "이벤트·모집 기간 등"),
                TrafficPattern.UNKNOWN: ("잘 모르겠어요", None),
            },
        ),
    ),
    InterviewQuestion(
        id="availability",
        required=False,  # 답하지 않으면 스키마 기본값 사용
        text="서비스가 잠깐 멈추면 얼마나 곤란한가요?",
        input_type="single",
        options=_opts(
            AvailabilityNeed,
            {
                AvailabilityNeed.BEST_EFFORT: ("잠깐은 괜찮아요", "개인·동아리 프로젝트"),
                AvailabilityNeed.STANDARD: ("되도록 안 멈췄으면", "사용자가 있는 서비스"),
                AvailabilityNeed.HIGH: ("멈추면 안 돼요", "결제·예약 등 중요한 서비스"),
            },
        ),
    ),
    InterviewQuestion(
        id="freeText",
        text="서비스를 자유롭게 설명해 주세요",
        help="주요 기능, 사용하는 언어·프레임워크, 걱정되는 점 등",
        input_type="text",
        required=False,
        placeholder="예: Next.js로 만든 동아리 모집 사이트예요. 지원서 PDF 업로드가 있어요.",
    ),
]


assert [q.id for q in INTERVIEW_QUESTIONS] == [
    f.alias for f in Requirements.model_fields.values()
], "인터뷰 질문 id와 Requirements 필드가 어긋났습니다"


def build_requirements(answers: dict[str, Any]) -> Requirements:
    """프론트 answers(camelCase) → Requirements. 실패 시 pydantic.ValidationError (400 INVALID_REQUEST)."""
    return Requirements.model_validate(answers)


def option_label(question_id: str, value: str) -> str:
    """프롬프트에 넣을 한국어 라벨. 없으면 값 그대로."""
    for q in INTERVIEW_QUESTIONS:
        if q.id == question_id:
            for o in q.options:
                if o.value == value:
                    return o.label + (f" ({o.description})" if o.description else "")
    return value
