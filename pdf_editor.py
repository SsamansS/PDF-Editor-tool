#!/usr/bin/env python3
"""CLI entry point for the PDF Editor tool.

Examples:
  python pdf_editor.py gui
  python pdf_editor.py info document.pdf
  python pdf_editor.py replace document.pdf out.pdf "old text" "new text"
  python pdf_editor.py edit-open document.pdf document.txt
  python pdf_editor.py edit-apply document.pdf document.txt result.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdf_core import (
    PdfEditorError,
    add_watermark,
    apply_edit_text,
    decrypt_pdf,
    delete_pages,
    encrypt_pdf,
    extract_pages,
    extract_text,
    get_info,
    merge_pdfs,
    replace_text,
    rotate_pages,
    split_pdf,
    write_edit_text,
)


def print_info(args: argparse.Namespace) -> None:
    info = get_info(args.input)
    print(f"Файл: {info.path}")
    print(f"Размер: {info.size_kb:.1f} KB")
    print(f"Страниц: {info.page_count}")
    print(f"Зашифрован: {'да' if info.encrypted else 'нет'}")

    if info.metadata:
        print("\nМетаданные:")
        for key, value in info.metadata.items():
            print(f"  {key}: {value}")

    if info.has_text_layer:
        print(f"\nТекстовый слой: есть (примерно {info.text_sample_chars} символов на первых страницах)")
    else:
        print("\nТекстовый слой: отсутствует — вероятно, это скан")


def run_merge(args: argparse.Namespace) -> None:
    pages = merge_pdfs(args.output, args.inputs)
    print(f"Готово: {args.output} ({pages} страниц)")


def run_split(args: argparse.Namespace) -> None:
    pages = split_pdf(args.input, args.output_dir)
    print(f"Готово: {pages} файлов в {args.output_dir}")


def run_extract(args: argparse.Namespace) -> None:
    pages = extract_pages(args.input, args.output, args.pages)
    print(f"Готово: извлечено {pages} страниц в {args.output}")


def run_delete(args: argparse.Namespace) -> None:
    pages = delete_pages(args.input, args.output, args.pages)
    print(f"Готово: удалено {pages} страниц, файл сохранён: {args.output}")


def run_rotate(args: argparse.Namespace) -> None:
    pages = rotate_pages(args.input, args.output, args.angle, args.pages)
    print(f"Готово: повёрнуто {pages} страниц на {args.angle}°, файл сохранён: {args.output}")


def run_text(args: argparse.Namespace) -> None:
    chars = extract_text(args.input, args.output)
    print(f"Готово: текст сохранён в {args.output} ({chars} символов)")
    if chars == 0:
        print("Внимание: текст не извлёкся. Похоже, это скан — нужен OCR.")


def run_watermark(args: argparse.Namespace) -> None:
    pages = add_watermark(args.input, args.output, args.text)
    print(f"Готово: водяной знак добавлен на {pages} страниц, файл сохранён: {args.output}")


def run_encrypt(args: argparse.Namespace) -> None:
    pages = encrypt_pdf(args.input, args.output, args.password)
    print(f"Готово: зашифровано {pages} страниц, файл сохранён: {args.output}")


def run_decrypt(args: argparse.Namespace) -> None:
    pages = decrypt_pdf(args.input, args.output, args.password)
    print(f"Готово: пароль снят с {pages} страниц, файл сохранён: {args.output}")


def run_replace(args: argparse.Namespace) -> None:
    result = replace_text(args.input, args.output, args.old, args.new, args.pages, args.max)
    if result.matches == 0:
        print("Текст не найден. PDF мог быть сканом или текст мог быть разбит на отдельные глифы.")
        return

    summary = f"Готово: заменено {result.matches} фрагментов"
    if result.overflow:
        summary += f", не поместилось: {result.overflow}"
    print(f"{summary}. Файл сохранён: {result.output}")


def run_edit_open(args: argparse.Namespace) -> None:
    pages = write_edit_text(args.input, args.output)
    print(f"Готово: {args.output} ({pages} страниц)")
    print("Редактируйте текст внутри блоков [[PDFTXT ...]], сами маркеры не меняйте.")
    print(f"Затем запустите: python pdf_editor.py edit-apply {args.input} {args.output} <результат.pdf>")


def run_edit_apply(args: argparse.Namespace) -> None:
    result = apply_edit_text(args.input, args.txt, args.output)
    summary = f"заменено: {result.replaced}, удалено: {result.deleted}"
    if result.not_placed:
        summary += f", не размещено/не поместилось: {result.not_placed}"
    print(f"Готово: {result.output} ({summary})")


def run_gui(args: argparse.Namespace) -> None:
    from pdf_editor_web import main as web_main

    web_main(args.host, args.port, not args.no_browser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF Editor — CLI и простой графический интерфейс")
    sub = parser.add_subparsers(dest="command")

    command = sub.add_parser("gui", help="открыть графический интерфейс")
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8765)
    command.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    command.set_defaults(func=run_gui)

    command = sub.add_parser("web", help="открыть web-интерфейс")
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8765)
    command.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    command.set_defaults(func=run_gui)

    command = sub.add_parser("info", help="показать информацию о PDF")
    command.add_argument("input")
    command.set_defaults(func=print_info)

    command = sub.add_parser("merge", help="объединить PDF")
    command.add_argument("output")
    command.add_argument("inputs", nargs="+")
    command.set_defaults(func=run_merge)

    command = sub.add_parser("split", help="разбить PDF на страницы")
    command.add_argument("input")
    command.add_argument("output_dir")
    command.set_defaults(func=run_split)

    command = sub.add_parser("extract", help="извлечь страницы")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("pages", nargs="+", help="номера и диапазоны, например: 1 3 5-8")
    command.set_defaults(func=run_extract)

    command = sub.add_parser("delete", help="удалить страницы")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("pages", nargs="+")
    command.set_defaults(func=run_delete)

    command = sub.add_parser("rotate", help="повернуть страницы")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("angle", type=int, choices=[90, 180, 270])
    command.add_argument("--pages", nargs="*", help="по умолчанию — все страницы")
    command.set_defaults(func=run_rotate)

    command = sub.add_parser("text", help="извлечь текст в .txt")
    command.add_argument("input")
    command.add_argument("output")
    command.set_defaults(func=run_text)

    command = sub.add_parser("watermark", help="добавить водяной знак")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("text")
    command.set_defaults(func=run_watermark)

    command = sub.add_parser("encrypt", help="установить пароль")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("password")
    command.set_defaults(func=run_encrypt)

    command = sub.add_parser("decrypt", help="снять пароль")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("password")
    command.set_defaults(func=run_decrypt)

    command = sub.add_parser("replace", help="заменить найденный текст прямо в PDF")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("old")
    command.add_argument("new")
    command.add_argument("--pages", nargs="*", help="страницы, например: 1 3 5-8")
    command.add_argument("--max", type=int, default=0, help="максимум замен; 0 — без ограничения")
    command.set_defaults(func=run_replace)

    command = sub.add_parser("edit-open", help="извлечь текст в .txt для round-trip редактирования")
    command.add_argument("input")
    command.add_argument("output")
    command.set_defaults(func=run_edit_open)

    command = sub.add_parser("edit-apply", help="применить отредактированный .txt обратно в PDF")
    command.add_argument("input")
    command.add_argument("txt")
    command.add_argument("output")
    command.set_defaults(func=run_edit_apply)

    return parser


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        args = parser.parse_args(["gui"])

    try:
        args.func(args)
    except (PdfEditorError, ValueError, OSError) as exc:
        raise SystemExit(f"Ошибка: {exc}") from exc


if __name__ == "__main__":
    main()
