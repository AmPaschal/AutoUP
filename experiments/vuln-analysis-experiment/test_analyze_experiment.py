import csv
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

build_row = analyze_experiment.build_row
format_optional_float = analyze_experiment.format_optional_float
initialize_row = analyze_experiment.initialize_row
mark_missing_proof = analyze_experiment.mark_missing_proof
parse_verification_time = analyze_experiment.parse_verification_time
recompute_api_cost = analyze_experiment.recompute_api_cost
resolve_proof_dir = analyze_experiment.resolve_proof_dir
verification_succeeds = analyze_experiment.verification_succeeds
write_csv = analyze_experiment.write_csv


class AnalyzeExperimentHelperTests(unittest.TestCase):
    def test_proof_root_prefixes_include_cbmc_relative_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "codexup-project" / "zephyrproject"
            experiment_dir = repo_root / "zephyr" / "cbmc" / "exp-codexup-0412"
            proof_root = experiment_dir
            proof_root.mkdir(parents=True)

            original_repo_root = analyze_experiment.REPO_ROOT
            analyze_experiment.REPO_ROOT = repo_root
            try:
                prefixes = analyze_experiment.proof_root_prefixes(proof_root, experiment_dir)
            finally:
                analyze_experiment.REPO_ROOT = original_repo_root

        self.assertIn("zephyr/cbmc/exp-codexup-0412", prefixes)
        self.assertIn("cbmc/exp-codexup-0412", prefixes)
        self.assertTrue(
            analyze_experiment.is_proof_side_file(
                "cbmc/exp-codexup-0412/gatt_find_info_rsp/gatt_find_info_rsp_harness.c",
                prefixes,
            )
        )

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

    def test_resolve_proof_dir_supports_nested_and_flat_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            experiment_dir = Path(temp_dir) / "exp-0413"
            nested_proof_dir = experiment_dir / "function_rndis" / "queue_encapsulated_cmd"
            nested_proof_dir.mkdir(parents=True)

            resolved_dir, relpath = resolve_proof_dir(
                experiment_dir,
                "zephyr/subsys/usb/device/class/netusb/function_rndis.c",
                "queue_encapsulated_cmd",
            )

            self.assertEqual(resolved_dir, nested_proof_dir)
            self.assertEqual(relpath, "function_rndis/queue_encapsulated_cmd")

        with tempfile.TemporaryDirectory() as temp_dir:
            experiment_dir = Path(temp_dir) / "exp-0413"
            flat_proof_dir = experiment_dir / "queue_encapsulated_cmd"
            flat_proof_dir.mkdir(parents=True)

            resolved_dir, relpath = resolve_proof_dir(
                experiment_dir,
                "zephyr/subsys/usb/device/class/netusb/function_rndis.c",
                "queue_encapsulated_cmd",
            )

            self.assertEqual(resolved_dir, flat_proof_dir)
            self.assertEqual(relpath, "queue_encapsulated_cmd")

    def test_verification_succeeds_ignores_excluded_failures(self) -> None:
        viewer_result_json = {
            "viewer-result": {
                "results": {
                    "false": [
                        "foo.no-body.bar",
                        "foo.unwind.1",
                        "foo.unwinding_assertion.1",
                    ]
                }
            }
        }
        viewer_property_json = {
            "viewer-property": {
                "properties": {
                    "foo.unwinding_assertion.1": {
                        "class": "unwinding assertion",
                        "location": {"file": "zephyr/a.c", "function": "foo", "line": 7},
                    }
                }
            }
        }

        self.assertIs(
            verification_succeeds(viewer_result_json, viewer_property_json),
            True,
        )

    def test_mark_missing_proof_renders_na_values(self) -> None:
        row = initialize_row(
            software="zephyr",
            config="scope-1",
            tag="exp-0413",
            source_file="zephyr/subsys/a.c",
            target_function="demo",
            proof_relpath="a/demo",
            proof_found=False,
        )

        mark_missing_proof(row)

        self.assertEqual(row["proof_found"], False)
        self.assertEqual(row["compile_succeeded"], "N/A")
        self.assertEqual(row["api_cost"], "N/A")
        self.assertEqual(row["target_function"], "demo")


class AnalyzeExperimentCsvTests(unittest.TestCase):
    def test_write_csv_uses_public_column_labels_in_order(self) -> None:
        row = {column_id: "" for column_id in analyze_experiment.COLUMN_ORDER}
        row.update(
            {
                "software": "zephyr",
                "config": "scope-1",
                "tag": "exp-0413",
                "source_file": "zephyr/subsys/demo.c",
                "target_function": "demo",
                "proof_relpath": "demo/demo",
                "proof_found": True,
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "assessment.csv"
            write_csv([row], output_path)

            with output_path.open(newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, analyze_experiment.CSV_COLUMNS)
                written_row = next(reader)

        self.assertEqual(written_row["Software"], "zephyr")
        self.assertEqual(written_row["Config"], "scope-1")
        self.assertEqual(written_row["Tag"], "exp-0413")


class AnalyzeExperimentMetricsTests(unittest.TestCase):
    def test_recompute_api_cost_matches_metric_summary_pricing_logic(self) -> None:
        records = [
            {
                "type": "task_attempt",
                "llm_data": {
                    "model_name": "test-model",
                    "token_usage": {
                        "input_tokens": 1000,
                        "cached_tokens": 200,
                        "output_tokens": 300,
                    },
                },
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            pricing_path = Path(temp_dir) / "model_pricing.json"
            pricing_path.write_text(
                json.dumps(
                    {
                        "test-model": {
                            "input": 2.0,
                            "cached": 1.0,
                            "output": 4.0,
                        }
                    }
                )
            )
            original_pricing_path = analyze_experiment.MODEL_PRICING_PATH
            analyze_experiment.MODEL_PRICING_PATH = pricing_path
            try:
                cost = recompute_api_cost(records)
            finally:
                analyze_experiment.MODEL_PRICING_PATH = original_pricing_path

        expected = ((800 / 1_000_000) * 2.0) + ((200 / 1_000_000) * 1.0) + ((300 / 1_000_000) * 4.0)
        self.assertEqual(cost, f"{expected:.4f}")

    def test_build_row_reads_metrics_coverage_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspace"
            experiment_dir = workspace_root / "zephyr" / "cbmc" / "exp-0413"
            proof_dir = experiment_dir / "function_rndis" / "queue_encapsulated_cmd"
            build_dir = proof_dir / "build"
            report_json_dir = build_dir / "report" / "json"
            report_html_dir = build_dir / "report" / "html"
            reports_dir = build_dir / "reports"
            metrics_dir = workspace_root / "output-2026-04-13_13-00-33"

            report_json_dir.mkdir(parents=True)
            report_html_dir.mkdir(parents=True)
            reports_dir.mkdir(parents=True)
            metrics_dir.mkdir(parents=True)
            (workspace_root / "zephyr" / "subsys" / "usb" / "device" / "class" / "netusb").mkdir(parents=True)

            target_file = "zephyr/subsys/usb/device/class/netusb/function_rndis.c"
            target_function = "queue_encapsulated_cmd"

            (proof_dir / "Makefile").write_text(
                "\n".join(
                    [
                        f"H_ENTRY = {target_function}",
                        "H_CBMCFLAGS = --unwind 2 --unwindset demo.0:4,helper.1:7",
                        "all:",
                        "\t@true",
                    ]
                )
            )
            (proof_dir / f"{target_function}_harness.c").write_text(
                "\n".join(
                    [
                        f'#include "{target_file}"',
                        "void harness(void) {",
                        "    int arg;",
                        "    __CPROVER_assume(arg > 0);",
                        f"    {target_function}(arg);",
                        "}",
                    ]
                )
            )

            (reports_dir / "cbmc.xml").write_text(
                "\n".join(
                    [
                        "<cprover>",
                        "<message><text>Runtime Symex: 1s</text></message>",
                        "<message><text>Runtime decision procedure: 2s</text></message>",
                        "<cprover-status>FAILURE</cprover-status>",
                        "</cprover>",
                    ]
                )
            )
            (report_html_dir / "index.html").write_text("<html></html>")
            (report_json_dir / "viewer-result.json").write_text(
                json.dumps(
                    {
                        "viewer-result": {
                            "prover": "failure",
                            "results": {
                                "false": [
                                    "queue_encapsulated_cmd.pointer_dereference.1",
                                    "queue_encapsulated_cmd.pointer_dereference.2",
                                    "helper.no-body.external",
                                ]
                            },
                        }
                    }
                )
            )
            (report_json_dir / "viewer-property.json").write_text(
                json.dumps(
                    {
                        "viewer-property": {
                            "properties": {
                                "queue_encapsulated_cmd.pointer_dereference.1": {
                                    "class": "pointer dereference",
                                    "location": {
                                        "file": target_file,
                                        "function": target_function,
                                        "line": 42,
                                    },
                                },
                                "queue_encapsulated_cmd.pointer_dereference.2": {
                                    "class": "pointer dereference",
                                    "location": {
                                        "file": target_file,
                                        "function": target_function,
                                        "line": 42,
                                    },
                                },
                            }
                        }
                    }
                )
            )
            (report_json_dir / "viewer-reachable.json").write_text(
                json.dumps(
                    {
                        "viewer-reachable": {
                            "reachable": {
                                "zephyr/cbmc/exp-0413/function_rndis/queue_encapsulated_cmd/queue_encapsulated_cmd_harness.c": [
                                    "harness"
                                ],
                                "zephyr/cbmc/exp-0413/function_rndis/general-stubs.c": ["memcpy"],
                                target_file: [target_function],
                                "zephyr/subsys/usb/device/class/netusb/helper.c": [
                                    "helper_one",
                                    "helper_two",
                                ],
                                "zephyr/include/demo.h": ["helper_inline"],
                            }
                        }
                    }
                )
            )
            (report_json_dir / "viewer-coverage.json").write_text(
                json.dumps(
                    {
                        "viewer-coverage": {
                            "function_coverage": {
                                "zephyr/cbmc/exp-0413/function_rndis/queue_encapsulated_cmd/queue_encapsulated_cmd_harness.c": {
                                    "harness": {"total": 12, "hit": 12},
                                    "helper_model": {"total": 8, "hit": 6},
                                },
                                "zephyr/cbmc/exp-0413/function_rndis/general-stubs.c": {
                                    "memcpy": {"total": 5, "hit": 5}
                                },
                                target_file: {
                                    target_function: {"total": 10, "hit": 7},
                                },
                                "zephyr/subsys/usb/device/class/netusb/helper.c": {
                                    "helper_fn": {"total": 20, "hit": 10},
                                },
                            }
                        }
                    }
                )
            )
            (proof_dir / "vulnerability-report.json").write_text(
                json.dumps(
                    {
                        "vulnerabilities": [
                            {
                                "error_id": "err-1",
                                "violated_preconditions": [{"precondition": "a"}],
                            },
                            {
                                "error_id": "err-2",
                                "violated_preconditions": [],
                            },
                            {
                                "error_id": "err-3",
                                "violated_preconditions": [{"precondition": "b"}],
                            },
                        ]
                    }
                )
            )

            metrics_path = metrics_dir / "metrics-function_rndis-queue_encapsulated_cmd.jsonl"
            metrics_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "task_attempt",
                                "agent_name": "InitialHarnessGenerator",
                                "llm_data": {
                                    "model_name": "test-model",
                                    "token_usage": {
                                        "input_tokens": 1000,
                                        "cached_tokens": 200,
                                        "output_tokens": 300,
                                        "total_tokens": 1300,
                                    },
                                    "function_call_count": 0,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "task_attempt",
                                "agent_name": "debugger",
                                "llm_data": {
                                    "model_name": "test-model",
                                    "token_usage": {
                                        "input_tokens": 500,
                                        "cached_tokens": 100,
                                        "output_tokens": 200,
                                        "total_tokens": 700,
                                    },
                                    "function_call_count": 0,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "agent_result",
                                "agent_name": "InitialHarnessGenerator",
                                "data": {
                                    "compilation_status": True,
                                    "verification_status": True,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "agent_name": "InitialHarnessGenerator",
                                "elapsed_time": 1.5,
                            }
                        ),
                        json.dumps(
                            {
                                "agent_name": "debugger",
                                "elapsed_time": 2.25,
                            }
                        ),
                    ]
                )
            )

            pricing_path = workspace_root / "model_pricing.json"
            pricing_path.write_text(
                json.dumps(
                    {
                        "test-model": {
                            "input": 2.0,
                            "cached": 1.0,
                            "output": 4.0,
                        }
                    }
                )
            )

            original_pricing_path = analyze_experiment.MODEL_PRICING_PATH
            analyze_experiment.MODEL_PRICING_PATH = pricing_path
            try:
                row = build_row(
                    experiment_dir=experiment_dir,
                    proof_dir=proof_dir,
                    target_file=target_file,
                    target_function=target_function,
                    config="scope-2",
                    metrics_source=metrics_dir,
                )
            finally:
                analyze_experiment.MODEL_PRICING_PATH = original_pricing_path

        expected_cost = (
            ((800 / 1_000_000) * 2.0) + ((200 / 1_000_000) * 1.0) + ((300 / 1_000_000) * 4.0)
            + ((400 / 1_000_000) * 2.0) + ((100 / 1_000_000) * 1.0) + ((200 / 1_000_000) * 4.0)
        )

        self.assertEqual(row["software"], "zephyr")
        self.assertEqual(row["config"], "scope-2")
        self.assertEqual(row["tag"], "exp-0413")
        self.assertEqual(row["proof_relpath"], "function_rndis/queue_encapsulated_cmd")
        self.assertIs(row["proof_found"], True)
        self.assertIs(row["compile_succeeded"], True)
        self.assertIs(row["links_target"], True)
        self.assertIs(row["semantic_valid"], True)
        self.assertIs(row["verification_completes"], True)
        self.assertEqual(row["verification_time"], "3.000000")
        self.assertIs(row["verification_succeeds"], False)
        self.assertEqual(row["target_function_reachable_line_count"], 10)
        self.assertEqual(row["target_function_covered_line_count"], 7)
        self.assertEqual(row["target_function_line_coverage_pct"], "70.000000")
        self.assertEqual(row["program_reachable_line_count"], 30)
        self.assertEqual(row["program_covered_line_count"], 17)
        self.assertEqual(row["program_line_coverage_pct"], format_optional_float((17 / 30) * 100))
        self.assertEqual(row["overall_reachable_line_count"], 55)
        self.assertEqual(row["overall_covered_line_count"], 40)
        self.assertEqual(row["overall_line_coverage_pct"], format_optional_float((40 / 55) * 100))
        self.assertEqual(row["property_violations"], 1)
        self.assertEqual(row["precondition_violations"], 2)
        self.assertEqual(row["generation_time"], "3.750000")
        self.assertEqual(row["api_cost"], f"{expected_cost:.4f}")
        self.assertEqual(row["harness_size_loc"], 12)
        self.assertEqual(row["source_files_in_scope"], 2)
        self.assertEqual(row["functions_in_scope"], 4)
        self.assertEqual(row["loop_unwindset_count"], 2)
        self.assertEqual(row["loop_unwind_min"], 2)
        self.assertEqual(row["loop_unwind_max"], 7)
        self.assertEqual(row["model_used_variable_count"], 1)
        self.assertEqual(row["assumption_variable_count"], 1)
        self.assertEqual(row["function_model_count"], 1)
        self.assertEqual(row["function_model_avg_loc"], "8.000000")


if __name__ == "__main__":
    unittest.main()
