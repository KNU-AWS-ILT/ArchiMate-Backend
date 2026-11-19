"""단가 동기화 Lambda [백엔드(API·비용)] — 고도화 아키텍처 문서 4장 ⑥.

EventBridge(매일 새벽) → AWS Price List API(us-east-1) → prices 테이블 덮어쓰기.
실패하면 전날 단가로 계속 동작한다. 개발 중에는 data/pricing/ JSON 스냅샷을 쓴다.
"""
