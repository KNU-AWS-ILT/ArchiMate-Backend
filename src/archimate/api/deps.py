"""라우트 공통 의존성."""

from typing import Annotated

from fastapi import Depends, Header, Request

from archimate.api.errors import ApiError
from archimate.api.runner import JobRunner
from archimate.schemas import ErrorCode
from archimate.storage import DesignRepository

DEV_USER_ID = "local-user"


def get_user_id(
    request: Request,
    x_user_id: Annotated[
        str | None,
        Header(
            description="로컬 개발 전용 임시 사용자 id. Lambda(API Gateway)에서는 무시하고 Cognito sub를 쓴다",
            max_length=128,
        ),
    ] = None,
) -> str:
    # Lambda(Mangum): API Gateway Cognito Authorizer가 검증한 claims의 sub만 신뢰한다.
    # 헤더는 클라이언트가 바꿔 보낼 수 있으므로 이 경로에서는 읽지 않는다.
    event = request.scope.get("aws.event")
    if isinstance(event, dict):
        claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ApiError(401, ErrorCode.UNAUTHORIZED, "로그인이 필요해요.")
        return sub
    # 로컬·Railway 개발용
    return x_user_id or DEV_USER_ID


def get_repo(request: Request) -> DesignRepository:
    return request.app.state.repo


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


UserId = Annotated[str, Depends(get_user_id)]
Repo = Annotated[DesignRepository, Depends(get_repo)]
Runner = Annotated[JobRunner, Depends(get_runner)]
