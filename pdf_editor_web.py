"""Web interface for the PDF Editor tool — PDF.js based block editor.

Files are uploaded from the browser and stored per-document under ``_uploads/<doc_id>/``.
The client never sends filesystem paths; it works with an opaque ``doc_id`` token.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from pdf_core import (
    BlockInfo,
    PdfEditorError,
    get_info,
    get_page_blocks,
    replace_block,
)


WORKSPACE = Path(__file__).resolve().parent
STATIC = WORKSPACE / "static"
UPLOAD_ROOT = WORKSPACE / "_uploads"

MAX_UPLOAD_BYTES = 50 * 1024 * 1024      # 50 MB hard cap for an uploaded PDF
DOC_TTL_SECONDS = 24 * 60 * 60           # purge uploaded docs older than 24h
_DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Optional .env support (python-dotenv); env vars still work without it.
try:
    from dotenv import load_dotenv
    load_dotenv(WORKSPACE / ".env")
except Exception:
    pass


def _auth_credentials() -> tuple[str, str] | None:
    """Return (user, password) if HTTP auth is configured via env, else None.

    Auth is OFF when credentials are absent (local dev). Set PDF_EDITOR_USER and
    PDF_EDITOR_PASSWORD (e.g. in .env) to require Basic Auth on every request.
    """
    user = os.environ.get("PDF_EDITOR_USER", "").strip()
    password = os.environ.get("PDF_EDITOR_PASSWORD", "")
    if user and password:
        return user, password
    return None

# session state: original_path (str) -> list of applied ops
# each op: {"page": int, "bbox": [x0,y0,x1,y1], "old": str, "new": str}
_sessions: dict[str, list[dict]] = {}
_sessions_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Document storage helpers (doc_id -> sandboxed files)
# ---------------------------------------------------------------------------

def _doc_dir(doc_id: str) -> Path:
    """Return the sandbox directory for a doc_id, validating the token."""
    if not doc_id or not _DOC_ID_RE.match(doc_id):
        raise PdfEditorError("Некорректный идентификатор документа.")
    return UPLOAD_ROOT / doc_id


def _resolve_doc(doc_id: str) -> Path:
    """Return the original PDF path for a doc_id (raises if missing)."""
    original = _doc_dir(doc_id) / "original.pdf"
    if not original.is_file():
        raise PdfEditorError(
            "Документ не найден или истёк срок хранения. Загрузите PDF заново."
        )
    return original


def _doc_display_name(doc_id: str) -> str:
    """Original filename the user uploaded (for download), with a safe default."""
    meta = _doc_dir(doc_id) / "name.txt"
    if meta.is_file():
        try:
            name = meta.read_text(encoding="utf-8").strip()
            if name:
                return name
        except Exception:
            pass
    return "document.pdf"


def _cleanup_old_docs() -> None:
    """Remove upload dirs older than DOC_TTL_SECONDS. Best-effort, never raises."""
    if not UPLOAD_ROOT.is_dir():
        return
    cutoff = time.time() - DOC_TTL_SECONDS
    for child in UPLOAD_ROOT.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except Exception:
            pass


def _parse_multipart(body: bytes, content_type: str) -> dict:
    """Minimal multipart/form-data parser.

    Returns ``{field_name: {"filename": str|None, "data": bytes}}``.
    Sufficient for our single-file upload; not a general-purpose implementation.
    """
    if "boundary=" not in content_type:
        raise PdfEditorError("Неверный формат загрузки (нет boundary).")
    boundary = content_type.split("boundary=", 1)[1].strip()
    if boundary.startswith('"') and boundary.endswith('"'):
        boundary = boundary[1:-1]
    delim = b"--" + boundary.encode("latin-1")

    result: dict = {}
    for part in body.split(delim):
        if not part or part in (b"--", b"--\r\n"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        header_blob, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        name = None
        filename = None
        for line in header_blob.decode("latin-1", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition"):
                for token in line.split(";"):
                    token = token.strip()
                    if token.startswith("name="):
                        name = token[len("name="):].strip('"')
                    elif token.startswith("filename="):
                        filename = token[len("filename="):].strip('"')
        if name is not None:
            result[name] = {"filename": filename, "data": data}
    return result


# ---------------------------------------------------------------------------
# Edited-file helpers (operate inside the per-doc sandbox)
# ---------------------------------------------------------------------------

def _edited_path(original: Path) -> Path:
    return original.with_name(f"{original.stem}_edited.pdf")


def _current_pdf(original: Path) -> Path:
    ep = _edited_path(original)
    return ep if ep.is_file() else original


def _rebuild_edited(original: Path, ops: list[dict]) -> None:
    """Apply ops sequentially from original, writing to _edited.pdf."""
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

    # --- authentication (HTTP Basic, optional) ---

    def _authorized(self) -> bool:
        creds = _auth_credentials()
        if creds is None:
            return True  # auth disabled (no credentials configured)
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[len("Basic "):]).decode("utf-8")
        except Exception:
            return False
        user, _, password = decoded.partition(":")
        exp_user, exp_pw = creds
        # constant-time comparison to avoid timing leaks
        return (hmac.compare_digest(user, exp_user)
                and hmac.compare_digest(password, exp_pw))

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="PDF Editor"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self) -> None:
        if not self._require_auth():
            return
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
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        path = parsed.path

        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_UPLOAD_BYTES + 1024 * 1024:
            self._send_json(413, {"error": "Запрос слишком большой."})
            return
        body = self.rfile.read(length)

        if path == "/api/upload":
            self._handle_upload(body, self.headers.get("Content-Type", ""))
        elif path == "/api/replace":
            self._handle_replace(body)
        elif path == "/api/undo":
            self._handle_undo(body)
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

    # --- /api/upload: receive a PDF from the browser ---

    def _handle_upload(self, body: bytes, content_type: str) -> None:
        try:
            parts = _parse_multipart(body, content_type)
        except PdfEditorError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        file_part = parts.get("file")
        if not file_part or not file_part.get("data"):
            self._send_json(400, {"error": "Файл не получен."})
            return

        data = file_part["data"]
        if len(data) > MAX_UPLOAD_BYTES:
            self._send_json(400, {"error": "Файл слишком большой (макс. 50 МБ)."})
            return
        if not data.startswith(b"%PDF-"):
            self._send_json(400, {"error": "Это не PDF-файл."})
            return

        doc_id = uuid.uuid4().hex
        doc_dir = UPLOAD_ROOT / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        original = doc_dir / "original.pdf"
        original.write_bytes(data)

        raw_name = file_part.get("filename") or "document.pdf"
        safe_name = Path(raw_name).name or "document.pdf"
        (doc_dir / "name.txt").write_text(safe_name, encoding="utf-8")

        try:
            info = get_info(original)
        except Exception as exc:
            shutil.rmtree(doc_dir, ignore_errors=True)
            self._send_json(400, {"error": f"Не удалось открыть PDF: {exc}"})
            return

        _cleanup_old_docs()
        self._send_json(200, {
            "doc_id": doc_id,
            "name": safe_name,
            "page_count": info.page_count,
            "size_kb": info.size_kb,
            "encrypted": info.encrypted,
            "has_text_layer": info.has_text_layer,
        })

    # --- preview (PNG, viewer fallback) ---

    def _handle_preview(self, qs: dict) -> None:
        try:
            import fitz
            doc_id = qs.get("doc", [""])[0]
            page_index = max(int(qs.get("page", ["0"])[0] or "0"), 0)
            zoom = min(max(float(qs.get("zoom", ["1.5"])[0] or "1.5"), 0.5), 4.0)
            original = _resolve_doc(doc_id)
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
        except PdfEditorError as exc:
            self.send_error(404, "Not Found", str(exc))
        except Exception as exc:
            self.send_error(500, "Server Error", str(exc))

    # --- /serve: serve raw PDF bytes for PDF.js ---

    def _handle_serve(self, qs: dict) -> None:
        try:
            doc_id = qs.get("doc", [""])[0]
            original = _resolve_doc(doc_id)
            src = _current_pdf(original)
            data = src.read_bytes()
            self._send(200, "application/pdf", data)
        except PdfEditorError as exc:
            self.send_error(404, "Not Found", str(exc))
        except Exception as exc:
            self.send_error(500, "Server Error", str(exc))

    # --- /download: serve edited PDF as a browser download ---

    def _handle_download(self, qs: dict) -> None:
        try:
            doc_id = qs.get("doc", [""])[0]
            original = _resolve_doc(doc_id)
            src = _current_pdf(original)
            data = src.read_bytes()
            display = _doc_display_name(doc_id)
            filename = f"{Path(display).stem}_edited.pdf"
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
            self.send_error(404, "Not Found", str(exc))
        except Exception as exc:
            self.send_error(500, "Server Error", str(exc))

    # --- /api/blocks ---

    def _handle_blocks(self, qs: dict) -> None:
        try:
            doc_id = qs.get("doc", [""])[0]
            page_num = int(qs.get("page", ["1"])[0] or "1")
            original = _resolve_doc(doc_id)
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
            doc_id = qs.get("doc", [""])[0]
            original = _resolve_doc(doc_id)
            src = _current_pdf(original)
            info = get_info(src)
            self._send_json(200, {
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
        doc_id = qs.get("doc", [""])[0]
        try:
            original = _resolve_doc(doc_id)
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
            doc_id = data.get("doc", "")
            page_num = int(data["page"])
            bbox = tuple(data["bbox"])
            old_text = data["old"]
            new_text = data["new"]
            original = _resolve_doc(doc_id)
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
            self._send_json(200, {"changed": changed})
        except PdfEditorError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    # --- /api/undo (POST) ---

    def _handle_undo(self, body: bytes) -> None:
        try:
            data = json.loads(body.decode("utf-8"))
            doc_id = data.get("doc", "")
            original = _resolve_doc(doc_id)
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
    UPLOAD_ROOT.mkdir(exist_ok=True)
    _cleanup_old_docs()
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
