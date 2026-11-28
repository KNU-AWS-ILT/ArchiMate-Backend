"""Railway용 requirements.txt가 pyproject.toml 기본 의존성과 같은지 (#58)."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _lines(path: Path) -> list[str]:
    out = []
    for line in path.read_text("utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def test_requirements_txt_is_ascii():
    # Windows의 pip는 이 파일을 로캘 인코딩(cp949)으로 읽어서 한글 주석이 있으면 설치가 실패한다
    (ROOT / "requirements.txt").read_bytes().decode("ascii")


def test_requirements_txt_matches_pyproject_dependencies():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    expected = sorted(pyproject["project"]["dependencies"])
    assert sorted(_lines(ROOT / "requirements.txt")) == expected
