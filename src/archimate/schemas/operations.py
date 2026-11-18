"""선택한 1안에 대한 배포·운영 절차 (사용자가 안을 고른 뒤 별도 생성)."""

from enum import StrEnum

from pydantic import BaseModel, Field


class DeploymentStep(BaseModel):
    order: int = Field(ge=1)
    title: str
    description: str = Field(description="초보자가 따라 할 수 있게 구체적으로")
    console_path: str | None = Field(
        default=None, description="AWS 콘솔 경로, 예: 'Lambda > 함수 생성'"
    )


class CostAlarm(BaseModel):
    name: str
    condition: str = Field(description="예: '월 예상 비용이 4만 원을 넘으면'")
    how_to_set: str = Field(description="AWS Budgets/CloudWatch 설정 방법")


class IncidentItem(BaseModel):
    symptom: str = Field(description="예: '사이트가 502 에러를 낸다'")
    likely_cause: str
    check: str = Field(description="확인 방법")
    action: str = Field(description="조치 방법")


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class RoutineTask(BaseModel):
    frequency: Frequency
    task: str


class OperationsGuide(BaseModel):
    deployment_steps: list[DeploymentStep]
    cost_alarms: list[CostAlarm]
    incident_checklist: list[IncidentItem]
    routine_tasks: list[RoutineTask]
