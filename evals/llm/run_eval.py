"""대표 시나리오 평가 (#25) — 생성 성공률·응답 시간·재시도를 시나리오마다 N회 측정한다.

    python evals/llm/run_eval.py                          # .env 설정(LLM_PROVIDER 등)으로 전체 시나리오 3회
    python evals/llm/run_eval.py --runs 1 --only demo_club_recruit
    LLM_PROVIDER=gemini python evals/llm/run_eval.py --interval 20   # 무료 한도: 실행 사이 20초 쉼

시나리오: evals/llm/scenarios/*.json ({"id", "title", "status", "answers"}). status=draft는 PM 확인 전 초안.
결과: out/eval/{시각}/ 에 results.csv(안 1개 = 1행), runs.csv(실행 1회 = 1행), summary.md(목표 대비 판정)

측정 기준 (기획서 5장)
- 성공 = 안이 스키마·의미 검증을 통과해 만들어짐 (= 유효한 구성도). 목표: 성공률 80% 이상
- 응답 시간 = 요구사항 입력부터 시도한 안이 모두 끝날 때까지. 목표: 60초 이내
- 재시도는 파이프라인 로그(llm.generate, llm.transient_retry, llm.fallback)에서 센다

⚠️ Gemini 무료 티어 입력은 Google 제품 개선에 쓰일 수 있다. 시나리오 데이터만 넣는다.
"""

import argparse
import asyncio
import csv
import json
import logging
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from archimate.llm import AllTiersFailedError, LLMError, build_requirements, generate_designs
from archimate.llm.providers import LLMProvider
from archimate.llm.settings import LLMSettings, get_settings
from archimate.schemas import Design, Requirements

SCENARIO_DIR = Path(__file__).parent / "scenarios"
TARGET_SUCCESS_RATE = 0.8
TARGET_SECONDS = 60.0

_LABEL = re.compile(r"label=(\S+)")


@dataclass
class Scenario:
    id: str
    title: str
    status: str
    requirements: Requirements


@dataclass
class TierRow:
    scenario: str
    run: int
    tier: str
    ok: bool
    reason: str  # 실패 이유 코드 (성공이면 빈 문자열)
    seconds: float  # 시작부터 이 안이 끝날 때까지 (실패면 전체 시간)
    guide_ok: bool  # 운영 절차까지 만들어졌는지
    validation_attempts: int  # 설계 요청 횟수 (1 = 재시도 없음)
    transient_retries: int
    fallback: bool


@dataclass
class RunRow:
    scenario: str
    run: int
    seconds: float
    tiers_ok: int
    tiers_tried: int
    error: str


@dataclass
class _LogStats:
    attempts: Counter = field(default_factory=Counter)
    transient: Counter = field(default_factory=Counter)
    fallback: set = field(default_factory=set)
    invalid: list = field(default_factory=list)  # 검증 실패 메시지 (실패 유형 정리용)


class _Capture(logging.Handler):
    """파이프라인 로그에서 label(design:minimal_cost 등)별 시도·재시도를 센다."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.stats = _LogStats()

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        m = _LABEL.search(msg)
        if not m:
            return
        label = m.group(1)
        if msg.startswith("llm.generate "):
            self.stats.attempts[label] += 1
        elif msg.startswith("llm.transient_retry "):
            self.stats.transient[label] += 1
        elif msg.startswith("llm.fallback "):
            self.stats.fallback.add(label)
        elif msg.startswith("llm.invalid_output ") and label.startswith("design:"):
            self.stats.invalid.append(msg)


def load_scenarios(directory: Path = SCENARIO_DIR, only: list[str] | None = None) -> list[Scenario]:
    scenarios = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text("utf-8"))
        if only and data["id"] not in only:
            continue
        scenarios.append(
            Scenario(
                id=data["id"],
                title=data["title"],
                status=data.get("status", "draft"),
                requirements=build_requirements(data["answers"]),
            )
        )
    return scenarios


async def run_once(
    scenario: Scenario,
    run: int,
    *,
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
) -> tuple[RunRow, list[TierRow], list[str]]:
    settings = settings or get_settings()
    capture = _Capture()
    llm_logger = logging.getLogger("archimate.llm")
    llm_logger.addHandler(capture)
    previous_level = llm_logger.level
    llm_logger.setLevel(logging.INFO)

    done_at: dict[str, float] = {}
    guide_ok: dict[str, bool] = {}
    started = time.perf_counter()

    async def on_tier_done(design: Design) -> None:
        done_at[design.tier.value] = time.perf_counter() - started
        guide_ok[design.tier.value] = design.operations_guide is not None

    reasons: dict[str, str] = {}
    error = ""
    try:
        result = await generate_designs(
            scenario.requirements, provider=provider, settings=settings, on_tier_done=on_tier_done
        )
        reasons = {f.tier.value: f.reason for f in result.failures}
    except AllTiersFailedError as e:
        reasons = {f.tier.value: f.reason for f in e.failures}
        error = e.code
    except LLMError as e:  # 설정 오류 등 전체 실패
        error = e.code
    finally:
        llm_logger.removeHandler(capture)
        llm_logger.setLevel(previous_level)
    total = time.perf_counter() - started

    stats = capture.stats
    tiers = [t.value for t in settings.tiers]
    rows = []
    for tier in tiers:
        label = f"design:{tier}"
        ok = tier in done_at
        rows.append(
            TierRow(
                scenario=scenario.id,
                run=run,
                tier=tier,
                ok=ok,
                reason="" if ok else reasons.get(tier, error or "UNKNOWN"),
                seconds=round(done_at.get(tier, total), 1),
                guide_ok=guide_ok.get(tier, False),
                validation_attempts=stats.attempts[label],
                transient_retries=stats.transient[label] + stats.transient[f"operations:{tier}"],
                fallback=label in stats.fallback or f"operations:{tier}" in stats.fallback,
            )
        )
    run_row = RunRow(
        scenario=scenario.id,
        run=run,
        seconds=round(total, 1),
        tiers_ok=sum(r.ok for r in rows),
        tiers_tried=len(rows),
        error=error,
    )
    return run_row, rows, stats.invalid


async def run_eval(
    scenarios: list[Scenario],
    *,
    runs: int,
    interval_s: float = 0.0,
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
) -> tuple[list[RunRow], list[TierRow], list[str]]:
    all_runs: list[RunRow] = []
    all_tiers: list[TierRow] = []
    invalid: list[str] = []
    first = True
    for scenario in scenarios:
        for run in range(1, runs + 1):
            if not first and interval_s:
                await asyncio.sleep(interval_s)
            first = False
            run_row, tier_rows, inv = await run_once(
                scenario, run, provider=provider, settings=settings
            )
            all_runs.append(run_row)
            all_tiers.extend(tier_rows)
            invalid.extend(inv)
            print(
                f"[{scenario.id} #{run}] {run_row.seconds:.1f}s "
                f"성공 {run_row.tiers_ok}/{run_row.tiers_tried} {run_row.error}".rstrip()
            )
    return all_runs, all_tiers, invalid


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def summarize(
    scenarios: list[Scenario],
    runs: list[RunRow],
    tiers: list[TierRow],
    invalid: list[str],
    *,
    provider: str,
    model: str,
) -> str:
    titles = {s.id: s for s in scenarios}
    by_scenario_tiers: dict[str, list[TierRow]] = defaultdict(list)
    by_scenario_runs: dict[str, list[RunRow]] = defaultdict(list)
    for t in tiers:
        by_scenario_tiers[t.scenario].append(t)
    for r in runs:
        by_scenario_runs[r.scenario].append(r)

    lines = [
        "# 대표 시나리오 평가 결과",
        "",
        f"- 실행: {datetime.now():%Y-%m-%d %H:%M}, 제공자 `{provider}`, 모델 `{model}`",
        f"- 목표: 생성 성공률 {_pct(TARGET_SUCCESS_RATE)} 이상, 응답 시간 {TARGET_SECONDS:.0f}초 이내",
        "",
        "| 시나리오 | 상태 | 실행 | 성공률(안) | 3안 모두 성공 | 평균 시간 | 최대 시간 "
        "| 60초 이내 | 검증 재시도 | 일시 오류 재시도 | 대체 모델 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sid, trows in by_scenario_tiers.items():
        rrows = by_scenario_runs[sid]
        secs = [r.seconds for r in rrows]
        lines.append(
            "| {title} | {status} | {n} | {rate} | {all_ok} | {avg:.1f}s | {mx:.1f}s | {fast} "
            "| {vr} | {tr} | {fb} |".format(
                title=titles[sid].title,
                status="확정" if titles[sid].status == "confirmed" else "초안",
                n=len(rrows),
                rate=_pct(sum(t.ok for t in trows) / len(trows)),
                all_ok=_pct(sum(r.tiers_ok == r.tiers_tried for r in rrows) / len(rrows)),
                avg=statistics.mean(secs),
                mx=max(secs),
                fast=_pct(sum(s <= TARGET_SECONDS for s in secs) / len(secs)),
                vr=sum(max(t.validation_attempts - 1, 0) for t in trows),
                tr=sum(t.transient_retries for t in trows),
                fb=sum(t.fallback for t in trows),
            )
        )

    success = sum(t.ok for t in tiers) / len(tiers) if tiers else 0.0
    fast = sum(r.seconds <= TARGET_SECONDS for r in runs) / len(runs) if runs else 0.0
    lines += [
        "",
        "## 목표 대비",
        "",
        f"- 생성 성공률: {_pct(success)} → {'달성' if success >= TARGET_SUCCESS_RATE else '미달'}",
        f"- 60초 이내 비율: {_pct(fast)} → {'달성' if fast == 1 else '미달'}",
    ]

    by_tier: dict[str, list[TierRow]] = defaultdict(list)
    for t in tiers:
        by_tier[t.tier].append(t)
    lines += [
        "",
        "## 안별",
        "",
        "| 안 | 성공률 | 평균 시간 | 운영 절차 생성 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for tier, trows in by_tier.items():
        lines.append(
            f"| {tier} | {_pct(sum(t.ok for t in trows) / len(trows))} "
            f"| {statistics.mean(t.seconds for t in trows):.1f}s "
            f"| {_pct(sum(t.guide_ok for t in trows) / len(trows))} |"
        )

    reasons = Counter(t.reason for t in tiers if not t.ok)
    if reasons:
        lines += ["", "## 실패 이유", ""]
        lines += [f"- `{reason}`: {count}회" for reason, count in reasons.most_common()]
    if invalid:
        lines += ["", "## 검증 실패 메시지 (최근 10개, 프롬프트 튜닝용)", ""]
        lines += [f"- {m[:300]}" for m in invalid[-10:]]
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--runs", type=int, default=3, help="시나리오마다 실행 횟수 (기본 3)")
    parser.add_argument("--only", action="append", help="이 id의 시나리오만 (여러 번 지정 가능)")
    parser.add_argument(
        "--interval", type=float, default=0.0, help="실행 사이 대기(초). 무료 한도용"
    )
    parser.add_argument("--out", type=Path, help="결과 폴더 (기본 out/eval/{시각})")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # 측정용으로 llm 로그를 INFO까지 모으지만, 화면에는 경고 이상만 보인다
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.WARNING)
    settings = get_settings()
    scenarios = load_scenarios(only=args.only)
    if not scenarios:
        raise SystemExit("평가할 시나리오가 없어요.")
    print(
        f"provider={settings.provider} model={settings.resolved_model} "
        f"시나리오 {len(scenarios)}개 × {args.runs}회, 안 {[t.value for t in settings.tiers]}"
    )

    runs, tiers, invalid = await run_eval(
        scenarios, runs=args.runs, interval_s=args.interval, settings=settings
    )
    out = args.out or Path("out/eval") / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "runs.csv", runs)
    write_csv(out / "results.csv", tiers)
    summary = summarize(
        scenarios,
        runs,
        tiers,
        invalid,
        provider=settings.provider,
        model=settings.resolved_model,
    )
    (out / "summary.md").write_text(summary, "utf-8")
    print(f"\n{summary}\n결과: {out}/")


if __name__ == "__main__":
    asyncio.run(main())
