"""스키마만으로는 못 잡는 '의미' 검증. 에러 메시지는 LLM 재시도 피드백으로 그대로 쓰인다.

메시지의 필드명은 LLM이 보는 JSON 이름(camelCase)으로 쓴다.
"""

import re

from archimate.schemas import (
    REQUIRED_USAGE_FIELDS,
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


def _usage_alias(field: str) -> str:
    return Usage.model_fields[field].alias or field


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
    for dup in {i for i in step_ids if step_ids.count(i) > 1}:
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
