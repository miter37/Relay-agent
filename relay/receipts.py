"""Receipt contract constants shared by the engine and API layers."""

from __future__ import annotations

LEGACY_RECEIPT_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 3
CATALOG_RECEIPT_SCHEMA_VERSION = RECEIPT_SCHEMA_VERSION

RECEIPT_SUMMARY_KEYS = ("task_summary", "result_summary", "failure_reason")
