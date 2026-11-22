"""프롬프트 조립.

- 고정 지시(system)는 .md 파일로 관리 → PM·팀원도 쉽게 읽고 고칠 수 있다.
- 요청마다 바뀌는 내용(요구사항, tier)은 user 메시지에 XML 태그로 넣는다.
  (system을 고정해 두면 Claude 프롬프트 캐싱에도 유리하다)
"""

from functools import cache
from importlib import resources

from archimate.llm.interview import option_label
from archimate.schemas import SERVICE_INFO, AwsService, Design, Node, Requirements, Tier

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
        "예산을 넘을 수 있으며, 그 경우 budgetNote에 분명히 적습니다."
    ),
}


@cache
def _load(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text("utf-8")


def design_system_prompt() -> str:
    return _load("design_system.md")


def operations_system_prompt() -> str:
    return _load("operations_system.md")


def explain_system_prompt() -> str:
    return _load("explain_system.md")


def render_requirements(req: Requirements) -> str:
    lines = [
        f"- 서비스 유형: {option_label('serviceType', req.service_type)}",
        f"- 동시 사용자: {option_label('concurrentUsers', req.concurrent_users)}",
        f"- 월 예산: {req.monthly_budget:,}원",
        f"- AWS 경험: {option_label('awsExperience', req.aws_experience)}",
        "- 저장 데이터: " + ", ".join(option_label("dataStorage", d) for d in req.data_storage),
        f"- 트래픽 패턴: {option_label('trafficPattern', req.traffic_pattern)}",
        f"- 가용성 요구: {option_label('availability', req.availability)}",
    ]
    body = "\n".join(lines)
    if req.free_text:
        # 자유 서술은 사용자 입력이므로 태그로 감싸 지시문과 구분한다.
        # 사용자가 닫는 태그를 직접 써서 태그 밖으로 나가지 못하게 꺾쇠를 바꾼다.
        text = req.free_text.replace("<", "‹").replace(">", "›")
        body += f"\n- 자유 서술 (보충 설명):\n<user_description>\n{text}\n</user_description>"
    return body


def design_user_prompt(req: Requirements, tier: Tier) -> str:
    return (
        f"<requirements>\n{render_requirements(req)}\n</requirements>\n\n"
        f"<tier>{tier.value}</tier>\n"
        f"<tier_guide>{TIER_GUIDES[tier]}</tier_guide>\n\n"
        "위 요구사항으로 지정된 안 하나를 스키마에 맞춰 설계하세요."
    )


def operations_user_prompt(req: Requirements, design: Design) -> str:
    body = design.model_dump_json(exclude_none=True, exclude={"operations_guide"})
    return (
        f"<requirements>\n{render_requirements(req)}\n</requirements>\n\n"
        f"<tier>{design.tier.value}</tier>\n"
        f"<design>\n{body}\n</design>\n\n"
        "위 설계를 실제로 배포하고 운영하는 절차를 스키마에 맞춰 작성하세요."
    )


def explain_user_prompt(req: Requirements, design: Design, node: Node) -> str:
    others = ", ".join(
        f"{n.label}({n.service_type.value})"
        for n in design.architecture.nodes
        if n.id != node.id and n.service_type != AwsService.USERS
    )
    name = SERVICE_INFO[node.service_type][0]
    return (
        f"<requirements>\n{render_requirements(req)}\n</requirements>\n\n"
        f'<design tier="{design.tier.value}">{design.title} — {design.summary}\n'
        f"함께 쓰는 서비스: {others}</design>\n\n"
        f"<node>\n- 서비스: {name} ({node.service_type.value})\n- 이름: {node.label}\n"
        f"- 역할: {node.role}\n- 설계 때 적은 선택 이유: {node.reason}\n</node>\n\n"
        "이 노드에 대해 사용자가 '왜 이 서비스인가, 대안은 무엇인가'를 물었습니다. 스키마에 맞춰 답하세요."
    )


def retry_feedback(errors: list[str]) -> str:
    joined = "\n".join(f"- {e}" for e in errors[:20])
    return (
        "직전 응답에 아래 문제가 있습니다. 문제를 모두 고친 전체 JSON을 다시 출력하세요.\n"
        f"<errors>\n{joined}\n</errors>"
    )
