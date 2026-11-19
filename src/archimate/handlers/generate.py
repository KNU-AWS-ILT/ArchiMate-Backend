"""⑤ 설계 생성 Lambda [백엔드(AI 생성)]

TODO(API 명세 확정 후 구현):
    입력  : 인터뷰 세션 id 또는 인터뷰 답변(Requirements)
    처리  : archimate.llm.generate_designs() → 3안 DesignSet
            (선택) 운영 절차 요청이면 generate_operations_guide()
    저장  : archimate.storage 의 리포지토리로 설계 결과 저장 (DynamoDB)
    출력  : DesignSet JSON (+ 비용 계산 호출 방식은 API·비용 담당과 합의)

주의: API Gateway REST 통합 타임아웃 기본 29초 < 3안 생성 목표 60초 → 호출 방식 합의 필요.
"""
