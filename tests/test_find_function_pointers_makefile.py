import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

clang_stub = types.ModuleType("clang")
clang_cindex_stub = types.ModuleType("clang.cindex")
clang_cindex_stub.CursorKind = types.SimpleNamespace(
    CALL_EXPR="CALL_EXPR",
    FUNCTION_DECL="FUNCTION_DECL",
    MEMBER_REF_EXPR="MEMBER_REF_EXPR",
    ARRAY_SUBSCRIPT_EXPR="ARRAY_SUBSCRIPT_EXPR",
    UNEXPOSED_EXPR="UNEXPOSED_EXPR",
)
clang_cindex_stub.Index = type("Index", (), {"create": staticmethod(lambda: object())})
clang_cindex_stub.Config = type(
    "Config",
    (),
    {
        "loaded": False,
        "set_library_path": staticmethod(lambda _path: None),
        "set_library_file": staticmethod(lambda _path: None),
    },
)
clang_cindex_stub.conf = types.SimpleNamespace(get_cindex_library=lambda: object())
clang_stub.cindex = clang_cindex_stub
sys.modules.setdefault("clang", clang_stub)
sys.modules.setdefault("clang.cindex", clang_cindex_stub)

from stub_generator.find_function_pointers import analyze_from_makefile


class AnalyzeFromMakefileTests(unittest.TestCase):
    def test_analyze_from_makefile_uses_single_file_path_when_no_linked_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            harness_dir = temp_path / "proof"
            project_dir = temp_path / "project"
            harness_dir.mkdir()
            project_dir.mkdir()

            target_file = project_dir / "demo.c"
            target_file.write_text("void demo(void) {}\n", encoding="utf-8")
            makefile_path = harness_dir / "Makefile"
            makefile_path.write_text(
                "\n".join(
                    [
                        "ROOT ?= ../project",
                        "H_DEF = -DCONFIG_DEMO=1",
                        "H_INC = -I$(ROOT)/include",
                        "LINK = notes.txt",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "stub_generator.find_function_pointers.analyze_file",
                return_value=[{"call_id": "demo.function_pointer_call.1"}],
            ) as analyze_file_mock:
                result = analyze_from_makefile(str(target_file), "demo", str(makefile_path))

            self.assertEqual(result, [{"call_id": "demo.function_pointer_call.1"}])
            analyze_file_mock.assert_called_once_with(
                str(target_file),
                "demo",
                ["-DCONFIG_DEMO=1", f"-I{project_dir}/include"],
            )

    def test_analyze_from_makefile_uses_multi_file_path_when_linked_sources_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            harness_dir = temp_path / "proof"
            project_dir = temp_path / "project"
            harness_dir.mkdir()
            (project_dir / "src").mkdir(parents=True)

            target_file = project_dir / "demo.c"
            linked_file = project_dir / "src" / "helper.c"
            target_file.write_text("void demo(void) {}\n", encoding="utf-8")
            linked_file.write_text("void helper(void) {}\n", encoding="utf-8")
            makefile_path = harness_dir / "Makefile"
            makefile_path.write_text(
                "\n".join(
                    [
                        "ROOT ?= ../project",
                        "H_DEF = -DCONFIG_DEMO=1",
                        "H_INC = -I$(ROOT)/include",
                        "LINK = $(ROOT)/src/helper.c",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "stub_generator.find_function_pointers.analyze_files",
                return_value=[{"call_id": "demo.function_pointer_call.1"}],
            ) as analyze_files_mock:
                result = analyze_from_makefile(str(target_file), "demo", str(makefile_path))

            self.assertEqual(result, [{"call_id": "demo.function_pointer_call.1"}])
            analyze_files_mock.assert_called_once_with(
                str(target_file),
                "demo",
                [str(linked_file)],
                ["-DCONFIG_DEMO=1", f"-I{project_dir}/include"],
            )


if __name__ == "__main__":
    unittest.main()
