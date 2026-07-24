from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.errors import RelayError
from relay.target_workspace import (
    apply_delta,
    calculate_delta,
    copy_delta_to_artifacts,
    infer_target_path,
    prepare_target_workspace,
    resolve_target_path,
)


class TargetPathInferenceTests(unittest.TestCase):
    def test_infers_one_absolute_path_for_write_task(self):
        self.assertEqual(infer_target_path(r"D:\temp 폴더에 계산기 코드를 만들어줘"), "D:\\temp")
        self.assertEqual(infer_target_path(r"`D:\My Work`에 파일을 생성해줘"), "D:\\My Work")

    def test_does_not_infer_for_analysis_only_language(self):
        self.assertIsNone(infer_target_path(r"D:\one 내용을 검토해서 설명해줘"))

    def test_rejects_ambiguous_write_paths(self):
        with self.assertRaises(RelayError) as context:
            infer_target_path(r"D:\one 파일을 D:\two 쪽으로 복사해줘")
        self.assertEqual(context.exception.code, "TARGET_PATH_AMBIGUOUS")

    def test_rejects_file_instead_of_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "one.py"
            file_path.write_text("x = 1", encoding="utf-8")
            with self.assertRaises(RelayError) as context:
                resolve_target_path(str(file_path))
            self.assertEqual(context.exception.code, "TARGET_PATH_INVALID")


class TargetWorkspaceTests(unittest.TestCase):
    def test_new_folder_is_created_and_changed_files_are_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "requested"
            workspace = prepare_target_workspace(target, root / "workspace" / "target")
            (workspace.working_copy / "calculator.py").write_text("print(2 + 2)\n", encoding="utf-8")
            delta = calculate_delta(workspace)
            artifacts = root / "artifacts"

            copy_delta_to_artifacts(workspace, delta, artifacts)
            apply_delta(workspace, delta)

            self.assertEqual(delta.added, ("calculator.py",))
            self.assertEqual((target / "calculator.py").read_text(encoding="utf-8"), "print(2 + 2)\n")
            self.assertEqual(
                (artifacts / "calculator.py").read_text(encoding="utf-8"),
                "print(2 + 2)\n",
            )

    def test_existing_folder_receives_only_the_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "project"
            target.mkdir()
            (target / "app.py").write_text("old\n", encoding="utf-8")
            (target / "keep.py").write_text("keep\n", encoding="utf-8")
            (target / "remove.py").write_text("remove\n", encoding="utf-8")
            (target / ".git").mkdir()
            (target / ".git" / "config").write_text("keep git\n", encoding="utf-8")
            workspace = prepare_target_workspace(target, root / "workspace" / "target")
            (workspace.working_copy / "app.py").write_text("new\n", encoding="utf-8")
            (workspace.working_copy / "added.py").write_text("added\n", encoding="utf-8")
            (workspace.working_copy / "remove.py").unlink()
            delta = calculate_delta(workspace)
            artifacts = root / "artifacts"

            copy_delta_to_artifacts(workspace, delta, artifacts)
            apply_delta(workspace, delta)

            self.assertEqual((target / "app.py").read_text(encoding="utf-8"), "new\n")
            self.assertEqual((target / "keep.py").read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((target / "remove.py").exists())
            self.assertTrue((target / ".git" / "config").is_file())
            self.assertEqual((artifacts / "added.py").read_text(encoding="utf-8"), "added\n")
            self.assertFalse((artifacts / "keep.py").exists())

    def test_external_change_causes_conflict_instead_of_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "project"
            target.mkdir()
            source = target / "app.py"
            source.write_text("old\n", encoding="utf-8")
            workspace = prepare_target_workspace(target, root / "workspace" / "target")
            (workspace.working_copy / "app.py").write_text("agent\n", encoding="utf-8")
            delta = calculate_delta(workspace)
            source.write_text("external\n", encoding="utf-8")

            with self.assertRaises(RelayError) as context:
                apply_delta(workspace, delta)

            self.assertEqual(context.exception.code, "TARGET_CONFLICT")
            self.assertEqual(source.read_text(encoding="utf-8"), "external\n")


if __name__ == "__main__":
    unittest.main()
