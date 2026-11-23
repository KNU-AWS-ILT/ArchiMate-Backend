"""Lambda 배포 패키지 만들기 (Issue #15) — 의존성과 `archimate/` 패키지를 한 폴더에 모은다.

    python scripts/build_lambda.py                      # build/lambda 에 ARM64(Python 3.12)용으로 만든다
    python scripts/build_lambda.py --out build/lambda --platform host   # 이 PC용 (import 확인용)

CDK는 이 폴더를 그대로 Lambda 코드로 묶으면 된다. (인프라: `Code.fromAsset("<backend>/build/lambda")`)
Lambda 함수가 ARM64라서 기본값은 aarch64용 wheel을 받는다. 다른 CPU에서 받아도 실행하지 않으니 괜찮다.
압축을 푼 크기가 Lambda 한도(250MB)에 가까우면 경고한다.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements-lambda.txt"
LIMIT_MB = 250  # 압축을 푼 배포 패키지 한도
WARN_MB = 220

# 배포 패키지에서 뺄 것 (실행에 필요 없다)
_PRUNE_DIRS = {"__pycache__", "tests"}
_PRUNE_SUFFIXES = {".pyc", ".pyo"}

# AL2023(glibc 2.34) 위에서 도는 ARM64용 manylinux 태그. 패키지마다 쓰는 태그가 달라 여러 개를 허용한다.
ARM64_PLATFORMS = ("manylinux2014_aarch64", "manylinux_2_17_aarch64", "manylinux_2_28_aarch64")


def pip_command(out: Path, platform: str, python_version: str) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--requirement",
        str(REQUIREMENTS),
        "--target",
        str(out),
        "--upgrade",
        "--no-compile",
        "--disable-pip-version-check",
    ]
    if platform == "host":
        return cmd
    cmd += ["--only-binary=:all:", "--implementation", "cp", "--python-version", python_version]
    for tag in ARM64_PLATFORMS if platform == "arm64" else (platform,):
        cmd += ["--platform", tag]
    return cmd


def prune(out: Path) -> None:
    for path in sorted(out.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and path.name in _PRUNE_DIRS:
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file() and path.suffix in _PRUNE_SUFFIXES:
            path.unlink(missing_ok=True)


def size_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)


def build(out: Path, platform: str, python_version: str) -> float:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    subprocess.run(pip_command(out, platform, python_version), check=True)
    shutil.copytree(ROOT / "src" / "archimate", out / "archimate")
    prune(out)
    return size_mb(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "lambda")
    parser.add_argument(
        "--platform",
        default="arm64",
        help="arm64(기본, Lambda용) | host(이 PC용) | pip --platform 태그",
    )
    parser.add_argument("--python-version", default="3.12")
    args = parser.parse_args()

    total = build(args.out.resolve(), args.platform, args.python_version)
    print(f"\n배포 패키지: {args.out} ({total:.0f}MB, 압축 해제 한도 {LIMIT_MB}MB)")
    if total >= LIMIT_MB:
        raise SystemExit(f"한도({LIMIT_MB}MB)를 넘었어요. requirements-lambda.txt를 줄이세요.")
    if total >= WARN_MB:
        print(f"⚠ 한도에 가까워요({WARN_MB}MB 이상).")


if __name__ == "__main__":
    main()
