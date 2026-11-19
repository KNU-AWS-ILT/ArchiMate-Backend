"""노드 설명 Lambda [백엔드(AI 생성)] — 고도화 아키텍처 문서 4장 ⑤.

TODO(API 명세 확정 후 구현):
    GET /designs/{designId}/nodes/{nodeId}/explanation
    explanations 캐시 확인 → 없으면 archimate.llm.explain_node()(소형 모델)로 생성
    → 캐시 저장(TTL 30일) → NodeExplanation 응답 (monthlyCost는 비용 계산 결과로 채움)
"""
