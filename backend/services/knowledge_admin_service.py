
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from services.knowledge_service import reload_knowledge, ensure_index

KB_FILE = (
    Path(__file__).resolve().parent.parent
    / "knowledge_base"
    / "agriculture.json"
)

KB_LOCK = RLock()

REQUIRED_FIELDS = {"id", "crop", "topic", "content"}


def _read_records() -> list[dict[str, Any]]:
    with KB_FILE.open("r", encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError("Knowledge base must be a JSON list.")

    return records


def _validate_record(record: dict[str, Any]) -> None:
    missing = REQUIRED_FIELDS - record.keys()

    if missing:
        raise ValueError(
            "Missing required fields: " + ", ".join(sorted(missing))
        )

    for field in REQUIRED_FIELDS:
        if not isinstance(record[field], str) or not record[field].strip():
            raise ValueError(f"{field} must be a non-empty string.")

    if not isinstance(record.get("keywords", []), list):
        raise ValueError("keywords must be a list.")

    if not isinstance(record.get("translations", {}), dict):
        raise ValueError("translations must be an object.")

    if not isinstance(record.get("verified", False), bool):
        raise ValueError("verified must be true or false.")


def _write_records(records: list[dict[str, Any]]) -> None:
    KB_FILE.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        dir=str(KB_FILE.parent),
        prefix="agriculture_",
        suffix=".tmp",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(records, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, KB_FILE)

    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _refresh_index() -> None:
    reload_knowledge()
    ensure_index()


def list_records() -> list[dict[str, Any]]:
    with KB_LOCK:
        return _read_records()


def add_record(record: dict[str, Any]) -> dict[str, Any]:
    _validate_record(record)

    with KB_LOCK:
        records = _read_records()

        if any(item.get("id") == record["id"] for item in records):
            raise ValueError("A record with this ID already exists.")

        updated = records + [record]
        _write_records(updated)

        try:
            _refresh_index()
        except Exception:
            _write_records(records)
            reload_knowledge()
            raise

        return record


def update_record(
    record_id: str,
    updated_record: dict[str, Any],
) -> dict[str, Any]:
    _validate_record(updated_record)

    if updated_record["id"] != record_id:
        raise ValueError("Record ID cannot be changed.")

    with KB_LOCK:
        records = _read_records()
        index = next(
            (i for i, item in enumerate(records)
             if item.get("id") == record_id),
            None,
        )

        if index is None:
            raise KeyError("Knowledge record not found.")

        previous = records[index]
        records[index] = updated_record
        _write_records(records)

        try:
            _refresh_index()
        except Exception:
            records[index] = previous
            _write_records(records)
            reload_knowledge()
            raise

        return updated_record


def delete_record(record_id: str) -> None:
    with KB_LOCK:
        records = _read_records()
        updated = [
            item for item in records
            if item.get("id") != record_id
        ]

        if len(updated) == len(records):
            raise KeyError("Knowledge record not found.")

        _write_records(updated)

        try:
            _refresh_index()
        except Exception:
            _write_records(records)
            reload_knowledge()
            raise
