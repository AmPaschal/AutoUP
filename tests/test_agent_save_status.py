import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

litellm_stub = types.ModuleType("litellm")
litellm_stub.ModelResponse = object
litellm_stub.get_llm_provider = lambda name: (None, "openai")
sys.modules.setdefault("litellm", litellm_stub)

from agent import AIAgent


def make_agent(tmp_path: Path) -> AIAgent:
    agent = object.__new__(AIAgent)
    agent.harness_dir = str(tmp_path)
    agent.harness_file_name = "target_harness.c"
    agent.harness_file_path = str(tmp_path / agent.harness_file_name)
    agent.makefile_path = str(tmp_path / "Makefile")
    agent.snapshot_dir = str(tmp_path / "snapshots")
    return agent


class AgentSaveStatusTests(unittest.TestCase):
    def test_save_status_writes_stage_snapshots_under_snapshot_dir(self):
        tmp_path = Path(self._testMethodName)
        tmp_path.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))

        agent = make_agent(tmp_path)
        harness_content = "void harness(void) {}\n"
        makefile_content = "all:\n\t@true\n"

        Path(agent.harness_file_path).write_text(harness_content, encoding="utf-8")
        Path(agent.makefile_path).write_text(makefile_content, encoding="utf-8")

        agent.save_status("coverage")

        snapshot_dir = tmp_path / "snapshots"
        self.assertTrue(snapshot_dir.is_dir())
        self.assertEqual(
            (snapshot_dir / "target_harness.c.coverage").read_text(encoding="utf-8"),
            harness_content,
        )
        self.assertEqual(
            (snapshot_dir / "Makefile.coverage").read_text(encoding="utf-8"),
            makefile_content,
        )
        self.assertFalse((tmp_path / "target_harness.c.coverage").exists())
        self.assertFalse((tmp_path / "Makefile.coverage").exists())

    def test_create_backup_keeps_rollback_files_in_proof_root(self):
        tmp_path = Path(self._testMethodName)
        tmp_path.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))

        agent = make_agent(tmp_path)
        Path(agent.harness_file_path).write_text("original harness\n", encoding="utf-8")
        Path(agent.makefile_path).write_text("all:\n\t@true\n", encoding="utf-8")
        build_report_dir = tmp_path / "build" / "report" / "json"
        build_report_dir.mkdir(parents=True, exist_ok=True)
        (build_report_dir / "viewer-property.json").write_text("{}", encoding="utf-8")

        agent.create_backup("ABCD")

        self.assertTrue((tmp_path / "target_harness.c.ABCD.backup").exists())
        self.assertTrue((tmp_path / "Makefile.ABCD.backup").exists())
        self.assertTrue((tmp_path / "build_backup.ABCD").is_dir())
        self.assertFalse((tmp_path / "snapshots" / "target_harness.c.ABCD.backup").exists())
        self.assertFalse((tmp_path / "snapshots" / "Makefile.ABCD.backup").exists())
