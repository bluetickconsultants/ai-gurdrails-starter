"""Run the eval suites and gate on the pass bars in config/thresholds.yaml.

    python -m evals.run_evals                                   # all suites, stub model
    python -m evals.run_evals --suites golden,grounding,incidents
    python -m evals.run_evals --provider anthropic              # real model; skips scripted cases
    python -m evals.run_evals --baseline evals/baseline.json    # print the diff to paste in the PR
    python -m evals.run_evals --write-baseline evals/baseline.json

Each suite is one .jsonl file under evals/; the suite name is the file stem.

Case fields
  id, note, incident       identification
  input                    user message (pipeline case)
  tool_call, context       tool gate case: {"name", "args"} plus Context fields
  stub                     forced outputs per role for the stub model (fault injection);
                           skipped when running against a real provider
  expect                   outcome | outcome_in | not_outcome | reason | path | layer |
                           must_contain | must_not_contain | redacted | quarantined |
                           decision (tool cases)

Exit code is 1 when any suite falls below its pass bar.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from layers.common import ROOT, load_yaml
from layers.context import Context
from layers.l4_tools import ToolCall, gate
from layers.llm import StubClient, get_client
from layers.pipeline import Pipeline

EVAL_DIR = ROOT / "evals"


def discover() -> dict[str, Path]:
    return {p.stem: p for p in sorted(EVAL_DIR.rglob("*.jsonl"))}


def load_cases(path: Path) -> list[dict]:
    cases = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip() and not line.lstrip().startswith("//"):
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{n}: invalid JSON: {exc}")
    return cases


def check_response(r, expect: dict) -> list[str]:
    failures = []
    text = r.text.lower()
    if "outcome" in expect and r.outcome != expect["outcome"]:
        failures.append(f"outcome {r.outcome!r}, expected {expect['outcome']!r}")
    if "outcome_in" in expect and r.outcome not in expect["outcome_in"]:
        failures.append(f"outcome {r.outcome!r}, expected one of {expect['outcome_in']}")
    if "not_outcome" in expect and r.outcome == expect["not_outcome"]:
        failures.append(f"outcome should not be {r.outcome!r}")
    for key in ("reason", "path", "layer"):
        if key in expect and getattr(r, key) != expect[key]:
            failures.append(f"{key} {getattr(r, key)!r}, expected {expect[key]!r}")
    for s in expect.get("must_contain", []):
        if s.lower() not in text:
            failures.append(f"missing {s!r}")
    for s in expect.get("must_not_contain", []):
        if s.lower() in text:
            failures.append(f"contains forbidden {s!r}")
    for entity in expect.get("redacted", []):
        if entity not in r.redactions:
            failures.append(f"{entity} not redacted (got {r.redactions})")
    for chunk_id in expect.get("quarantined", []):
        if chunk_id not in r.quarantined:
            failures.append(f"chunk {chunk_id} not quarantined")
    return failures


def check_tool(d, expect: dict) -> list[str]:
    failures = []
    if d.decision != expect.get("decision"):
        failures.append(f"decision {d.decision!r}, expected {expect.get('decision')!r}")
    if "reason" in expect and d.reason != expect["reason"]:
        failures.append(f"reason {d.reason!r}, expected {expect['reason']!r}")
    return failures


def run_case(case: dict, provider: str, real_client) -> tuple[str, list[str]]:
    """Returns (status, failures) where status is pass | fail | skip."""
    expect = case.get("expect", {})
    ctx = Context(**case.get("context", {}))

    if "tool_call" in case:
        call = ToolCall(case["tool_call"]["name"], case["tool_call"].get("args", {}))
        failures = check_tool(gate(call, ctx), expect)
        return ("fail" if failures else "pass"), failures

    if provider == "stub":
        client = StubClient(case.get("stub"))
    elif "stub" in case:
        return "skip", ["scripted fault-injection case; stub only"]
    else:
        client = real_client

    response = Pipeline(client, tracing=False).handle(case["input"], ctx)
    failures = check_response(response, expect)
    return ("fail" if failures else "pass"), failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suites", help="comma-separated suite names (default: all)")
    parser.add_argument("--provider", default="stub", help="stub (default) or a provider from config/models.yaml")
    parser.add_argument("--report", help="write a JSON report here")
    parser.add_argument("--baseline", help="compare pass rates with this earlier report")
    parser.add_argument("--write-baseline", help="save this run's pass rates as the new baseline")
    args = parser.parse_args(argv)

    available = discover()
    selected = args.suites.split(",") if args.suites else list(available)
    unknown = [s for s in selected if s not in available]
    if unknown:
        raise SystemExit(f"Unknown suites {unknown}. Available: {sorted(available)}")

    bars = load_yaml("config/thresholds.yaml").get("eval_pass_bars", {})
    real_client = None if args.provider == "stub" else get_client(args.provider)

    report: dict = {"provider": args.provider, "suites": {}}
    any_below_bar = False

    for suite in selected:
        cases = load_cases(available[suite])
        results = []
        for case in cases:
            status, failures = run_case(case, args.provider, real_client)
            results.append({"id": case.get("id"), "status": status, "failures": failures})
        ran = [r for r in results if r["status"] != "skip"]
        passed = sum(r["status"] == "pass" for r in ran)
        rate = passed / len(ran) if ran else 1.0
        bar = bars.get(suite, 1.0)
        ok = rate >= bar
        any_below_bar |= not ok
        report["suites"][suite] = {
            "passed": passed, "ran": len(ran), "skipped": len(results) - len(ran),
            "rate": round(rate, 4), "bar": bar, "ok": ok, "cases": results,
        }

    baseline = None
    if args.baseline and Path(args.baseline).exists():
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")).get("suites", {})

    print(f"\nGuardrail evals (provider: {args.provider})\n")
    print(f"{'suite':<20}{'passed':>10}{'rate':>9}{'bar':>7}{'vs base':>10}  status")
    for suite, s in report["suites"].items():
        delta = ""
        if baseline and suite in baseline:
            d = (s["rate"] - baseline[suite]["rate"]) * 100
            delta = f"{d:+.1f}pp" if abs(d) >= 0.05 else "same"
        skipped = f" ({s['skipped']} skipped)" if s["skipped"] else ""
        print(f"{suite:<20}{s['passed']:>5}/{s['ran']:<4}{s['rate']*100:>8.1f}%{s['bar']*100:>6.0f}%{delta:>10}  "
              f"{'ok' if s['ok'] else 'BELOW BAR'}{skipped}")
        for case in s["cases"]:
            if case["status"] == "fail":
                print(f"    x {case['id']}: " + "; ".join(case["failures"]))

    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.write_baseline:
        slim = {"provider": args.provider,
                "suites": {k: {"rate": v["rate"], "passed": v["passed"], "ran": v["ran"]} for k, v in report["suites"].items()}}
        Path(args.write_baseline).write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")

    print("\nRESULT:", "FAIL (a suite is below its pass bar)" if any_below_bar else "PASS")
    return 1 if any_below_bar else 0


if __name__ == "__main__":
    sys.exit(main())
