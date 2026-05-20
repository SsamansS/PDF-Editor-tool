#!/usr/bin/env python3
"""
PDF Editor — консольная утилита для базового редактирования PDF.

Возможности:
  info       — показать информацию о файле (страницы, метаданные, защита)
  merge      — объединить несколько PDF в один
  split      — разбить PDF на отдельные страницы
  extract    — извлечь конкретные страницы в новый файл
  delete     — удалить указанные страницы
  rotate     — повернуть страницы на 90/180/270 градусов
  text       — извлечь весь текст в .txt файл
  watermark  — добавить текстовый водяной знак на каждую страницу
  encrypt    — установить пароль на файл
  decrypt    — снять пароль (нужно знать текущий)
  edit-open  — извлечь текст в .txt для редактирования (round-trip)
  edit-apply — применить отредактированный .txt обратно в PDF

Установка зависимостей:
  pip install pypdf reportlab pymupdf

Примеры:
  python pdf_editor.py info document.pdf
  python pdf_editor.py merge out.pdf a.pdf b.pdf c.pdf
  python pdf_editor.py split document.pdf ./pages/
  python pdf_editor.py extract document.pdf out.pdf 1 3 5-8
  python pdf_editor.py delete document.pdf out.pdf 2 4
  python pdf_editor.py rotate document.pdf out.pdf 90 --pages 1 2
  python pdf_editor.py text document.pdf out.txt
  python pdf_editor.py watermark document.pdf out.pdf "ЧЕРНОВИК"
  python pdf_editor.py encrypt document.pdf out.pdf secret123
  python pdf_editor.py decrypt document.pdf out.pdf secret123
  python pdf_editor.py edit-open document.pdf document.txt
  python pdf_editor.py edit-apply document.pdf document.txt result.pdf
"""

import argparse
import difflib
import io
import os
import re
import sys
from pathlib import Path

# Корректный вывод UTF-8 в терминале Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError
except ImportError:
    sys.exit("Нужен pypdf. Установите: pip install pypdf")


def parse_pages(spec, total):
    """
    Превращает строки вида '1 3 5-8 10' в отсортированный список индексов (0-based).
    Принимает список аргументов как с CLI: ['1', '3', '5-8', '10'].
    """
    result = set()
    for item in spec:
        item = item.strip()
        if "-" in item:
            start, end = item.split("-", 1)
            start, end = int(start), int(end)
            if start < 1 or end > total or start > end:
                raise ValueError(f"Неверный диапазон страниц: {item} (всего страниц: {total})")
            result.update(range(start - 1, end))
        else:
            n = int(item)
            if n < 1 or n > total:
                raise ValueError(f"Страница {n} вне диапазона (всего: {total})")
            result.add(n - 1)
    return sorted(result)


def open_reader(path):
    """Открывает PDF и обрабатывает типичные ошибки."""
    if not os.path.isfile(path):
        sys.exit(f"Файл не найден: {path}")
    try:
        reader = PdfReader(path)
    except PdfReadError as e:
        sys.exit(f"Не удалось прочитать PDF: {e}")

    if reader.is_encrypted:
        # Пытаемся открыть без пароля (некоторые PDF зашифрованы пустым паролем)
        if not reader.decrypt(""):
            password = input(f"Файл '{path}' защищён паролем. Введите пароль: ")
            if not reader.decrypt(password):
                sys.exit("Неверный пароль.")
    return reader


def cmd_info(args):
    reader = open_reader(args.input)
    print(f"Файл: {args.input}")
    print(f"Размер: {os.path.getsize(args.input) / 1024:.1f} KB")
    print(f"Страниц: {len(reader.pages)}")
    print(f"Зашифрован: {'да' if reader.is_encrypted else 'нет'}")

    meta = reader.metadata
    if meta:
        print("\nМетаданные:")
        for key in ("title", "author", "subject", "creator", "producer", "creation_date"):
            value = getattr(meta, key, None)
            if value:
                print(f"  {key}: {value}")

    # Простая проверка, есть ли извлекаемый текст (отличить текстовый PDF от скана)
    sample = ""
    for page in reader.pages[:3]:
        try:
            sample += page.extract_text() or ""
        except Exception:
            pass
    if sample.strip():
        print(f"\nТекстовый слой: есть (≈{len(sample)} символов на первых страницах)")
    else:
        print("\nТекстовый слой: отсутствует — вероятно, отсканированный PDF")


def cmd_merge(args):
    writer = PdfWriter()
    for path in args.inputs:
        reader = open_reader(path)
        for page in reader.pages:
            writer.add_page(page)
    with open(args.output, "wb") as f:
        writer.write(f)
    print(f"Готово: {args.output} ({len(writer.pages)} страниц)")


def cmd_split(args):
    reader = open_reader(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(args.input).stem
    for i, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        out_path = out_dir / f"{base}_page_{i:03d}.pdf"
        with open(out_path, "wb") as f:
            writer.write(f)
    print(f"Готово: {len(reader.pages)} файлов в {out_dir}/")


def cmd_extract(args):
    reader = open_reader(args.input)
    indices = parse_pages(args.pages, len(reader.pages))
    writer = PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
    with open(args.output, "wb") as f:
        writer.write(f)
    print(f"Готово: извлечено {len(indices)} страниц в {args.output}")


def cmd_delete(args):
    reader = open_reader(args.input)
    to_delete = set(parse_pages(args.pages, len(reader.pages)))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i not in to_delete:
            writer.add_page(page)
    with open(args.output, "wb") as f:
        writer.write(f)
    print(f"Готово: удалено {len(to_delete)} страниц, осталось {len(writer.pages)}")


def cmd_rotate(args):
    if args.angle not in (90, 180, 270):
        sys.exit("Угол должен быть 90, 180 или 270 градусов.")
    reader = open_reader(args.input)
    total = len(reader.pages)
    target_indices = set(parse_pages(args.pages, total)) if args.pages else set(range(total))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i in target_indices:
            page.rotate(args.angle)
        writer.add_page(page)
    with open(args.output, "wb") as f:
        writer.write(f)
    print(f"Готово: повёрнуто {len(target_indices)} страниц на {args.angle}°")


def cmd_text(args):
    reader = open_reader(args.input)
    with open(args.output, "w", encoding="utf-8") as f:
        for i, page in enumerate(reader.pages, start=1):
            f.write(f"=== Страница {i} ===\n")
            text = page.extract_text() or ""
            f.write(text)
            f.write("\n\n")
    print(f"Готово: текст сохранён в {args.output}")
    if not any(p.extract_text() for p in reader.pages):
        print("Внимание: текст не извлёкся. Похоже, это скан — нужен OCR (pytesseract).")


def cmd_watermark(args):
    """Добавляет текстовый водяной знак по диагонали через всю страницу."""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
    except ImportError:
        sys.exit("Нужен reportlab для водяных знаков. Установите: pip install reportlab")

    reader = open_reader(args.input)

    # Создаём страницу с водяным знаком в памяти
    buffer = io.BytesIO()
    # Берём размер первой страницы за основу
    first = reader.pages[0]
    width = float(first.mediabox.width)
    height = float(first.mediabox.height)

    c = canvas.Canvas(buffer, pagesize=(width, height))
    c.setFont("Helvetica-Bold", 60)
    c.setFillGray(0.5, 0.3)  # серый, полупрозрачный
    c.saveState()
    c.translate(width / 2, height / 2)
    c.rotate(45)
    c.drawCentredString(0, 0, args.text)
    c.restoreState()
    c.save()
    buffer.seek(0)

    watermark_page = PdfReader(buffer).pages[0]

    writer = PdfWriter()
    for page in reader.pages:
        page.merge_page(watermark_page)
        writer.add_page(page)

    with open(args.output, "wb") as f:
        writer.write(f)
    print(f"Готово: водяной знак '{args.text}' добавлен в {args.output}")


def cmd_encrypt(args):
    reader = open_reader(args.input)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(args.password)
    with open(args.output, "wb") as f:
        writer.write(f)
    print(f"Готово: файл зашифрован паролем и сохранён в {args.output}")


_PAGE_MARKER_RE = re.compile(r"^=== Страница (\d+) ===$", re.MULTILINE)


def _parse_txt_pages(content: str) -> dict:
    """Разбирает TXT на {номер_страницы: текст} (1-based)."""
    pages = {}
    markers = list(_PAGE_MARKER_RE.finditer(content))
    for idx, m in enumerate(markers):
        page_num = int(m.group(1))
        start = m.end()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(content)
        pages[page_num] = content[start:end].strip()
    return pages


def cmd_edit_open(args):
    """Извлекает текст PDF в TXT с маркерами страниц для последующего round-trip редактирования."""
    try:
        import fitz
    except ImportError:
        sys.exit("Нужен PyMuPDF: pip install pymupdf")

    doc = fitz.open(args.input)
    with open(args.output, "w", encoding="utf-8") as f:
        for i in range(len(doc)):
            f.write(f"=== Страница {i + 1} ===\n")
            f.write(doc[i].get_text("text"))
            f.write("\n")

    print(f"Готово: {args.output} ({len(doc)} страниц)")
    print(f"Отредактируйте файл, затем запустите:")
    print(f"  python pdf_editor.py edit-apply {args.input} {args.output} <результат.pdf>")


def cmd_edit_apply(args):
    """Применяет отредактированный TXT к оригинальному PDF: заменяет изменившийся текст на исходных позициях.

    Изображения, таблицы и прочие элементы остаются нетронутыми.
    Вставка принципиально новых строк (которых не было в оригинале) невозможна —
    такие строки будут перечислены в предупреждениях.
    """
    try:
        import fitz
    except ImportError:
        sys.exit("Нужен PyMuPDF: pip install pymupdf")

    with open(args.txt, "r", encoding="utf-8") as f:
        content = f.read()

    edited_pages = _parse_txt_pages(content)
    if not edited_pages:
        sys.exit("Не найдено ни одного маркера '=== Страница N ===' в файле. Используйте edit-open для экспорта.")

    doc = fitz.open(args.input)
    total_replaced = 0
    total_deleted = 0
    skipped_inserts = 0

    for page_idx in range(len(doc)):
        page_num = page_idx + 1
        if page_num not in edited_pages:
            continue

        page = doc[page_idx]
        orig_text = page.get_text("text").strip()
        new_text = edited_pages[page_num]

        if orig_text == new_text:
            continue

        orig_lines = [l for l in orig_text.splitlines() if l.strip()]
        edit_lines = [l for l in new_text.splitlines() if l.strip()]

        sm = difflib.SequenceMatcher(None, orig_lines, edit_lines, autojunk=False)

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue

            old_block = orig_lines[i1:i2]
            new_block = edit_lines[j1:j2]

            if tag in ("replace", "delete"):
                for k, old_line in enumerate(old_block):
                    replacement = new_block[k].strip() if tag == "replace" and k < len(new_block) else ""
                    for rect in page.search_for(old_line.strip()):
                        page.add_redact_annot(rect, replacement)
                        if replacement:
                            total_replaced += 1
                        else:
                            total_deleted += 1

            elif tag == "insert":
                skipped_inserts += len(new_block)
                for line in new_block:
                    print(f"  ! Стр.{page_num}: невозможно разместить новую строку: '{line[:60]}'")

        page.apply_redactions()

    doc.save(args.output, garbage=4, deflate=True)

    summary = f"заменено: {total_replaced}, удалено: {total_deleted}"
    if skipped_inserts:
        summary += f", не размещено (новые строки): {skipped_inserts}"
    print(f"Готово → {args.output} ({summary})")


def cmd_decrypt(args):
    if not os.path.isfile(args.input):
        sys.exit(f"Файл не найден: {args.input}")
    reader = PdfReader(args.input)
    if reader.is_encrypted and not reader.decrypt(args.password):
        sys.exit("Неверный пароль.")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    with open(args.output, "wb") as f:
        writer.write(f)
    print(f"Готово: пароль снят, файл сохранён в {args.output}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="PDF Editor — консольная утилита для редактирования PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="показать информацию о файле")
    p.add_argument("input")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("merge", help="объединить PDF")
    p.add_argument("output", help="выходной файл")
    p.add_argument("inputs", nargs="+", help="входные PDF в нужном порядке")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("split", help="разбить на отдельные страницы")
    p.add_argument("input")
    p.add_argument("output_dir", help="папка для страниц")
    p.set_defaults(func=cmd_split)

    p = sub.add_parser("extract", help="извлечь страницы")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("pages", nargs="+", help="номера и диапазоны, напр. 1 3 5-8")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("delete", help="удалить страницы")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("pages", nargs="+")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("rotate", help="повернуть страницы")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("angle", type=int, choices=[90, 180, 270])
    p.add_argument("--pages", nargs="*", help="страницы (по умолчанию — все)")
    p.set_defaults(func=cmd_rotate)

    p = sub.add_parser("text", help="извлечь текст в .txt")
    p.add_argument("input")
    p.add_argument("output")
    p.set_defaults(func=cmd_text)

    p = sub.add_parser("watermark", help="добавить водяной знак")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("text", help="текст водяного знака")
    p.set_defaults(func=cmd_watermark)

    p = sub.add_parser("encrypt", help="установить пароль")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("password")
    p.set_defaults(func=cmd_encrypt)

    p = sub.add_parser("decrypt", help="снять пароль")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("password")
    p.set_defaults(func=cmd_decrypt)

    p = sub.add_parser("edit-open", help="извлечь текст в .txt для round-trip редактирования")
    p.add_argument("input", help="исходный PDF")
    p.add_argument("output", help="выходной .txt файл")
    p.set_defaults(func=cmd_edit_open)

    p = sub.add_parser("edit-apply", help="применить отредактированный .txt обратно в PDF")
    p.add_argument("input", help="оригинальный PDF")
    p.add_argument("txt", help="отредактированный .txt файл")
    p.add_argument("output", help="выходной PDF")
    p.set_defaults(func=cmd_edit_apply)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except ValueError as e:
        sys.exit(f"Ошибка: {e}")


if __name__ == "__main__":
    main()
