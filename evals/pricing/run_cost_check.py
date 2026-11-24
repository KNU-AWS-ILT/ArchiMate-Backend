"""비용 오차 검증 (#25) — 같은 사용량을 넣었을 때 우리 계산기와 AWS Pricing Calculator의 월 금액을 비교한다.

    python evals/pricing/run_cost_check.py               # out/cost-check/summary.md 와 표 출력
    python evals/pricing/run_cost_check.py --threshold 10

진행 방법
1. 이 스크립트로 우리 계산 금액(USD)을 본다. Calculator 값이 비어 있는 케이스는 '입력 필요'로 나온다.
2. calculator.aws(로그인 없이 쓸 수 있다)에서 cases.json의 calculatorInput대로 입력하고,
   나온 월 금액(USD)을 cases.json의 calculator.usd에 적는다 (by·date도 같이).
3. 다시 실행하면 차이(%)와 ±20% 판정이 나온다. 허용 범위를 넘는 케이스가 있으면 종료 코드 1이다.

비교 기준
- 무료 한도는 양쪽 모두 빼지 않는다. 우리 계산기의 '항상 무료' 차감은 단위 테스트(tests/pricing)로 따로 확인한다.
- 우리 계산기가 계산하지 않는 요금(케이스의 excludes)은 Calculator에서도 0으로 둔다.
- 금액은 USD로 비교한다. 원화 환산(환율)은 비교하지 않는다.
- LLM이 추정하는 사용량이 맞는지는 이 검증 범위가 아니다 (evals/llm/run_eval.py에서 본다).
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from archimate.pricing import PriceBook, estimate_monthly_cost, load_price_book
from archimate.schemas import (
    Architecture,
    AwsService,
    Design,
    Difficulty,
    Node,
    Tier,
    Usage,
)

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = Path(__file__).parent / "cases.json"
DEFAULT_THRESHOLD_PCT = 20.0  # 기획서: AWS Pricing Calculator 대비 ±20%
VERDICTS = ("통과", "초과", "입력 필요", "계산 안 됨")


@dataclass(frozen=True)
class Case:
    id: str
    service: AwsService
    title: str
    usage: Usage
    calculator_input: str
    excludes: tuple[str, ...]
    note: str
    calculator_usd: float | None
    entered_by: str | None


@dataclass(frozen=True)
class Result:
    case: Case
    ours_usd: float | None  # None이면 우리 계산기가 금액을 내지 못함 (사용량 누락 등)
    ours_note: str

    @property
    def diff_pct(self) -> float | None:
        calc = self.case.calculator_usd
        if self.ours_usd is None or calc is None or calc == 0:
            return None
        return (self.ours_usd - calc) / calc * 100

    def verdict(self, threshold_pct: float = DEFAULT_THRESHOLD_PCT) -> str:
        if self.ours_usd is None:
            return "계산 안 됨"
        if self.case.calculator_usd is None:
            return "입력 필요"
        diff = self.diff_pct
        if diff is None:  # Calculator 값이 0인데 우리는 금액이 있는 경우
            return "통과" if self.ours_usd == 0 else "초과"
        return "통과" if abs(diff) <= threshold_pct else "초과"


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    data = json.loads(path.read_text("utf-8"))
    cases = []
    for raw in data["cases"]:
        calc = raw.get("calculator") or {}
        cases.append(
            Case(
                id=raw["id"],
                service=AwsService(raw["service"]),
                title=raw["title"],
                usage=Usage.model_validate(raw["usage"]),
                calculator_input=raw["calculatorInput"],
                excludes=tuple(raw.get("excludes", ())),
                note=raw.get("note", ""),
                calculator_usd=None if calc.get("usd") is None else float(calc["usd"]),
                entered_by=calc.get("by"),
            )
        )
    return cases


def without_free_tier(book: PriceBook) -> PriceBook:
    """같은 단가에 '항상 무료' 한도만 0으로 바꾼 단가표 (Calculator에서 Free Tier를 끈 것과 맞춘다)."""
    data = json.loads(book.model_dump_json())
    data["awsLambda"]["freeRequests"] = 0
    data["awsLambda"]["freeGbSeconds"] = 0
    data["cloudfront"]["freeGbTransfer"] = 0
    data["cloudfront"]["freeRequests"] = 0
    data["dynamodb"]["freeStorageGb"] = 0
    return PriceBook.model_validate(data)


def _design(case: Case) -> Design:
    users = Node(id="users", service_type=AwsService.USERS, label="사용자", role="-", reason="-")
    node = Node(
        id=case.id,
        service_type=case.service,
        label=case.title,
        role="-",
        reason="-",
        usage=case.usage,
    )
    return Design(
        tier=Tier.MINIMAL_COST,
        title=case.title,
        summary="-",
        difficulty=Difficulty(level=1, reason="-"),
        architecture=Architecture(nodes=[users, node], edges=[], groups=[]),
        pros=["-"],
        cons=["-"],
        budget_note="-",
        scaling_path=["-"],
    )


def compute(cases: list[Case], prices: PriceBook) -> list[Result]:
    """케이스마다 노드 하나짜리 설계를 만들어 우리 계산기로 월 금액(USD)을 구한다."""
    results = []
    for case in cases:
        cost = estimate_monthly_cost(_design(case), monthly_budget=10**9, prices=prices)
        item = next(i for i in cost.items if i.node_id == case.id)
        results.append(Result(case, item.amount_usd, item.note))
    return results


def _usd(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


def render(
    results: list[Result],
    *,
    prices: PriceBook,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
) -> str:
    lines = [
        "# 비용 오차 검증 (우리 계산기 vs AWS Pricing Calculator)",
        "",
        f"- 리전: 서울(`{prices.region}`), 온디맨드, 무료 한도 미포함, USD 기준",
        f"- 우리 단가 스냅샷: {prices.price_date} ({prices.source})",
        f"- 목표: Pricing Calculator 대비 ±{threshold_pct:g}% (기획서)",
        "",
        "| 케이스 | 서비스 | 우리 계산 | Calculator | 차이 | 판정 |",
        "| --- | --- | ---: | ---: | ---: | :---: |",
    ]
    for r in results:
        diff = "-" if r.diff_pct is None else f"{r.diff_pct:+.1f}%"
        lines.append(
            f"| {r.case.title} | {r.case.service.value} | {_usd(r.ours_usd)} "
            f"| {_usd(r.case.calculator_usd)} | {diff} | {r.verdict(threshold_pct)} |"
        )
    count = {v: sum(r.verdict(threshold_pct) == v for r in results) for v in VERDICTS}
    entered = len(results) - count["입력 필요"]
    lines += [
        "",
        f"- 케이스 {len(results)}개 중 Calculator 값 입력 {entered}개: "
        + " · ".join(f"{v} {count[v]}" for v in VERDICTS),
    ]
    diffs = [abs(r.diff_pct) for r in results if r.diff_pct is not None]
    if diffs:
        lines.append(f"- 가장 큰 차이: {max(diffs):.1f}%")
    lines += ["", "## Calculator 입력 방법", ""]
    for r in results:
        lines.append(f"### {r.case.title} (`{r.case.id}`)")
        lines.append(f"- 입력: {r.case.calculator_input}")
        lines.append(f"- 우리 계산: {_usd(r.ours_usd)} — {r.ours_note}")
        if r.case.excludes:
            lines.append(f"- 양쪽 모두 제외: {', '.join(r.case.excludes)}")
        if r.case.note:
            lines.append(f"- 참고: {r.case.note}")
        if r.case.entered_by:
            lines.append(f"- 입력한 사람: {r.case.entered_by}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_PCT)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "cost-check")
    args = parser.parse_args()

    prices = without_free_tier(load_price_book())
    results = compute(load_cases(args.cases), prices)
    report = render(results, prices=prices, threshold_pct=args.threshold)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.md").write_text(report, "utf-8")
    sys.stdout.buffer.write(report.encode("utf-8"))
    over = [r for r in results if r.verdict(args.threshold) in ("초과", "계산 안 됨")]
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
