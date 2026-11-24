"""단가 스냅샷을 AWS 공개 Price List와 대조한다 (Issue #25).

    python scripts/verify_prices.py                  # out/price-check/에 내려받아 대조, 표를 출력
    python scripts/verify_prices.py --refresh        # Price List를 다시 내려받는다
    python scripts/verify_prices.py --threshold 10   # 허용 차이를 10%로 (기본 20%, 기획서 목표)

차이가 허용 범위를 넘는 항목이 있으면 종료 코드 1이다. 인증·AWS 계정이 필요 없다 (공개 파일).
내려받는 파일이 합쳐서 약 25MB(RDS가 대부분)이고 `out/price-check/`에 저장돼 git에는 올라가지 않는다.
"""

import argparse
import sys
from pathlib import Path

from archimate.pricing import load_price_book
from archimate.pricing.verify import (
    DEFAULT_THRESHOLD_PCT,
    build_checks,
    load_offers,
    render_report,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD_PCT, help="허용 차이(%%)"
    )
    parser.add_argument("--refresh", action="store_true", help="Price List를 다시 내려받기")
    parser.add_argument("--cache", type=Path, default=ROOT / "out" / "price-check")
    args = parser.parse_args()

    book = load_price_book()
    offers = load_offers(args.cache, region=book.region, refresh=args.refresh)
    checks = build_checks(book, offers)
    report = render_report(checks, threshold_pct=args.threshold, price_date=str(book.price_date))

    args.cache.mkdir(parents=True, exist_ok=True)
    (args.cache / "report.md").write_text(report, "utf-8")
    sys.stdout.buffer.write(report.encode("utf-8"))
    return 1 if any(c.status(args.threshold) in ("초과", "미확인") for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
