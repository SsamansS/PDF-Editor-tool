# AGENTS.md — PDF Editor Tool

Инструкция для AI-агентов, работающих с этим проектом.

## Что это за проект

Локальный веб-инструмент для редактирования PDF-файлов. Пользователь открывает браузер, загружает PDF, кликает на текстовый блок, правит текст — и видит результат сразу в PDF.js. Проект принадлежит RunPro (разработчик: ssamanss).

## Стек

| Слой | Технология |
|------|-----------|
| Рендеринг PDF | PDF.js (клиент, `static/pdf.min.js`) |
| Фронтенд | Vanilla JS + HTML/CSS (`static/`) |
| Бэкенд | Python `http.server` — `pdf_editor_web.py` |
| Ядро PDF-операций | PyMuPDF (fitz) + pypdf — `pdf_core.py` |
| Запуск | `python pdf_editor.py gui` → `http://localhost:8765` |

## Архитектура — 3 файла

```
pdf_editor.py       ← точка входа: CLI-разбор аргументов + запуск сервера
pdf_editor_web.py   ← HTTP-сервер: роутинг, сессии, API-эндпоинты
pdf_core.py         ← все операции с PDF: блоки, замена, undo, merge и т.д.
static/             ← фронтенд: index.html, editor.js, editor.css
```

**Правило:** бизнес-логика работы с PDF — только в `pdf_core.py`. Веб-сервер (`pdf_editor_web.py`) только вызывает функции из ядра и возвращает JSON.

## Ключевые API-эндпоинты

| Endpoint | Метод | Что делает |
|----------|-------|-----------|
| `/serve?path=...` | GET | отдаёт PDF-байты для PDF.js |
| `/api/blocks?path=...&page=N` | GET | возвращает блоки страницы с bbox и текстом |
| `/api/replace` | POST | заменяет блок по bbox (`page`, `bbox`, `old`, `new`, `path`) |
| `/api/undo` | POST | откатывает последнее изменение |
| `/api/info?path=...` | GET | мета-информация о PDF |

## Механизм undo

Хранится в памяти сервера (`_sessions` dict, ключ — путь к оригиналу):
- Оригинал (`document.pdf`) никогда не перезаписывается
- Рабочий файл — `document_edited.pdf`, пересобирается при каждом изменении
- Undo = удалить последний элемент из `edit_history` → пересобрать `_edited.pdf` с нуля

## Модели данных (pdf_core.py)

```python
BlockInfo(index, page, bbox, text)       # текстовый блок PDF
PdfInfo(path, size_kb, page_count, ...)  # мета-информация
ReplaceResult(matches, overflow, output) # результат замены
```

## Что уже реализовано

- Просмотр PDF через PDF.js
- Hover-подсветка текстовых блоков (overlay поверх canvas)
- Клик → textarea с текстом блока в правой панели
- Замена блока по bbox (`replace_at_bbox` через `page.search_for(old, clip=bbox)`)
- Undo с полной пересборкой PDF
- История изменений в правой панели
- Базовые CLI-операции: merge, split, rotate, watermark, encrypt, extract-text

## Текущая ветка разработки

Ветка `portable_v` — переработка под портативный режим (без установки, из папки).

## Соглашения

- Язык кода: **английский** (переменные, функции, комментарии)
- Язык общения с пользователем в UI и ошибках: **русский**
- Ошибки пробрасываются как `PdfEditorError` (user-facing) или стандартные исключения
- Никаких глобальных переменных кроме `_sessions` и `_sessions_lock` в веб-слое
- Python 3.10+, venv в папке `venv/`

## Как запустить для проверки

```bash
# из корня проекта, venv активирован
python pdf_editor.py gui
# откроется http://localhost:8765
```

## Чего не делать

- Не трогать `static/pdf.min.js` и `static/pdf.worker.min.js` — это вендорные файлы PDF.js
- Не писать PDF-логику напрямую в `pdf_editor_web.py` — только через `pdf_core.py`
- Не коммитить `.pdf` и `.docx` файлы (они в `.gitignore`)
