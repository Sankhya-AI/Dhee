from __future__ import annotations

import sqlite3
import zipfile
import json
from pathlib import Path

from dhee.benchmarks.hippocamp import (
    CONFIG_SPECS,
    _emit_progress,
    _make_gold_repo_path,
    _preview_text,
    _relative_path_from_environment,
    exact_match,
    file_retrieval_metrics,
    gold_document_to_items,
    token_f1,
)
from dhee.benchmarks.raw_extractors import raw_file_to_items


def test_relative_path_and_gold_path_mapping() -> None:
    spec = CONFIG_SPECS["adam_subset"]
    repo_path = "Adam/Subset/Adam_Subset/contractnli/Tazza-CAFFE-Confidentiality-Agreement.pdf"

    relative = _relative_path_from_environment(spec, repo_path)
    assert relative == "contractnli/Tazza-CAFFE-Confidentiality-Agreement.pdf"

    gold_path = _make_gold_repo_path(spec.profile, relative)
    assert gold_path == "HippoCamp_Gold/Adam/contractnli/Tazza-CAFFE-Confidentiality-Agreement.json"


def test_gold_document_chunking_keeps_metadata() -> None:
    document = {
        "file_info": {
            "file_name": "Diary Entry.pdf",
            "file_type": "pdf",
            "file_modality": "document",
            "creation_date": "2025-10-20 09:00:00",
            "location": "Home",
        },
        "summary": "This file summarizes a weekly routine.",
        "segments": [
            {"page": 1, "content": "First page content " * 40},
            {"page": 2, "content": "Second page content " * 40},
        ],
    }

    items = gold_document_to_items(
        doc=document,
        profile="Adam",
        config_name="adam_subset",
        relative_path="docs/Diary Entry.pdf",
        chunk_chars=500,
    )

    assert len(items) >= 2
    assert items[0]["metadata"]["file_path"] == "docs/Diary Entry.pdf"
    assert items[0]["metadata"]["profile"] == "Adam"
    assert "Summary: This file summarizes a weekly routine." in items[0]["content"]
    assert "Relative Path: docs/Diary Entry.pdf" in items[0]["content"]


def test_file_retrieval_metrics_deduplicate_predictions() -> None:
    metrics = file_retrieval_metrics(
        predicted_paths=["a.txt", "a.txt", "b.txt"],
        gold_paths=["a.txt", "c.txt"],
    )
    assert round(metrics["precision"], 4) == 0.5
    assert round(metrics["recall"], 4) == 0.5
    assert round(metrics["f1"], 4) == 0.5


def test_answer_matching_helpers() -> None:
    assert exact_match("Cursor", "cursor")
    assert not exact_match("Cursor editor", "VS Code")
    assert token_f1("Cursor editor", "Cursor") > 0.0
    assert token_f1("", "") == 1.0


def test_progress_jsonl_event_emission(tmp_path: Path) -> None:
    path = tmp_path / "adam_subset.progress.jsonl"
    _emit_progress(
        path,
        "judge_done",
        config="adam_subset",
        question_index=3,
        judge_correct=True,
        judge_score_0_to_5=4.0,
        judge_rationale_preview=_preview_text("This answer matches the gold evidence and resolves the question cleanly."),
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "judge_done"
    assert payload["config"] == "adam_subset"
    assert payload["question_index"] == 3
    assert payload["judge_correct"] is True
    assert payload["judge_score_0_to_5"] == 4.0
    assert "gold evidence" in payload["judge_rationale_preview"]


def test_raw_text_file_extraction(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("alpha\nbeta\ngamma", encoding="utf-8")

    result = raw_file_to_items(
        local_path=path,
        relative_path="docs/note.txt",
        profile="Adam",
        config_name="adam_subset",
        chunk_chars=1000,
    )

    assert result.mode == "text"
    assert len(result.items) == 1
    assert "alpha" in result.items[0]["content"]
    assert result.items[0]["metadata"]["exposure_mode"] == "raw_files_only"


def test_raw_sqlite_extraction(tmp_path: Path) -> None:
    db_path = tmp_path / "sample.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE visits (url TEXT, title TEXT)")
        conn.execute("INSERT INTO visits VALUES (?, ?)", ("https://example.com", "Example"))
        conn.commit()
    finally:
        conn.close()

    result = raw_file_to_items(
        local_path=db_path,
        relative_path="Browser_History.sqlite",
        profile="Adam",
        config_name="adam_subset",
        chunk_chars=2000,
    )

    assert result.mode == "text"
    assert "table=visits" in result.items[0]["content"]
    assert "https://example.com" in result.items[0]["content"]


def test_raw_xlsx_extraction_from_minimal_ooxml(tmp_path: Path) -> None:
    xlsx_path = tmp_path / "plan.xlsx"
    with zipfile.ZipFile(xlsx_path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
             xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheets>
                <sheet name="Schedule" sheetId="1" r:id="rId1"/>
              </sheets>
            </workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>Run</t></si>
              <si><t>Tempo</t></si>
            </sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="s"><v>0</v></c>
                  <c r="B1" t="s"><v>1</v></c>
                </row>
                <row r="2">
                  <c r="A2"><v>5</v></c>
                  <c r="B2"><v>42</v></c>
                </row>
              </sheetData>
            </worksheet>""",
        )

    result = raw_file_to_items(
        local_path=xlsx_path,
        relative_path="plan.xlsx",
        profile="Adam",
        config_name="adam_subset",
        chunk_chars=2000,
    )

    assert result.mode == "text"
    content = result.items[0]["content"]
    assert "sheet=Schedule" in content
    assert "Run\tTempo" in content
    assert "5\t42" in content
