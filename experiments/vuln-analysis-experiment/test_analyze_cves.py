import csv
import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("analyze_cves.py")
SPEC = importlib.util.spec_from_file_location("analyze_cves", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
analyze_cves = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyze_cves
SPEC.loader.exec_module(analyze_cves)


class AnalyzeCvesErrorFoundTests(unittest.TestCase):
    def test_error_found_matches_viewer_report_location(self) -> None:
        target = {
            "affectedFunction": "cmd_write",
            "affectedFile": "subsys/settings/src/settings_shell.c",
            "sinkFunction": "cmd_write",
            "manifestationLines": [189],
        }
        viewer_result_json = {
            "viewer-result": {
                "results": {
                    "false": ["cmd_write.precondition_instance.3"],
                }
            }
        }
        viewer_property_json = {
            "viewer-property": {
                "properties": {
                    "cmd_write.precondition_instance.3": {
                        "class": "precondition",
                        "description": "memcpy destination region writeable",
                        "location": {
                            "file": "subsys/settings/src/settings_shell.c",
                            "function": "cmd_write",
                            "line": 189,
                        },
                    }
                }
            }
        }

        self.assertIs(
            analyze_cves.error_found_for_target(
                target,
                viewer_result_json,
                viewer_property_json,
                None,
            ),
            True,
        )

    def test_error_found_rejects_non_matching_lines(self) -> None:
        target = {
            "affectedFunction": "cmd_write",
            "affectedFile": "subsys/settings/src/settings_shell.c",
            "sinkFunction": "cmd_write",
            "manifestationLines": [189],
        }
        viewer_result_json = {
            "viewer-result": {
                "results": {
                    "false": ["cmd_write.precondition_instance.3"],
                }
            }
        }
        viewer_property_json = {
            "viewer-property": {
                "properties": {
                    "cmd_write.precondition_instance.3": {
                        "class": "precondition",
                        "description": "memcpy destination region writeable",
                        "location": {
                            "file": "subsys/settings/src/settings_shell.c",
                            "function": "cmd_write",
                            "line": 188,
                        },
                    }
                }
            }
        }

        self.assertIs(
            analyze_cves.error_found_for_target(
                target,
                viewer_result_json,
                viewer_property_json,
                None,
            ),
            False,
        )

    def test_error_found_falls_back_to_cbmc_xml(self) -> None:
        target = {
            "affectedFunction": "cmd_write",
            "affectedFile": "subsys/settings/src/settings_shell.c",
            "sinkFunction": "cmd_write",
            "manifestationLines": [189],
        }
        cbmc_root = ET.fromstring(
            "\n".join(
                [
                    "<cprover>",
                    '<result property="cmd_write.precondition_instance.3" status="FAILURE">',
                    '<location file="../../../subsys/settings/src/settings_shell.c" function="cmd_write" line="189"/>',
                    "</result>",
                    "</cprover>",
                ]
            )
        )

        self.assertIs(
            analyze_cves.error_found_for_target(target, None, None, cbmc_root),
            True,
        )


class AnalyzeCvesCsvTests(unittest.TestCase):
    def test_write_csv_uses_internal_row_keys_with_public_column_labels(self) -> None:
        row = {
            "cve_id": "CVE-TEST-1",
            "software": "zephyr",
            "config": "exp-codexup-0412",
            "tag": "exp-0412",
            "target_function": "cmd_write",
            "target_file": "subsys/settings/src/settings_shell.c",
            "vulnerability_type": "overflow",
            "sink": "cmd_write:189",
            "proof_found": True,
            "compile_succeeded": True,
            "verification_completes": True,
            "verification_time": "1.000000",
            "reachable_line_count": 10,
            "covered_line_count": 8,
            "line_coverage_pct": "80.000000",
            "reported_error_count": 1,
            "error_found": True,
            "sink_included": True,
            "sink_covered": True,
            "precondition_addressed": False,
            "cve_exposed_strict": True,
            "cve_exposed_partial": False,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "assessment.csv"
            analyze_cves.write_csv([row], output_path)

            with output_path.open(newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, analyze_cves.CSV_COLUMNS)
                written_row = next(reader)

        self.assertEqual(written_row["CVE ID"], "CVE-TEST-1")
        self.assertEqual(written_row["Software"], "zephyr")
        self.assertEqual(written_row["Config"], "exp-codexup-0412")
        self.assertEqual(written_row["Tag"], "exp-0412")
        self.assertEqual(written_row["Error Found"], "True")

    def test_column_order_places_config_and_error_fields_correctly(self) -> None:
        self.assertEqual(
            analyze_cves.CSV_COLUMNS,
            [
                "CVE ID",
                "Software",
                "Config",
                "Tag",
                "Target Function",
                "Target File",
                "Vulnerability Type",
                "Sink",
                "Proof Found",
                "Compile Succeeded",
                "Verification Completes",
                "Verification Time",
                "Reachable Line Count",
                "Covered Line Count",
                "Line Coverage %",
                "Reported Error Count",
                "Sink Included",
                "Sink Covered",
                "Error Found",
                "Precondition Addressed",
                "CVE Exposed Strict",
                "CVE Exposed Partial",
            ],
        )


class AnalyzeCvesProofDirResolutionTests(unittest.TestCase):
    def test_resolve_proof_dir_supports_flat_function_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            experiment_dir = Path(temp_dir) / "exp-codexup-0412"
            proof_dir = experiment_dir / "cmd_write"
            proof_dir.mkdir(parents=True)

            resolved_dir, relpath = analyze_cves.resolve_proof_dir(
                experiment_dir,
                "subsys/settings/src/settings_shell.c",
                "cmd_write",
            )

        self.assertEqual(resolved_dir, proof_dir)
        self.assertEqual(relpath, "cmd_write")


if __name__ == "__main__":
    unittest.main()
