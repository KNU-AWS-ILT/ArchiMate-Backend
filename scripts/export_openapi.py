"""OpenAPI(Swagger) 명세 내보내기 → contracts/openapi.json

    python scripts/export_openapi.py

서버를 띄우지 않고 FastAPI 앱에서 바로 뽑는다. 라우트·스키마를 바꾸면 실행해서 같은 PR에 포함한다.
프론트는 이 파일로 타입을 생성할 수 있다. 예: `pnpm dlx openapi-typescript contracts/openapi.json -o src/api/schema.d.ts`
"""

import json
from pathlib import Path

from archimate.api.app import create_app
from archimate.api.settings import ApiSettings

OUT = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"


if __name__ == "__main__":
    spec = create_app(settings=ApiSettings(docs_enabled=True)).openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"wrote {OUT.relative_to(OUT.parents[1])}")
