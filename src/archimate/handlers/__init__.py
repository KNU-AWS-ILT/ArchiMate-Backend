"""Lambda 진입점 — 기획서 7장 시스템 아키텍처의 Lambda와 1:1 대응.

    interview_api.py      ④ 인터뷰·API Lambda   [백엔드(API·비용)]
    design_generation.py  ⑤ 설계 생성 Lambda    [백엔드(AI 생성)]
    cost_calculation.py   ⑦ 비용 계산 Lambda    [백엔드(API·비용)]

진입점에는 이벤트 파싱·응답 변환만 두고, 로직은 llm / api / pricing / storage 에 둔다.
(개발 중 Railway 등에서도 같은 로직을 쓰기 위함 — 팀 미팅 안건 1-3)
"""
