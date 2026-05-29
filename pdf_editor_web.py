"""Local web interface for the PDF Editor tool — PDF.js based block editor."""

from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from pdf_core import (
    BlockInfo,
    PdfEditorError,
    get_info,
    get_page_blocks,
    replace_block,
)


WORKSPACE = Path(__file__).resolve().parent
STATIC = WORKSPACE / "static"

# session state: original_path (str) -> list of applied ops
# each op: {"page": int, "bbox": [x0,y0,x1,y1], "old": str, "new": str}
_sessions: dict[str, list[dict]] = {}
_sessions_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_pdf(value: str) -> Path:
    if not value:
        raise PdfEditorError("Укажите PDF-файл.")
    path = Path(unquote(value)).expanduser()
    if not path.is_absolute():
        path = WORKSPACE / path
    if not path.is_file():
        raise PdfEditorError(f"PDF не найден: {path}")
    return path


def _edited_path(original: Path) -> Path:
    return original.with_name(f"{original.stem}_edited.pdf")


def _current_pdf(original: Path) -> Path:
    ep = _edited_path(original)
    return ep if ep.is_file() else original


def _rebuild_edited(original: Path, ops: list[dict]) -> None:
    """Apply ops sequentially from original, writing to _edited.pdf."""
    import shutil

    ep = _edited_path(original)
    if not ops:
        if ep.is_file():
            ep.unlink()
        return

    # Start from original for each rebuild
    src = original
    tmp = ep.with_suffix(".tmp.pdf")
    for op in ops:
        replace_block(
            src,
            str(tmp),
            op["page"],
            tuple(op["bbox"]),
            op["old"],
            op["new"],
        )
        if tmp.is_file():
            shutil.move(str(tmp), str(ep))
            src = ep
        # If replace_block returned False (no change), src stays as-is

    # If src is still original (all ops were no-ops), remove any stale _edited
    if src == original and ep.is_file():
        ep.unlink()


def _json_response(data) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _error_json(msg: str, status: int = 400) -> tuple[bytes, int]:
    return _json_response({"error": msg}), status


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------

class PdfEditorHandler(BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "":
            self._serve_static("index.html", "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
            self._serve_static(rel)
        elif path == "/preview":
            self._handle_preview(qs)
        elif path == "/api/blocks":
            self._handle_blocks(qs)
        elif path == "/api/history":
            self._handle_history(qs)
        elif path == "/api/info":
            self._handle_info(qs)
        elif path == "/serve":
            self._handle_serve(qs)
        elif path == "/download":
            self._handle_download(qs)
        elif path == "/pdf.js" or path == "/pdf.worker.js":
            # Redirect to CDN — handled client-side
            self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        if path == "/api/replace":
            self._handle_replace(body)
        elif path == "/api/undo":
            self._handle_undo(body)
        elif path == "/action":
            # Legacy compatibility
            self._handle_legacy_action(parse_qs(body.decode("utf-8")))
        else:
            self.send_error(404)

    # --- static files ---

    def _serve_static(self, rel: str, content_type: str | None = None) -> None:
        file_path = STATIC / rel
        if not file_path.is_file():
            self.send_error(404)
            return
        if content_type is None:
            ext = file_path.suffix.lower()
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".png": "image/png",
                ".ico": "image/x-icon",
            }.get(ext, "application/octet-stream")
        data = file_path.read_bytes()
        self._send(200, content_type, data)

    # --- preview (PNG, legacy + viewer fallback) ---

    def _handle_preview(self, qs: dict) -> None:
        try:
            import fitz
            path_str = qs.get("path", [""])[0]
            page_index = max(int(qs.get("page", ["0"])[0] or "0"), 0)
            zoom = min(max(float(qs.get("zoom", ["1.5"])[0] or "1.5"), 0.5), 4.0)
            original = _resolve_pdf(path_str)
            src = _current_pdf(original)
            doc = fitz.open(stream=src.read_bytes(), filetype="pdf")
            try:
                if doc.needs_pass:
                    raise PdfEditorError("Файл защищён паролем.")
                page_index = min(page_index, len(doc) - 1)
                page = doc[page_index]
                pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                data = pixmap.tobytes("png")
            finally:
                doc.close()
            self._send(200, "image/png", data)
        except Exception as exc:
            self.send_error(500, str(exc))

    # --- /serve: serve raw PDF bytes ---

    def _handle_serve(self, qs: dict) -> None:
        try:
            path_str = qs.get("path", [""])[0]
            original = _resolve_pdf(path_str)
            src = _current_pdf(original)
            data = src.read_bytes()
            self._send(200, "application/pdf", data)
        except PdfEditorError as exc:
            self.send_error(404, str(exc))
        except Exception as exc:
            self.send_error(500, str(exc))

    # --- /download: serve edited PDF as a browser download ---

    def _handle_download(self, qs: dict) -> None:
        try:
            path_str = qs.get("path", [""])[0]
            original = _resolve_pdf(path_str)
            src = _current_pdf(original)
            data = src.read_bytes()
            filename = _edited_path(original).name
            ascii_name = filename.encode("ascii", "replace").decode("ascii")
            disposition = (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", disposition)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except PdfEditorError as exc:
            self.send_error(404, str(exc))
        except Exception as exc:
            self.send_error(500, str(exc))

    # --- /api/blocks ---

    def _handle_blocks(self, qs: dict) -> None:
        try:
            path_str = qs.get("path", [""])[0]
            page_num = int(qs.get("page", ["1"])[0] or "1")
            original = _resolve_pdf(path_str)
            src = _current_pdf(original)
            blocks = get_page_blocks(src, page_num)
            payload = [
                {
                    "index": b.index,
                    "page": b.page,
                    "bbox": list(b.bbox),
                    "text": b.text,
                }
                for b in blocks
            ]
            self._send_json(200, payload)
        except PdfEditorError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    # --- /api/info ---

    def _handle_info(self, qs: dict) -> None:
        try:
            path_str = qs.get("path", [""])[0]
            original = _resolve_pdf(path_str)
            src = _current_pdf(original)
            info = get_info(src)
            self._send_json(200, {
                "path": str(info.path),
                "page_count": info.page_count,
                "size_kb": info.size_kb,
                "encrypted": info.encrypted,
                "has_text_layer": info.has_text_layer,
            })
        except PdfEditorError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    # --- /api/history ---

    def _handle_history(self, qs: dict) -> None:
        path_str = qs.get("path", [""])[0]
        try:
            original = _resolve_pdf(path_str)
        except PdfEditorError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        with _sessions_lock:
            ops = list(_sessions.get(str(original), []))
        self._send_json(200, {"ops": ops})

    # --- /api/replace (POST) ---

    def _handle_replace(self, body: bytes) -> None:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "Некорректный JSON."})
            return

        try:
            path_str = data.get("path", "")
            page_num = int(data["page"])
            bbox = tuple(data["bbox"])
            old_text = data["old"]
            new_text = data["new"]
            original = _resolve_pdf(path_str)
        except (KeyError, TypeError, ValueError) as exc:
            self._send_json(400, {"error": f"Неверные параметры: {exc}"})
            return
        except PdfEditorError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            src = _current_pdf(original)
            ep = _edited_path(original)
            changed = replace_block(src, str(ep), page_num, bbox, old_text, new_text)
            if changed:
                op = {"page": page_num, "bbox": list(bbox), "old": old_text, "new": new_text}
                with _sessions_lock:
                    _sessions.setdefault(str(original), []).append(op)
            self._send_json(200, {"changed": changed, "edited_path": str(ep) if changed else None})
        except PdfEditorError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    # --- /api/undo (POST) ---

    def _handle_undo(self, body: bytes) -> None:
        try:
            data = json.loads(body.decode("utf-8"))
            path_str = data.get("path", "")
            original = _resolve_pdf(path_str)
        except PdfEditorError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception:
            self._send_json(400, {"error": "Некорректный запрос."})
            return

        with _sessions_lock:
            ops = _sessions.get(str(original), [])
            if not ops:
                self._send_json(200, {"undone": False, "ops_remaining": 0})
                return
            ops.pop()
            _sessions[str(original)] = ops
            ops_copy = list(ops)

        try:
            _rebuild_edited(original, ops_copy)
            self._send_json(200, {"undone": True, "ops_remaining": len(ops_copy)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    # --- legacy /action (POST form) ---

    def _handle_legacy_action(self, fields: dict) -> None:
        from pdf_core import add_watermark, extract_text, replace_text, write_edit_text

        def fv(key: str, default: str = "") -> str:
            return fields.get(key, [default])[0].strip()

        pdf_path = fv("pdf_path")
        action = fv("action")
        message_title = "Готово"
        message = ""

        try:
            input_path = _resolve_pdf(pdf_path)
            if action == "info":
                info = get_info(input_path)
                message_title = "Информация"
                message = (
                    f"Файл: {info.path}\n"
                    f"Размер: {info.size_kb:.1f} KB\n"
                    f"Страниц: {info.page_count}\n"
                    f"Текстовый слой: {'есть' if info.has_text_layer else 'нет'}"
                )
            elif action == "replace":
                output = fv("output") or str(input_path.with_name(f"{input_path.stem}_edited.pdf"))
                pages = fv("pages") or None
                max_matches = int(fv("max_matches", "0") or "0")
                result = replace_text(input_path, output, fv("old_text"), fv("new_text"), pages, max_matches)
                if result.matches == 0:
                    message_title = "Замена текста"
                    message = "Текст не найден."
                else:
                    message_title = "Замена текста"
                    message = f"Заменено: {result.matches}. Файл: {result.output}"
            elif action == "text":
                output = fv("txt_output") or str(input_path.with_suffix(".txt"))
                chars = extract_text(input_path, output)
                message_title = "Извлечение текста"
                message = f"Сохранено: {output}\nСимволов: {chars}"
            elif action == "edit_open":
                output = fv("txt_output") or str(input_path.with_name(f"{input_path.stem}_editable.txt"))
                pages = write_edit_text(input_path, output)
                message_title = "TXT для правки"
                message = f"Создано: {output}\nСтраниц: {pages}"
            elif action == "watermark":
                output = fv("output") or str(input_path.with_name(f"{input_path.stem}_watermark.pdf"))
                pages = add_watermark(input_path, output, fv("watermark_text", "DRAFT"))
                message_title = "Водяной знак"
                message = f"Сохранено: {output}\nСтраниц: {pages}"
            else:
                message_title = "Ошибка"
                message = "Неизвестное действие."
        except Exception as exc:
            message_title = "Ошибка"
            message = str(exc)

        # Redirect back to main editor with a message param
        import urllib.parse
        redirect = f"/?msg={urllib.parse.quote(message_title + ': ' + message)}"
        self.send_response(302)
        self.send_header("Location", redirect)
        self.end_headers()

    # --- send helpers ---

    def _send(self, status: int, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, data) -> None:
        payload = _json_response(data)
        self._send(status, "application/json; charset=utf-8", payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    STATIC.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((host, port), PdfEditorHandler)
    url = f"http://{host}:{port}"
    print(f"PDF Editor: {url}")
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
