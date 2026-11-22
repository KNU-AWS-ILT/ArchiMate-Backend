"""프롬프트 조립.

- 고정 지시(system)는 .md 파일로 관리 → PM·팀원도 쉽게 읽고 고칠 수 있다.
- 요청마다 바뀌는 내용(요구사항, tier)은 user 메시지에 XML 태그로 넣는다.
  (system을 고정해 두면 Claude 프롬프트 캐싱에도 유리하다)
"""

from functools import cache
from importlib import resources

from archimate.llm.conflicts import find_conflicts
from archimate.llm.interview import option_label
from archimate.schemas import SERVICE_INFO, AwsService, Design, Node, Requirements, Tier
from archimate.schemas.requirements import ServiceType, UserScale

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


# ── 사용량 기준값 (#5) ─────────────────────────────────
# 트래픽은 요구사항이 정하는 값이라 세 안이 같아야 월 비용 비교가 공정하다.
# 안마다 LLM이 따로 추정하면 같은 요구사항인데 요청 수가 10배씩 달라졌다(평가에서 확인).
# 그래서 기준값을 코드로 계산해 넣고, 세 안 모두 그대로 쓰게 한다. 값은 대략적인 가정이다.
PEAK_USERS: dict[UserScale, int] = {
    UserScale.UNDER_100: 100,
    UserScale.UNDER_1K: 1_000,
    UserScale.UNDER_10K: 10_000,
    UserScale.OVER_10K: 30_000,
}
DAILY_USERS_PER_PEAK = 5  # 하루 이용자 ≈ 동시 접속 최대치 × 5
API_CALLS_PER_USER = 20  # 이용자 1명이 하루에 부르는 API 수
CDN_REQUESTS_PER_API_CALL = 2  # 화면 파일(HTML·JS·이미지) 요청
AVG_RESPONSE_KB = 100  # CDN 요청 1건의 평균 전송량


def _num(n: int) -> str:
    if n >= 100_000_000:
        return f"약 {n / 100_000_000:g}억"
    if n >= 10_000:
        return f"약 {n / 10_000:,.0f}만"
    return f"약 {n:,}"


def usage_baseline(req: Requirements) -> str:
    """세 안이 함께 쓰는 월 사용량 기준값 (요구사항에서 계산)."""
    monthly_users = PEAK_USERS[req.concurrent_users] * DAILY_USERS_PER_PEAK * 30
    api = monthly_users * API_CALLS_PER_USER
    has_api = req.service_type != ServiceType.STATIC_SITE
    cdn = (api if has_api else monthly_users * API_CALLS_PER_USER) * CDN_REQUESTS_PER_API_CALL
    transfer_gb = max(1, round(cdn * AVG_RESPONSE_KB / 1024 / 1024))
    lines = []
    if has_api:
        lines.append(
            f"- 월 API 요청: {api:,}건 ({_num(api)}) → api_gateway·lambda의 monthlyRequests"
        )
        lines.append(f"- DynamoDB: monthlyReads {api:,}, monthlyWrites {api // 5:,} (읽기의 1/5)")
    lines.append(
        f"- 월 화면 요청(CDN): {cdn:,}건 ({_num(cdn)}), 전송 {transfer_gb:,}GB "
        "→ cloudfront의 monthlyRequests·dataTransferOutGb"
    )
    if req.service_type == ServiceType.AI_CHATBOT:
        lines.append("- 외부 AI API를 기다리는 lambda는 avgDurationMs를 5000 이상으로 잡습니다.")
    if req.service_type == ServiceType.DATA_BATCH:
        lines.append("- 주기 작업은 위 값 대신 실행 주기·처리량으로 추정합니다.")
    return "\n".join(lines)


@cache
def _load(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text("utf-8")


@cache
def design_system_prompt() -> str:
    # 인스턴스 유형 목록은 단가 스냅샷에서 만든다 (단가가 없는 유형을 고르면 월 비용이 비어서, #5)
    from archimate.llm.validation import allowed_instance_types

    lines = [f"  - {s.value}: {', '.join(t)}" for s, t in allowed_instance_types().items()]
    return (
        _load("design_system.md").rstrip()
        + "\n\n## 인스턴스 유형\n- instanceType은 아래 목록에서만 고릅니다. 목록에 없는 유형은 비용을 계산할 수 없습니다.\n"
        + "\n".join(lines)
        + "\n"
    )


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


def render_conflicts(req: Requirements) -> str:
    """코드가 찾은 자유 서술·선택 항목 충돌 (#5). 없으면 빈 문자열."""
    conflicts = find_conflicts(req)
    if not conflicts:
        return ""
    notes = "\n".join(f"- {c.note}" for c in conflicts)
    return (
        "<conflicts>\n보충 설명이 선택 항목과 다릅니다. 선택 항목 기준으로 설계하고, "
        f"cons 첫 항목에 아래 문장을 그대로 쓰세요.\n{notes}\n</conflicts>\n\n"
    )


def render_budget_rule(req: Requirements) -> str:
    """예산 0원이면 budgetNote에 넣을 문장 규칙 (#5). 설계 전이라 서비스별 두 가지 형식을 준다."""
    if req.monthly_budget != 0:
        return ""
    return (
        "<budget_rule>\n예산이 0원입니다. 설계에 쓴 서비스에 따라 budgetNote에 아래 문장을 넣으세요.\n"
        "- RDS·EC2·ALB·NAT Gateway·ElastiCache·ECS(Fargate)가 있으면: "
        '"{그 서비스 이름들}는 켜 두는 동안 고정 요금이 나와 예산 0원을 넘어요."\n'
        "- 위 서비스는 없고 S3·API Gateway·Route 53·Secrets Manager 등이 있으면: "
        '"{그 서비스 이름들}는 항상 무료가 아니라 사용량에 따라 소액 요금이 생길 수 있어요."\n'
        "- Lambda·DynamoDB·CloudFront·Cognito·SQS·SNS·EventBridge·CloudWatch만 쓰면 문장이 필요 없습니다.\n"
        "</budget_rule>\n\n"
    )


def design_user_prompt(req: Requirements, tier: Tier) -> str:
    return (
        f"<requirements>\n{render_requirements(req)}\n</requirements>\n\n"
        f"<usage_baseline>\n{usage_baseline(req)}\n</usage_baseline>\n\n"
        f"{render_conflicts(req)}"
        f"{render_budget_rule(req)}"
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
