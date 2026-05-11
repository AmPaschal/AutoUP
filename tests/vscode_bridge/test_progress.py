import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

litellm_stub = types.ModuleType("litellm")
litellm_stub.ModelResponse = object
litellm_stub.get_llm_provider = lambda name: (None, "openai")
sys.modules.setdefault("litellm", litellm_stub)

from vscode_bridge.progress import VSCodeJobProgress, build_verification_summary


class VSCodeProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.proof_dir = Path(self.temp_dir.name) / "proof"
        self.report_dir = self.proof_dir / "build" / "report" / "json"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        (self.proof_dir / "demo_harness.c").write_text("void harness(void) {}\n", encoding="utf-8")
        (self.proof_dir / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
        self.source_file = Path(self.temp_dir.name) / "src" / "demo.c"
        self.source_file.parent.mkdir(parents=True, exist_ok=True)
        self.source_file.write_text("void demo(void) {}\n", encoding="utf-8")

    def _write_reports(self):
        (self.report_dir / "viewer-property.json").write_text(
            json.dumps(
                {
                    "viewer-property": {
                        "properties": {"P1": {}, "P2": {}, "P3": {}}
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.report_dir / "viewer-result.json").write_text(
            json.dumps(
                {
                    "viewer-result": {
                        "results": {
                            "true": ["P1", "P2"],
                            "false": ["P3"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.report_dir / "viewer-coverage.json").write_text(
            json.dumps(
                {
                    "viewer-coverage": {
                        "function_coverage": {
                            str(self.source_file): {
                                "demo": {"hit": 4, "total": 5}
                            },
                            str(self.proof_dir / "demo_harness.c"): {
                                "harness": {"hit": 8, "total": 8}
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        html_dir = self.proof_dir / "build" / "report" / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        (html_dir / "index.html").write_text("<html><body>demo</body></html>\n", encoding="utf-8")

    def test_summary_uses_report_files(self):
        self._write_reports()

        summary = build_verification_summary(
            harness_dir=str(self.proof_dir),
            root_dir=self.temp_dir.name,
            target_file_path=str(self.source_file),
            target_function="demo",
            harness_file_name="demo_harness.c",
            log_file=str(self.proof_dir / "autoup.log"),
        )

        self.assertEqual(summary["propertiesInstrumented"], 3)
        self.assertEqual(summary["propertiesVerified"], 2)
        self.assertEqual(summary["coverageHit"], 4)
        self.assertEqual(summary["coverageTotal"], 5)
        self.assertAlmostEqual(summary["coveragePercentage"], 0.8)
        self.assertEqual(summary["artifactPaths"]["source"], str(self.source_file))
        self.assertEqual(
            summary["artifactPaths"]["reportHtml"],
            str(self.proof_dir / "build" / "report" / "html" / "index.html"),
        )

    def test_progress_writes_job_and_events(self):
        self._write_reports()
        progress = VSCodeJobProgress(
            job_id="job-1",
            proof_dir=str(self.proof_dir),
            workspace_root=self.temp_dir.name,
            source_file=str(self.source_file),
            function_name="demo",
            execution_host="local-linux",
            log_file=str(self.proof_dir / "autoup.log"),
            line=9,
            column=4,
        )
        progress.initialize_job(pid=123)
        progress.job_started()
        progress.stage_started("CoverageDebugger")
        progress.refinement_accepted(
            stage="CoverageDebugger",
            message="Accepted coverage refinement",
            harness_dir=str(self.proof_dir),
            root_dir=self.temp_dir.name,
            target_file_path=str(self.source_file),
            target_function="demo",
            harness_file_name="demo_harness.c",
            extra={"taskId": "cov-demo-10"},
        )
        progress.job_completed()

        job_data = json.loads((self.proof_dir / ".autoup" / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job_data["status"], "completed")
        self.assertEqual(job_data["currentStage"], "CoverageDebugger")
        self.assertEqual(job_data["line"], 9)
        self.assertEqual(job_data["column"], 4)

        events = [
            json.loads(line)
            for line in (self.proof_dir / ".autoup" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(events[0]["type"], "job_started")
        self.assertEqual(events[2]["type"], "refinement_accepted")
        self.assertEqual(events[2]["data"]["summary"]["propertiesInstrumented"], 3)


if __name__ == "__main__":
    unittest.main()
