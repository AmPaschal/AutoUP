import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

litellm_stub = types.ModuleType("litellm")
litellm_stub.ModelResponse = object
litellm_stub.get_llm_provider = lambda name: (None, "openai")
sys.modules.setdefault("litellm", litellm_stub)

from scope_widener.scope_widener import ScopeWidener


class FakeMakefileGenerator:
    results_queue: list[bool] = []

    def __init__(self, *args, **kwargs):
        pass

    def generate(self) -> bool:
        if self.results_queue:
            return self.results_queue.pop(0)
        return True


class FakeAgent:
    def __init__(
        self,
        temp_dir: str,
        compile_results: list[dict] | None = None,
    ):
        self.temp_dir = Path(temp_dir)
        self.root_dir = str(self.temp_dir / "project")
        self.harness_dir = str(self.temp_dir / "proofs" / "demo" / "target")
        self.target_function = "target"
        self.harness_file_name = "target_harness.c"
        self.harness_file_path = str(Path(self.harness_dir) / self.harness_file_name)
        self.makefile_path = str(Path(self.harness_dir) / "Makefile")
        self.args = SimpleNamespace()
        self.project_container = object()
        self.compile_results = list(compile_results or [])
        self.run_make_calls: list[bool] = []
        self.backup_tags: list[str] = []

        Path(self.root_dir, "src").mkdir(parents=True, exist_ok=True)
        Path(self.harness_dir).mkdir(parents=True, exist_ok=True)
        Path(self.harness_dir).parent.joinpath("general-stubs.c").write_text(
            "void stub(void) {}\n",
            encoding="utf-8",
        )
        Path(self.harness_file_path).write_text(
            "void harness(void) {}\n",
            encoding="utf-8",
        )
        Path(self.makefile_path).write_text(
            "\n".join([
                f"ROOT ?= {self.root_dir}",
                "MAKE_INCLUDE_PATH ?= ..",
                "LINK = $(MAKE_INCLUDE_PATH)/general-stubs.c",
                "",
            ]),
            encoding="utf-8",
        )

    def get_makefile(self) -> str:
        return Path(self.makefile_path).read_text(encoding="utf-8")

    def update_makefile(self, makefile_content: str) -> None:
        Path(self.makefile_path).write_text(makefile_content, encoding="utf-8")

    def run_make(self, compile_only: bool = False) -> dict:
        self.run_make_calls.append(compile_only)
        result = dict(self.compile_results.pop(0))
        build_dir = Path(self.harness_dir) / "build"
        build_dir.mkdir(parents=True, exist_ok=True)

        if result.get("create_goto", True):
            build_dir.joinpath(f"{self.target_function}.goto").write_text(
                "goto\n",
                encoding="utf-8",
            )

        return {
            "status": result.get("status"),
            "exit_code": result.get("exit_code", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0.0),
            "stdout": "",
            "stderr": "",
        }

    def create_backup(self, tag: str) -> None:
        self.backup_tags.append(tag)
        harness_backup = Path(self.harness_dir) / f"{self.harness_file_name}.{tag}.backup"
        harness_backup.write_text(
            Path(self.harness_file_path).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        makefile_backup = Path(self.harness_dir) / f"Makefile.{tag}.backup"
        makefile_backup.write_text(
            Path(self.makefile_path).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        build_dir = Path(self.harness_dir) / "build"
        build_backup = Path(self.harness_dir) / f"build_backup.{tag}"
        if build_backup.exists():
            shutil.rmtree(build_backup)
        if build_dir.exists():
            shutil.copytree(build_dir, build_backup)

    def restore_backup(self, tag: str) -> None:
        Path(self.harness_file_path).write_text(
            Path(self.harness_dir, f"{self.harness_file_name}.{tag}.backup").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        Path(self.makefile_path).write_text(
            Path(self.harness_dir, f"Makefile.{tag}.backup").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        build_dir = Path(self.harness_dir) / "build"
        if build_dir.exists():
            shutil.rmtree(build_dir)
        build_backup = Path(self.harness_dir) / f"build_backup.{tag}"
        if build_backup.exists():
            shutil.copytree(build_backup, build_dir)

    def discard_backup(self, tag: str) -> None:
        for path in [
            Path(self.harness_dir) / f"{self.harness_file_name}.{tag}.backup",
            Path(self.harness_dir) / f"Makefile.{tag}.backup",
        ]:
            if path.exists():
                path.unlink()
        build_backup = Path(self.harness_dir) / f"build_backup.{tag}"
        if build_backup.exists():
            shutil.rmtree(build_backup)

    def execute_command(self, cmd: str, workdir: str | None = None, timeout: int | None = None) -> dict:
        return {"exit_code": 1, "stdout": "", "stderr": f"Unexpected command: {cmd}"}


class ScriptedScopeWidener(ScopeWidener):
    def __init__(
        self,
        agent,
        bodyless_sequences: list[list[dict]],
        source_map: dict[str, str],
    ):
        super().__init__(agent)
        self.bodyless_sequences = list(bodyless_sequences)
        self.source_map = source_map

    def extract_functions_without_body(self, goto_file: str) -> list[dict]:
        if self.bodyless_sequences:
            return self.bodyless_sequences.pop(0)
        return []

    def locate_function_source(
        self,
        function_name: str,
        declaration_hint: str = "",
        declaration_line: str = "",
    ) -> str | None:
        return self.source_map.get(function_name)


class ScopeWidenerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        fake_module = types.ModuleType("makefile_generator.makefile_generator")
        fake_module.MakefileGenerator = FakeMakefileGenerator
        self.makefile_generator_patch = mock.patch.dict(
            sys.modules,
            {"makefile_generator.makefile_generator": fake_module},
        )
        self.makefile_generator_patch.start()
        self.addCleanup(self.makefile_generator_patch.stop)

    def create_agent(
        self,
        compile_results: list[dict] | None = None,
    ) -> FakeAgent:
        return FakeAgent(
            self.temp_dir.name,
            compile_results=compile_results,
        )

    def create_source(self, relative_path: str) -> str:
        source_path = Path(self.temp_dir.name) / "project" / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text("void body(void) {}\n", encoding="utf-8")
        return str(source_path)

    def test_no_budget_and_unset_or_minimal_bound_do_not_widen(self):
        agent = self.create_agent()
        widener = ScriptedScopeWidener(agent, bodyless_sequences=[], source_map={})

        self.assertTrue(widener.widen_scope(scope_bound=None))
        self.assertEqual(agent.run_make_calls, [])

        self.assertTrue(widener.widen_scope(scope_bound=1))
        self.assertEqual(agent.run_make_calls, [])

    def test_bound_only_widens_without_full_verification_runs(self):
        FakeMakefileGenerator.results_queue = [True]
        source_one = self.create_source("src/one.c")
        agent = self.create_agent(
            compile_results=[{"exit_code": 0, "elapsed_seconds": 1.0}],
        )
        widener = ScriptedScopeWidener(
            agent,
            bodyless_sequences=[[{"name": "one"}]],
            source_map={"one": source_one},
        )

        self.assertTrue(widener.widen_scope(scope_bound=2))
        self.assertEqual(agent.run_make_calls, [True])
        self.assertIn("$(ROOT)/src/one.c", agent.get_makefile())

    def test_widen_scope_level_returns_complete_when_no_bodyless_functions(self):
        agent = self.create_agent(
            compile_results=[{"exit_code": 0, "elapsed_seconds": 1.0}],
        )
        widener = ScriptedScopeWidener(
            agent,
            bodyless_sequences=[[]],
            source_map={},
        )

        result = widener.widen_scope_level(current_level=1)

        self.assertEqual(result.outcome, "complete")
        self.assertEqual(result.level, 1)
        self.assertEqual(agent.run_make_calls, [True])

    def test_widen_scope_level_returns_failed_when_makefile_generator_cannot_repair(self):
        FakeMakefileGenerator.results_queue = [False]
        agent = self.create_agent(
            compile_results=[{"exit_code": 2, "elapsed_seconds": 1.0}],
        )
        widener = ScriptedScopeWidener(
            agent,
            bodyless_sequences=[],
            source_map={},
        )

        result = widener.widen_scope_level(current_level=1)

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.level, 1)

    def test_add_source_files_rewrites_existing_multiline_link_block(self):
        agent = self.create_agent()
        agent.update_makefile(
            "\n".join([
                f"ROOT ?= {agent.root_dir}",
                "MAKE_INCLUDE_PATH ?= ..",
                "LINK = $(MAKE_INCLUDE_PATH)/general-stubs.c \\",
                "      $(ROOT)/zephyr/drivers/usb/device/usb_dc_native_posix_adapt.c \\",
                "      $(ROOT)/zephyr/subsys/logging/log_minimal.c \\",
                "      $(ROOT)/zephyr/subsys/logging/log_msg.c",
                "",
            ])
        )
        new_one = self.create_source("zephyr/lib/os/printk.c")
        new_two = self.create_source(
            "zephyr/subsys/logging/frontends/log_frontend_dict_uart.c"
        )
        widener = ScriptedScopeWidener(agent, bodyless_sequences=[], source_map={})

        widener.add_source_files_to_makefile([new_one, new_two])

        makefile_content = agent.get_makefile()
        self.assertIn("$(ROOT)/zephyr/drivers/usb/device/usb_dc_native_posix_adapt.c \\", makefile_content)
        self.assertIn("$(ROOT)/zephyr/subsys/logging/log_msg.c \\", makefile_content)
        self.assertIn("$(ROOT)/zephyr/lib/os/printk.c \\", makefile_content)
        self.assertIn(
            "$(ROOT)/zephyr/subsys/logging/frontends/log_frontend_dict_uart.c",
            makefile_content,
        )
        self.assertNotIn(
            "$(ROOT)/zephyr/subsys/logging/frontends/log_frontend_dict_uart.c\n      $(ROOT)/",
            makefile_content,
        )

    def test_no_new_files_stops_cleanly(self):
        agent = self.create_agent(
            compile_results=[{"exit_code": 0, "elapsed_seconds": 1.0}],
        )
        widener = ScriptedScopeWidener(
            agent,
            bodyless_sequences=[[]],
            source_map={},
        )

        self.assertTrue(widener.widen_scope(scope_bound=3))
        self.assertEqual(agent.backup_tags, [])
        self.assertEqual(agent.run_make_calls, [True])

    def test_locate_function_source_ignores_generated_harness_candidates(self):
        agent = self.create_agent()
        real_source = self.create_source("src/net_buf.c")
        harness_candidate = Path(agent.harness_file_path)
        harness_candidate.write_text(
            "uint8_t *net_buf_simple_add_u8(struct net_buf_simple *buf, uint8_t val) { return 0; }\n",
            encoding="utf-8",
        )

        class CscopeAgent(FakeAgent):
            def __init__(self, base_agent: FakeAgent, cscope_stdout: str):
                self.__dict__ = base_agent.__dict__.copy()
                self._cscope_stdout = cscope_stdout

            def execute_command(self, cmd: str, workdir: str | None = None, timeout: int | None = None) -> dict:
                if cmd.startswith("cscope -dL -1 "):
                    return {
                        "exit_code": 0,
                        "stdout": self._cscope_stdout,
                        "stderr": "",
                    }
                return {"exit_code": 1, "stdout": "", "stderr": f"Unexpected command: {cmd}"}

        rel_harness = os.path.relpath(harness_candidate, agent.root_dir)
        rel_real = os.path.relpath(real_source, agent.root_dir)
        cscope_stdout = "\n".join([
            f"{rel_harness} context 1 uint8_t *net_buf_simple_add_u8(",
            f"{rel_real} context 1 uint8_t *net_buf_simple_add_u8(",
        ])
        cscope_agent = CscopeAgent(agent, cscope_stdout)
        widener = ScopeWidener(cscope_agent)

        chosen = widener.locate_function_source("net_buf_simple_add_u8")

        self.assertEqual(chosen, os.path.realpath(real_source))


if __name__ == "__main__":
    unittest.main()
