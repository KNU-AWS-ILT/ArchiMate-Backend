"""스키마만으로는 못 잡는 '의미' 검증. 에러 메시지는 LLM 재시도 피드백으로 그대로 쓰인다.

메시지의 필드명은 LLM이 보는 JSON 이름(camelCase)으로 쓴다.
"""

import re
from functools import cache

from archimate.schemas import (
    REQUIRED_USAGE_FIELDS,
    SERVICE_INFO,
    Architecture,
    AwsService,
    DesignDraft,
    ExplanationDraft,
    OperationsGuide,
    Usage,
)

_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
# 요금 숫자: "$3", "4만 원", "1,000원", "5 USD" 등
_PRICE_PATTERN = re.compile(r"\$\s*\d|\d[\d,.]*\s*(만|천|억)?\s*(원|달러|USD|KRW)")


@cache
def allowed_instance_types() -> dict[AwsService, tuple[str, ...]]:
    """단가 스냅샷에 있는 인스턴스 유형 (#5). 이 밖의 유형은 월 비용을 계산할 수 없다."""
    from archimate.pricing import load_price_book  # 단가 데이터는 비용 모듈이 관리한다

    p = load_price_book()
    return {
        AwsService.RDS: tuple(p.rds.instance_hourly),
        AwsService.EC2: tuple(p.ec2.instance_hourly),
        AwsService.ELASTICACHE: tuple(p.elasticache.instance_hourly),
    }


# ── 예산 0원 안내 (#5) ─────────────────────────────────
# 기간 제한 없는 '항상 무료' 한도로 0원 운영이 가능한 서비스. 나머지는 쓰는 만큼 요금이 생긴다.
# (S3·API Gateway의 12개월 무료는 2025년 7월 이후 가입 계정에 없다)
ALWAYS_FREE_SERVICES = frozenset(
    {
        AwsService.USERS,
        AwsService.LAMBDA,
        AwsService.DYNAMODB,
        AwsService.CLOUDFRONT,
        AwsService.COGNITO,
        AwsService.SQS,
        AwsService.SNS,
        AwsService.EVENTBRIDGE,
        AwsService.CLOUDWATCH,
    }
)
# 켜 두기만 해도 시간 요금이 나오는 서비스 (예산 0원을 확실히 넘는다)
FIXED_COST_SERVICES = frozenset(
    {
        AwsService.RDS,
        AwsService.EC2,
        AwsService.ALB,
        AwsService.NAT_GATEWAY,
        AwsService.ELASTICACHE,
        AwsService.ECS_FARGATE,
    }
)
ZERO_BUDGET_NOTE = "사용량에 따라 소액 요금이 생길 수 있어요"
ZERO_BUDGET_FIXED_NOTE = "켜 두는 동안 고정 요금이 나와 예산 0원을 넘어요"
# 예산 0원인데 요금이 생기는 서비스를 쓰면서 0원을 단정하는 표현 (평가에서 실제로 나온 문구)
_ZERO_COST_CLAIM = re.compile(
    r"완벽히\s*부합|모두\s*충당|추가\s*비용[이은]?\s*(전혀\s*)?(발생하지|들지)\s*않|"
    r"비용\s*부담[이은]?\s*없|0원\s*(내에서|범위\s*내|안에서|으로)"
)


def _names(services: set[AwsService]) -> str:
    return "·".join(SERVICE_INFO[s][0] for s in AwsService if s in services)


def zero_budget_sentence(services: set[AwsService]) -> str | None:
    """예산 0원 설계의 budgetNote에 들어가야 할 문장. 항상 무료 서비스만 쓰면 None."""
    fixed = services & FIXED_COST_SERVICES
    if fixed:
        return f"{_names(fixed)}는 {ZERO_BUDGET_FIXED_NOTE}."
    paid = services - ALWAYS_FREE_SERVICES
    if paid:
        return f"{_names(paid)}는 항상 무료가 아니라 {ZERO_BUDGET_NOTE}."
    return None


def check_budget_note(d: DesignDraft, monthly_budget: int) -> list[str]:
    """예산 0원이면 요금이 생기는 서비스가 있을 때 budgetNote에 정해진 안내를 넣게 한다.

    설계 설명이 "추가 비용 없음"이라고 단정하는데 화면의 월 비용은 0원이 아니어서 앞뒤가 맞지 않았다.
    고정 요금 서비스(RDS 등)가 있으면 예산을 넘는다고, 사용량 과금 서비스만 있으면 소액 요금이라고 쓴다.
    """
    if monthly_budget != 0:
        return []
    sentence = zero_budget_sentence({n.service_type for n in d.architecture.nodes})
    if sentence is None:
        return []
    errors = []
    key = ZERO_BUDGET_FIXED_NOTE if ZERO_BUDGET_FIXED_NOTE in sentence else ZERO_BUDGET_NOTE
    if key not in d.budget_note:
        errors.append(f"예산이 0원이라 budgetNote에 다음 문장을 넣으세요: '{sentence}'")
    if m := _ZERO_COST_CLAIM.search(d.budget_note):
        errors.append(f"budgetNote의 '{m.group(0)}'처럼 0원을 단정하는 표현을 빼세요.")
    return errors


def _usage_alias(field: str) -> str:
    return Usage.model_fields[field].alias or field


# 값이 정해져 있거나 무난한 기본값이 있는 필수 사용량은 LLM이 빠뜨려도 채운다 (#5).
# 모델이 검증 피드백을 받고도 다른 필드를 또 빠뜨려 안 전체가 실패하는 일을 줄인다.
# instanceType처럼 설계 판단이 필요한 값은 채우지 않고 검증으로 다시 요청한다.
ALWAYS_ON_HOURS = 730
USAGE_DEFAULTS: dict[AwsService, dict[str, object]] = {
    AwsService.RDS: {
        "hours_per_month": ALWAYS_ON_HOURS,
        "instance_count": 1,
        "multi_az": False,
        "storage_gb": 20,  # RDS 최소 할당 용량
    },
    AwsService.EC2: {"hours_per_month": ALWAYS_ON_HOURS, "instance_count": 1},
    AwsService.ALB: {"hours_per_month": ALWAYS_ON_HOURS},
    # NAT 비용은 대부분 시간 요금이라 처리량은 작은 기본값으로 둔다 (모델이 피드백 후에도 자주 빠뜨림)
    AwsService.NAT_GATEWAY: {"hours_per_month": ALWAYS_ON_HOURS, "data_transfer_out_gb": 10},
    AwsService.ELASTICACHE: {"hours_per_month": ALWAYS_ON_HOURS, "instance_count": 1},
}


def fill_usage_defaults(d: DesignDraft) -> list[str]:
    """빠진 필수 사용량을 USAGE_DEFAULTS로 채우고, 채운 항목을 'nodeId.field' 목록으로 돌려준다."""
    filled: list[str] = []
    for n in d.architecture.nodes:
        defaults = USAGE_DEFAULTS.get(n.service_type)
        if not defaults:
            continue
        if n.usage is None:
            n.usage = Usage()
        for field, value in defaults.items():
            if getattr(n.usage, field) is None:
                setattr(n.usage, field, value)
                filled.append(f"{n.id}.{_usage_alias(field)}")
    return filled


def check_design(d: DesignDraft) -> list[str]:
    errors: list[str] = []
    arch = d.architecture

    if not arch.nodes:
        return ["architecture.nodes가 비어 있습니다."]

    group_ids = [g.id for g in arch.groups]
    node_ids = [n.id for n in arch.nodes]

    # id 형식·중복
    all_ids = group_ids + node_ids
    for i in all_ids:
        if not _ID_PATTERN.match(i):
            errors.append(f"id '{i}'는 영문 소문자·숫자·밑줄만 쓸 수 있습니다.")
    for dup in {i for i in all_ids if all_ids.count(i) > 1}:
        errors.append(f"id '{dup}'가 중복됩니다.")

    # 사용자 노드: 정확히 1개, 그룹 밖
    users = [n for n in arch.nodes if n.service_type == AwsService.USERS]
    if len(users) != 1:
        errors.append(
            f"serviceType이 'users'인 사용자 노드가 정확히 1개 있어야 합니다(현재 {len(users)}개)."
        )
    for u in users:
        if u.group_id is not None:
            errors.append(f"사용자 노드 '{u.id}'의 groupId는 null이어야 합니다.")

    # 그룹 계층
    group_set = set(group_ids)
    parents = {g.id: g.parent_id for g in arch.groups}
    for g in arch.groups:
        if g.parent_id is not None and g.parent_id not in group_set:
            errors.append(f"그룹 '{g.id}'의 parentId '{g.parent_id}'가 존재하지 않습니다.")
    for start in group_ids:
        seen: set[str] = set()
        cur: str | None = start
        while cur is not None and cur in parents:
            if cur in seen:
                errors.append(f"그룹 '{start}'의 parentId가 순환합니다.")
                break
            seen.add(cur)
            cur = parents[cur]

    # 노드 소속
    for n in arch.nodes:
        if n.group_id is not None and n.group_id not in group_set:
            errors.append(f"노드 '{n.id}'의 groupId '{n.group_id}'가 존재하지 않습니다.")

    # 엣지
    node_set = set(node_ids)
    connected: set[str] = set()
    for e in arch.edges:
        for end in (e.from_, e.to):
            if end not in node_set:
                errors.append(f"edge {e.from_}→{e.to}: '{end}'는 없는 노드입니다.")
        if e.from_ == e.to:
            errors.append(f"edge {e.from_}→{e.to}: 자기 자신을 가리킵니다.")
        connected.update((e.from_, e.to))
    for n in arch.nodes:
        if n.id not in connected:
            errors.append(f"노드 '{n.id}'가 어떤 edge에도 연결되지 않았습니다.")
    for u in users:
        if not any(e.from_ == u.id for e in arch.edges):
            errors.append(f"사용자 진입점이 없습니다. from이 '{u.id}'인 edge를 추가하세요.")

    # 비용 계산 필수 사용량
    for n in arch.nodes:
        for field in REQUIRED_USAGE_FIELDS.get(n.service_type, ()):
            if n.usage is None or getattr(n.usage, field) is None:
                errors.append(
                    f"노드 '{n.id}'({n.service_type.value})의 usage.{_usage_alias(field)}가 필요합니다."
                )

    # 인스턴스 유형은 단가가 있는 것만 (없으면 월 비용이 비어 합계가 작게 나온다)
    allowed = allowed_instance_types()
    for n in arch.nodes:
        choices = allowed.get(n.service_type)
        itype = n.usage.instance_type if n.usage else None
        if choices and itype is not None and itype not in choices:
            errors.append(
                f"노드 '{n.id}'({n.service_type.value})의 instanceType '{itype}'은 쓸 수 없습니다. "
                f"다음 중에서 고르세요: {', '.join(choices)}"
            )

    return errors


def check_operations_guide(g: OperationsGuide, arch: Architecture | None = None) -> list[str]:
    """arch를 주면 절차의 nodeIds가 그 설계에 있는 노드인지도 확인한다."""
    errors: list[str] = []
    if not g.phases:
        errors.append("phases가 비어 있습니다.")

    keys = [p.key for p in g.phases]
    step_ids = [s.id for p in g.phases for s in p.steps]
    for i in keys + step_ids:
        if not _ID_PATTERN.match(i):
            errors.append(f"key/id '{i}'는 영문 소문자·숫자·밑줄만 쓸 수 있습니다.")
    for dup in sorted({k for k in keys if keys.count(k) > 1}):
        errors.append(f"단계 key '{dup}'가 중복됩니다.")
    for dup in sorted({i for i in step_ids if step_ids.count(i) > 1}):
        errors.append(f"절차 id '{dup}'가 중복됩니다.")
    for p in g.phases:
        if not p.steps:
            errors.append(f"단계 '{p.key}'에 steps가 없습니다.")

    if arch is not None:
        node_set = {n.id for n in arch.nodes}
        for p in g.phases:
            for s in p.steps:
                for nid in s.node_ids:
                    if nid not in node_set:
                        errors.append(f"절차 '{s.id}'의 nodeIds에 없는 노드 '{nid}'가 있습니다.")

    texts = [" ".join([s.title, s.desc, *s.todo]) for p in g.phases for s in p.steps]
    if not any("예산" in t or "비용" in t for t in texts):
        errors.append("비용·예산 알람을 설정하는 절차가 없습니다. 최소 1개 추가하세요.")

    checklist_keys = {c.key for c in g.checklists}
    for required in ("cost", "incident"):
        if required not in checklist_keys:
            errors.append(f"checklists에 key가 '{required}'인 체크리스트가 필요합니다.")
    return errors


def check_explanation(e: ExplanationDraft, service: AwsService) -> list[str]:
    errors: list[str] = []
    if not e.alternatives:
        errors.append("alternatives가 비어 있습니다. 대안을 1~2개 쓰세요.")
    for a in e.alternatives:
        if a.service_type in (service, AwsService.USERS):
            errors.append(
                f"대안 '{a.service_type.value}'는 쓸 수 없습니다. 다른 서비스를 고르세요."
            )
    if not e.cautions:
        errors.append("cautions가 비어 있습니다.")

    texts = [e.reason, *e.cautions, *(a.comparison for a in e.alternatives)]
    for t in texts:
        if _PRICE_PATTERN.search(t):
            errors.append(
                f"요금 숫자를 쓰면 안 됩니다: '{t[:40]}…' (금액은 화면에서 따로 채웁니다)"
            )
    return errors
