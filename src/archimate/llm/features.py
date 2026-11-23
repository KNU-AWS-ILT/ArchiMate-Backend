"""요구사항에서 꼭 필요한 서비스 찾기 (#5).

자유 서술의 "로그인", "회원가입"을 모델이 자주 빠뜨렸다 (평가: 스타트업 시나리오 20안 중 1안만 Cognito).
초보자는 인증을 직접 만들지 않고 관리형을 쓰는 게 원칙(기획서 4장)이라, 확실한 경우만 코드가 찾아
세 안 모두에 넣게 하고 검증으로 확인한다.
"""

import re
from dataclasses import dataclass

from archimate.schemas import SERVICE_INFO, AwsService, DesignDraft, Requirements
from archimate.schemas.requirements import DataStorageNeed

# "로그인 없이", "회원가입은 필요 없어요"처럼 부정하는 표현은 제외한다
_LOGIN = re.compile(r"(로그인|회원\s*가입)(?![^.。,]{0,6}(없이|없어|없음|않|안\s*해|필요\s*없))")


@dataclass(frozen=True)
class RequiredService:
    service: AwsService
    why: str  # 사용자가 쓴 표현 또는 선택 항목

    @property
    def note(self) -> str:
        return f"{self.why} → {SERVICE_INFO[self.service][0]}({self.service.value})"


def required_services(req: Requirements) -> list[RequiredService]:
    found: list[RequiredService] = []
    if m := _LOGIN.search(req.free_text or ""):
        found.append(RequiredService(AwsService.COGNITO, f"보충 설명의 '{m.group(1)}'"))
    if DataStorageNeed.FILE_STORAGE in req.data_storage:
        found.append(RequiredService(AwsService.S3, "선택한 '이미지·파일 업로드'"))
    return found


def check_required_services(d: DesignDraft, required: list[RequiredService]) -> list[str]:
    services = {n.service_type for n in d.architecture.nodes}
    return [
        f"{r.why} 기능이 있어서 {r.service.value} 노드가 필요합니다. 쓰는 쪽 노드와 연결해 넣으세요."
        for r in required
        if r.service not in services
    ]
