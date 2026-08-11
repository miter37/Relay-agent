from __future__ import annotations

import os
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QImage, QPainter, QPdfWriter
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - GUI extra is optional
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

try:
    from relay.gui.artifacts import ArtifactExplorerView, ArtifactGroup, ArtifactRecord, artifact_kind
except ImportError as exc:  # RED phase: the shared module is not implemented yet.
    ArtifactExplorerView = None
    ArtifactGroup = None
    ArtifactRecord = None
    artifact_kind = None
    ARTIFACT_IMPORT_ERROR = str(exc)
else:
    ARTIFACT_IMPORT_ERROR = ""


class ArtifactRecordTests(unittest.TestCase):
    def setUp(self):
        if ArtifactRecord is None:
            self.fail(f"shared Artifact model is missing: {ARTIFACT_IMPORT_ERROR}")

    def test_normalize_and_merge_preserve_primary_and_review_scope(self):
        summary = ArtifactRecord.from_mapping(
            {"artifact_uid": "a-1", "role": "result", "relative_path": "result.json"},
            is_primary=True,
        )
        detail = ArtifactRecord.from_mapping(
            {
                "artifact_uid": "a-1",
                "role": "result",
                "relative_path": "result.json",
                "final_path": "/relay/review/result.json",
                "mime_type": "application/json",
                "size": 42,
                "sha256": "abc",
                "publication_status": "candidate",
            },
            review_id="review-1",
        )
        merged = ArtifactRecord.merge([summary, detail])

        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].is_primary)
        self.assertEqual(merged[0].review_id, "review-1")
        self.assertEqual(merged[0].final_path, "/relay/review/result.json")
        self.assertEqual(artifact_kind(merged[0]), "json")

    def test_mime_wins_over_misleading_extension(self):
        record = ArtifactRecord.from_mapping(
            {"artifact_uid": "a-2", "relative_path": "payload.bin", "mime_type": "text/csv"}
        )
        self.assertEqual(artifact_kind(record), "table")

    def test_uidless_records_use_stable_fallback_identity(self):
        first = ArtifactRecord.from_mapping(
            {"relative_path": "notes.md", "role": "notes", "job_id": "job-1"},
            node_id="research",
        )
        second = ArtifactRecord.from_mapping(
            {
                "relative_path": "notes.md",
                "role": "notes",
                "job_id": "job-1",
                "final_path": "/relay/artifacts/notes.md",
            },
            node_id="research",
        )
        merged = ArtifactRecord.merge([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].final_path, "/relay/artifacts/notes.md")


class ArtifactExplorerShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        if ArtifactExplorerView is None:
            self.fail(f"shared Artifact Explorer is missing: {ARTIFACT_IMPORT_ERROR}")

    def test_explorer_selects_primary_and_emits_scoped_preview(self):
        view = ArtifactExplorerView()
        record = ArtifactRecord.from_mapping(
            {
                "artifact_uid": "candidate-1",
                "role": "result",
                "relative_path": "result.json",
                "final_path": "/relay/review/result.json",
                "publication_status": "candidate",
            },
            review_id="review-1",
            is_primary=True,
        )
        requests: list[tuple[str, str]] = []
        view.preview_requested.connect(lambda uid, review_id: requests.append((uid, review_id)))

        view.set_groups([ArtifactGroup("Current candidate", (record,))], auto_select_primary=True)

        self.assertEqual(view.selected_record().artifact_uid, "candidate-1")
        self.assertEqual(requests, [("candidate-1", "review-1")])
        self.assertIn("/relay/review/result.json", view.path_label.text())

    def test_copy_and_open_actions_use_selected_full_path(self):
        view = ArtifactExplorerView()
        path = str(Path("/relay") / "artifacts" / "report.md")
        record = ArtifactRecord.from_mapping(
            {
                "artifact_uid": "a-md",
                "role": "notes",
                "relative_path": "report.md",
                "final_path": path,
            },
            is_primary=True,
        )
        opened: list[str] = []
        view.open_file_requested.connect(opened.append)

        view.set_groups([ArtifactGroup("Artifacts", (record,))], auto_select_primary=True)
        view._emit_copy_path()
        view._emit_open_file()

        self.assertEqual(view.path_label.text(), path)
        self.assertEqual(opened, [path])
        self.assertEqual(QApplication.clipboard().text(), path)


class ArtifactKindTests(unittest.TestCase):
    def setUp(self):
        if ArtifactRecord is None:
            self.fail(f"shared Artifact model is missing: {ARTIFACT_IMPORT_ERROR}")

    def test_classifies_all_primary_preview_families(self):
        expected = {
            "data.jsonl": "json_lines",
            "data.csv": "table",
            "README.md": "markdown",
            "page.html": "html",
            "script.py": "text",
            "cover.png": "image",
            "drawing.svg": "svg",
            "manual.pdf": "pdf",
            "bundle.zip": "archive",
            "report.docx": "unsupported",
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                record = ArtifactRecord.from_mapping({"artifact_uid": name, "relative_path": name})
                self.assertEqual(artifact_kind(record), kind)


class ArtifactRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        if ArtifactExplorerView is None:
            self.fail(f"shared Artifact Explorer is missing: {ARTIFACT_IMPORT_ERROR}")

    @staticmethod
    def _view(name: str, *, final_path: str = ""):
        view = ArtifactExplorerView()
        record = ArtifactRecord.from_mapping(
            {"artifact_uid": name, "role": "result", "relative_path": name, "final_path": final_path},
            is_primary=True,
        )
        view.set_groups([ArtifactGroup("Result", (record,))], auto_select_primary=True)
        return view

    def test_json_renders_as_structure_and_keeps_raw_toggle(self):
        view = self._view("result.json")
        view.cache_content("result.json", {"available": True, "text": '{"headline":"Relay","items":[1,2]}'})

        self.assertIs(view.preview_stack.currentWidget(), view.json_preview)
        self.assertEqual(view.json_preview.topLevelItem(0).text(0), "headline")
        self.assertGreater(view.json_preview.topLevelItem(1).childCount(), 0)
        self.assertIn('"headline"', view.raw_preview.toPlainText())

    def test_markdown_html_and_table_use_format_specific_widgets(self):
        markdown = self._view("readme.md")
        markdown.cache_content("readme.md", {"available": True, "text": "# Relay"})
        self.assertIs(markdown.preview_stack.currentWidget(), markdown.text_preview)
        self.assertIn("Relay", markdown.text_preview.toPlainText())

        html = self._view("report.html")
        html.cache_content("report.html", {"available": True, "text": "<h1>Report</h1><script>bad()</script>"})
        self.assertIs(html.preview_stack.currentWidget(), html.text_preview)
        self.assertIn("Report", html.text_preview.toPlainText())
        self.assertNotIn("bad()", html.text_preview.toHtml())

        table = self._view("data.csv")
        table.cache_content("data.csv", {"available": True, "text": "name,value\nRelay,1"})
        self.assertIs(table.preview_stack.currentWidget(), table.table_preview)
        self.assertEqual(table.table_preview.item(0, 0).text(), "Relay")

    def test_image_pdf_archive_and_unsupported_have_bounded_preview_states(self):
        with self.subTest(kind="image"):
            image_path = Path(self.id().replace(".", "_") + ".png")
            try:
                image = QImage(20, 20, QImage.Format_ARGB32)
                image.fill(0xFF336699)
                image.save(str(image_path))
                view = self._view("cover.png", final_path=str(image_path))
                self.assertIs(view.preview_stack.currentWidget(), view.image_preview)
            finally:
                image_path.unlink(missing_ok=True)

        with self.subTest(kind="pdf"):
            pdf_path = Path(self.id().replace(".", "_") + ".pdf")
            try:
                writer = QPdfWriter(str(pdf_path))
                painter = QPainter(writer)
                painter.drawText(40, 40, "Relay")
                painter.end()
                view = self._view("manual.pdf", final_path=str(pdf_path))
                self.assertIs(view.preview_stack.currentWidget(), view.pdf_view)
            finally:
                pdf_path.unlink(missing_ok=True)

        with self.subTest(kind="archive"):
            archive_path = Path(self.id().replace(".", "_") + ".zip")
            try:
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("notes.txt", "Relay")
                view = self._view("bundle.zip", final_path=str(archive_path))
                self.assertIs(view.preview_stack.currentWidget(), view.archive_preview)
                self.assertIn("notes.txt", view.archive_preview.toPlainText())
            finally:
                archive_path.unlink(missing_ok=True)

        unsupported = self._view("manual.docx")
        self.assertIs(unsupported.preview_stack.currentWidget(), unsupported.metadata_preview)
        self.assertIn("not available", unsupported.metadata_preview.text().casefold())


if __name__ == "__main__":
    unittest.main()
