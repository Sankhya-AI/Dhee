"""Raw-file extraction helpers for HippoCamp-style benchmarks.

These extractors operate only on released raw files. They intentionally avoid
benchmark gold text and QA annotations.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
import subprocess
import zipfile
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


TEXT_EXTENSIONS = {"txt", "md", "py", "log", "ics", "json", "csv"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
VIDEO_EXTENSIONS = {"mp4", "mkv"}
AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "aac"}


@dataclass
class RawExtractionResult:
    items: List[Dict[str, Any]]
    mode: str
    notes: List[str]


def _run_command(args: Sequence[str]) -> str:
    proc = subprocess.run(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"Command failed: {' '.join(args)}")
    return proc.stdout


def _chunk_blocks(
    *,
    header: str,
    blocks: Sequence[str],
    chunk_chars: int,
    metadata: Dict[str, Any],
    categories: Sequence[str],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    target_chars = max(400, int(chunk_chars))
    current_blocks: List[str] = []
    current_len = 0
    chunk_index = 1

    def flush() -> None:
        nonlocal current_blocks, current_len, chunk_index
        if not current_blocks:
            return
        body = "\n\n".join(current_blocks)
        content = f"{header}\nChunk: {chunk_index}\n\nContent:\n{body}"
        item_meta = dict(metadata)
        item_meta["chunk_index"] = chunk_index
        items.append({"content": content, "metadata": item_meta, "categories": list(categories)})
        current_blocks = []
        current_len = 0
        chunk_index += 1

    for raw_block in blocks:
        block = str(raw_block or "").strip()
        if not block:
            continue
        block_len = len(block)
        if current_blocks and current_len + block_len + 2 > target_chars:
            flush()
        current_blocks.append(block)
        current_len += block_len + 2
    flush()
    return items


def _read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except Exception:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_email_text(path: Path) -> str:
    with path.open("rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)
    parts: List[str] = []
    for header in ("subject", "from", "to", "cc", "date"):
        value = message.get(header)
        if value:
            parts.append(f"{header.title()}: {value}")
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        try:
            body_text = body.get_content()
        except Exception:
            body_text = ""
        if body_text:
            parts.extend(["", str(body_text)])
    return "\n".join(parts).strip()


def _extract_pdf_pages(path: Path) -> List[str]:
    output = _run_command(["pdftotext", str(path), "-"])
    return [page.strip() for page in output.split("\f") if page.strip()]


def _extract_docx_text(path: Path) -> str:
    try:
        text = _run_command(["textutil", "-convert", "txt", "-stdout", str(path)]).strip()
        if text:
            return text
    except Exception:
        pass
    return _extract_docx_ooxml_text(path)


def _extract_docx_ooxml_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.startswith("word/") and name.endswith(".xml")]
        parts: List[str] = []
        for name in sorted(names):
            if not any(key in name for key in ("document.xml", "header", "footer", "footnotes", "endnotes")):
                continue
            root = ET.fromstring(archive.read(name))
            texts = [elem.text for elem in root.iter() if elem.tag.endswith("}t") and elem.text]
            if texts:
                parts.append("\n".join(texts))
    return "\n\n".join(parts).strip()


def _extract_pptx_ooxml_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        slides = []
        for name in sorted(archive.namelist()):
            if not name.startswith("ppt/slides/slide") or not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name))
            texts = [elem.text for elem in root.iter() if elem.tag.endswith("}t") and elem.text]
            if texts:
                slides.append("\n".join(texts))
    return "\n\n".join(f"[slide={idx + 1}]\n{text}" for idx, text in enumerate(slides)).strip()


def _col_ref_to_index(col_ref: str) -> int:
    result = 0
    for ch in col_ref:
        if not ch.isalpha():
            break
        result = result * 26 + (ord(ch.upper()) - ord("A") + 1)
    return max(1, result)


def _extract_xlsx_ooxml_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for elem in root.iter():
                if elem.tag.endswith("}t") and elem.text is not None:
                    shared_strings.append(elem.text)

        sheet_names: Dict[str, str] = {}
        if "xl/workbook.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/workbook.xml"))
            for elem in root.iter():
                if elem.tag.endswith("}sheet"):
                    rel_id = elem.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
                    if rel_id:
                        sheet_names[rel_id] = elem.attrib.get("name", rel_id)

        rel_map: Dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in archive.namelist():
            root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for elem in root.iter():
                if elem.tag.endswith("}Relationship"):
                    rel_id = elem.attrib.get("Id", "")
                    target = elem.attrib.get("Target", "")
                    if rel_id and target:
                        rel_map[target] = sheet_names.get(rel_id, PurePosixPath(target).stem)

        sheets_out: List[str] = []
        for name in sorted(archive.namelist()):
            if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name))
            rows_out: List[str] = []
            for row in root.iter():
                if not row.tag.endswith("}row"):
                    continue
                values: Dict[int, str] = {}
                for cell in row:
                    if not cell.tag.endswith("}c"):
                        continue
                    ref = cell.attrib.get("r", "")
                    col_idx = _col_ref_to_index(ref) if ref else (len(values) + 1)
                    cell_type = cell.attrib.get("t", "")
                    value_text = ""
                    value_elem = None
                    for child in cell:
                        if child.tag.endswith("}v"):
                            value_elem = child
                            break
                    if value_elem is not None and value_elem.text is not None:
                        raw_value = value_elem.text
                        if cell_type == "s":
                            try:
                                value_text = shared_strings[int(raw_value)]
                            except Exception:
                                value_text = raw_value
                        else:
                            value_text = raw_value
                    if value_text:
                        values[col_idx] = value_text
                if values:
                    rows_out.append("\t".join(values[idx] for idx in sorted(values)))
            if rows_out:
                sheet_label = rel_map.get(name.replace("xl/", ""), Path(name).stem)
                sheets_out.append(f"[sheet={sheet_label}]\n" + "\n".join(rows_out))
        return "\n\n".join(sheets_out).strip()


def _extract_ipynb_text(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        notebook = json.load(handle)
    parts: List[str] = []
    for idx, cell in enumerate(notebook.get("cells") or [], start=1):
        source = cell.get("source") or []
        if isinstance(source, list):
            text = "".join(str(part) for part in source)
        else:
            text = str(source)
        text = text.strip()
        if text:
            parts.append(f"[{cell.get('cell_type', 'cell')} {idx}]\n{text}")
    return "\n\n".join(parts).strip()


def _extract_sqlite_text(path: Path, max_tables: int = 6, max_rows: int = 10) -> str:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        parts: List[str] = []
        for row in rows[:max_tables]:
            table = str(row["name"])
            escaped_table = table.replace("'", "''")
            parts.append(f"[table={table}]")
            schema = conn.execute(f"PRAGMA table_info('{escaped_table}')").fetchall()
            if schema:
                parts.append("columns: " + ", ".join(f"{col['name']}:{col['type']}" for col in schema))
            try:
                sample_rows = conn.execute(
                    f"SELECT * FROM '{escaped_table}' LIMIT {int(max_rows)}"
                ).fetchall()
            except Exception:
                sample_rows = []
            for sample in sample_rows:
                payload = {key: sample[key] for key in sample.keys()}
                parts.append(json.dumps(payload, ensure_ascii=False))
            parts.append("")
        return "\n".join(parts).strip()
    finally:
        conn.close()


def _extract_csv_text(path: Path, max_rows: int = 100) -> str:
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.reader(handle)
        rows = []
        for idx, row in enumerate(reader):
            if idx >= max_rows:
                break
            rows.append("\t".join(str(cell) for cell in row))
    return "\n".join(rows).strip()


def _extract_media_metadata(path: Path) -> str:
    try:
        output = _run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=filename,format_name,duration,size:stream=index,codec_type,codec_name,width,height,sample_rate,channels:stream_tags=language,title",
                "-of",
                "json",
                str(path),
            ]
        )
        return json.dumps(json.loads(output), ensure_ascii=False, indent=2)
    except Exception:
        return ""


def _extract_subtitles(path: Path) -> str:
    try:
        output = _run_command(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:s:0", "-f", "srt", "-"])
    except Exception:
        return ""
    lines: List[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_image_metadata(path: Path) -> str:
    parts: List[str] = []
    try:
        from PIL import Image

        with Image.open(path) as image:
            parts.append(f"format: {image.format}")
            parts.append(f"size: {image.size[0]}x{image.size[1]}")
            parts.append(f"mode: {image.mode}")
    except Exception:
        pass
    return "\n".join(parts).strip()


def _raw_header(*, profile: str, config_name: str, relative_path: str, local_path: Path, mode: str) -> str:
    stat = local_path.stat()
    ext = local_path.suffix.lower().lstrip(".")
    return "\n".join(
        [
            "[HippoCamp Raw File]",
            f"Profile: {profile}",
            f"Config: {config_name}",
            f"Relative Path: {relative_path}",
            f"File Type: {ext}",
            f"Extraction Mode: {mode}",
            f"File Size Bytes: {stat.st_size}",
        ]
    )


def _extract_audio_transcript(local_path: Path) -> Optional[str]:
    """Transcribe audio using OpenAI whisper (local model). Returns None if not available."""
    try:
        import whisper  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("whisper not installed — skipping audio transcription for %s", local_path.name)
        return None
    try:
        model = whisper.load_model("base")
        result = model.transcribe(str(local_path), fp16=False)
        text = str(result.get("text", "")).strip()
        if not text:
            return None
        return f"[audio_transcript]\n{text}"
    except Exception as exc:
        logger.warning("Whisper transcription failed for %s: %s", local_path.name, exc)
        return None


def _extract_image_ocr(local_path: Path) -> Optional[str]:
    """Extract text from image using pytesseract OCR. Returns None if not available."""
    try:
        from PIL import Image  # type: ignore[import-untyped]
        import pytesseract  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("pytesseract/Pillow not installed — skipping OCR for %s", local_path.name)
        return None
    try:
        img = Image.open(local_path)
        text = pytesseract.image_to_string(img).strip()
        if not text or len(text) < 3:
            return None
        return f"[ocr_text]\n{text}"
    except Exception as exc:
        logger.warning("OCR failed for %s: %s", local_path.name, exc)
        return None


def raw_file_to_items(
    *,
    local_path: Path,
    relative_path: str,
    profile: str,
    config_name: str,
    chunk_chars: int,
) -> RawExtractionResult:
    ext = local_path.suffix.lower().lstrip(".")
    mode = "text"
    notes: List[str] = []
    blocks: List[str] = []

    try:
        if ext in TEXT_EXTENSIONS:
            blocks = [_extract_csv_text(local_path) if ext == "csv" else _read_text_file(local_path)]
        elif ext == "eml":
            blocks = [_extract_email_text(local_path)]
        elif ext == "pdf":
            blocks = [f"[page={idx + 1}]\n{page}" for idx, page in enumerate(_extract_pdf_pages(local_path))]
        elif ext == "docx":
            blocks = [_extract_docx_text(local_path)]
        elif ext == "pptx":
            blocks = [_extract_pptx_ooxml_text(local_path)]
        elif ext == "xlsx":
            blocks = [_extract_xlsx_ooxml_text(local_path)]
        elif ext == "ipynb":
            blocks = [_extract_ipynb_text(local_path)]
        elif ext == "sqlite":
            blocks = [_extract_sqlite_text(local_path)]
        elif ext in VIDEO_EXTENSIONS:
            subtitles = _extract_subtitles(local_path)
            metadata = _extract_media_metadata(local_path)
            if subtitles:
                mode = "subtitle_text"
                blocks = [subtitles]
                if metadata:
                    blocks.append("[media_metadata]\n" + metadata)
            else:
                mode = "metadata_only"
                blocks = ["[media_metadata]\n" + metadata] if metadata else [f"Raw {ext} video file."]
                notes.append("No subtitle text extracted.")
        elif ext in AUDIO_EXTENSIONS:
            transcript = _extract_audio_transcript(local_path)
            if transcript:
                mode = "transcript"
                blocks = [transcript]
                metadata_text = _extract_media_metadata(local_path)
                if metadata_text:
                    blocks.append("[media_metadata]\n" + metadata_text)
            else:
                mode = "metadata_only"
                metadata_text = _extract_media_metadata(local_path)
                blocks = ["[media_metadata]\n" + metadata_text] if metadata_text else [f"Raw {ext} audio file."]
                notes.append("No speech-to-text available (whisper not installed or failed).")
        elif ext in IMAGE_EXTENSIONS:
            ocr_text = _extract_image_ocr(local_path)
            if ocr_text:
                mode = "ocr_text"
                blocks = [ocr_text]
                img_metadata = _extract_image_metadata(local_path)
                if img_metadata:
                    blocks.append("[image_metadata]\n" + img_metadata)
            else:
                mode = "metadata_only"
                img_metadata = _extract_image_metadata(local_path)
                blocks = ["[image_metadata]\n" + img_metadata] if img_metadata else [f"Raw {ext} image file."]
                notes.append("No OCR text extracted (pytesseract not installed or no text found).")
        else:
            mode = "metadata_only"
            blocks = [f"Unsupported raw file type for text extraction: {ext or 'unknown'}"]
            notes.append(f"Unsupported file extension: {ext or 'unknown'}")
    except Exception as exc:
        mode = "metadata_only"
        blocks = [f"Extraction failed for {relative_path}: {exc}"]
        notes.append(str(exc))

    blocks = [block for block in blocks if str(block or "").strip()]
    if not blocks:
        blocks = [f"Empty extracted content for {relative_path}"]
        notes.append("Empty extracted content.")

    header = _raw_header(
        profile=profile,
        config_name=config_name,
        relative_path=relative_path,
        local_path=local_path,
        mode=mode,
    )
    metadata = {
        "benchmark": "hippocamp",
        "exposure_mode": "raw_files_only",
        "config_name": config_name,
        "profile": profile,
        "file_path": relative_path,
        "file_name": local_path.name,
        "file_type": ext or None,
        "raw_extraction_mode": mode,
        "raw_extraction_notes": notes,
    }
    categories = ["hippocamp", "raw_files_only", config_name.lower(), profile.lower()]
    items = _chunk_blocks(
        header=header,
        blocks=blocks,
        chunk_chars=chunk_chars,
        metadata=metadata,
        categories=categories,
    )
    return RawExtractionResult(items=items, mode=mode, notes=notes)
