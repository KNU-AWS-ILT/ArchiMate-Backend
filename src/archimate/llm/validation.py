"""스키마만으로는 못 잡는 '의미' 검증. 에러 메시지는 LLM 재시도 피드백으로 그대로 쓰인다."""

import re

from archimate.schemas import REQUIRED_USAGE_FIELDS, USER_NODE_ID, DesignDraft, OperationsGuide

_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


def check_design(d: DesignDraft) -> list[str]:
    errors: list[str] = []

    if not d.nodes:
        return ["nodes가 비어 있습니다."]

    group_ids = [g.id for g in d.groups]
    node_ids = [n.id for n in d.nodes]

    # id 형식·중복
    for i in group_ids + node_ids:
        if not _ID_PATTERN.match(i):
            errors.append(f"id '{i}'는 영문 소문자·숫자·밑줄만 쓸 수 있습니다.")
    for dup in {i for i in group_ids + node_ids if (group_ids + node_ids).count(i) > 1}:
        errors.append(f"id '{dup}'가 중복됩니다.")
    if USER_NODE_ID in node_ids:
        errors.append(f"'{USER_NODE_ID}'는 예약 id라 노드로 만들 수 없습니다. edges에서만 쓰세요.")

    # 그룹 계층
    group_set = set(group_ids)
    parents = {g.id: g.parent_id for g in d.groups}
    for g in d.groups:
        if g.parent_id is not None and g.parent_id not in group_set:
            errors.append(f"그룹 '{g.id}'의 parent_id '{g.parent_id}'가 존재하지 않습니다.")
    for start in group_ids:
        seen, cur = set(), start
        while cur is not None and cur in parents:
            if cur in seen:
                errors.append(f"그룹 '{start}'의 parent_id가 순환합니다.")
                break
            seen.add(cur)
            cur = parents[cur]

    # 노드 소속
    for n in d.nodes:
        if n.group_id is not None and n.group_id not in group_set:
            errors.append(f"노드 '{n.id}'의 group_id '{n.group_id}'가 존재하지 않습니다.")

    # 엣지
    endpoints = set(node_ids) | {USER_NODE_ID}
    connected: set[str] = set()
    for e in d.edges:
        for end in (e.source, e.target):
            if end not in endpoints:
                errors.append(f"edge {e.source}→{e.target}: '{end}'는 없는 노드입니다.")
        if e.source == e.target:
            errors.append(f"edge {e.source}→{e.target}: 자기 자신을 가리킵니다.")
        connected.update((e.source, e.target))
    for n in d.nodes:
        if n.id not in connected:
            errors.append(f"노드 '{n.id}'가 어떤 edge에도 연결되지 않았습니다.")
    if USER_NODE_ID not in connected:
        errors.append("사용자 진입점이 없습니다. source가 'user'인 edge를 추가하세요.")

    # 비용 계산 필수 사용량
    for n in d.nodes:
        for field in REQUIRED_USAGE_FIELDS.get(n.service, ()):
            if getattr(n.usage, field) is None:
                errors.append(f"노드 '{n.id}'({n.service.value})의 usage.{field}가 필요합니다.")

    return errors


def check_operations_guide(g: OperationsGuide) -> list[str]:
    errors: list[str] = []
    if not g.deployment_steps:
        errors.append("deployment_steps가 비어 있습니다.")
    orders = [s.order for s in g.deployment_steps]
    if orders != list(range(1, len(orders) + 1)):
        errors.append("deployment_steps의 order는 1부터 빠짐없이 증가해야 합니다.")
    if not g.cost_alarms:
        errors.append("cost_alarms가 비어 있습니다. 최소 1개의 예산 알람이 필요합니다.")
    return errors
