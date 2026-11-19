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
    """다이어그램 노드의 서비스 종류 (JSON 필드 `serviceType`).

    LLM은 이 목록 밖의 서비스를 출력할 수 없다(스키마로 강제).
    프론트는 이 값으로 AWS 공식 아이콘을 매핑한다.
    """

    # ── AWS 서비스가 아닌 노드 ──
    USERS = "users"  # 사용자(브라우저·앱). 다이어그램의 시작점, 설계마다 정확히 1개

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


class ServiceCategory(StrEnum):
    """노드 설명(D) 화면의 분류 표시용."""

    EXTERNAL = "external"
    NETWORK = "network"
    COMPUTE = "compute"
    STORAGE = "storage"
    DATABASE = "database"
    SECURITY = "security"
    INTEGRATION = "integration"
    MONITORING = "monitoring"
    AI = "ai"


# 서비스 공식 이름과 분류. LLM이 만들지 않고 이 표에서 채운다.
SERVICE_INFO: dict[AwsService, tuple[str, ServiceCategory]] = {
    AwsService.USERS: ("사용자", ServiceCategory.EXTERNAL),
    AwsService.LAMBDA: ("AWS Lambda", ServiceCategory.COMPUTE),
    AwsService.S3: ("Amazon S3", ServiceCategory.STORAGE),
    AwsService.DYNAMODB: ("Amazon DynamoDB", ServiceCategory.DATABASE),
    AwsService.RDS: ("Amazon RDS", ServiceCategory.DATABASE),
    AwsService.EC2: ("Amazon EC2", ServiceCategory.COMPUTE),
    AwsService.CLOUDFRONT: ("Amazon CloudFront", ServiceCategory.NETWORK),
    AwsService.API_GATEWAY: ("Amazon API Gateway", ServiceCategory.NETWORK),
    AwsService.ALB: ("Application Load Balancer", ServiceCategory.NETWORK),
    AwsService.ECS_FARGATE: ("Amazon ECS (Fargate)", ServiceCategory.COMPUTE),
    AwsService.NAT_GATEWAY: ("NAT Gateway", ServiceCategory.NETWORK),
    AwsService.ELASTICACHE: ("Amazon ElastiCache", ServiceCategory.DATABASE),
    AwsService.COGNITO: ("Amazon Cognito", ServiceCategory.SECURITY),
    AwsService.ROUTE53: ("Amazon Route 53", ServiceCategory.NETWORK),
    AwsService.SQS: ("Amazon SQS", ServiceCategory.INTEGRATION),
    AwsService.SNS: ("Amazon SNS", ServiceCategory.INTEGRATION),
    AwsService.EVENTBRIDGE: ("Amazon EventBridge", ServiceCategory.INTEGRATION),
    AwsService.CLOUDWATCH: ("Amazon CloudWatch", ServiceCategory.MONITORING),
    AwsService.SECRETS_MANAGER: ("AWS Secrets Manager", ServiceCategory.SECURITY),
    AwsService.SES: ("Amazon SES", ServiceCategory.INTEGRATION),
    AwsService.BEDROCK: ("Amazon Bedrock", ServiceCategory.AI),
}
assert set(SERVICE_INFO) == set(AwsService), "모든 서비스에 이름·분류가 필요합니다"
