import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stub_generator.makefile_helpers import build_analysis_args, resolve_linked_source_files


class MakefileHelpersTests(unittest.TestCase):
    def test_build_analysis_args_expands_root_and_preserves_defs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            harness_dir = temp_path / "proof"
            project_dir = temp_path / "project"
            harness_dir.mkdir()
            project_dir.mkdir()

            makefile_content = "\n".join(
                [
                    "ROOT ?= ../project",
                    "H_DEF = -DCONFIG_DEMO=1 \\",
                    "        -DOTHER_FLAG=2",
                    "H_INC = -I$(ROOT)/include \\",
                    "        -include $(ROOT)/generated/autoconf.h",
                    "",
                ]
            )

            args = build_analysis_args(makefile_content, str(harness_dir))

            self.assertEqual(args[:2], ["-DCONFIG_DEMO=1", "-DOTHER_FLAG=2"])
            self.assertEqual(
                args[2:],
                [
                    f"-I{project_dir}/include",
                    "-include",
                    f"{project_dir}/generated/autoconf.h",
                ],
            )

    def test_resolve_linked_source_files_handles_root_and_make_include_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            harness_dir = temp_path / "proof"
            project_dir = temp_path / "project"
            make_include_dir = harness_dir.parent
            harness_dir.mkdir()
            (project_dir / "src").mkdir(parents=True)

            root_link = project_dir / "src" / "linked.c"
            include_link = make_include_dir / "general-stubs.c"
            root_link.write_text("void linked(void) {}\n", encoding="utf-8")
            include_link.write_text("void helper(void) {}\n", encoding="utf-8")

            makefile_content = "\n".join(
                [
                    "ROOT ?= ../project",
                    "LINK = $(ROOT)/src/linked.c \\",
                    "       $(MAKE_INCLUDE_PATH)/general-stubs.c \\",
                    "       notes.txt",
                    "",
                ]
            )

            linked_files = resolve_linked_source_files(makefile_content, str(harness_dir))

            self.assertEqual(
                linked_files,
                [str(root_link), str(include_link)],
            )


if __name__ == "__main__":
    unittest.main()
