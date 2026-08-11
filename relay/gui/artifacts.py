"""Shared read-only Artifact records, grouping, and preview shell."""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import tarfile
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from html import escape as html_escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

TEXT_SUFFIXES = {
    ".bash",
    ".bat",
    ".c",
    ".cfg",
    ".cmd",
    ".conf",
    ".cpp",
    ".css",
    ".diff",
    ".env",
    ".fish",
    ".go",
    ".h",
    ".hpp",
    ".ini",
    ".java",
    ".js",
    ".jsx",
    ".jsonl",
    ".log",
    ".md",
    ".markdown",
    ".ndjson",
    ".patch",
    ".ps1",
    ".py",
    ".r",
    ".rst",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}

MAX_TEXT_BYTES = 262_144
MAX_TABLE_ROWS = 500
MAX_TABLE_COLUMNS = 100
MAX_ARCHIVE_ENTRIES = 2_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_PDF_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_uid: str
    role: str = "output"
    name: str = ""
    relative_path: str = ""
    final_path: str = ""
    mime_type: str = ""
    size: int | None = None
    sha256: str = ""
    publication_status: str = "published"
    producer: str = "worker"
    source_task_run_id: str = ""
    node_id: str = ""
    review_id: str = ""
    is_primary: bool = False

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        node_id: str = "",
        review_id: str = "",
        is_primary: bool = False,
    ) -> ArtifactRecord:
        relative_path = str(raw.get("relative_path") or raw.get("name") or "")
        raw_size = raw.get("size")
        try:
            size = int(raw_size) if raw_size is not None else None
        except (TypeError, ValueError):
            size = None
        role = str(raw.get("role") or "output")
        source_task_run_id = str(raw.get("job_id") or raw.get("task_run_id") or "")
        stable_uid = "path:" + "|".join((source_task_run_id, node_id, role, relative_path))
        return cls(
            artifact_uid=str(raw.get("artifact_uid") or stable_uid),
            role=role,
            name=Path(relative_path).name if relative_path else "",
            relative_path=relative_path,
            final_path=str(raw.get("final_path") or ""),
            mime_type=str(raw.get("mime_type") or ""),
            size=size,
            sha256=str(raw.get("sha256") or ""),
            publication_status=str(raw.get("publication_status") or "published"),
            producer=str(raw.get("producer") or "worker"),
            source_task_run_id=source_task_run_id,
            node_id=str(raw.get("node_id") or node_id),
            review_id=str(raw.get("review_id") or review_id),
            is_primary=bool(raw.get("is_primary") or is_primary),
        )

    def identity(self) -> str:
        if self.artifact_uid:
            return f"uid:{self.artifact_uid}"
        return "path:" + "|".join(
            (self.source_task_run_id, self.node_id, self.role, self.relative_path)
        )

    @classmethod
    def merge(cls, records: Iterable[ArtifactRecord]) -> list[ArtifactRecord]:
        merged: list[ArtifactRecord] = []
        indexes: dict[str, int] = {}
        for record in records:
            key = record.identity()
            index = indexes.get(key)
            if index is None:
                indexes[key] = len(merged)
                merged.append(record)
                continue
            current = merged[index]
            changes: dict[str, Any] = {}
            for field_name in (
                "artifact_uid",
                "role",
                "name",
                "relative_path",
                "final_path",
                "mime_type",
                "size",
                "sha256",
                "publication_status",
                "producer",
                "source_task_run_id",
                "node_id",
                "review_id",
            ):
                incoming = getattr(record, field_name)
                existing = getattr(current, field_name)
                if incoming not in (None, "") and existing in (None, ""):
                    changes[field_name] = incoming
            changes["is_primary"] = current.is_primary or record.is_primary
            merged[index] = replace(current, **changes)
        return merged


@dataclass(frozen=True, slots=True)
class ArtifactGroup:
    label: str
    records: tuple[ArtifactRecord, ...]


def artifact_kind(record: ArtifactRecord) -> str:
    mime = record.mime_type.casefold()
    suffix = Path(record.relative_path or record.name).suffix.casefold()
    if mime == "application/json" or mime.endswith("+json") or suffix == ".json":
        return "json"
    if mime in {"application/x-ndjson", "application/ndjson"} or suffix in {".jsonl", ".ndjson"}:
        return "json_lines"
    if mime in {"text/csv", "text/tab-separated-values"} or suffix in {".csv", ".tsv"}:
        return "table"
    if mime in {"text/markdown", "text/x-markdown"} or suffix in {".md", ".markdown"}:
        return "markdown"
    if mime == "text/html" or suffix in {".html", ".htm"}:
        return "html"
    if mime == "image/svg+xml" or suffix == ".svg":
        return "svg"
    if mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image"
    if mime == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if suffix in {".zip", ".tar", ".tgz", ".gz"} or mime in {
        "application/zip",
        "application/x-tar",
        "application/gzip",
    }:
        return "archive"
    if mime.startswith("text/") or suffix in TEXT_SUFFIXES:
        return "text"
    guessed = mimetypes.guess_type(record.relative_path or record.name)[0] or ""
    if guessed.startswith("text/"):
        return "text"
    return "unsupported"


class _SafeHTMLParser(HTMLParser):
    _allowed = {
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
    _blocked = {"audio", "embed", "iframe", "img", "link", "object", "script", "style", "video"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self._blocked_depth:
            self._blocked_depth += 1
            return
        if tag in self._blocked:
            self._blocked_depth = 1
            return
        if tag not in self._allowed:
            return
        safe_attrs: list[str] = []
        for key, value in attrs:
            key = key.casefold()
            if key in {"colspan", "rowspan"} and value and value.isdigit():
                safe_attrs.append(f'{key}="{value}"')
        suffix = f" {' '.join(safe_attrs)}" if safe_attrs else ""
        self.parts.append(f"<{tag}{suffix}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() in self._allowed:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._blocked_depth:
            self._blocked_depth -= 1
            return
        if tag in self._allowed and tag not in {"br", "hr"}:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self.parts.append(html_escape(data))

    def handle_entityref(self, name: str) -> None:
        if not self._blocked_depth:
            self.parts.append(f"&amp;{html_escape(name)};")

    def handle_charref(self, name: str) -> None:
        if not self._blocked_depth:
            self.parts.append(f"&amp;#{html_escape(name)};")


def _safe_html(value: str) -> str:
    parser = _SafeHTMLParser()
    parser.feed(value[:MAX_TEXT_BYTES])
    parser.close()
    return "".join(parser.parts)


class _SafeTextBrowser(QTextBrowser):
    """Rich-text surface that never follows links or fetches resources."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def loadResource(self, _resource_type, _name):  # noqa: N802 - Qt virtual method
        return None


class ArtifactExplorerView(QWidget):
    """Read-only grouped Artifact browser shared by all evidence screens."""

    preview_requested = Signal(str, str)
    open_file_requested = Signal(str)
    open_folder_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups: list[ArtifactGroup] = []
        self._records_by_uid: dict[str, ArtifactRecord] = {}
        self._content_by_uid: dict[str, dict[str, Any]] = {}
        self._selected_uid = ""
        self._selected_artifact_uid: str | None = None

        root = QHBoxLayout(self)
        self.artifact_tree = QTreeWidget()
        self.artifact_tree.setHeaderLabels(["Artifact", "Details"])
        self.artifact_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.artifact_tree.setMinimumWidth(300)
        self.artifact_tree.itemClicked.connect(self._on_item_clicked)
        root.addWidget(self.artifact_tree, 0)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        header = QHBoxLayout()
        self.preview_header = QLabel("Select an Artifact to preview.")
        self.preview_header.setWordWrap(True)
        header.addWidget(self.preview_header, 1)
        self.raw_button = QPushButton("Raw")
        self.raw_button.clicked.connect(self._show_raw)
        header.addWidget(self.raw_button)
        self.open_file_button = QPushButton("Open file")
        self.open_file_button.clicked.connect(self._emit_open_file)
        header.addWidget(self.open_file_button)
        self.open_folder_button = QPushButton("Open containing folder")
        self.open_folder_button.clicked.connect(self._emit_open_folder)
        header.addWidget(self.open_folder_button)
        self.copy_path_button = QPushButton("Copy path")
        self.copy_path_button.clicked.connect(self._emit_copy_path)
        header.addWidget(self.copy_path_button)
        detail_layout.addLayout(header)

        self.path_label = QLabel("")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        detail_layout.addWidget(self.path_label)
        self.metadata_label = QLabel("")
        self.metadata_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.metadata_label.setWordWrap(True)
        detail_layout.addWidget(self.metadata_label)

        self.preview_stack = QStackedWidget()
        self.empty_preview = QLabel("Select an Artifact from the list to preview it.")
        self.empty_preview.setAlignment(Qt.AlignCenter)
        self.metadata_preview = QLabel("")
        self.metadata_preview.setWordWrap(True)
        self.metadata_preview.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.json_preview = QTreeWidget()
        self.json_preview.setHeaderLabels(["Field", "Value"])
        self.json_preview.setColumnWidth(0, 220)
        self.json_lines_preview = QTreeWidget()
        self.json_lines_preview.setHeaderLabels(["Row", "Value"])
        self.table_preview = QTableWidget()
        self.text_preview = _SafeTextBrowser()
        self.raw_preview = _SafeTextBrowser()
        self.image_preview = QLabel("Image preview unavailable.")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.svg_preview = QLabel("SVG preview unavailable.")
        self.svg_preview.setAlignment(Qt.AlignCenter)
        self.pdf_document = QPdfDocument(self)
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        self.archive_preview = _SafeTextBrowser()
        for widget in (
            self.empty_preview,
            self.metadata_preview,
            self.json_preview,
            self.json_lines_preview,
            self.table_preview,
            self.text_preview,
            self.raw_preview,
            self.image_preview,
            self.svg_preview,
            self.pdf_view,
            self.archive_preview,
        ):
            self.preview_stack.addWidget(widget)
        detail_layout.addWidget(self.preview_stack, 1)
        root.addWidget(detail, 1)
        self._raw_text = ""
        self.raw_button.setEnabled(False)
        self._show_empty()

    def set_groups(self, groups: Iterable[ArtifactGroup], *, auto_select_primary: bool) -> None:
        self._groups = [group for group in groups if group.records]
        self._records_by_uid = {}
        for group in self._groups:
            for record in group.records:
                if not record.artifact_uid:
                    continue
                existing = self._records_by_uid.get(record.artifact_uid)
                self._records_by_uid[record.artifact_uid] = (
                    ArtifactRecord.merge([existing, record])[0] if existing else record
                )
        self._render_tree()
        selected = self._default_uid() if auto_select_primary else ""
        if selected:
            self.select_artifact(selected)
        else:
            self._selected_uid = ""
            self._selected_artifact_uid = None
            self._show_empty()

    def selected_record(self) -> ArtifactRecord | None:
        return self._records_by_uid.get(self._selected_uid)

    def select_artifact(self, artifact_uid: str, *, request_missing: bool = True) -> None:
        uid = str(artifact_uid or "")
        self._selected_uid = uid
        self._selected_artifact_uid = uid or None
        item = self._find_tree_item(uid)
        if item is not None:
            self.artifact_tree.setCurrentItem(item)
        self._render_selected()
        if request_missing and uid and uid not in self._content_by_uid and artifact_kind(self.selected_record()) in {
            "json",
            "json_lines",
            "table",
            "markdown",
            "html",
            "text",
        }:
            record = self.selected_record()
            self.preview_requested.emit(uid, record.review_id if record else "")

    def cache_artifact_detail(self, artifact_uid: str, raw: Mapping[str, Any]) -> None:
        uid = str(artifact_uid or "")
        current = self._records_by_uid.get(uid)
        if not uid or current is None:
            return
        updated = ArtifactRecord.from_mapping(
            {**asdict(current), **dict(raw)},
            node_id=current.node_id,
            review_id=current.review_id,
            is_primary=current.is_primary,
        )
        self._records_by_uid[uid] = updated
        self._render_selected()

    def cache_content(self, artifact_uid: str, payload: Mapping[str, Any]) -> None:
        uid = str(artifact_uid or "")
        if uid:
            self._content_by_uid[uid] = dict(payload)
            if uid == self._selected_uid:
                self._render_selected()

    def cache_error(self, artifact_uid: str, message: str) -> None:
        self.cache_content(artifact_uid, {"available": False, "error": str(message or "Preview unavailable.")})

    def _default_uid(self) -> str:
        records = [record for group in self._groups for record in group.records if record.artifact_uid]
        primary = next((record for record in records if record.is_primary), None)
        return (primary or (records[0] if records else None)).artifact_uid if records else ""

    def _render_tree(self) -> None:
        self.artifact_tree.clear()
        for group in self._groups:
            parent = QTreeWidgetItem([group.label, f"{len(group.records)} item(s)"])
            parent.setFlags(Qt.ItemIsEnabled)
            self.artifact_tree.addTopLevelItem(parent)
            for record in group.records:
                status = record.publication_status
                child = QTreeWidgetItem(
                    [f"{record.role} · {record.name or record.relative_path or 'Artifact'}", status]
                )
                child.setData(0, Qt.UserRole, record.artifact_uid)
                child.setToolTip(0, record.relative_path or record.name)
                parent.addChild(child)
            parent.setExpanded(True)

    def _find_tree_item(self, artifact_uid: str) -> QTreeWidgetItem | None:
        for index in range(self.artifact_tree.topLevelItemCount()):
            parent = self.artifact_tree.topLevelItem(index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                if str(child.data(0, Qt.UserRole) or "") == artifact_uid:
                    return child
        return None

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        uid = str(item.data(0, Qt.UserRole) or "")
        if uid:
            self.select_artifact(uid)

    def _render_selected(self) -> None:
        record = self.selected_record()
        if record is None:
            self._show_empty()
            return
        self.preview_header.setText(f"{record.role} · {record.name or record.relative_path or record.artifact_uid}")
        self.path_label.setText(record.final_path or "Full path unavailable")
        size = "—" if record.size is None else f"{record.size} bytes"
        self.metadata_label.setText(
            f"Status: {record.publication_status} · MIME: {record.mime_type or 'unknown'} · "
            f"Size: {size} · SHA-256: {record.sha256 or '—'}"
        )
        self.open_file_button.setEnabled(bool(record.final_path))
        self.open_folder_button.setEnabled(bool(record.final_path))
        self.copy_path_button.setEnabled(bool(record.final_path))
        self.raw_button.setEnabled(False)
        self._raw_text = ""
        kind = artifact_kind(record)
        cached = self._content_by_uid.get(record.artifact_uid)
        if cached and cached.get("error"):
            self.metadata_preview.setText(str(cached["error"]))
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if kind in {"image", "svg", "pdf", "archive"}:
            self._render_local_kind(record, kind)
            return
        if kind == "unsupported":
            self.metadata_preview.setText(
                "In-app preview is not available for this format.\n\n"
                f"{record.name or record.relative_path or record.artifact_uid}"
            )
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if cached is None:
            self.metadata_preview.setText(f"Loading Artifact preview for {kind}…")
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if not cached.get("available", True):
            self.metadata_preview.setText("Artifact content is unavailable.")
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        text = str(cached.get("text") or "")
        self._raw_text = text
        self.raw_preview.setPlainText(text)
        self.raw_button.setEnabled(True)
        if bool(cached.get("truncated")):
            self.metadata_label.setText(self.metadata_label.text() + " · Preview truncated")
        if kind == "json":
            self._render_json(text)
        elif kind == "json_lines":
            self._render_json_lines(text)
        elif kind == "table":
            self._render_table(text, delimiter="\t" if record.relative_path.casefold().endswith(".tsv") else ",")
        elif kind == "markdown":
            self.text_preview.setMarkdown(text)
            self.preview_stack.setCurrentWidget(self.text_preview)
        elif kind == "html":
            self.text_preview.setHtml(_safe_html(text))
            self.preview_stack.setCurrentWidget(self.text_preview)
        else:
            self.text_preview.setPlainText(text)
            self.preview_stack.setCurrentWidget(self.text_preview)

    def _show_empty(self) -> None:
        self._selected_artifact_uid = None
        self.preview_header.setText("Select an Artifact to preview.")
        self.path_label.clear()
        self.metadata_label.clear()
        self.open_file_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.copy_path_button.setEnabled(False)
        self.raw_button.setEnabled(False)
        self._raw_text = ""
        self.preview_stack.setCurrentWidget(self.empty_preview)

    def _show_raw(self) -> None:
        if self._raw_text:
            self.preview_stack.setCurrentWidget(self.raw_preview)

    def _render_json(self, text: str) -> None:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            self.metadata_preview.setText("JSON preview unavailable: the content is not valid JSON.")
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        self.json_preview.clear()
        if isinstance(value, dict):
            for key, child_value in value.items():
                self._add_json_value(self.json_preview, str(key), child_value)
        else:
            self._add_json_value(self.json_preview, "value", value)
        self.preview_stack.setCurrentWidget(self.json_preview)

    def _add_json_value(self, parent: QTreeWidget | QTreeWidgetItem, key: str, value: Any) -> None:
        if isinstance(value, dict):
            item = QTreeWidgetItem([key, "object"])
            (parent.addTopLevelItem if isinstance(parent, QTreeWidget) else parent.addChild)(item)
            for child_key, child_value in value.items():
                self._add_json_value(item, str(child_key), child_value)
            item.setExpanded(True)
            return
        if isinstance(value, list):
            item = QTreeWidgetItem([key, f"array · {len(value)} item(s)"])
            (parent.addTopLevelItem if isinstance(parent, QTreeWidget) else parent.addChild)(item)
            for index, child_value in enumerate(value):
                self._add_json_value(item, f"[{index}]", child_value)
            item.setExpanded(True)
            return
        item = QTreeWidgetItem([key, json.dumps(value, ensure_ascii=False)])
        (parent.addTopLevelItem if isinstance(parent, QTreeWidget) else parent.addChild)(item)

    def _render_json_lines(self, text: str) -> None:
        self.json_lines_preview.clear()
        for index, line in enumerate(text.splitlines(), start=1):
            try:
                value = json.loads(line)
                rendered = json.dumps(value, ensure_ascii=False)
            except json.JSONDecodeError:
                rendered = line
            self.json_lines_preview.addTopLevelItem(QTreeWidgetItem([str(index), rendered]))
        self.preview_stack.setCurrentWidget(self.json_lines_preview)

    def _render_table(self, text: str, *, delimiter: str) -> None:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))[:MAX_TABLE_ROWS]
        columns = min(MAX_TABLE_COLUMNS, max((len(row) for row in rows), default=0))
        self.table_preview.clear()
        self.table_preview.setRowCount(len(rows))
        self.table_preview.setColumnCount(columns)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row[:columns]):
                self.table_preview.setItem(row_index, column_index, QTableWidgetItem(value))
        if rows:
            self.table_preview.setHorizontalHeaderLabels(rows[0][:columns])
            self.table_preview.removeRow(0)
        self.preview_stack.setCurrentWidget(self.table_preview)

    def _render_local_kind(self, record: ArtifactRecord, kind: str) -> None:
        path = Path(record.final_path)
        if not path.is_file():
            self.metadata_preview.setText(
                "In-app preview is unavailable because the file is missing.\n\n"
                f"{record.name or record.relative_path or record.artifact_uid}"
            )
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if kind == "image":
            reader = QImageReader(str(path))
            size = reader.size()
            if size.isValid() and size.width() * size.height() > MAX_IMAGE_PIXELS:
                self.metadata_preview.setText("Image is too large for an in-app preview.")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            image = reader.read()
            if image.isNull():
                self.metadata_preview.setText("Image preview is unavailable for this file.")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            self.image_preview.setPixmap(QPixmap.fromImage(image).scaled(1000, 700, Qt.KeepAspectRatio))
            self.preview_stack.setCurrentWidget(self.image_preview)
            return
        if kind == "svg":
            raw = path.read_bytes()[:MAX_TEXT_BYTES]
            if b"<script" in raw.casefold() or b"<image" in raw.casefold() or b"<foreignobject" in raw.casefold():
                self.metadata_preview.setText("SVG preview is blocked because it references active or external content.")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            renderer = QSvgRenderer(QByteArray(raw))
            image = QImage(1000, 700, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            if renderer.isValid():
                self.svg_preview.setPixmap(QPixmap.fromImage(image))
                self.preview_stack.setCurrentWidget(self.svg_preview)
            else:
                self.metadata_preview.setText("SVG preview is unavailable for this file.")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if kind == "pdf":
            if path.stat().st_size > MAX_PDF_BYTES or self.pdf_document.load(str(path)) != QPdfDocument.Error.None_:
                self.metadata_preview.setText("PDF is too large or unavailable for an in-app preview.")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
            else:
                self.preview_stack.setCurrentWidget(self.pdf_view)
            return
        if kind == "archive":
            lines: list[str] = []
            try:
                if path.suffix.casefold() == ".zip":
                    with zipfile.ZipFile(path) as archive:
                        lines = [f"{item.filename} · {item.file_size} bytes" for item in archive.infolist()[:MAX_ARCHIVE_ENTRIES]]
                else:
                    with tarfile.open(path, "r:*") as archive:
                        lines = [f"{item.name} · {item.size} bytes" for item in archive.getmembers()[:MAX_ARCHIVE_ENTRIES]]
            except (OSError, tarfile.TarError, zipfile.BadZipFile):
                self.metadata_preview.setText("Archive member listing is unavailable.")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            self.archive_preview.setPlainText("\n".join(lines) or "Archive is empty.")
            self.preview_stack.setCurrentWidget(self.archive_preview)

    def _emit_copy_path(self) -> None:
        record = self.selected_record()
        if record and record.final_path:
            QApplication.clipboard().setText(record.final_path)

    def _emit_open_file(self) -> None:
        record = self.selected_record()
        if record and record.final_path:
            self.open_file_requested.emit(record.final_path)

    def _emit_open_folder(self) -> None:
        record = self.selected_record()
        if record and record.final_path:
            self.open_folder_requested.emit(str(Path(record.final_path).parent))
