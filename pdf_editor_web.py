"""Dependency-light local web interface for the PDF Editor tool."""

from __future__ import annotations

import html
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from pdf_core import PdfEditorError, add_watermark, extract_text, get_info, replace_text, write_edit_text


WORKSPACE = Path(__file__).resolve().parent


def _pdf_choices() -> list[Path]:
    return sorted(WORKSPACE.glob("*.pdf"))


def _default_output(input_path: Path, suffix: str) -> str:
    return str(input_path.with_name(f"{input_path.stem}_{suffix}.pdf"))


def _resolve_pdf(value: str) -> Path:
    if not value:
        raise PdfEditorError("Укажите PDF-файл.")
    path = Path(unquote(value)).expanduser()
    if not path.is_absolute():
        path = WORKSPACE / path
    if not path.is_file():
        raise PdfEditorError(f"PDF не найден: {path}")
    return path


def _render_page(path_value: str, page_value: str, zoom_value: str) -> tuple[bytes, str]:
    import fitz

    path = _resolve_pdf(path_value)
    page_index = max(int(page_value or "0"), 0)
    zoom = min(max(float(zoom_value or "1.2"), 0.5), 3.0)
    doc = fitz.open(str(path))
    try:
        if doc.needs_pass:
            raise PdfEditorError("Предпросмотр защищённых паролем PDF пока не поддерживается.")
        page_index = min(page_index, len(doc) - 1)
        page = doc[page_index]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png"), "image/png"
    finally:
        doc.close()


def _form_value(fields: dict[str, list[str]], key: str, default: str = "") -> str:
    return fields.get(key, [default])[0].strip()


def _handle_action(fields: dict[str, list[str]]) -> tuple[str, str]:
    action = _form_value(fields, "action")
    input_path = _resolve_pdf(_form_value(fields, "pdf_path"))

    if action == "info":
        info = get_info(input_path)
        text_layer = "есть" if info.has_text_layer else "нет"
        details = [
            f"Файл: {info.path}",
            f"Размер: {info.size_kb:.1f} KB",
            f"Страниц: {info.page_count}",
            f"Текстовый слой: {text_layer}",
        ]
        return "Информация", "\n".join(details)

    if action == "replace":
        output = _form_value(fields, "output") or _default_output(input_path, "edited")
        pages = _form_value(fields, "pages") or None
        max_matches = int(_form_value(fields, "max_matches", "0") or "0")
        result = replace_text(
            input_path,
            output,
            _form_value(fields, "old_text"),
            _form_value(fields, "new_text"),
            pages,
            max_matches,
        )
        if result.matches == 0:
            return "Замена текста", "Текст не найден. Возможно, PDF является сканом."
        note = f"Заменено: {result.matches}. Файл: {result.output}"
        if result.overflow:
            note += f"\nНе поместилось в исходную область: {result.overflow}"
        return "Замена текста", note

    if action == "text":
        output = _form_value(fields, "txt_output") or str(input_path.with_suffix(".txt"))
        chars = extract_text(input_path, output)
        return "Извлечение текста", f"Сохранено: {output}\nСимволов: {chars}"

    if action == "edit_open":
        output = _form_value(fields, "txt_output") or str(input_path.with_name(f"{input_path.stem}_editable.txt"))
        pages = write_edit_text(input_path, output)
        return "TXT для правки", f"Создано: {output}\nСтраниц: {pages}"

    if action == "watermark":
        output = _form_value(fields, "output") or _default_output(input_path, "watermark")
        pages = add_watermark(input_path, output, _form_value(fields, "watermark_text", "DRAFT"))
        return "Водяной знак", f"Сохранено: {output}\nСтраниц: {pages}"

    raise PdfEditorError("Неизвестное действие.")


def _page_html(pdf_path: str = "", message_title: str = "", message: str = "", page: int = 0, zoom: float = 1.2) -> bytes:
    pdfs = _pdf_choices()
    selected = _resolve_selected(pdf_path, pdfs)
    preview_src = ""
    page_count = 0
    if selected:
        try:
            info = get_info(selected)
            page_count = info.page_count
            preview_src = f"/preview?path={quote(str(selected))}&page={page}&zoom={zoom}"
        except Exception as exc:
            message_title = "Предпросмотр"
            message = str(exc)

    options = "\n".join(
        f'<option value="{html.escape(str(path))}" {"selected" if selected == path else ""}>{html.escape(path.name)}</option>'
        for path in pdfs
    )
    selected_text = html.escape(str(selected or pdf_path or ""))
    message_block = ""
    if message:
        message_block = f"""
        <section class="notice">
          <strong>{html.escape(message_title)}</strong>
          <pre>{html.escape(message)}</pre>
        </section>
        """

    next_page = min(page + 1, max(page_count - 1, 0))
    prev_page = max(page - 1, 0)
    body = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PDF Editor</title>
  <style>
    body {{ margin: 0; font-family: Segoe UI, Arial, sans-serif; background: #f5f5f5; color: #202020; }}
    header {{ display: flex; gap: 10px; align-items: center; padding: 12px 16px; background: #ffffff; border-bottom: 1px solid #ddd; }}
    h1 {{ font-size: 18px; margin: 0 14px 0 0; }}
    main {{ display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 14px; padding: 14px; height: calc(100vh - 58px); box-sizing: border-box; }}
    .viewer {{ overflow: auto; display: grid; place-items: start center; background: #e9e9e9; border: 1px solid #d0d0d0; }}
    .viewer img {{ margin: 16px; background: white; box-shadow: 0 1px 10px rgba(0,0,0,.16); }}
    .panel {{ overflow: auto; display: flex; flex-direction: column; gap: 12px; }}
    section {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px; }}
    h2 {{ font-size: 15px; margin: 0 0 10px; }}
    label {{ display: block; font-size: 12px; color: #555; margin: 8px 0 4px; }}
    input, select, button {{ font: inherit; box-sizing: border-box; }}
    input, select {{ width: 100%; padding: 8px; border: 1px solid #c8c8c8; border-radius: 6px; background: white; }}
    button {{ padding: 8px 10px; border: 1px solid #b8b8b8; border-radius: 6px; background: #fff; cursor: pointer; }}
    button.primary {{ background: #1b5e9e; color: #fff; border-color: #1b5e9e; width: 100%; margin-top: 10px; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .notice pre {{ white-space: pre-wrap; margin: 8px 0 0; }}
    @media (max-width: 900px) {{ main {{ grid-template-columns: 1fr; height: auto; }} .viewer {{ min-height: 70vh; }} }}
  </style>
</head>
<body>
  <header>
    <h1>PDF Editor</h1>
    <form method="get" action="/" style="display:flex; gap:8px; align-items:center; flex:1;">
      <select name="path">{options}</select>
      <input name="custom_path" value="{selected_text}" placeholder="Или полный путь к PDF">
      <input type="hidden" name="page" value="{page}">
      <input type="hidden" name="zoom" value="{zoom}">
      <button>Открыть</button>
    </form>
  </header>
  <main>
    <div class="viewer">
      {f'<img src="{preview_src}" alt="Предпросмотр страницы">' if preview_src else '<p>Выберите PDF из списка или укажите путь.</p>'}
    </div>
    <aside class="panel">
      {message_block}
      <section>
        <h2>Навигация</h2>
        <div class="row">
          <a href="/?path={quote(str(selected or ''))}&page={prev_page}&zoom={zoom}"><button type="button">Назад</button></a>
          <a href="/?path={quote(str(selected or ''))}&page={next_page}&zoom={zoom}"><button type="button">Вперёд</button></a>
        </div>
        <div class="row" style="margin-top:8px;">
          <a href="/?path={quote(str(selected or ''))}&page={page}&zoom={max(0.5, zoom - 0.2):.1f}"><button type="button">-</button></a>
          <a href="/?path={quote(str(selected or ''))}&page={page}&zoom={min(3.0, zoom + 0.2):.1f}"><button type="button">+</button></a>
        </div>
        <p>Страница {page + 1 if selected else 0} из {page_count}</p>
      </section>
      <section>
        <h2>Замена текста</h2>
        <form method="post" action="/action">
          <input type="hidden" name="action" value="replace">
          <input type="hidden" name="pdf_path" value="{selected_text}">
          <label>Найти</label><input name="old_text">
          <label>Заменить</label><input name="new_text">
          <label>Страницы, например 1 3 5-8</label><input name="pages">
          <label>Максимум замен, 0 без ограничения</label><input name="max_matches" value="0">
          <label>Выходной PDF</label><input name="output" value="{html.escape(_default_output(selected, 'edited') if selected else '')}">
          <button class="primary">Заменить и сохранить</button>
        </form>
      </section>
      <section>
        <h2>Другие действия</h2>
        <form method="post" action="/action">
          <input type="hidden" name="pdf_path" value="{selected_text}">
          <label>TXT файл</label><input name="txt_output" value="{html.escape(str(selected.with_suffix('.txt')) if selected else '')}">
          <div class="row" style="margin-top:8px;">
            <button name="action" value="text">Извлечь текст</button>
            <button name="action" value="edit_open">TXT для правки</button>
          </div>
        </form>
        <form method="post" action="/action" style="margin-top:10px;">
          <input type="hidden" name="action" value="watermark">
          <input type="hidden" name="pdf_path" value="{selected_text}">
          <label>Текст водяного знака</label><input name="watermark_text" value="DRAFT">
          <label>Выходной PDF</label><input name="output" value="{html.escape(_default_output(selected, 'watermark') if selected else '')}">
          <button class="primary">Добавить водяной знак</button>
        </form>
        <form method="post" action="/action" style="margin-top:10px;">
          <input type="hidden" name="action" value="info">
          <input type="hidden" name="pdf_path" value="{selected_text}">
          <button>Показать информацию</button>
        </form>
      </section>
    </aside>
  </main>
</body>
</html>"""
    return body.encode("utf-8")


def _resolve_selected(path_value: str, pdfs: list[Path]) -> Path | None:
    if path_value:
        try:
            return _resolve_pdf(path_value)
        except PdfEditorError:
            return None
    return pdfs[0] if pdfs else None


class PdfEditorHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        fields = parse_qs(parsed.query)
        if parsed.path == "/preview":
            self._send_preview(fields)
            return

        path = fields.get("custom_path", fields.get("path", [""]))[0]
        page = int(fields.get("page", ["0"])[0] or "0")
        zoom = float(fields.get("zoom", ["1.2"])[0] or "1.2")
        self._send_html(_page_html(path, page=page, zoom=zoom))

    def do_POST(self) -> None:
        if self.path != "/action":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        fields = parse_qs(self.rfile.read(length).decode("utf-8"))
        pdf_path = _form_value(fields, "pdf_path")
        try:
            title, message = _handle_action(fields)
        except Exception as exc:
            title, message = "Ошибка", str(exc)
        self._send_html(_page_html(pdf_path, title, message))

    def _send_preview(self, fields: dict[str, list[str]]) -> None:
        try:
            image, content_type = _render_page(
                fields.get("path", [""])[0],
                fields.get("page", ["0"])[0],
                fields.get("zoom", ["1.2"])[0],
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
        except Exception as exc:
            self.send_error(500, str(exc))

    def _send_html(self, payload: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return


def main(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), PdfEditorHandler)
    url = f"http://{host}:{port}"
    print(f"PDF Editor interface: {url}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()


if __name__ == "__main__":
    host_arg = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port_arg = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    main(host_arg, port_arg)
