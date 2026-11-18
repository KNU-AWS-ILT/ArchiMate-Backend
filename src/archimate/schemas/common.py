"""여러 스키마가 함께 쓰는 열거형.

⚠️ 이 파일은 프론트엔드·백엔드(비용 계산)·LLM이 공유하는 '계약'이다.
값을 바꾸면 `scripts/export_contracts.py`로 contracts/를 다시 생성하고 팀에 공유한다.
"""

from enum import StrEnum


class Tier(StrEnum):
    """3안 구분."""

    MINIMAL_COST = "minimal_cost"  # 최소비용
    BALANCED = "balanced"  # 균형
    SCALABLE = "scalable"  # 확장형


class AwsService(StrEnum):
    """다이어그램 노드로 쓸 수 있는 AWS 서비스.

    LLM은 이 목록 밖의 서비스를 출력할 수 없다(스키마로 강제).
    프론트는 이 값으로 AWS 공식 아이콘을 매핑한다.
    """

    # ── 비용 계산 지원 8종 (기획안 4장) ──
    LAMBDA = "lambda"
    S3 = "s3"
    DYNAMODB = "dynamodb"
    RDS = "rds"
    EC2 = "ec2"
    CLOUDFRONT = "cloudfront"
    API_GATEWAY = "api_gateway"
    ALB = "alb"

    # ── 다이어그램에는 나오지만 비용 계산은 미지원(백엔드와 범위 논의 필요) ──
    ECS_FARGATE = "ecs_fargate"
    NAT_GATEWAY = "nat_gateway"
    ELASTICACHE = "elasticache"
    COGNITO = "cognito"
    ROUTE53 = "route53"
    SQS = "sqs"
    SNS = "sns"
    EVENTBRIDGE = "eventbridge"
    CLOUDWATCH = "cloudwatch"
    SECRETS_MANAGER = "secrets_manager"
    SES = "ses"
    BEDROCK = "bedrock"  # AI 서비스용. 도그푸딩(ArchiMate 자체 구성도) 시연에 필요


PRICED_SERVICES: frozenset[AwsService] = frozenset(
    {
        AwsService.LAMBDA,
        AwsService.S3,
        AwsService.DYNAMODB,
        AwsService.RDS,
        AwsService.EC2,
        AwsService.CLOUDFRONT,
        AwsService.API_GATEWAY,
        AwsService.ALB,
    }
)
