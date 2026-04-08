import unittest
from pathlib import Path

from tests import run_tests as batch_run_tests


class RunTestsPathResolutionTests(unittest.TestCase):
    def test_resolve_entry_source_paths_anchors_relative_paths_to_base_dir(self):
        entries = [
            {
                "function_name": "demo",
                "source_file": "zephyr/subsys/demo.c",
            },
            {
                "function_name": "already_absolute",
                "source_file": "/tmp/project/src/already.c",
            },
        ]

        resolved = batch_run_tests.resolve_entry_source_paths(entries, "../zephyr")

        self.assertEqual(
            resolved[0]["source_file"],
            str((Path("../zephyr").resolve() / "zephyr/subsys/demo.c").resolve()),
        )
        self.assertEqual(resolved[1]["source_file"], "/tmp/project/src/already.c")


if __name__ == "__main__":
    unittest.main()
