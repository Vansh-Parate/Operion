"""Benchmark the currently active incident without changing cluster resources."""

import argparse
import sys
from pathlib import Path
from statistics import mean
from time import perf_counter

from app.models.benchmark import BenchmarkResult, RetrievedRunbook


SCENARIOS = {
    "INC-001": {
        "expected_root_cause": "missing_environment_variable",
        "expected_runbook": "CrashLoopBackOff.md",
    },
    "INC-002": {
        "expected_root_cause": "container_memory_limit_exceeded",
        "expected_runbook": "OOMKilled.md",
    },
    "INC-003": {
        "expected_root_cause": "readiness_probe_failure",
        "expected_runbook": "ReadinessProbeFailure.md",
    },
    "INC-004": {
        "expected_root_cause": "redis_dependency_unavailable",
        "expected_runbook": "RedisUnavailable.md",
    },
}
RESULTS_PATH = Path(__file__).resolve().parents[1] / "benchmark_results.jsonl"


def run_benchmark(scenario: str, expected: str | None = None) -> BenchmarkResult:
    # Total includes evidence collection and cold retrieval initialization,
    # but excludes terminal output and writing the result file.
    started = perf_counter()
    from app.services.investigation import collect_incident_context
    from app.services.diagnosis import diagnose_incident
    from app.services.llm_diagnosis import (
        build_retrieval_query,
        diagnose_with_llm,
        retrieve_runbooks,
    )

    truth = SCENARIOS[scenario]
    expected_root_cause = expected or truth["expected_root_cause"]
    incident = collect_incident_context()
    baseline = diagnose_incident(incident)

    runbooks: list[dict] = []
    retrieval_error = None
    retrieval_started = perf_counter()
    try:
        runbooks = retrieve_runbooks(build_retrieval_query(incident), top_k=3)
    except Exception as exc:
        retrieval_error = f"{type(exc).__name__}: {exc}"
    retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000

    diagnosis = None
    llm_error = None
    llm_started = perf_counter()
    try:
        # An empty list also prevents a second retrieval after retrieval failure.
        diagnosis = diagnose_with_llm(incident, runbooks=runbooks)
    except Exception as exc:
        llm_error = f"{type(exc).__name__}: {exc}"
    llm_latency_ms = (perf_counter() - llm_started) * 1000

    top1 = runbooks[0] if runbooks else None
    top1_runbook = Path(top1["source"].replace("\\", "/")).name if top1 else None
    return BenchmarkResult(
        incident_id=incident.incident_id,
        scenario=scenario,
        expected_root_cause=expected_root_cause,
        deterministic_root_cause=baseline.root_cause,
        llm_root_cause=diagnosis.root_cause if diagnosis else None,
        llm_confidence=diagnosis.confidence if diagnosis else None,
        top1_runbook=top1_runbook,
        top1_score=top1["score"] if top1 else None,
        retrieval_hit=top1_runbook == truth["expected_runbook"],
        deterministic_correct=baseline.root_cause == expected_root_cause,
        llm_correct=diagnosis is not None and diagnosis.root_cause == expected_root_cause,
        total_latency_ms=(perf_counter() - started) * 1000,
        llm_latency_ms=llm_latency_ms,
        retrieval_latency_ms=retrieval_latency_ms,
        retrieved_runbooks=[
            RetrievedRunbook(source=item["source"], score=item["score"],
                             chunk_index=item["chunk_index"])
            for item in runbooks
        ],
        retrieval_error=retrieval_error,
        llm_error=llm_error,
    )


def print_result(result: BenchmarkResult) -> None:
    def status(correct: bool) -> str:
        return "PASS" if correct else "FAIL"

    print("\n===== BENCHMARK RESULT =====")
    print(f"Scenario: {result.scenario}")
    print(f"Expected: {result.expected_root_cause}")
    print(f"Deterministic: {result.deterministic_root_cause} [{status(result.deterministic_correct)}]")
    print(f"LLM+RAG: {result.llm_root_cause} [{status(result.llm_correct)}]")
    print(f"Top-1 Runbook: {result.top1_runbook} [{status(result.retrieval_hit)}]")
    score = f"{result.top1_score:.4f}" if result.top1_score is not None else "N/A"
    confidence = f"{result.llm_confidence:.2f}" if result.llm_confidence is not None else "N/A"
    print(f"Top-1 Score: {score}")
    print(f"LLM Confidence: {confidence}")
    for label, value in (
        ("Retrieval", result.retrieval_latency_ms),
        ("LLM", result.llm_latency_ms),
        ("Total", result.total_latency_ms),
    ):
        elapsed = f"{value:.0f} ms" if value is not None else "N/A"
        print(f"{label} Latency: {elapsed}")
    if result.retrieval_error:
        print(f"Retrieval Error: {result.retrieval_error}")
    if result.llm_error:
        print(f"LLM Error: {result.llm_error}")


def print_summary(path: Path = RESULTS_PATH) -> None:
    results = []
    if path.exists():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                results.append(BenchmarkResult.model_validate_json(line))
            except ValueError as exc:
                print(f"Skipping invalid row {line_number}: {exc}", file=sys.stderr)

    print("===== BENCHMARK SUMMARY =====")
    print(f"Number of runs: {len(results)}")
    if not results:
        return
    # Failed stages count as incorrect; their measured attempt latency is included.
    for label, field in (
        ("Deterministic accuracy", "deterministic_correct"),
        ("LLM root-cause accuracy", "llm_correct"),
        ("Top-1 retrieval accuracy", "retrieval_hit"),
    ):
        print(f"{label}: {mean(getattr(row, field) for row in results):.2%}")
    for label, field in (
        ("Average LLM latency", "llm_latency_ms"),
        ("Average total latency", "total_latency_ms"),
    ):
        values = [getattr(row, field) for row in results if getattr(row, field) is not None]
        elapsed = f"{mean(values):.0f} ms" if values else "N/A"
        print(f"{label}: {elapsed}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scenario", choices=SCENARIOS)
    mode.add_argument("--summary", action="store_true")
    parser.add_argument("--expected", help="Override the scenario's expected root cause")
    args = parser.parse_args()
    if args.summary:
        if args.expected:
            parser.error("--expected requires --scenario")
        print_summary()
        return

    result = run_benchmark(args.scenario, args.expected)
    with RESULTS_PATH.open("a", encoding="utf-8") as output:
        output.write(result.model_dump_json() + "\n")
    print_result(result)
    print(f"Saved to: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
