"""설계 생성 Lambda [백엔드(AI 생성)] — 고도화 아키텍처 문서 4장 ④.

TODO(API 명세 확정 후 구현):
    ④-1 접수  POST /designs
        CreateDesignRequest 검증 → designs 테이블에 jobId 저장(queued)
        → 자기 자신을 비동기 호출 → 202 DesignJobAccepted 즉시 응답 (API Gateway 29초 제한 회피)
    ④-2 생성  (비동기 호출로 실행)
        archimate.llm.generate_designs(on_tier_done=...)로 3안 병렬 생성
        → 안이 끝날 때마다 월 비용 계산(archimate.pricing) → 저장 (끝난 안부터 조회 가능)
    ④-3 조회  GET /designs/jobs/{jobId}, GET /designs/{designId}/guide
        userId(Cognito 토큰) + jobId로 저장소 조회 (조회 로직은 API·비용 담당 storage 사용)
"""
