"""Lambda 배포 의존성(requirements-lambda.txt)이 코드와 맞는지 확인한다 (#15).

Lambda 패키지에는 이 목록만 들어간다. 코드가 목록에 없는 외부 패키지를 모듈 맨 위에서 import하면
배포한 뒤에야 `ModuleNotFoundError`로 드러나서, 여기서 미리 잡는다.
"""

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "archimate"

# 이 파일들은 필요할 때만 import된다(예: llm/providers/factory.py의 지연 import).
# LLM_PROVIDER=bedrock이면 불러오지 않아서 Lambda 패키지에 의존성이 없어도 된다.
LAZY_FILES = {"llm/providers/gemini.py"}

# 목록의 패키지가 함께 설치해 주는 모듈 (import 이름 → 그 모듈을 설치해 주는 목록의 패키지)
INSTALLED_WITH = {
    "starlette": "fastapi",
    "pydantic_core": "pydantic",
    "botocore": "boto3",
}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _requirement_names(lines: list[str]) -> set[str]:
    names = set()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(_normalize(re.split(r"[\[<>=!~;\s]", line, maxsplit=1)[0]))
    return names


def lambda_requirements() -> set[str]:
    return _requirement_names((ROOT / "requirements-lambda.txt").read_text("utf-8").splitlines())


def module_level_imports(path: Path) -> set[str]:
    """모듈 맨 위에서 import하는 최상위 모듈 이름. 함수 안의 지연 import는 제외한다."""
    roots: set[str] = set()
    for node in ast.parse(path.read_text("utf-8")).body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_requirements_are_declared_in_pyproject():
    """requirements-lambda.txt의 패키지는 pyproject.toml에도 있어야 한다 (오타·낡은 이름 방지)."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    declared = _requirement_names(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        declared |= _requirement_names(extra)
    assert lambda_requirements() - declared == set()


def test_code_only_imports_packages_in_the_lambda_requirements():
    allowed = lambda_requirements() | set(INSTALLED_WITH)
    stdlib = set(sys.stdlib_module_names)
    missing: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative in LAZY_FILES:
            continue
        unknown = {
            root
            for root in module_level_imports(path)
            if root not in stdlib and root != "archimate" and _normalize(root) not in allowed
        }
        if unknown:
            missing[relative] = unknown
    assert missing == {}, f"requirements-lambda.txt에 없는 패키지를 import해요: {missing}"


def test_packages_installed_with_others_are_really_provided():
    provided = lambda_requirements()
    assert {INSTALLED_WITH[m] for m in INSTALLED_WITH} <= provided


def test_lambda_does_not_bundle_local_only_packages():
    assert not ({"google_genai", "uvicorn"} & lambda_requirements())


def test_every_lambda_requirement_is_pinned_in_the_constraints():
    """배포일에 빌드해도 확인한 버전이 들어가도록, 직접 의존성은 모두 ==로 고정돼 있어야 한다."""
    lines = [
        line.split("#", 1)[0].strip()
        for line in (ROOT / "constraints-lambda.txt").read_text("utf-8").splitlines()
    ]
    pins = [line for line in lines if line]
    assert pins and all("==" in line for line in pins), "제약 파일은 모두 ==로 고정해야 해요"
    pinned = {_normalize(line.split("==", 1)[0]) for line in pins}
    assert lambda_requirements() - pinned == set()
