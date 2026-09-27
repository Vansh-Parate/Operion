import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app import benchmark
from app.models.diagnosis import Diagnosis
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.services import investigation, llm_diagnosis


class BenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.incident = IncidentContext(
            incident_id="INC-TEST", service="payment-service", namespace="operion-sandbox",
            evidence=[
                Evidence(source="kubernetes", category="pod_status", summary="OOMKilled",
                         data={"pod_name": "payment-test", "last_termination_reason": "OOMKilled",
                               "last_exit_code": 137}),
                Evidence(source="kubernetes", category="deployment_config", summary="Configuration",
                         data={"containers": [{"environment_variables": [
                             {"name": "INCIDENT_MODE", "value": "synthetic-trigger"},
                             {"name": "PAYMENT_PROVIDER_URL", "value": "http://payment"},
                         ]}]}),
            ],
        )
        self.runbooks = [{"source": "runbooks/kubernetes/OOMKilled.md", "score": 0.7798,
                          "chunk_index": 0, "text": "Memory troubleshooting"}]

    def run_case(self, retrieval_error: Exception | None = None,
                 llm_error: Exception | None = None) -> benchmark.BenchmarkResult:
        with patch.object(investigation, "collect_incident_context", return_value=self.incident), \
             patch.object(llm_diagnosis, "retrieve_runbooks", return_value=self.runbooks,
                          side_effect=retrieval_error) as retrieval, \
             patch.object(llm_diagnosis, "diagnose_with_llm", side_effect=llm_error,
                          return_value=Diagnosis(root_cause="container_memory_limit_exceeded",
                                                 confidence=0.9, summary="OOM")) as diagnose:
            result = benchmark.run_benchmark("INC-002")
        retrieval.assert_called_once()
        diagnose.assert_called_once_with(self.incident,
                                         runbooks=[] if retrieval_error else self.runbooks)
        return result

    def test_success_and_json_round_trip(self) -> None:
        result = self.run_case()
        self.assertTrue(result.deterministic_correct and result.llm_correct and result.retrieval_hit)
        self.assertEqual(result.incident_id, self.incident.incident_id)
        self.assertEqual(result.top1_runbook, "OOMKilled.md")
        self.assertEqual(result.top1_score, 0.7798)
        self.assertGreaterEqual(result.total_latency_ms,
                                result.llm_latency_ms + result.retrieval_latency_ms)
        self.assertEqual(type(result).model_validate_json(result.model_dump_json()), result)

    def test_ollama_failure_preserves_retrieval_and_writes_row(self) -> None:
        result = self.run_case(llm_error=requests.ConnectionError("Ollama offline"))
        self.assertTrue(result.deterministic_correct and result.retrieval_hit)
        self.assertFalse(result.llm_correct)
        self.assertIsNone(result.llm_confidence)
        self.assertIn("Ollama offline", result.llm_error)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.jsonl"
            with patch.object(benchmark, "RESULTS_PATH", path), \
                 patch.object(benchmark, "run_benchmark", return_value=result), \
                 patch("sys.argv", ["benchmark", "--scenario", "INC-002"]), \
                 contextlib.redirect_stdout(io.StringIO()):
                benchmark.main()
            self.assertEqual(len(path.read_text().splitlines()), 1)
            self.assertEqual(type(result).model_validate_json(path.read_text()), result)

    def test_retrieval_failure_does_not_retry(self) -> None:
        result = self.run_case(retrieval_error=RuntimeError("Qdrant offline"))
        self.assertTrue(result.deterministic_correct)
        self.assertFalse(result.retrieval_hit)
        self.assertIsNone(result.top1_score)
        self.assertIn("Qdrant offline", result.retrieval_error)

    def test_existing_llm_call_and_supplied_empty_runbooks(self) -> None:
        response = Mock()
        response.json.return_value = {"response": '{"root_cause": "unknown", "summary": "test"}'}
        with patch.object(llm_diagnosis, "retrieve_runbooks", return_value=self.runbooks) as retrieval, \
             patch.object(llm_diagnosis.requests, "post", return_value=response), \
             contextlib.redirect_stdout(io.StringIO()):
            llm_diagnosis.diagnose_with_llm(self.incident)
            retrieval.assert_called_once()
            retrieval.reset_mock()
            llm_diagnosis.diagnose_with_llm(self.incident, runbooks=[])
            retrieval.assert_not_called()

    def test_trigger_filtered_without_mutating_incident(self) -> None:
        prompt = llm_diagnosis.build_prompt(self.incident, self.runbooks)
        self.assertNotIn("INCIDENT_MODE", prompt)
        self.assertNotIn("synthetic-trigger", prompt)
        self.assertIn("PAYMENT_PROVIDER_URL", prompt)
        self.assertEqual(self.incident.evidence[1].data["containers"][0]
                         ["environment_variables"][0]["name"], "INCIDENT_MODE")

    def test_summary_includes_failures_and_skips_malformed_rows(self) -> None:
        success = self.run_case()
        failure = self.run_case(llm_error=ValueError("Invalid JSON"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.jsonl"
            path.write_text(success.model_dump_json() + "\n" + failure.model_dump_json()
                            + "\ninvalid\n", encoding="utf-8")
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                benchmark.print_summary(path)
            self.assertIn("Number of runs: 2", output.getvalue())
            self.assertIn("LLM root-cause accuracy: 50.00%", output.getvalue())
            self.assertIn("Top-1 retrieval accuracy: 100.00%", output.getvalue())
            self.assertIn("Skipping invalid row 3", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
