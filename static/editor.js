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
  pages: [],          // [{num, wrap, canvas, ctx, overlay, blocks}]
  selectedBlock: null,
  selectedPage: null,
};

// ---- DOM refs ----
const pagesContainer  = document.getElementById('pagesContainer');
const viewerWrap      = document.getElementById('viewerWrap');
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
const filePicker      = document.getElementById('filePicker');
const saveBtn         = document.getElementById('saveBtn');

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
  pdfPathInput.value = file.path ? file.path : file.name;
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

    await reloadPdfDoc();

    placeholder.style.display = 'none';
    pagesContainer.style.display = 'flex';
    saveBtn.disabled = false;

    await buildAndRenderAll();
    await refreshHistory();
  } catch (err) {
    setStatus('Ошибка: ' + err.message, true);
    console.error(err);
  } finally {
    setLoading(false);
  }
}

// ---- Navigation (scroll to a page) ----
document.getElementById('prevPage').addEventListener('click', () => {
  scrollToPage(state.currentPage - 1);
});
document.getElementById('nextPage').addEventListener('click', () => {
  scrollToPage(state.currentPage + 1);
});
document.getElementById('zoomOut').addEventListener('click', async () => {
  if (!state.pdfDoc) return;
  state.zoom = Math.max(0.5, +(state.zoom - 0.25).toFixed(2));
  await rerenderAll();
});
document.getElementById('zoomIn').addEventListener('click', async () => {
  if (!state.pdfDoc) return;
  state.zoom = Math.min(4.0, +(state.zoom + 0.25).toFixed(2));
  await rerenderAll();
});

function scrollToPage(n) {
  if (n < 1 || n > state.pageCount) return;
  const p = state.pages[n - 1];
  if (p) p.wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---- Reload PDF.js document after open/edit/undo ----
async function reloadPdfDoc() {
  const url = `/serve?path=${encodeURIComponent(state.originalPath)}&_t=${Date.now()}`;
  state.pdfDoc = await pdfjsLib.getDocument(url).promise;
}

// ---- Build page wrappers and render every page ----
async function buildAndRenderAll() {
  pagesContainer.innerHTML = '';
  state.pages = [];

  for (let n = 1; n <= state.pageCount; n++) {
    const wrap = document.createElement('div');
    wrap.className = 'page-wrap';
    wrap.dataset.page = n;

    const canvas = document.createElement('canvas');
    const overlay = document.createElement('div');
    overlay.className = 'page-overlay';

    wrap.appendChild(canvas);
    wrap.appendChild(overlay);
    pagesContainer.appendChild(wrap);

    state.pages.push({
      num: n,
      wrap,
      canvas,
      ctx: canvas.getContext('2d'),
      overlay,
      blocks: [],
    });
  }

  for (const p of state.pages) {
    await renderPageCanvas(p);
    await loadBlocksForPage(p);
  }
  updateCurrentPageFromScroll();
}

// ---- Re-render all pages (zoom change) keeping DOM ----
async function rerenderAll() {
  setLoading(true);
  try {
    for (const p of state.pages) {
      await renderPageCanvas(p);
      drawOverlayForPage(p);
    }
  } finally {
    setLoading(false);
  }
}

// ---- Render one page's canvas ----
async function renderPageCanvas(p) {
  const page = await state.pdfDoc.getPage(p.num);
  const viewport = page.getViewport({ scale: state.zoom });
  p.canvas.width = viewport.width;
  p.canvas.height = viewport.height;
  p.overlay.style.width = viewport.width + 'px';
  p.overlay.style.height = viewport.height + 'px';
  await page.render({ canvasContext: p.ctx, viewport }).promise;
}

// ---- Load + draw blocks for one page ----
async function loadBlocksForPage(p) {
  const data = await apiFetch(
    `/api/blocks?path=${encodeURIComponent(state.originalPath)}&page=${p.num}`
  );
  p.blocks = Array.isArray(data) ? data : [];
  drawOverlayForPage(p);
}

function drawOverlayForPage(p) {
  p.overlay.innerHTML = '';
  const scale = state.zoom;
  for (const b of p.blocks) {
    const [x0, y0, x1, y1] = b.bbox;
    const div = document.createElement('div');
    div.className = 'block-rect';
    div.style.left   = (x0 * scale) + 'px';
    div.style.top    = (y0 * scale) + 'px';
    div.style.width  = ((x1 - x0) * scale) + 'px';
    div.style.height = ((y1 - y0) * scale) + 'px';
    div.dataset.idx  = b.index;
    div.title = b.text.substring(0, 80).replace(/\n/g, ' ');
    div.addEventListener('click', () => selectBlock(b, p));
    p.overlay.appendChild(div);
  }
}

// ---- Refresh a single page after an edit ----
async function refreshPage(pageNum) {
  const p = state.pages[pageNum - 1];
  if (!p) return;
  await renderPageCanvas(p);
  await loadBlocksForPage(p);
}

// ---- Block selection ----
function selectBlock(block, page) {
  clearSelection(false);
  state.selectedBlock = block;
  state.selectedPage = page.num;

  const el = page.overlay.querySelector(`[data-idx="${block.index}"]`);
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
  state.selectedPage = null;
  document.querySelectorAll('.block-rect.selected')
    .forEach(el => el.classList.remove('selected'));
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
  const pageNum = state.selectedPage;
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
        page: pageNum,
        bbox: block.bbox,
        old: oldText,
        new: newText,
      }),
    });
    if (res.error) { setStatus('Ошибка: ' + res.error, true); return; }

    if (res.changed) {
      setStatus('Применено.');
      await reloadPdfDoc();
      await refreshPage(pageNum);
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

// ---- Save (download edited PDF via browser) ----
saveBtn.addEventListener('click', () => {
  if (!state.originalPath) { setStatus('Сначала откройте PDF.', true); return; }
  const url = `/download?path=${encodeURIComponent(state.originalPath)}&_t=${Date.now()}`;
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setStatus('Файл выгружается через браузер.');
});

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
      await rerenderAll();
      for (const p of state.pages) await loadBlocksForPage(p);
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

// ---- Track current page on scroll ----
let scrollRaf = null;
viewerWrap.addEventListener('scroll', () => {
  if (scrollRaf) return;
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = null;
    updateCurrentPageFromScroll();
  });
});

function updateCurrentPageFromScroll() {
  if (!state.pages.length) return;
  const mid = viewerWrap.scrollTop + viewerWrap.clientHeight / 2;
  let best = 1, bestDist = Infinity;
  for (const p of state.pages) {
    const center = p.wrap.offsetTop + p.wrap.offsetHeight / 2;
    const dist = Math.abs(center - mid);
    if (dist < bestDist) { bestDist = dist; best = p.num; }
  }
  if (best !== state.currentPage) state.currentPage = best;
  pageLabel.textContent = `${state.currentPage} / ${state.pageCount}`;
}

// ---- Utilities ----
function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  return res.json();
}
