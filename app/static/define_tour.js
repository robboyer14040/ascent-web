// Define As Tour — build a shared tour from existing activities (non-phone only).
// A floating panel (no backdrop) so the activity list behind it stays live and
// rows can be dragged in. Seeded from the current selection, in selection order.
// The stage-list reorder mirrors the Define New Tour modal in tour.html.

let _dtStages = [];   // ordered [{id, name}]

function openDefineTourModal() {
  if (_isPhoneLayout()) return;

  // Seed from selection (multi-select set is in selection order), else the single
  // selected row, else empty.
  const seed = state.selectedIds.size ? [...state.selectedIds]
             : (state.selectedId != null ? [state.selectedId] : []);
  _dtStages = seed
    .map(id => { const a = state.all.find(x => x.id === id); return a ? { id: a.id, name: a.name || '(unnamed)' } : null; })
    .filter(Boolean);

  document.getElementById('dt-title-input').value = '';
  document.getElementById('dt-shared-input').checked = false;
  document.getElementById('dt-status').textContent = '';
  document.getElementById('dt-submit-btn').disabled = false;
  _dtRenderStages();

  const panel = document.getElementById('def-tour-panel');
  panel.style.left = '50%';
  panel.style.top = '84px';
  panel.style.transform = 'translateX(-50%)';
  panel.classList.add('open');
  document.getElementById('dt-title-input').focus();
}

function closeDefineTourModal() {
  document.getElementById('def-tour-panel').classList.remove('open');
}

function _dtRenderStages() {
  const list = document.getElementById('dt-stage-list');
  list.innerHTML = '';
  if (!_dtStages.length) {
    list.innerHTML = '<div style="color:var(--muted2);font-size:12px;padding:6px 4px">No stages yet — drag activities in below.</div>';
    return;
  }
  _dtStages.forEach((s, idx) => {
    const item = document.createElement('div');
    item.className = 'dt-item';
    item.dataset.idx = idx;
    item.draggable = true;
    item.innerHTML = `
      <span class="dt-drag" title="Drag to reorder">⠿</span>
      <span class="dt-num">${idx + 1}</span>
      <span class="dt-name" title="${escHtml(s.name)}">${escHtml(s.name)}</span>
      <button class="dt-remove" title="Remove" onclick="_dtRemoveStage(${idx})">×</button>`;
    _dtAttachReorder(item);
    list.appendChild(item);
  });
}

function _dtRemoveStage(idx) { _dtStages.splice(idx, 1); _dtRenderStages(); }

// ── Stage reorder (mouse drag within the list) ────────────────────────────────
let _dtDragSrc = null;

function _dtAttachReorder(item) {
  item.addEventListener('dragstart', e => {
    _dtDragSrc = parseInt(item.dataset.idx);
    e.dataTransfer.effectAllowed = 'move';
    setTimeout(() => item.classList.add('dragging'), 0);
  });
  item.addEventListener('dragend', () => {
    item.classList.remove('dragging');
    document.querySelectorAll('.dt-item').forEach(el => el.classList.remove('drop-above', 'drop-below'));
    _dtDragSrc = null;
  });
  item.addEventListener('dragover', e => {
    if (_dtDragSrc === null) return;   // an activity drag from the list — leave for the drop zone
    e.preventDefault();
    const rect = item.getBoundingClientRect();
    const above = e.clientY < rect.top + rect.height / 2;
    document.querySelectorAll('.dt-item').forEach(el => el.classList.remove('drop-above', 'drop-below'));
    item.classList.toggle('drop-above', above);
    item.classList.toggle('drop-below', !above);
  });
  item.addEventListener('dragleave', () => item.classList.remove('drop-above', 'drop-below'));
  item.addEventListener('drop', e => {
    if (_dtDragSrc === null) return;
    e.preventDefault();
    e.stopPropagation();
    item.classList.remove('drop-above', 'drop-below');
    const dest = parseInt(item.dataset.idx);
    if (_dtDragSrc === dest) return;
    const rect = item.getBoundingClientRect();
    const before = e.clientY < rect.top + rect.height / 2;
    const moved = _dtStages.splice(_dtDragSrc, 1)[0];
    let ins = dest > _dtDragSrc ? dest - 1 : dest;
    if (!before && ins < _dtStages.length) ins++;
    _dtStages.splice(ins, 0, moved);
    _dtRenderStages();
  });
}

// ── Activity rows as a drag source → the panel's drop zone ────────────────────
// The ids being dragged are held in a module-level flag rather than read back
// from dataTransfer: dataTransfer.types is unreliable across browsers during
// dragover (protected mode), and if the drop zone's dragover doesn't preventDefault
// the browser never fires drop at all. The flag makes the hand-off deterministic.
// Native DnD only carries the row under the cursor, so if that row is part of a
// multi-selection we drag the whole selection (in selection order).
let _dtDragActivityIds = null;

document.addEventListener('dragstart', e => {
  const row = e.target.closest && e.target.closest('.act-row');
  if (!row) return;
  const id = parseInt(row.dataset.id);
  if (isNaN(id)) return;
  _dtDragActivityIds = state.selectedIds.has(id) ? [...state.selectedIds] : [id];
  e.dataTransfer.effectAllowed = 'copy';
  // setData is still required or Firefox won't start the drag.
  try { e.dataTransfer.setData('text/plain', String(id)); } catch (_) {}
});
document.addEventListener('dragend', () => { _dtDragActivityIds = null; });

(function initDtDropZone() {
  const zone = document.getElementById('dt-drop-zone');
  if (!zone) return;
  zone.addEventListener('dragover', e => {
    if (!_dtDragActivityIds) return;   // not an activity drag (e.g. stage reorder)
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    zone.classList.add('drag-active');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-active'));
  zone.addEventListener('drop', e => {
    zone.classList.remove('drag-active');
    if (!_dtDragActivityIds) return;
    e.preventDefault();
    _dtDragActivityIds.forEach(id => {
      if (_dtStages.some(s => s.id === id)) return;   // skip dups
      const a = state.all.find(x => x.id === id);
      if (a) _dtStages.push({ id: a.id, name: a.name || '(unnamed)' });
    });
    _dtRenderStages();
  });
})();

// ── Drag the panel by its header (so it never blocks the rows you need) ────────
(function initDtPanelDrag() {
  const panel = document.getElementById('def-tour-panel');
  const head = document.getElementById('dt-head');
  if (!panel || !head) return;
  let sx, sy, ox, oy, dragging = false;
  head.addEventListener('pointerdown', e => {
    if (e.target.closest('.dt-x')) return;
    dragging = true;
    const r = panel.getBoundingClientRect();
    panel.style.transform = 'none';      // drop the centering translate
    panel.style.left = r.left + 'px';
    panel.style.top = r.top + 'px';
    sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
    head.setPointerCapture(e.pointerId);
  });
  head.addEventListener('pointermove', e => {
    if (!dragging) return;
    panel.style.left = (ox + e.clientX - sx) + 'px';
    panel.style.top = (oy + e.clientY - sy) + 'px';
  });
  const end = e => { dragging = false; try { head.releasePointerCapture(e.pointerId); } catch (_) {} };
  head.addEventListener('pointerup', end);
  head.addEventListener('pointercancel', end);
})();

async function submitDefineTour() {
  const title = document.getElementById('dt-title-input').value.trim();
  const stat = document.getElementById('dt-status');
  if (!title) { stat.textContent = 'Title is required.'; return; }
  if (!_dtStages.length) { stat.textContent = 'Add at least one activity.'; return; }

  const btn = document.getElementById('dt-submit-btn');
  btn.disabled = true;
  stat.textContent = 'Creating…';

  const form = new FormData();
  form.append('title', title);
  form.append('shared', document.getElementById('dt-shared-input').checked ? '1' : '0');
  form.append('activity_ids', JSON.stringify(_dtStages.map(s => s.id)));

  try {
    const resp = await fetch('/tours/from-activities', { method: 'POST', body: form });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const detail = Array.isArray(err.detail)
        ? err.detail.map(x => x && x.msg ? x.msg : String(x)).join('; ')
        : err.detail;
      stat.textContent = (detail || 'Error creating tour.') + ' (' + resp.status + ')';
      btn.disabled = false;
      return;
    }
    const { id } = await resp.json();
    // Persist last-selected tour so /tour opens with the new tour selected.
    await fetch('/api/settings/ui-prefs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prefs: { last_tour_id: id } }),
    }).catch(() => {});
    window.location.href = '/tour';
  } catch (e) {
    stat.textContent = 'Network error.';
    btn.disabled = false;
  }
}
