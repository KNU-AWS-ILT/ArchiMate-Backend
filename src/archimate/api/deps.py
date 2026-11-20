"""라우트 공통 의존성."""

from typing import Annotated

from fastapi import Depends, Header, Request

from archimate.api.runner import JobRunner
from archimate.storage import DesignRepository

DEV_USER_ID = "local-user"


def get_user_id(
    x_user_id: Annotated[
        str | None,
        Header(
            description="개발용 임시 사용자 id. 최종(AWS)에서는 Cognito 토큰의 sub로 바뀐다",
            max_length=128,
        ),
    ] = None,
) -> str:
    # TODO(AWS): API Gateway Cognito Authorizer가 넘겨준 claims의 sub를 쓴다
    return x_user_id or DEV_USER_ID


def get_repo(request: Request) -> DesignRepository:
    return request.app.state.repo


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


UserId = Annotated[str, Depends(get_user_id)]
Repo = Annotated[DesignRepository, Depends(get_repo)]
Runner = Annotated[JobRunner, Depends(get_runner)]
