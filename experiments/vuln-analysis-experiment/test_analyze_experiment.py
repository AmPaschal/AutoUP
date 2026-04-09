import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("analyze_experiment.py")
SPEC = importlib.util.spec_from_file_location("analyze_experiment", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
analyze_experiment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyze_experiment
SPEC.loader.exec_module(analyze_experiment)

MakeMetadata = analyze_experiment.MakeMetadata
RunResult = analyze_experiment.RunResult
build_row = analyze_experiment.build_row
determine_target_vulnerability_reported = (
    analyze_experiment.determine_target_vulnerability_reported
)
ensure_build = analyze_experiment.ensure_build
parse_verification_time = analyze_experiment.parse_verification_time


class AnalyzeExperimentTargetVulnerabilityTests(unittest.TestCase):
    def test_strict_target_vulnerability_match(self) -> None:
        target = {
            "affectedFunction": "usb_dc_ep_write",
            "affectedFile": "drivers/usb/device/usb_dc_native_posix.c",
            "sinkFunction": "usb_dc_ep_write",
            "manifestationLines": [123],
        }
        report = {
            "vulnerabilities": [
                {
                    "code_location": {
                        "file": "zephyr/drivers/usb/device/usb_dc_native_posix.c",
                        "function": "usb_dc_ep_write",
                        "line": 123,
                    }
                }
            ]
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            proof_dir = Path(temp_dir)
            (proof_dir / "vulnerability-report.json").write_text(json.dumps(report))
            result = determine_target_vulnerability_reported(
                proof_dir, "usb_dc_ep_write", {"usb_dc_ep_write": target}
            )

        self.assertIs(result, True)

    def test_empty_report_is_false(self) -> None:
        target = {"affectedFunction": "bt_buf_get_tx"}

        with tempfile.TemporaryDirectory() as temp_dir:
            proof_dir = Path(temp_dir)
            (proof_dir / "vulnerability-report.json").write_text(
                json.dumps({"vulnerabilities": []})
            )
            result = determine_target_vulnerability_reported(
                proof_dir, "bt_buf_get_tx", {"bt_buf_get_tx": target}
            )

        self.assertIs(result, False)

    def test_partial_fallback_matches_affected_variable_in_error_type(self) -> None:
        target = {
            "affectedFunction": "usb_dc_ep_write",
            "affectedVariable": "ctrl->buf",
        }
        report = {
            "vulnerabilities": [
                {
                    "error_type": "synthetic issue mentioning ctrl->buf",
                    "code_location": {
                        "file": "zephyr/drivers/usb/device/usb_dc_native_posix.c",
                        "function": "some_other_function",
                        "line": 999,
                    },
                }
            ]
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            proof_dir = Path(temp_dir)
            (proof_dir / "vulnerability-report.json").write_text(json.dumps(report))
            result = determine_target_vulnerability_reported(
                proof_dir, "usb_dc_ep_write", {"usb_dc_ep_write": target}
            )

        self.assertEqual(result, "Partial")

    def test_missing_metadata_entry_leaves_cell_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = determine_target_vulnerability_reported(
                Path(temp_dir), "not_a_real_entry", {}
            )

        self.assertEqual(result, "")


class AnalyzeExperimentBuildRowTests(unittest.TestCase):
    def test_parse_verification_time_ignores_total_time(self) -> None:
        cbmc_root = ET.fromstring(
            "<cprover><statistics><total-time>1.25s</total-time></statistics></cprover>"
        )

        self.assertIsNone(parse_verification_time(cbmc_root))

    def test_parse_verification_time_sums_runtime_messages(self) -> None:
        cbmc_root = ET.fromstring(
            "\n".join(
                [
                    "<cprover>",
                    "<message><text>Runtime Symex: 0.25s</text></message>",
                    "<message><text>Runtime Solver: 10s</text></message>",
                    "<message><text>Runtime decision procedure: 11s</text></message>",
                    "<message><text>Runtime Postprocess Equation: 0.5s</text></message>",
                    "</cprover>",
                ]
            )
        )

        self.assertEqual(parse_verification_time(cbmc_root), 21.75)

    def test_build_row_marks_completed_when_cbmc_and_reports_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            experiment_dir = Path(temp_dir) / "experiment"
            proof_dir = experiment_dir / "suite" / "proof"
            build_dir = proof_dir / "build"
            (build_dir / "reports").mkdir(parents=True)
            (build_dir / "report" / "json").mkdir(parents=True)
            (build_dir / "report" / "html").mkdir(parents=True)
            (build_dir / "reports" / "cbmc.xml").write_text(
                "\n".join(
                    [
                        "<cprover>",
                        "<message><text>Runtime Symex: 1s</text></message>",
                        "<message><text>Runtime decision procedure: 1.5s</text></message>",
                        "<cprover-status>SUCCESS</cprover-status>",
                        "</cprover>",
                    ]
                )
            )
            (build_dir / "report" / "json" / "viewer-result.json").write_text(
                json.dumps({"viewer-result": {"prover": "success"}})
            )
            (build_dir / "report" / "html" / "index.html").write_text("<html></html>")
            (build_dir / "entry.goto").write_text("")

            row = build_row(
                experiment_dir=experiment_dir,
                proof_dir=proof_dir,
                metadata=MakeMetadata(entry="entry", cbmcflags="", proof_root=experiment_dir),
                run_result=RunResult(
                    make_ran=False,
                    clean_returncode=None,
                    make_returncode=None,
                    timed_out=False,
                    wall_time_s=None,
                ),
            )

        self.assertIs(row["compile_succeeded"], True)
        self.assertIs(row["verification_completed"], True)
        self.assertEqual(row["verification_time"], "2.500000")
        self.assertEqual(row["make_wall_time_s"], "")

    def test_existing_build_is_reused_without_wall_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            proof_dir = Path(temp_dir)
            (proof_dir / "build").mkdir()

            result = ensure_build(proof_dir, timeout_s=1, force_make=False)

        self.assertIs(result.make_ran, False)
        self.assertIsNone(result.wall_time_s)


if __name__ == "__main__":
    unittest.main()
