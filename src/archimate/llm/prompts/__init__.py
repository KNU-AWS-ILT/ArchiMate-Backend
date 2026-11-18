"""프롬프트 조립.

- 고정 지시(system)는 .md 파일로 관리 → PM·팀원도 쉽게 읽고 고칠 수 있다.
- 요청마다 바뀌는 내용(요구사항, tier)은 user 메시지에 XML 태그로 넣는다.
  (system을 고정해 두면 Claude 프롬프트 캐싱에도 유리하다)
"""

from functools import cache
from importlib import resources

from archimate.llm.interview import option_label
from archimate.schemas import Design, Requirements, Tier

TIER_GUIDES: dict[Tier, str] = {
    Tier.MINIMAL_COST: (
        "최소비용안. 트래픽이 없을 때 비용이 0에 가까운 서버리스·Free Tier 중심 구성. "
        "VPC·NAT Gateway·상시 실행 인스턴스는 꼭 필요할 때만 씁니다. 운영 난이도는 가장 낮아야 합니다."
    ),
    Tier.BALANCED: (
        "균형안. 예산 안에서 안정성과 개발 편의(예: 관계형 DB, 관리형 인증)를 더한 구성. "
        "최소비용안보다 약간의 고정 비용을 감수하되 예산을 넘지 않게 합니다."
    ),
    Tier.SCALABLE: (
        "확장형안. 사용자 10만 명까지 구조 변경 없이 늘릴 수 있고 가용 영역 장애에 견디는 구성. "
        "예산을 넘을 수 있으며, 그 경우 budget_note에 분명히 적습니다."
    ),
}


@cache
def _load(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text("utf-8")


def design_system_prompt() -> str:
    return _load("design_system.md")


def operations_system_prompt() -> str:
    return _load("operations_system.md")


def render_requirements(req: Requirements) -> str:
    lines = [
        f"- 서비스 유형: {option_label('service_type', req.service_type)}",
        f"- 동시 사용자: {option_label('concurrent_users', req.concurrent_users)}",
        f"- 월 예산: {req.monthly_budget_krw:,}원",
        f"- AWS 경험: {option_label('team_experience', req.team_experience)}",
        "- 저장 데이터: " + ", ".join(option_label("data_storage", d) for d in req.data_storage),
        f"- 트래픽 패턴: {option_label('traffic_pattern', req.traffic_pattern)}",
        f"- 가용성 요구: {option_label('availability', req.availability)}",
    ]
    body = "\n".join(lines)
    if req.free_text:
        # 자유 서술은 사용자 입력이므로 태그로 감싸 지시문과 구분한다
        body += f"\n- 자유 서술:\n<user_description>\n{req.free_text}\n</user_description>"
    return body


def design_user_prompt(req: Requirements, tier: Tier) -> str:
    return (
        f"<requirements>\n{render_requirements(req)}\n</requirements>\n\n"
        f"<tier>{tier.value}</tier>\n"
        f"<tier_guide>{TIER_GUIDES[tier]}</tier_guide>\n\n"
        "위 요구사항으로 지정된 안 하나를 스키마에 맞춰 설계하세요."
    )


def operations_user_prompt(req: Requirements, design: Design) -> str:
    return (
        f"<requirements>\n{render_requirements(req)}\n</requirements>\n\n"
        f"<selected_design>\n{design.model_dump_json(exclude_none=True)}\n</selected_design>\n\n"
        "사용자가 위 안을 선택했습니다. 이 안의 배포·운영 절차를 스키마에 맞춰 작성하세요."
    )


def retry_feedback(errors: list[str]) -> str:
    joined = "\n".join(f"- {e}" for e in errors[:20])
    return (
        "직전 응답에 아래 문제가 있습니다. 문제를 모두 고친 전체 JSON을 다시 출력하세요.\n"
        f"<errors>\n{joined}\n</errors>"
    )
