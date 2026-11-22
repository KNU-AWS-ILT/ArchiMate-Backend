"""1안에 대한 배포·운영 절차 — 프론트 명세 C(`GET /designs/{designId}/guide`)와 같은 모양.

3안을 생성할 때 안마다 함께 만들어 저장하고, C는 저장된 값을 조회만 한다(고도화 문서 5장).
초보자 대상이므로 AWS CLI 대신 콘솔 경로로 안내한다.
"""

from pydantic import Field, field_validator

from archimate.schemas.base import CamelModel


class GuideStep(CamelModel):
    id: str = Field(
        description="절차 id. 영문 소문자·숫자·밑줄, 안 안에서 고유 (완료 체크 저장 키)"
    )
    title: str
    desc: str = Field(description="무엇을 왜 하는지 1~2문장")
    node_ids: list[str] = Field(description="캔버스에서 강조할 노드 id")
    todo: list[str] = Field(description="따라 할 일 2~5개. 초보자가 그대로 따라 할 수 있게")
    console_path: str | None = Field(
        default=None, description="AWS 콘솔 경로, 예: 'Lambda > 함수 > 함수 생성'"
    )
    warn: str | None = Field(default=None, description="주의 문구 (없으면 null)")

    @field_validator("console_path", "warn", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        # LLM이 "없음"을 빈 문자열로 내도 프론트에는 항상 null로 보낸다 (#13)
        if isinstance(v, str):
            return v.strip() or None
        return v


class GuidePhase(CamelModel):
    key: str = Field(description="단계 id. 영문 소문자·숫자·밑줄")
    title: str = Field(description="단계 이름, 예: '저장소·데이터베이스'")
    sub: str = Field(description="보조 문구, 예: '1단계'")
    intro: str = Field(description="단계 소개 한 줄")
    steps: list[GuideStep]


class Checklist(CamelModel):
    key: str = Field(description="'cost'(비용 이상 대응) 또는 'incident'(장애 대응)")
    title: str
    items: list[str]


class OperationsGuide(CamelModel):
    phases: list[GuidePhase] = Field(
        description="배포 순서대로. 마지막 단계는 운영(비용 알람 포함)"
    )
    checklists: list[Checklist]
