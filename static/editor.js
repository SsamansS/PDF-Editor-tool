'use strict';

// PDF.js served locally
pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/pdf.worker.min.js';

// ---- State ----
const state = {
  originalPath: '',
  pageCount: 0,
  currentPage: 1,
  zoom: 1.5,
  pdfDoc: null,
  blocks: [],
  selectedBlock: null,
  rendering: false,
};

// ---- DOM refs ----
const pdfCanvas       = document.getElementById('pdfCanvas');
const ctx             = pdfCanvas.getContext('2d');
const blockOverlay    = document.getElementById('blockOverlay');
const pageLabel       = document.getElementById('pageLabel');
const pdfPathInput    = document.getElementById('pdfPathInput');
const statusMsg       = document.getElementById('statusMsg');
const editHint        = document.getElementById('editHint');
const editForm        = document.getElementById('editForm');
const editOld         = document.getElementById('editOld');
const editNew         = document.getElementById('editNew');
const historySection  = document.getElementById('historySection');
const historyList     = document.getElementById('historyList');
const placeholder     = document.getElementById('placeholder');
const viewerContainer = document.getElementById('viewerContainer');
const filePicker      = document.getElementById('filePicker');

// ---- Loading overlay ----
const loadingOverlay = document.createElement('div');
loadingOverlay.id = 'loadingOverlay';
loadingOverlay.innerHTML = '<div class="spinner"></div>';
document.body.appendChild(loadingOverlay);

function setLoading(on) {
  loadingOverlay.classList.toggle('active', on);
}

// ---- Status messages ----
let statusTimer = null;
function setStatus(msg, isError = false) {
  statusMsg.textContent = msg;
  statusMsg.style.color = isError ? '#c0392b' : '#555';
  if (statusTimer) clearTimeout(statusTimer);
  if (msg) statusTimer = setTimeout(() => { statusMsg.textContent = ''; }, 5000);
}

// ---- File picker ----
filePicker.addEventListener('change', () => {
  const file = filePicker.files[0];
  if (!file) return;
  // Build a server-relative path: user picked a local file, put its name so the
  // server resolves it from WORKSPACE, or use the full path if available via webkitRelativePath.
  // The File API doesn't expose full paths for security reasons, so we rely on the
  // name typed in the text field if present, otherwise just use file.name and let
  // the server resolve from its working directory.
  if (file.path) {
    // Electron / non-sandboxed env (unlikely in browser, but some Chromium builds expose this)
    pdfPathInput.value = file.path;
  } else {
    pdfPathInput.value = file.name;
  }
  openPdf();
});

// ---- Open PDF ----
document.getElementById('openBtn').addEventListener('click', openPdf);
pdfPathInput.addEventListener('keydown', e => { if (e.key === 'Enter') openPdf(); });

async function openPdf() {
  const path = pdfPathInput.value.trim();
  if (!path) { setStatus('Укажите путь к PDF.', true); return; }

  state.originalPath = path;
  state.currentPage = 1;
  setLoading(true);
  clearSelection();

  try {
    const info = await apiFetch(`/api/info?path=${encodeURIComponent(path)}`);
    if (info.error) throw new Error(info.error);

    state.pageCount = info.page_count;
    pageLabel.textContent = `1 / ${state.pageCount}`;

    // Load PDF via PDF.js from /serve endpoint
    const url = `/serve?path=${encodeURIComponent(path)}&_t=${Date.now()}`;
    state.pdfDoc = await pdfjsLib.getDocument(url).promise;

    placeholder.style.display = 'none';
    viewerContainer.style.display = 'inline-block';

    await renderPage();
    await loadBlocks();
    await refreshHistory();
  } catch (err) {
    setStatus('Ошибка: ' + err.message, true);
    console.error(err);
  } finally {
    setLoading(false);
  }
}

// ---- Page navigation ----
document.getElementById('prevPage').addEventListener('click', async () => {
  if (state.currentPage > 1) { state.currentPage--; await changePage(); }
});
document.getElementById('nextPage').addEventListener('click', async () => {
  if (state.currentPage < state.pageCount) { state.currentPage++; await changePage(); }
});
document.getElementById('zoomOut').addEventListener('click', async () => {
  if (!state.pdfDoc) return;
  state.zoom = Math.max(0.5, +(state.zoom - 0.25).toFixed(2));
  await changePage();
});
document.getElementById('zoomIn').addEventListener('click', async () => {
  if (!state.pdfDoc) return;
  state.zoom = Math.min(4.0, +(state.zoom + 0.25).toFixed(2));
  await changePage();
});

async function changePage() {
  clearSelection();
  setLoading(true);
  try {
    await renderPage();
    await loadBlocks();
  } catch (err) {
    setStatus('Ошибка: ' + err.message, true);
  } finally {
    setLoading(false);
  }
}

// ---- Render page via PDF.js ----
async function renderPage() {
  if (!state.pdfDoc || state.rendering) return;
  state.rendering = true;
  try {
    const page = await state.pdfDoc.getPage(state.currentPage);
    const viewport = page.getViewport({ scale: state.zoom });
    pdfCanvas.width = viewport.width;
    pdfCanvas.height = viewport.height;
    blockOverlay.style.width  = viewport.width + 'px';
    blockOverlay.style.height = viewport.height + 'px';
    await page.render({ canvasContext: ctx, viewport }).promise;
    pageLabel.textContent = `${state.currentPage} / ${state.pageCount}`;
  } finally {
    state.rendering = false;
  }
}

// ---- Reload PDF.js document after edit/undo ----
async function reloadPdfDoc() {
  const url = `/serve?path=${encodeURIComponent(state.originalPath)}&_t=${Date.now()}`;
  state.pdfDoc = await pdfjsLib.getDocument(url).promise;
}

// ---- Load blocks from server ----
async function loadBlocks() {
  if (!state.originalPath) return;
  const data = await apiFetch(
    `/api/blocks?path=${encodeURIComponent(state.originalPath)}&page=${state.currentPage}`
  );
  if (data.error) { setStatus(data.error, true); return; }
  state.blocks = Array.isArray(data) ? data : [];
  drawOverlay();
}

function drawOverlay() {
  blockOverlay.innerHTML = '';
  const scale = state.zoom;
  for (const b of state.blocks) {
    const [x0, y0, x1, y1] = b.bbox;
    const div = document.createElement('div');
    div.className = 'block-rect';
    div.style.left   = (x0 * scale) + 'px';
    div.style.top    = (y0 * scale) + 'px';
    div.style.width  = ((x1 - x0) * scale) + 'px';
    div.style.height = ((y1 - y0) * scale) + 'px';
    div.dataset.idx  = b.index;
    div.title = b.text.substring(0, 80).replace(/\n/g, ' ');
    div.addEventListener('click', () => selectBlock(b));
    blockOverlay.appendChild(div);
  }
}

// ---- Block selection ----
function selectBlock(block) {
  clearSelection(false);
  state.selectedBlock = block;

  const el = blockOverlay.querySelector(`[data-idx="${block.index}"]`);
  if (el) el.classList.add('selected');

  editHint.style.display = 'none';
  editForm.style.display = 'flex';
  editOld.value = block.text;
  editNew.value = block.text;
  editNew.focus();
  editNew.setSelectionRange(0, editNew.value.length);
}

function clearSelection(showHint = true) {
  state.selectedBlock = null;
  blockOverlay.querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
  if (showHint) {
    editHint.style.display = 'block';
    editForm.style.display = 'none';
  }
}

document.getElementById('cancelBtn').addEventListener('click', () => clearSelection(true));

// ---- Apply edit ----
document.getElementById('applyBtn').addEventListener('click', applyEdit);
editNew.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) applyEdit();
});

async function applyEdit() {
  const block = state.selectedBlock;
  if (!block) return;
  const oldText = editOld.value;
  const newText = editNew.value;
  if (oldText === newText) { clearSelection(true); return; }

  setLoading(true);
  try {
    const res = await apiFetch('/api/replace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: state.originalPath,
        page: state.currentPage,
        bbox: block.bbox,
        old: oldText,
        new: newText,
      }),
    });
    if (res.error) { setStatus('Ошибка: ' + res.error, true); return; }

    if (res.changed) {
      setStatus('Сохранено.');
      await reloadPdfDoc();
      await renderPage();
      await loadBlocks();
      await refreshHistory();
    } else {
      setStatus('Изменений не обнаружено.');
    }
    clearSelection(true);
  } catch (err) {
    setStatus('Ошибка: ' + err.message, true);
  } finally {
    setLoading(false);
  }
}

// ---- Undo ----
document.getElementById('undoBtn').addEventListener('click', async () => {
  if (!state.originalPath) return;
  setLoading(true);
  try {
    const res = await apiFetch('/api/undo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: state.originalPath }),
    });
    if (res.error) { setStatus('Ошибка: ' + res.error, true); return; }
    if (res.undone) {
      setStatus('Отменено.');
      document.getElementById('undoBtn').disabled = (res.ops_remaining === 0);
      await reloadPdfDoc();
      await renderPage();
      await loadBlocks();
      await refreshHistory();
    } else {
      setStatus('Нечего отменять.');
    }
  } catch (err) {
    setStatus('Ошибка: ' + err.message, true);
  } finally {
    setLoading(false);
  }
});

// ---- History panel ----
async function refreshHistory() {
  if (!state.originalPath) return;
  const data = await apiFetch(
    `/api/history?path=${encodeURIComponent(state.originalPath)}`
  );
  if (data.error || !data.ops) return;
  const ops = data.ops;
  document.getElementById('undoBtn').disabled = (ops.length === 0);

  historyList.innerHTML = '';
  historySection.style.display = ops.length > 0 ? 'block' : 'none';

  [...ops].reverse().forEach(op => {
    const li = document.createElement('li');
    const oldSnip = op.old.substring(0, 40).replace(/\n/g, '↵');
    const newSnip = op.new.substring(0, 40).replace(/\n/g, '↵');
    li.innerHTML =
      `<div class="op-meta">Стр. ${op.page}</div>` +
      `<div>${esc(oldSnip)} <span class="op-arrow">→</span> ${esc(newSnip)}</div>`;
    historyList.appendChild(li);
  });
}

// ---- Utilities ----
function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok && res.headers.get('content-type')?.includes('application/json')) {
    return res.json();
  }
  return res.json();
}
