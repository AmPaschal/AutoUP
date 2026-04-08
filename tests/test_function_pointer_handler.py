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

from commons.utils import Status
from makefile.output_models import MakefileFields
from stub_generator.handle_function_pointers import FunctionPointerHandler


class FakeLLM:
    def chat_llm(self, *args, **kwargs):
        return (
            MakefileFields(
                analysis="ok",
                updated_makefile="ROOT ?= /tmp/project\nH_INC =\nH_DEF =\nLINK =\n",
                updated_harness="void harness(void) {}\n",
            ),
            {},
        )


class TestableFunctionPointerHandler(FunctionPointerHandler):
    def __init__(self, temp_dir: str):
        self.root_dir = str(Path(temp_dir) / "project")
        self.harness_dir = str(Path(temp_dir) / "proof")
        self.target_function = "demo"
        self.target_file_path = str(Path(self.root_dir) / "src" / "demo.c")
        self.harness_file_name = "demo_harness.c"
        self.harness_file_path = str(Path(self.harness_dir) / self.harness_file_name)
        self.makefile_path = str(Path(self.harness_dir) / "Makefile")
        self.snapshot_dir = str(Path(self.harness_dir) / "snapshots")
        self.project_container = object()
        self.args = SimpleNamespace()
        self.llm = FakeLLM()
        self._max_attempts = 1

        Path(self.target_file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.target_file_path).write_text("void demo(void) {}\n", encoding="utf-8")
        Path(self.harness_dir).mkdir(parents=True, exist_ok=True)
        Path(self.harness_file_path).write_text("void harness(void) {}\n", encoding="utf-8")
        Path(self.makefile_path).write_text(
            "\n".join([
                f"ROOT ?= {self.root_dir}",
                "H_INC =",
                "H_DEF =",
                "LINK =",
                "",
            ]),
            encoding="utf-8",
        )

    def prepare_initial_prompt(self, function_pointers):
        return "system", "user"

    def get_tools(self):
        return []

    def run_make(self, compile_only: bool = False) -> dict:
        return {
            "status": Status.SUCCESS,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }

    def log_task_attempt(self, *args, **kwargs):
        return None

    def log_agent_result(self, *args, **kwargs):
        return None

    def save_status(self, *args, **kwargs):
        return None


class FunctionPointerHandlerTests(unittest.TestCase):
    def test_compile_only_generation_returns_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = TestableFunctionPointerHandler(temp_dir)
            with mock.patch(
                "stub_generator.handle_function_pointers.analyze_file",
                return_value=[{"call_id": "demo.function_pointer_call.1"}],
            ):
                self.assertTrue(handler.generate(verify_after_generation=False))


if __name__ == "__main__":
    unittest.main()
