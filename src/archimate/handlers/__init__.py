"""Lambda 진입점 — 고도화 아키텍처 문서 3장의 Lambda 3개와 1:1 대응.

    generate.py     설계 생성 Lambda   [백엔드(AI 생성)]   POST /designs, GET /designs/jobs/{jobId} 등
    explain.py      노드 설명 Lambda   [백엔드(AI 생성)]   GET /designs/{designId}/nodes/{nodeId}/explanation
    sync_prices.py  단가 동기화 Lambda [백엔드(API·비용)]  EventBridge 매일 실행 → Price List API → prices 테이블

진입점에는 이벤트 파싱·응답 변환만 두고, 로직은 llm / api / pricing / storage 에 둔다.
(개발 중 로컬·Railway에서도 같은 로직을 쓰기 위함 — 코딩 컨벤션 4장)
요청·응답 모양은 archimate.schemas.api 참고.
"""
