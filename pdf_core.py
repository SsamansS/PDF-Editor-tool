"""Readable PDF editing operations used by the CLI and the Tkinter app."""

from __future__ import annotations

import difflib
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class PdfEditorError(RuntimeError):
    """User-facing PDF editor error."""


@dataclass(frozen=True)
class PdfInfo:
    path: Path
    size_kb: float
    page_count: int
    encrypted: bool
    metadata: dict[str, str]
    text_sample_chars: int

    @property
    def has_text_layer(self) -> bool:
        return self.text_sample_chars > 0


@dataclass(frozen=True)
class ReplaceResult:
    matches: int
    overflow: int
    output: Path | None


@dataclass(frozen=True)
class EditApplyResult:
    replaced: int
    deleted: int
    not_placed: int
    output: Path


PAGE_MARKER_RE = re.compile(r"^=== Страница (\d+) ===$", re.MULTILINE)
BLOCK_MARKER_RE = re.compile(
    r"^\[\[PDFTXT page=(\d+) block=(\d+) "
    r"bbox=([-0-9.]+),([-0-9.]+),([-0-9.]+),([-0-9.]+) "
    r"font=([A-Za-z0-9_-]+) size=([0-9.]+) "
    r"color=([0-9.]+),([0-9.]+),([0-9.]+)\]\]$"
)
END_BLOCK_MARKER = "[[/PDFTXT]]"
UNICODE_FONT_NAME = "PDFTXTUnicode"
UNICODE_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\times.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


def parse_pages(spec: Iterable[str] | str | None, total: int) -> list[int]:
    """Convert CLI page specs like ``1 3 5-8`` to sorted zero-based indices."""
    if not spec:
        return list(range(total))
    if isinstance(spec, str):
        spec = spec.split()

    result: set[int] = set()
    for raw_item in spec:
        item = raw_item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end > total or start > end:
                raise PdfEditorError(f"Неверный диапазон страниц: {item} (всего: {total})")
            result.update(range(start - 1, end))
        else:
            page_number = int(item)
            if page_number < 1 or page_number > total:
                raise PdfEditorError(f"Страница {page_number} вне диапазона (всего: {total})")
            result.add(page_number - 1)
    return sorted(result)


def _require_file(path: str | os.PathLike[str]) -> Path:
    file_path = Path(path)
    if not file_path.is_file():
        raise PdfEditorError(f"Файл не найден: {file_path}")
    return file_path


def _open_reader(path: str | os.PathLike[str], password: str | None = None) -> PdfReader:
    file_path = _require_file(path)
    try:
        reader = PdfReader(str(file_path))
    except PdfReadError as exc:
        raise PdfEditorError(f"Не удалось прочитать PDF: {exc}") from exc

    if reader.is_encrypted:
        if reader.decrypt(password or "") == 0:
            raise PdfEditorError("PDF защищён паролем. Передайте пароль или снимите защиту.")
    return reader


def _open_fitz(path: str | os.PathLike[str], password: str | None = None):
    import fitz

    file_path = _require_file(path)
    doc = fitz.open(str(file_path))
    if doc.needs_pass and not doc.authenticate(password or ""):
        doc.close()
        raise PdfEditorError("PDF защищён паролем. Передайте пароль или снимите защиту.")
    return doc


def _save_fitz(doc, output: str | os.PathLike[str]) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path), garbage=4, deflate=True)
    return output_path


def get_info(path: str | os.PathLike[str], password: str | None = None) -> PdfInfo:
    doc = _open_fitz(path, password)
    try:
        metadata = {k: str(v) for k, v in (doc.metadata or {}).items() if v}
        sample = ""
        for page_index in range(min(3, len(doc))):
            page = doc[page_index]
            sample += page.get_text("text") or ""
        return PdfInfo(
            path=Path(path),
            size_kb=Path(path).stat().st_size / 1024,
            page_count=len(doc),
            encrypted=doc.needs_pass,
            metadata=metadata,
            text_sample_chars=len(sample.strip()),
        )
    finally:
        doc.close()


def merge_pdfs(output: str | os.PathLike[str], inputs: Iterable[str | os.PathLike[str]]) -> int:
    writer = PdfWriter()
    for path in inputs:
        reader = _open_reader(path)
        for page in reader.pages:
            writer.add_page(page)

    output_path = Path(output)
    with output_path.open("wb") as file:
        writer.write(file)
    return len(writer.pages)


def split_pdf(input_path: str | os.PathLike[str], output_dir: str | os.PathLike[str]) -> int:
    reader = _open_reader(input_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(input_path).stem

    for page_index, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        out_path = out_dir / f"{base}_page_{page_index:03d}.pdf"
        with out_path.open("wb") as file:
            writer.write(file)
    return len(reader.pages)


def extract_pages(input_path: str | os.PathLike[str], output: str | os.PathLike[str], pages: Iterable[str] | str) -> int:
    reader = _open_reader(input_path)
    indices = parse_pages(pages, len(reader.pages))
    writer = PdfWriter()
    for index in indices:
        writer.add_page(reader.pages[index])

    with Path(output).open("wb") as file:
        writer.write(file)
    return len(indices)


def delete_pages(input_path: str | os.PathLike[str], output: str | os.PathLike[str], pages: Iterable[str] | str) -> int:
    reader = _open_reader(input_path)
    to_delete = set(parse_pages(pages, len(reader.pages)))
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index not in to_delete:
            writer.add_page(page)

    with Path(output).open("wb") as file:
        writer.write(file)
    return len(to_delete)


def rotate_pages(
    input_path: str | os.PathLike[str],
    output: str | os.PathLike[str],
    angle: int,
    pages: Iterable[str] | str | None = None,
) -> int:
    if angle not in (90, 180, 270):
        raise PdfEditorError("Угол должен быть 90, 180 или 270 градусов.")

    reader = _open_reader(input_path)
    target = set(parse_pages(pages, len(reader.pages)))
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index in target:
            page.rotate(angle)
        writer.add_page(page)

    with Path(output).open("wb") as file:
        writer.write(file)
    return len(target)


def extract_text(input_path: str | os.PathLike[str], output: str | os.PathLike[str]) -> int:
    reader = _open_reader(input_path)
    chars = 0
    with Path(output).open("w", encoding="utf-8") as file:
        for index, page in enumerate(reader.pages, start=1):
            file.write(f"=== Страница {index} ===\n")
            text = page.extract_text() or ""
            chars += len(text)
            file.write(text)
            file.write("\n\n")
    return chars


def add_watermark(input_path: str | os.PathLike[str], output: str | os.PathLike[str], text: str) -> int:
    try:
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise PdfEditorError("Нужен reportlab для водяных знаков: pip install reportlab") from exc

    reader = _open_reader(input_path)
    writer = PdfWriter()

    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        buffer = io.BytesIO()
        canvas_page = canvas.Canvas(buffer, pagesize=(width, height))
        canvas_page.setFont("Helvetica-Bold", 60)
        canvas_page.setFillGray(0.5, 0.3)
        canvas_page.saveState()
        canvas_page.translate(width / 2, height / 2)
        canvas_page.rotate(45)
        canvas_page.drawCentredString(0, 0, text)
        canvas_page.restoreState()
        canvas_page.save()
        buffer.seek(0)

        watermark_page = PdfReader(buffer).pages[0]
        page.merge_page(watermark_page)
        writer.add_page(page)

    with Path(output).open("wb") as file:
        writer.write(file)
    return len(writer.pages)


def encrypt_pdf(input_path: str | os.PathLike[str], output: str | os.PathLike[str], password: str) -> int:
    reader = _open_reader(input_path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    with Path(output).open("wb") as file:
        writer.write(file)
    return len(writer.pages)


def decrypt_pdf(input_path: str | os.PathLike[str], output: str | os.PathLike[str], password: str) -> int:
    reader = _open_reader(input_path, password=password)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    with Path(output).open("wb") as file:
        writer.write(file)
    return len(writer.pages)


def _span_text(line: dict) -> str:
    parts = []
    for span in line.get("spans", []):
        if "text" in span:
            parts.append(span.get("text", ""))
        else:
            parts.append("".join(char.get("c", "") for char in span.get("chars", [])))
    return "".join(parts)


def _block_text(block: dict) -> str:
    return "\n".join(_span_text(line).rstrip() for line in block.get("lines", [])).strip("\n")


def _raw_line_chars(line: dict) -> list[tuple[dict, dict]]:
    chars = []
    for span in line.get("spans", []):
        for char in span.get("chars", []):
            chars.append((char, span))
    return chars


def _base_font_name(font_name: str | None) -> str:
    font = (font_name or "").lower()
    if "mono" in font or "courier" in font or "consol" in font:
        if "bold" in font and ("italic" in font or "oblique" in font):
            return "cobi"
        if "bold" in font:
            return "cobo"
        if "italic" in font or "oblique" in font:
            return "coit"
        return "cour"
    if "times" in font or "serif" in font:
        if "bold" in font and ("italic" in font or "oblique" in font):
            return "tibi"
        if "bold" in font:
            return "tibo"
        if "italic" in font or "oblique" in font:
            return "tiit"
        return "tiro"
    if "bold" in font and ("italic" in font or "oblique" in font):
        return "hebi"
    if "bold" in font:
        return "hebo"
    if "italic" in font or "oblique" in font:
        return "heit"
    return "helv"


def _span_color_to_rgb(color: int | None) -> tuple[float, float, float]:
    if color is None:
        return (0.0, 0.0, 0.0)
    return (
        round(((color >> 16) & 255) / 255, 4),
        round(((color >> 8) & 255) / 255, 4),
        round((color & 255) / 255, 4),
    )


def _block_style(block: dict) -> tuple[str, float, tuple[float, float, float]]:
    best_span = None
    best_score = -1.0
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            score = len(span.get("text", "").strip()) * float(span.get("size", 0) or 0)
            if score > best_score:
                best_score = score
                best_span = span

    if not best_span:
        return "helv", 9.0, (0.0, 0.0, 0.0)

    fontsize = round(float(best_span.get("size", 9.0) or 9.0), 1)
    return _base_font_name(best_span.get("font")), max(fontsize, 5.5), _span_color_to_rgb(best_span.get("color"))


def _find_unicode_font() -> str | None:
    for path in UNICODE_FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def _font_for_text(page, text: str, fontname: str) -> str:
    if all(ord(char) <= 255 for char in text):
        return fontname

    font_path = _find_unicode_font()
    if not font_path:
        return fontname
    try:
        page.insert_font(fontname=UNICODE_FONT_NAME, fontfile=font_path)
        return UNICODE_FONT_NAME
    except Exception:
        return fontname


def _expanded_rect(rect, page_rect, pad: float = 1.5):
    import fitz

    return fitz.Rect(
        max(page_rect.x0, rect.x0 - pad),
        max(page_rect.y0, rect.y0 - pad),
        min(page_rect.x1, rect.x1 + pad),
        min(page_rect.y1, rect.y1 + pad),
    )


def _fontsize_from_rect(rect) -> float:
    return round(rect.height * 0.85, 1) if rect.height > 1 else 9.0


def _line_height_overlap(a, b) -> float:
    top = max(a.y0, b.y0)
    bottom = min(a.y1, b.y1)
    return max(0.0, bottom - top)


def _available_inline_width(page, rect, padding: float = 2.4) -> float:
    """Return width available before the next visible character on the same line."""
    import fitz

    available = rect.width
    raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    if not char.get("c", "").strip():
                        continue
                    char_rect = fitz.Rect(char["bbox"])
                    if char_rect.x0 <= rect.x1 + 0.1:
                        continue
                    if _line_height_overlap(rect, char_rect) < min(rect.height, char_rect.height) * 0.45:
                        continue
                    available = max(rect.width, char_rect.x0 - rect.x0 - padding)
                    return available
    return available


def _replacement_style_at(page, rect) -> tuple[str, float, tuple[float, float, float], float]:
    import fitz

    best_fontname = "helv"
    best_color = (0.0, 0.0, 0.0)
    best_baseline = rect.y1 - max(rect.height * 0.22, 1.0)
    best_overlap = 0.0

    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                overlap = (rect & fitz.Rect(span["bbox"])).get_area()
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_fontname = _base_font_name(span.get("font"))
                    best_color = _span_color_to_rgb(span.get("color"))
                    origin = span.get("origin")
                    if origin:
                        best_baseline = float(origin[1])

    return best_fontname, _fontsize_from_rect(rect), best_color, best_baseline


def _insert_textbox_fit(page, rect, text: str, fontname: str, fontsize: float, color: tuple[float, float, float]):
    import fitz

    if not text.strip():
        return True, fontsize

    fontname = _font_for_text(page, text, fontname)
    page_rect = page.rect
    sizes = []
    size = max(float(fontsize), 5.5)
    while size >= 5.5:
        sizes.append(round(size, 1))
        size -= 0.5

    candidate_rects = [
        rect,
        fitz.Rect(rect.x0, rect.y0, min(page_rect.x1, rect.x1 + 24), min(page_rect.y1, rect.y1 + rect.height * 0.35)),
        fitz.Rect(rect.x0, rect.y0, min(page_rect.x1, rect.x1 + 48), min(page_rect.y1, rect.y1 + rect.height)),
    ]

    for candidate_rect in candidate_rects:
        for candidate_size in sizes:
            result = page.insert_textbox(
                candidate_rect,
                text,
                fontsize=candidate_size,
                fontname=fontname,
                color=color,
                align=fitz.TEXT_ALIGN_LEFT,
            )
            if result >= 0:
                return True, candidate_size
    return False, sizes[-1] if sizes else fontsize


def _insert_inline_replacement(
    page,
    rect,
    text: str,
    fontname: str,
    fontsize: float,
    color: tuple[float, float, float],
    baseline_y: float,
    max_width: float | None = None,
) -> tuple[bool, float]:
    """Insert replacement text with original visual height.

    ``insert_textbox`` shrinks text vertically to fit a small search rectangle.
    For short inline replacements that makes the font look wrong. This function
    preserves the visual font size and compresses only horizontally if the new
    text is wider than the old fragment.
    """
    import fitz

    if not text.strip():
        return True, 1.0

    fontname = _font_for_text(page, text, fontname)
    text_width = fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)
    available_width = max_width if max_width is not None else _available_inline_width(page, rect)
    scale_x = min(1.0, available_width / text_width) if text_width > 0 else 1.0
    origin = fitz.Point(rect.x0, baseline_y)
    page.insert_text(
        origin,
        text,
        fontsize=fontsize,
        fontname=fontname,
        color=color,
        morph=(origin, fitz.Matrix(scale_x, 1)),
        overlay=True,
    )
    return True, scale_x


def replace_text(
    input_path: str | os.PathLike[str],
    output: str | os.PathLike[str],
    old: str,
    new: str,
    pages: Iterable[str] | str | None = None,
    max_matches: int = 0,
) -> ReplaceResult:
    import fitz

    if not old:
        raise PdfEditorError("Искомый текст не должен быть пустым.")

    doc = _open_fitz(input_path)
    try:
        target_pages = set(parse_pages(pages, len(doc)))
        total_matches = 0
        total_overflow = 0
        limit = max_matches if max_matches and max_matches > 0 else None

        for page_index in range(len(doc)):
            if page_index not in target_pages:
                continue

            page = doc[page_index]
            replacements = []
            for rect in page.search_for(old):
                if limit is not None and total_matches >= limit:
                    break
                rect = rect & page.rect
                if rect.is_empty:
                    continue
                fontname, fontsize, color, baseline_y = _replacement_style_at(page, rect)
                page.add_redact_annot(_expanded_rect(rect, page.rect), fill=(1, 1, 1))
                max_width = _available_inline_width(page, rect)
                replacements.append((rect, fontname, fontsize, color, baseline_y, max_width))
                total_matches += 1

            if not replacements:
                continue

            page.apply_redactions()
            for rect, fontname, fontsize, color, baseline_y, max_width in replacements:
                ok, _scale_x = _insert_inline_replacement(page, rect, new, fontname, fontsize, color, baseline_y, max_width)
                if not ok:
                    total_overflow += 1

            if limit is not None and total_matches >= limit:
                break

        if total_matches == 0:
            return ReplaceResult(matches=0, overflow=0, output=None)

        output_path = _save_fitz(doc, output)
        return ReplaceResult(matches=total_matches, overflow=total_overflow, output=output_path)
    finally:
        doc.close()


def write_edit_text(input_path: str | os.PathLike[str], output_txt: str | os.PathLike[str]) -> int:
    import fitz

    doc = _open_fitz(input_path)
    try:
        with Path(output_txt).open("w", encoding="utf-8") as file:
            for page_index in range(len(doc)):
                page = doc[page_index]
                file.write(f"=== Страница {page_index + 1} ===\n")
                file.write("# Редактируйте текст только между [[PDFTXT ...]] и [[/PDFTXT]].\n")
                file.write("# Маркеры нужны, чтобы вернуть текст в те же места PDF.\n\n")

                block_id = 0
                text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue

                    text = _block_text(block)
                    if not text.strip():
                        continue

                    rect = fitz.Rect(block["bbox"])
                    fontname, fontsize, color = _block_style(block)
                    file.write(
                        "[[PDFTXT "
                        f"page={page_index + 1} block={block_id} "
                        f"bbox={rect.x0:.3f},{rect.y0:.3f},{rect.x1:.3f},{rect.y1:.3f} "
                        f"font={fontname} size={fontsize:.1f} "
                        f"color={color[0]:.4f},{color[1]:.4f},{color[2]:.4f}"
                        "]]\n"
                    )
                    file.write(text.rstrip("\n"))
                    file.write(f"\n{END_BLOCK_MARKER}\n\n")
                    block_id += 1
        return len(doc)
    finally:
        doc.close()


def _parse_txt_pages(content: str) -> dict[int, str]:
    pages = {}
    markers = list(PAGE_MARKER_RE.finditer(content))
    for index, marker in enumerate(markers):
        page_num = int(marker.group(1))
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(content)
        pages[page_num] = content[start:end].strip()
    return pages


def _parse_txt_blocks(content: str) -> dict[int, list[dict]]:
    pages: dict[int, list[dict]] = {}
    current = None
    buffer: list[str] = []

    for raw_line in content.split("\n"):
        line = raw_line.rstrip("\n")
        marker = BLOCK_MARKER_RE.match(line)
        if marker:
            if current is not None:
                raise PdfEditorError("Найден новый маркер блока до закрывающего [[/PDFTXT]].")
            current = {
                "page": int(marker.group(1)),
                "block": int(marker.group(2)),
                "bbox": tuple(float(marker.group(i)) for i in range(3, 7)),
                "fontname": marker.group(7),
                "fontsize": float(marker.group(8)),
                "color": tuple(float(marker.group(i)) for i in range(9, 12)),
            }
            buffer = []
            continue

        if line == END_BLOCK_MARKER:
            if current is None:
                raise PdfEditorError("Найден закрывающий [[/PDFTXT]] без открывающего маркера блока.")
            current["text"] = "\n".join(buffer).strip("\n")
            pages.setdefault(current["page"], []).append(current)
            current = None
            buffer = []
            continue

        if current is not None:
            buffer.append(raw_line)

    if current is not None:
        raise PdfEditorError("Последний блок TXT не закрыт маркером [[/PDFTXT]].")
    return pages


def _rect_from_chars(chars: list[tuple[dict, dict]]):
    import fitz

    rect = fitz.Rect(chars[0][0]["bbox"])
    for char, _span in chars[1:]:
        rect.include_rect(fitz.Rect(char["bbox"]))
    return rect


def _visible_line_chars(line: dict) -> list[tuple[dict, dict]]:
    chars = _raw_line_chars(line)
    while chars and not chars[-1][0].get("c", "").strip():
        chars.pop()
    return chars


def _runs_of_changes(old_text: str, new_text: str):
    start = None
    for index, (old_char, new_char) in enumerate(zip(old_text, new_text)):
        if old_char != new_char and start is None:
            start = index
        elif old_char == new_char and start is not None:
            yield start, index
            start = None
    if start is not None:
        yield start, len(old_text)


def _overlay_equal_length_line_edits(page, original_block: dict, new_text: str):
    import fitz

    old_lines = []
    line_chars = []
    for line in original_block.get("lines", []):
        chars = _visible_line_chars(line)
        text = "".join(char["c"] for char, _span in chars).rstrip()
        old_lines.append(text)
        line_chars.append(chars[: len(text)])

    new_lines = new_text.split("\n")
    if len(old_lines) != len(new_lines):
        return False, 0
    if any(len(old_line) != len(new_line) for old_line, new_line in zip(old_lines, new_lines)):
        return False, 0

    replacements = []
    for old_line, new_line, chars in zip(old_lines, new_lines, line_chars):
        for start, end in _runs_of_changes(old_line, new_line):
            changed_chars = chars[start:end]
            if not changed_chars:
                continue
            first_span = changed_chars[0][1]
            rect = _rect_from_chars(changed_chars)
            origin = changed_chars[0][0].get("origin", (rect.x0, rect.y1))
            replacement_text = new_line[start:end]
            fontname = _base_font_name(first_span.get("font"))
            fontsize = _fontsize_from_rect(rect)
            text_width = fitz.get_text_length(replacement_text, fontname=fontname, fontsize=fontsize)
            scale_x = rect.width / text_width if text_width > 0 else 1.0
            replacements.append((rect, fitz.Point(origin), replacement_text, fontname, fontsize, _span_color_to_rgb(first_span.get("color")), scale_x))

    for rect, origin, text, fontname, fontsize, color, scale_x in replacements:
        page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
        page.insert_text(
            origin,
            text,
            fontsize=fontsize,
            fontname=_font_for_text(page, text, fontname),
            color=color,
            morph=(origin, fitz.Matrix(scale_x, 1)),
            overlay=True,
        )

    return True, len(replacements)


def _overlay_same_line_count_edits(page, original_block: dict, new_text: str) -> tuple[bool, int]:
    import fitz

    original_lines = []
    line_styles = []
    for line in original_block.get("lines", []):
        chars = _visible_line_chars(line)
        if not chars:
            continue
        text = "".join(char["c"] for char, _span in chars).rstrip()
        rect = _rect_from_chars(chars[: len(text)])
        first_char, first_span = chars[0]
        origin = first_char.get("origin", (rect.x0, rect.y1))
        original_lines.append(text)
        line_styles.append(
            (
                rect,
                fitz.Point(origin),
                _base_font_name(first_span.get("font")),
                _fontsize_from_rect(rect),
                _span_color_to_rgb(first_span.get("color")),
            )
        )

    new_lines = new_text.split("\n")
    if len(original_lines) != len(new_lines):
        return False, 0

    replacements = []
    for old_line, new_line, style in zip(original_lines, new_lines, line_styles):
        if old_line == new_line:
            continue
        replacements.append((*style, new_line))

    for rect, origin, fontname, fontsize, color, line_text in replacements:
        page.draw_rect(_expanded_rect(rect, page.rect, pad=0.6), color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
        _insert_inline_replacement(page, rect, line_text, fontname, fontsize, color, origin.y, max_width=rect.width)

    return True, len(replacements)


def _apply_block_edits(doc, edited_blocks: dict[int, list[dict]]) -> tuple[int, int, int]:
    import fitz

    changed = deleted = overflow = 0
    for page_index in range(len(doc)):
        page_number = page_index + 1
        blocks = edited_blocks.get(page_number)
        if not blocks:
            continue

        page = doc[page_index]
        original_blocks = [
            block
            for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
            if block.get("type") == 0 and _block_text(block).strip()
        ]
        raw_blocks = [
            block
            for block in page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
            if block.get("type") == 0 and _block_text(block).strip()
        ]

        pending = []
        for item in sorted(blocks, key=lambda value: value["block"]):
            block_id = item["block"]
            new_text = item["text"]
            old_text = _block_text(original_blocks[block_id]) if block_id < len(original_blocks) else None
            if old_text == new_text:
                continue

            if block_id < len(raw_blocks):
                handled, replacements = _overlay_equal_length_line_edits(page, raw_blocks[block_id], new_text)
                if handled:
                    changed += replacements
                    continue
                handled, replacements = _overlay_same_line_count_edits(page, raw_blocks[block_id], new_text)
                if handled:
                    changed += replacements
                    continue

            rect = fitz.Rect(item["bbox"]) & page.rect
            if rect.is_empty:
                continue

            page.add_redact_annot(_expanded_rect(rect, page.rect), fill=(1, 1, 1))
            pending.append((rect, new_text, item["fontname"], item["fontsize"], item["color"]))
            if new_text.strip():
                changed += 1
            else:
                deleted += 1

        if not pending:
            continue

        page.apply_redactions()
        for rect, text, fontname, fontsize, color in pending:
            ok, _used_size = _insert_textbox_fit(page, rect, text, fontname, fontsize, color)
            if not ok:
                overflow += 1

    return changed, deleted, overflow


def _span_style_at(page, rect) -> tuple[str, float]:
    fontname, fontsize, _color, _baseline_y = _replacement_style_at(page, rect)
    return fontname, fontsize


def _apply_legacy_page_edits(doc, edited_pages: dict[int, str]) -> tuple[int, int, int]:
    replaced = deleted = skipped_inserts = 0

    for page_index in range(len(doc)):
        page_number = page_index + 1
        if page_number not in edited_pages:
            continue

        page = doc[page_index]
        original_text = page.get_text("text").strip()
        new_text = edited_pages[page_number]
        if original_text == new_text:
            continue

        original_lines = [line for line in original_text.splitlines() if line.strip()]
        edited_lines = [line for line in new_text.splitlines() if line.strip()]
        matcher = difflib.SequenceMatcher(None, original_lines, edited_lines, autojunk=False)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            old_block = original_lines[i1:i2]
            new_block = edited_lines[j1:j2]

            if tag in ("replace", "delete"):
                for index, old_line in enumerate(old_block):
                    replacement = new_block[index].strip() if tag == "replace" and index < len(new_block) else ""
                    for rect in page.search_for(old_line.strip()):
                        fontname, fontsize = _span_style_at(page, rect)
                        page.add_redact_annot(rect, replacement, fontname=fontname, fontsize=fontsize)
                        if replacement:
                            replaced += 1
                        else:
                            deleted += 1
            elif tag == "insert":
                skipped_inserts += len(new_block)

        page.apply_redactions()

    return replaced, deleted, skipped_inserts


def apply_edit_text(
    input_path: str | os.PathLike[str],
    txt_path: str | os.PathLike[str],
    output: str | os.PathLike[str],
) -> EditApplyResult:
    content = Path(txt_path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    edited_blocks = _parse_txt_blocks(content)
    edited_pages = _parse_txt_pages(content) if not edited_blocks else {}
    if not edited_blocks and not edited_pages:
        raise PdfEditorError("Не найдено ни одного маркера '=== Страница N ==='.")

    doc = _open_fitz(input_path)
    try:
        if edited_blocks:
            replaced, deleted, not_placed = _apply_block_edits(doc, edited_blocks)
        else:
            replaced, deleted, not_placed = _apply_legacy_page_edits(doc, edited_pages)
        output_path = _save_fitz(doc, output)
        return EditApplyResult(replaced=replaced, deleted=deleted, not_placed=not_placed, output=output_path)
    finally:
        doc.close()
