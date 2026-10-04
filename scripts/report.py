"""Render evidence summaries only from machine-produced test/eval/benchmark files."""

import json
import os
import platform
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path


def git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def main():
    root = Path("artifacts")
    root.mkdir(exist_ok=True)
    metadata = {
        "timestamp": datetime.now(UTC).isoformat(),
        "tested_commit": git("rev-parse", "HEAD"),
        "source_commit": os.environ.get("EVIDENCE_SOURCE_SHA", git("rev-parse", "HEAD")),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "trees": {
            p: git("rev-parse", "HEAD:" + p) for p in ["app", "tests", "migrations", "uv.lock"]
        },
    }
    (root / "verification-provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    context = f"Generated {metadata['timestamp']} on {metadata['platform']}, Python {metadata['python']}.\n\nTested commit: `{metadata['tested_commit']}`. Source commit: `{metadata['source_commit']}`.\n\n"
    junit = root / "junit.xml"
    if junit.exists():
        categories = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})
        for case in ET.parse(junit).iter("testcase"):
            parts = case.attrib.get("classname", "unknown").split(".")
            category = parts[1] if len(parts) > 1 else parts[0]
            status = (
                "failed"
                if case.find("failure") is not None or case.find("error") is not None
                else "skipped"
                if case.find("skipped") is not None
                else "passed"
            )
            categories[category][status] += 1
        content = (
            "# Test evidence\n\n"
            + context
            + "| Category | Passed | Failed | Skipped |\n|---|---:|---:|---:|\n"
        )
        for name, counts in sorted(categories.items()):
            content += (
                f"| {name} | {counts['passed']} | {counts['failed']} | {counts['skipped']} |\n"
            )
        totals = {
            k: sum(v[k] for v in categories.values()) for k in ["passed", "failed", "skipped"]
        }
        content += f"\nTotals: {totals['passed']} passed; {totals['failed']} failed; {totals['skipped']} skipped.\n"
        content += "\nThe unit category includes the exhaustive 81-pair state-transition matrix. Test count is not a measure of production scale.\n"
        coverage = root / "coverage.json"
        if coverage.exists():
            c = json.loads(coverage.read_text())["totals"]
            content += f"\nCoverage (lines + branches): **{c['percent_covered']:.2f}%**; {c['covered_lines']}/{c['num_statements']} statements, {c['covered_branches']}/{c['num_branches']} branches.\n"
        (root / "test-summary.md").write_text(content)
        (root / "test-counts.json").write_text(
            json.dumps({"categories": categories, "totals": totals}, indent=2) + "\n"
        )
        print(content)
    evaluation = root / "eval-results.json"
    if evaluation.exists():
        result = json.loads(evaluation.read_text())
        content = (
            "# Deterministic contract evals\n\n"
            + context
            + f"{result['passed']}/{result['total']} cases passed. Model quality was **not evaluated**.\n\n| Case | Observed state | Error | Passed |\n|---|---|---|---|\n"
        )
        for case in result["cases"]:
            content += f"| {case['id']} | {case['observed_state']} | {case['observed_error'] or 'none'} | {case['passed']} |\n"
        (root / "eval-summary.md").write_text(content)
        print(content)
    benchmark = root / "benchmark-results.json"
    if benchmark.exists():
        b = json.loads(benchmark.read_text())
        content = (
            "# Admission benchmark\n\n" + context + f"Scope: {b['scope']}. {b['transport']}.\n\n"
        )
        content += f"Database: {b['database']}. CPU count reported by runner: {b['cpu_count']}.\n\n"
        content += f"Requests: {b['request_count']}; concurrency: {b['concurrency']}; warm-up: {b['warmup']}.\n\n"
        content += (
            f"p50: {b['p50_ms']:.2f} ms; p95: {b['p95_ms']:.2f} ms; p99: {b['p99_ms']:.2f} ms.\n\n"
        )
        content += f"Throughput: {b['requests_per_second']:.2f} admissions/s; errors: {b['errors']}; failures: {b['failure_types']}.\n\n"
        content += "This is one local CI-runner measurement, **not production capacity**. Profiling ran before the timed sample; see `admission-profile.txt` and the query plan in `benchmark-results.json`.\n"
        (root / "benchmark-summary.md").write_text(content)
        print(content)


if __name__ == "__main__":
    main()
