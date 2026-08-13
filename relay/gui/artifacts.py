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
from urllib.parse import urlparse

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
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

from .design_icons import icon
from .design_typography import apply_type, font_for
from .design_widgets import SectionHeader
from .json_display import render_json_report_html
from .scroll_state import preserve_scroll, set_html, set_markdown, set_plain_text

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
MAX_SVG_BYTES = 10 * 1024 * 1024
SVG_PREVIEW_WIDTH = 1000
SVG_PREVIEW_HEIGHT = 700
JSON_REPORT_STYLE_SHEET = """
body, div, span {
    font-size: 13px;
    font-weight: normal;
}
"""


def _format_artifact_size(size: int | None) -> str:
    if size is None or size < 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _artifact_kind_label(record: ArtifactRecord | None) -> str:
    labels = {
        "json": "JSON",
        "json_lines": "JSONL",
        "table": "Table",
        "markdown": "Markdown",
        "html": "HTML",
        "image": "Image",
        "svg": "SVG",
        "pdf": "PDF",
        "archive": "Archive",
        "text": "Text",
        "unsupported": "File",
    }
    return labels.get(artifact_kind(record), "File")


def _artifact_status_state(status: str) -> str:
    normalized = status.casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"candidate", "pending", "pending_human", "needs_human"}:
        return "candidate"
    if normalized in {"published", "confirmed", "approved", "complete", "completed"}:
        return "completed"
    if normalized in {"rejected", "failed", "delivery_failed"}:
        return "failed"
    if normalized in {"superseded", "archived"}:
        return "muted"
    return "muted"


def _artifact_status_icon(status: str) -> tuple[str, str, str]:
    state = _artifact_status_state(status)
    if state == "completed":
        return "check-circle", "success", "Ready"
    if state == "candidate":
        return "info", "warning", "Needs review"
    if state == "failed":
        return "alert-triangle", "danger", "Failed"
    return "dot", "muted", str(status or "Unavailable").replace("_", " ").title()


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
        return "path:" + "|".join((self.source_task_run_id, self.node_id, self.role, self.relative_path))

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
        "a",
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
        self._external_link_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self._blocked_depth:
            self._blocked_depth += 1
            return
        if tag in self._blocked:
            self._blocked_depth = 1
            return
        if tag == "a":
            href = next((value or "" for key, value in attrs if key.casefold() == "href"), "")
            parsed = urlparse(href)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                label = f"[External link: {html_escape(href)}] "
            elif parsed.scheme == "mailto" and parsed.path:
                label = f"[External mail link: {html_escape(href)}] "
            else:
                label = "[Link omitted] "
            self.parts.append(f"<span>{label}")
            self._external_link_depth += 1
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
        if tag == "a":
            if self._external_link_depth:
                self._external_link_depth -= 1
                self.parts.append("</span>")
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
        self._rendered_uid = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        list_panel = QWidget()
        list_panel.setObjectName("artifactListPanel")
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        list_layout.addWidget(SectionHeader("Artifacts", "Select a result to inspect"))
        self.artifact_tree = QTreeWidget()
        self.artifact_tree.setHeaderLabels(["Artifact", "Status", "Format"])
        self.artifact_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.artifact_tree.setMinimumWidth(300)
        self.artifact_tree.setColumnWidth(0, 260)
        self.artifact_tree.setColumnWidth(1, 100)
        self.artifact_tree.setColumnWidth(2, 120)
        self.artifact_tree.header().setStretchLastSection(True)
        self.artifact_tree.itemClicked.connect(self._on_item_clicked)
        self.artifact_tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        list_layout.addWidget(self.artifact_tree, 1)
        root.addWidget(list_panel, 0)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        detail_layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.preview_header = QLabel("Select an Artifact to preview.")
        self.preview_header.setObjectName("detailTitle")
        apply_type(self.preview_header, "title.detail")
        self.preview_header.setWordWrap(True)
        header.addWidget(self.preview_header, 1)
        self.format_label = QLabel("")
        self.format_label.setObjectName("artifactFormat")
        apply_type(self.format_label, "overline")
        header.addWidget(self.format_label)
        self.status_label = QLabel("")
        self.status_label.setObjectName("artifactStatus")
        apply_type(self.status_label, "overline")
        header.addWidget(self.status_label)
        self.json_mode_button = QPushButton("Text view")
        self.json_mode_button.setObjectName("jsonModeToggle")
        self.json_mode_button.setCheckable(True)
        self.json_mode_button.setToolTip("Switch to the JSON tree view")
        self.json_mode_button.toggled.connect(self._toggle_json_mode)
        header.addWidget(self.json_mode_button)
        detail_layout.addLayout(header)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.open_file_button = QPushButton("Open")
        self.open_file_button.setToolTip("Open this Artifact with the system default application")
        self.open_file_button.clicked.connect(self._emit_open_file)
        actions.addWidget(self.open_file_button)
        self.open_folder_button = QPushButton("Show folder")
        self.open_folder_button.setToolTip("Open the folder containing this Artifact")
        self.open_folder_button.clicked.connect(self._emit_open_folder)
        actions.addWidget(self.open_folder_button)
        self.copy_path_button = QPushButton("Copy path")
        self.copy_path_button.clicked.connect(self._emit_copy_path)
        actions.addWidget(self.copy_path_button)
        actions.addStretch(1)
        detail_layout.addLayout(actions)

        self.metadata_panel = QFrame()
        self.metadata_panel.setObjectName("artifactMetadata")
        metadata_layout = QVBoxLayout(self.metadata_panel)
        metadata_layout.setContentsMargins(10, 8, 10, 8)
        metadata_layout.setSpacing(2)
        self.path_label = QLabel("")
        self.path_label.setObjectName("artifactPath")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        metadata_layout.addWidget(self.path_label)
        detail_layout.addWidget(self.metadata_panel)

        self.preview_stack = QStackedWidget()
        self.preview_stack.setObjectName("artifactPreviewSurface")
        self.empty_preview = QLabel("Select an Artifact from the list to preview it.")
        self.empty_preview.setObjectName("emptyState")
        self.empty_preview.setAlignment(Qt.AlignCenter)
        self.metadata_preview = QLabel("")
        self.metadata_preview.setWordWrap(True)
        self.metadata_preview.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.json_preview = QTreeWidget()
        self.json_preview.setHeaderLabels(["Field", "Value", "Type"])
        self.json_preview.setColumnWidth(0, 220)
        self.json_preview.setColumnWidth(1, 420)
        self.json_preview.setColumnWidth(2, 100)
        self.json_preview.setAlternatingRowColors(True)
        self.json_outline_preview = QTextBrowser()
        self.json_outline_preview.setObjectName("evidencePane")
        self.json_outline_preview.setOpenExternalLinks(False)
        outline_font = QFont(font_for("body"))
        outline_font.setPixelSize(13)
        self.json_outline_preview.setFont(outline_font)
        self.json_outline_preview.document().setDefaultFont(outline_font)
        self.json_outline_preview.document().setDefaultStyleSheet(JSON_REPORT_STYLE_SHEET)
        self.json_lines_preview = QTreeWidget()
        self.json_lines_preview.setHeaderLabels(["Row", "Value"])
        self.table_preview = QTableWidget()
        self.text_preview = _SafeTextBrowser()
        self.image_preview = QLabel("Image preview unavailable.")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.svg_preview = QLabel("SVG preview unavailable.")
        self.svg_preview.setAlignment(Qt.AlignCenter)
        self.pdf_document = QPdfDocument(self)
        self._pdf_buffer = QBuffer(self)
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        self.archive_preview = _SafeTextBrowser()
        for widget in (
            self.empty_preview,
            self.metadata_preview,
            self.json_preview,
            self.json_outline_preview,
            self.json_lines_preview,
            self.table_preview,
            self.text_preview,
            self.image_preview,
            self.svg_preview,
            self.pdf_view,
            self.archive_preview,
        ):
            self.preview_stack.addWidget(widget)
        detail_layout.addWidget(self.preview_stack, 1)
        root.addWidget(detail, 1)
        self._json_view_mode = "outline"
        self._json_value: Any | None = None
        self._set_json_mode_available(False)
        self._show_empty()

    def set_groups(self, groups: Iterable[ArtifactGroup], *, auto_select_primary: bool) -> None:
        previous_uid = self._selected_uid
        filtered_groups: list[ArtifactGroup] = []
        for group in groups:
            records = tuple(
                record
                for record in group.records
                if not (
                    group.label.strip().casefold() == "files"
                    and Path(record.relative_path or record.name).name.casefold() == "result.json"
                )
            )
            if records:
                filtered_groups.append(ArtifactGroup(group.label, records))
        self._groups = filtered_groups
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
        selected = (
            previous_uid
            if previous_uid and previous_uid in self._records_by_uid
            else self._default_uid()
            if auto_select_primary
            else ""
        )
        if selected:
            self.select_artifact(selected, request_missing=selected != previous_uid)
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
        if (
            request_missing
            and uid
            and uid not in self._content_by_uid
            and artifact_kind(self.selected_record())
            in {
                "json",
                "json_lines",
                "table",
                "markdown",
                "html",
                "text",
            }
        ):
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
        with preserve_scroll(self.artifact_tree):
            self._render_tree_content()

    def _render_tree_content(self) -> None:
        self.artifact_tree.clear()
        for group in self._groups:
            parent = QTreeWidgetItem([group.label, f"{len(group.records)}", ""])
            parent.setFlags(Qt.ItemIsEnabled)
            self.artifact_tree.addTopLevelItem(parent)
            for record in group.records:
                status = record.publication_status
                kind = _artifact_kind_label(record)
                child = QTreeWidgetItem(
                    [
                        f"{'★ ' if record.is_primary else ''}{record.role} · "
                        f"{record.name or record.relative_path or 'Artifact'}",
                        status.replace("_", " ").title(),
                        f"{kind} · {_format_artifact_size(record.size)}",
                    ]
                )
                child.setData(0, Qt.UserRole, record.artifact_uid)
                child.setToolTip(
                    0,
                    "\n".join(
                        item
                        for item in (
                            record.relative_path or record.name,
                            f"Role: {record.role}",
                            f"Status: {record.publication_status}",
                            f"Format: {kind}",
                        )
                        if item
                    ),
                )
                child.setToolTip(1, record.publication_status)
                child.setToolTip(2, f"{kind} · {_format_artifact_size(record.size)}")
                child.setForeground(1, QColor(self._status_color(record.publication_status)))
                parent.addChild(child)
            parent.setExpanded(True)

    @staticmethod
    def _status_color(status: str) -> str:
        state = _artifact_status_state(status)
        return {
            "candidate": "#E3B341",
            "completed": "#5BD48A",
            "failed": "#F07A75",
            "muted": "#999999",
        }.get(state, "#999999")

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

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        uid = str(item.data(0, Qt.UserRole) or "")
        if not uid:
            return
        self.select_artifact(uid, request_missing=False)
        self._emit_open_file()

    def _capture_preview_scroll(self) -> tuple[int, int] | None:
        widget = self.preview_stack.currentWidget()
        if not hasattr(widget, "verticalScrollBar") or not hasattr(widget, "horizontalScrollBar"):
            return None
        return widget.verticalScrollBar().value(), widget.horizontalScrollBar().value()

    def _restore_preview_scroll(self, position: tuple[int, int] | None) -> None:
        if position is None:
            return
        widget = self.preview_stack.currentWidget()
        if not hasattr(widget, "verticalScrollBar") or not hasattr(widget, "horizontalScrollBar"):
            return
        vertical, horizontal = position
        widget.verticalScrollBar().setValue(min(vertical, widget.verticalScrollBar().maximum()))
        widget.horizontalScrollBar().setValue(min(horizontal, widget.horizontalScrollBar().maximum()))

    def _render_selected(self) -> None:
        position = self._capture_preview_scroll() if self._rendered_uid == self._selected_uid else None
        try:
            self._render_selected_impl()
        finally:
            self._rendered_uid = self._selected_uid
            self._restore_preview_scroll(position)

    def _render_selected_impl(self) -> None:
        record = self.selected_record()
        if record is None:
            self._show_empty()
            return
        self.preview_header.setText(f"{record.role} · {record.name or record.relative_path or record.artifact_uid}")
        self.format_label.setText(_artifact_kind_label(record))
        self.status_label.setProperty("state", _artifact_status_state(record.publication_status))
        status_icon, status_tone, status_description = _artifact_status_icon(record.publication_status)
        self.status_label.setText("")
        self.status_label.setPixmap(icon(status_icon, status_tone).pixmap(16, 16))
        self.status_label.setToolTip(status_description)
        self.status_label.setAccessibleName(status_description)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.path_label.setText(record.final_path or "Full path unavailable")
        self.open_file_button.setEnabled(bool(record.final_path))
        self.open_folder_button.setEnabled(bool(record.final_path))
        self.copy_path_button.setEnabled(bool(record.final_path))
        self._set_json_mode_available(False)
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
                f"{record.name or record.relative_path or record.artifact_uid}\n"
                f"Path: {record.final_path or 'Full path unavailable'}\n\n"
                "Use 'Open' to inspect it with the system default application."
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
        if kind == "json":
            self._render_json(text)
        elif kind == "json_lines":
            self._render_json_lines(text)
        elif kind == "table":
            self._render_table(text, delimiter="\t" if record.relative_path.casefold().endswith(".tsv") else ",")
        elif kind == "markdown":
            set_markdown(self.text_preview, text)
            self.preview_stack.setCurrentWidget(self.text_preview)
        elif kind == "html":
            set_html(self.text_preview, _safe_html(text))
            self.preview_stack.setCurrentWidget(self.text_preview)
        else:
            set_plain_text(self.text_preview, text)
            self.preview_stack.setCurrentWidget(self.text_preview)

    def _show_empty(self) -> None:
        self._selected_artifact_uid = None
        self.preview_header.setText("Select an Artifact to preview.")
        self.format_label.clear()
        self.status_label.clear()
        self.path_label.clear()
        self.open_file_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.copy_path_button.setEnabled(False)
        self._set_json_mode_available(False)
        self.preview_stack.setCurrentWidget(self.empty_preview)

    def _set_json_mode_available(self, available: bool) -> None:
        self.json_mode_button.blockSignals(True)
        self.json_mode_button.setEnabled(available)
        if not available:
            self.json_mode_button.setChecked(False)
            self.json_mode_button.setText("Text view")
            self.json_mode_button.setToolTip("Available for JSON Artifacts")
        self.json_mode_button.blockSignals(False)
        if available:
            self._update_json_mode_button()

    def _update_json_mode_button(self) -> None:
        tree_mode = self._json_view_mode == "tree"
        self.json_mode_button.blockSignals(True)
        self.json_mode_button.setChecked(tree_mode)
        self.json_mode_button.blockSignals(False)
        self.json_mode_button.setText("Tree view" if tree_mode else "Text view")
        self.json_mode_button.setToolTip(
            "Switch to the text outline view" if tree_mode else "Switch to the JSON tree view"
        )

    def _toggle_json_mode(self, tree_mode: bool) -> None:
        self._json_view_mode = "tree" if tree_mode else "outline"
        self._update_json_mode_button()
        self._show_json_preview()

    def _show_json_preview(self) -> None:
        self.preview_stack.setCurrentWidget(
            self.json_preview if self._json_view_mode == "tree" else self.json_outline_preview
        )

    def _render_json(self, text: str) -> None:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            self._json_value = None
            self._set_json_mode_available(False)
            self.metadata_preview.setText("JSON preview unavailable: the content is not valid JSON.")
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        self._json_value = value
        self._set_json_mode_available(True)
        self.json_preview.clear()
        if isinstance(value, dict):
            for key, child_value in value.items():
                self._add_json_value(self.json_preview, str(key), child_value)
        else:
            self._add_json_value(self.json_preview, "value", value)
        set_html(self.json_outline_preview, render_json_report_html(value))
        self._show_json_preview()

    def _add_json_value(self, parent: QTreeWidget | QTreeWidgetItem, key: str, value: Any) -> None:
        if isinstance(value, dict):
            item = QTreeWidgetItem([key, "object", "object"])
            (parent.addTopLevelItem if isinstance(parent, QTreeWidget) else parent.addChild)(item)
            for child_key, child_value in value.items():
                self._add_json_value(item, str(child_key), child_value)
            item.setExpanded(True)
            return
        if isinstance(value, list):
            item = QTreeWidgetItem([key, "array", "array"])
            (parent.addTopLevelItem if isinstance(parent, QTreeWidget) else parent.addChild)(item)
            for index, child_value in enumerate(value):
                self._add_json_value(item, f"[{index}]", child_value)
            item.setExpanded(True)
            return
        item = QTreeWidgetItem([key, self._json_scalar_text(value), self._json_type_label(value)])
        (parent.addTopLevelItem if isinstance(parent, QTreeWidget) else parent.addChild)(item)

    @staticmethod
    def _json_scalar_text(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _json_type_label(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        return "string"

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
                f"{record.name or record.relative_path or record.artifact_uid}\n"
                f"Path: {record.final_path or 'Full path unavailable'}"
            )
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if kind == "image":
            reader = QImageReader(str(path))
            size = reader.size()
            if size.isValid() and size.width() * size.height() > MAX_IMAGE_PIXELS:
                self.metadata_preview.setText(f"Image is too large for an in-app preview.\n\nPath: {record.final_path}")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            image = reader.read()
            if image.isNull():
                self.metadata_preview.setText(
                    f"Image preview is unavailable for this file.\n\nPath: {record.final_path}"
                )
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            self.image_preview.setPixmap(QPixmap.fromImage(image).scaled(1000, 700, Qt.KeepAspectRatio))
            self.preview_stack.setCurrentWidget(self.image_preview)
            return
        if kind == "svg":
            try:
                svg_size = path.stat().st_size
                raw = path.read_bytes() if svg_size <= MAX_SVG_BYTES else b""
            except OSError:
                svg_size = -1
                raw = b""
            if svg_size > MAX_SVG_BYTES:
                self.metadata_preview.setText(
                    "SVG is too large for an in-app preview.\n\n"
                    f"Path: {record.final_path}"
                )
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            if not raw:
                self.metadata_preview.setText(
                    f"SVG preview is unavailable for this file.\n\nPath: {record.final_path}"
                )
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            lowered = raw.lower()
            if b"<script" in lowered or b"<image" in lowered or b"<foreignobject" in lowered:
                self.metadata_preview.setText(
                    "SVG preview is blocked because it references active or external content.\n\n"
                    f"Path: {record.final_path}"
                )
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            renderer = QSvgRenderer(QByteArray(raw))
            if not renderer.isValid():
                self.metadata_preview.setText(
                    "SVG preview is unavailable because the SVG is invalid or uses unsupported content.\n\n"
                    f"Path: {record.final_path}"
                )
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            view_box = renderer.viewBoxF()
            if not view_box.isValid() or view_box.width() <= 0 or view_box.height() <= 0:
                default_size = renderer.defaultSize()
                view_width = max(1, default_size.width())
                view_height = max(1, default_size.height())
            else:
                view_width = view_box.width()
                view_height = view_box.height()
            aspect_ratio = view_width / view_height
            image_width = SVG_PREVIEW_WIDTH
            image_height = max(1, round(image_width / aspect_ratio))
            if image_height > SVG_PREVIEW_HEIGHT:
                image_height = SVG_PREVIEW_HEIGHT
                image_width = max(1, round(image_height * aspect_ratio))
            image = QImage(image_width, image_height, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            renderer.render(painter, QRectF(0, 0, image_width, image_height))
            painter.end()
            if not image.isNull():
                self.svg_preview.setPixmap(QPixmap.fromImage(image))
                self.preview_stack.setCurrentWidget(self.svg_preview)
            else:
                self.metadata_preview.setText(f"SVG preview is unavailable for this file.\n\nPath: {record.final_path}")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if kind == "pdf":
            self.pdf_document.close()
            self._pdf_buffer.close()
            try:
                pdf_size = path.stat().st_size
                pdf_data = path.read_bytes() if pdf_size <= MAX_PDF_BYTES else b""
                if pdf_size > MAX_PDF_BYTES:
                    pdf_error = QPdfDocument.Error.Unknown
                else:
                    self._pdf_buffer.setData(QByteArray(pdf_data))
                    self._pdf_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                    self.pdf_document.load(self._pdf_buffer)
                    pdf_error = self.pdf_document.error()
            except OSError:
                pdf_error = QPdfDocument.Error.Unknown
            if pdf_error != QPdfDocument.Error.None_:
                self.metadata_preview.setText(
                    f"PDF is too large or unavailable for an in-app preview.\n\nPath: {record.final_path}"
                )
                self.preview_stack.setCurrentWidget(self.metadata_preview)
            else:
                self.preview_stack.setCurrentWidget(self.pdf_view)
            return
        if kind == "archive":
            lines: list[str] = []
            try:
                if path.suffix.casefold() == ".zip":
                    with zipfile.ZipFile(path) as archive:
                        lines = [
                            f"{item.filename} · {item.file_size} bytes"
                            for item in archive.infolist()[:MAX_ARCHIVE_ENTRIES]
                        ]
                else:
                    with tarfile.open(path, "r:*") as archive:
                        lines = [
                            f"{item.name} · {item.size} bytes" for item in archive.getmembers()[:MAX_ARCHIVE_ENTRIES]
                        ]
            except (OSError, tarfile.TarError, zipfile.BadZipFile):
                self.metadata_preview.setText("Archive member listing is unavailable.")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            set_plain_text(self.archive_preview, "\n".join(lines) or "Archive is empty.")
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
