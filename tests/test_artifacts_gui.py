from __future__ import annotations

import json
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
    from relay.gui.artifacts import ArtifactExplorerView, ArtifactGroup, ArtifactRecord, _safe_html, artifact_kind
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

    def test_open_actions_use_generic_label_and_artifact_parent_folder(self):
        view = ArtifactExplorerView()
        path = str(Path("/relay") / "review" / "result.json")
        record = ArtifactRecord.from_mapping(
            {
                "artifact_uid": "a-json",
                "role": "result",
                "relative_path": "result.json",
                "final_path": path,
            },
            is_primary=True,
        )
        folders: list[str] = []
        view.open_folder_requested.connect(folders.append)

        view.set_groups([ArtifactGroup("Result", (record,))], auto_select_primary=True)
        view._emit_open_folder()

        self.assertEqual(view.open_file_button.text(), "Open")
        self.assertEqual(folders, [str(Path("/relay") / "review")])

    def test_artifact_surface_surfaces_status_format_and_collapsible_details(self):
        view = ArtifactExplorerView()
        view.resize(900, 480)
        view.show()
        record = ArtifactRecord.from_mapping(
            {
                "artifact_uid": "candidate-html",
                "role": "final_report",
                "relative_path": "proposal.html",
                "final_path": "/relay/review/proposal.html",
                "mime_type": "text/html",
                "size": 2048,
                "publication_status": "candidate",
            },
            is_primary=True,
        )

        view.set_groups([ArtifactGroup("Review candidate", (record,))], auto_select_primary=True)
        QApplication.processEvents()

        item = view._find_tree_item("candidate-html")
        self.assertIsNotNone(item)
        self.assertEqual(view.artifact_tree.headerItem().text(1), "Status")
        self.assertEqual(view.artifact_tree.headerItem().text(2), "Format")
        self.assertIn("final_report", item.text(0))
        self.assertEqual(item.text(1), "Candidate")
        self.assertEqual(item.text(2), "HTML · 2.0 KB")
        self.assertEqual(view.format_label.text(), "HTML")
        self.assertEqual(view.status_label.text(), "Candidate")
        self.assertFalse(view.details_button.isChecked())

        view.details_button.click()
        self.assertTrue(view.details_button.isChecked())
        self.assertTrue(view.metadata_panel.isVisible())
        self.assertIn("/relay/review/proposal.html", view.path_label.text())

        view.details_button.click()
        self.assertFalse(view.metadata_panel.isVisible())
        view.close()

    def test_refresh_preserves_selected_artifact_and_cached_preview(self):
        view = ArtifactExplorerView()
        first = ArtifactRecord.from_mapping(
            {"artifact_uid": "result", "role": "result", "relative_path": "result.json"},
            is_primary=True,
        )
        second = ArtifactRecord.from_mapping({"artifact_uid": "notes", "role": "notes", "relative_path": "notes.md"})
        requests: list[str] = []
        view.preview_requested.connect(lambda uid, _review_id: requests.append(uid))
        view.set_groups([ArtifactGroup("Artifacts", (first, second))], auto_select_primary=True)
        view.select_artifact("notes")
        view.cache_content("notes", {"available": True, "text": "Keep this selection."})
        request_count = len(requests)

        refreshed_second = ArtifactRecord.from_mapping(
            {
                "artifact_uid": "notes",
                "role": "notes",
                "relative_path": "notes.md",
                "final_path": "/relay/artifacts/notes.md",
            }
        )
        view.set_groups([ArtifactGroup("Artifacts", (first, refreshed_second))], auto_select_primary=True)

        self.assertEqual(view.selected_record().artifact_uid, "notes")
        self.assertEqual(view.path_label.text(), "/relay/artifacts/notes.md")
        self.assertEqual(view.preview_stack.currentWidget(), view.text_preview)
        self.assertEqual(len(requests), request_count)

    def test_double_click_emits_selected_file_path(self):
        view = ArtifactExplorerView()
        path = str(Path("/relay") / "artifacts" / "manual.pdf")
        record = ArtifactRecord.from_mapping(
            {"artifact_uid": "pdf", "role": "document", "relative_path": "manual.pdf", "final_path": path},
            is_primary=True,
        )
        opened: list[str] = []
        view.open_file_requested.connect(opened.append)
        view.set_groups([ArtifactGroup("Artifacts", (record,))], auto_select_primary=True)

        view._on_item_double_clicked(view._find_tree_item("pdf"), 0)

        self.assertEqual(opened, [path])


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

        self.assertIs(view.preview_stack.currentWidget(), view.json_outline_preview)
        self.assertEqual(view.json_mode_button.text(), "Text view")
        self.assertFalse(view.json_mode_button.isChecked())
        view.json_mode_button.click()
        self.assertIs(view.preview_stack.currentWidget(), view.json_preview)
        self.assertEqual(view.json_mode_button.text(), "Tree view")
        self.assertEqual(view.json_preview.topLevelItem(0).text(0), "headline")
        self.assertGreater(view.json_preview.topLevelItem(1).childCount(), 0)
        self.assertIn('"headline"', view.raw_preview.toPlainText())

    def test_json_arrays_show_children_without_item_count_label(self):
        view = self._view("result.json")
        sources = [f"input/A{i}.json" for i in range(1, 6)]
        view.cache_content("result.json", {"available": True, "text": json.dumps({"sources": sources})})

        sources_item = view.json_preview.topLevelItem(0)
        self.assertEqual(sources_item.text(0), "sources")
        self.assertEqual(sources_item.text(1), "array")
        self.assertEqual(sources_item.childCount(), 5)
        self.assertEqual(sources_item.child(0).text(1), "input/A1.json")
        self.assertEqual(sources_item.child(0).text(2), "string")

    def test_json_scroll_position_survives_content_refresh(self):
        view = self._view("result.json")
        view.resize(800, 300)
        view.show()
        payload = json.dumps({"row": list(range(150))})
        view.cache_content("result.json", {"available": True, "text": payload})
        view.json_mode_button.click()
        QApplication.processEvents()
        scrollbar = view.json_preview.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        previous = scrollbar.value()
        self.assertGreater(previous, 0)

        view.cache_content("result.json", {"available": True, "text": payload})

        self.assertEqual(view.json_preview.verticalScrollBar().value(), previous)
        view.close()

    def test_artifact_tree_scroll_position_survives_refresh(self):
        view = ArtifactExplorerView()
        records = tuple(
            ArtifactRecord.from_mapping(
                {"artifact_uid": f"artifact-{index}", "role": "output", "relative_path": f"file-{index}.txt"}
            )
            for index in range(40)
        )
        view.resize(800, 300)
        view.show()
        view.set_groups([ArtifactGroup("Files", records)], auto_select_primary=False)
        QApplication.processEvents()
        scrollbar = view.artifact_tree.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        position = min(30, scrollbar.maximum())
        scrollbar.setValue(position)

        view.set_groups([ArtifactGroup("Files", records)], auto_select_primary=False)
        QApplication.processEvents()

        self.assertEqual(scrollbar.value(), position)
        view.close()

    def test_files_group_hides_duplicate_result_json_but_keeps_real_result_group(self):
        view = ArtifactExplorerView()
        result = ArtifactRecord.from_mapping(
            {"artifact_uid": "result", "role": "result", "relative_path": "result.json"},
            is_primary=True,
        )
        duplicate = ArtifactRecord.from_mapping(
            {"artifact_uid": "duplicate", "role": "file", "relative_path": "result.json"}
        )
        notes = ArtifactRecord.from_mapping({"artifact_uid": "notes", "relative_path": "notes.md"})

        view.set_groups(
            [
                ArtifactGroup("Result", (result,)),
                ArtifactGroup("Files", (duplicate, notes)),
            ],
            auto_select_primary=True,
        )

        self.assertEqual([view.artifact_tree.topLevelItem(i).text(0) for i in range(view.artifact_tree.topLevelItemCount())], ["Result", "Files"])
        files = view.artifact_tree.topLevelItem(1)
        self.assertEqual(files.childCount(), 1)
        self.assertIn("notes.md", files.child(0).text(0))
        self.assertEqual(view.selected_record().artifact_uid, "result")
        view.close()

    def test_json_outline_uses_same_size_headings_with_bold_top_levels(self):
        view = self._view("result.json")
        view.cache_content("result.json", {"available": True, "text": '{"section": {"child": {"value": 1}}}'})

        stylesheet = view.json_outline_preview.document().defaultStyleSheet()
        self.assertIn("font-size: 13px", stylesheet)
        self.assertIn("body, div, span", stylesheet)
        self.assertNotIn("h1, h2", stylesheet)
        self.assertIs(view.preview_stack.currentWidget(), view.json_outline_preview)
        view.close()

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

    def test_external_html_links_are_visible_but_not_loaded_or_clickable(self):
        rendered = _safe_html(
            '<p><a href="https://example.com/docs?q=1">Docs</a> '
            '<a href="mailto:relay@example.com">Email</a> '
            '<a href="file:///secret.txt">Local</a> '
            '<a href="javascript:alert(1)">Unsafe</a></p>'
        )

        self.assertIn("[External link: https://example.com/docs?q=1]", rendered)
        self.assertIn("[External mail link: mailto:relay@example.com]", rendered)
        self.assertIn("[Link omitted]", rendered)
        self.assertNotIn("<a", rendered)
        self.assertNotIn("javascript:", rendered)

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

        unavailable_with_path = self._view("manual.docx", final_path="C:/relay/manual.docx")
        self.assertTrue(unavailable_with_path.open_file_button.isEnabled())
        self.assertIn("C:/relay/manual.docx", unavailable_with_path.metadata_preview.text())


if __name__ == "__main__":
    unittest.main()
