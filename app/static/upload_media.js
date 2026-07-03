// upload_media.js — Upload photos/videos modal + caption persistence helper

// ── CAPTION PERSISTENCE ───────────────────────────────────────────────────────

function _mediaActIdAndFile(url) {
  const parts = url.split('/').filter(Boolean);
  return { actId: parseInt(parts[parts.length - 2]), filename: parts[parts.length - 1] };
}

async function _saveCaption(url, caption, isUserUpload) {
  const { actId, filename } = _mediaActIdAndFile(url);
  const resp = await fetch(`/activities/${actId}/set-media-caption`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, caption, user_upload: isUserUpload }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || resp.statusText);
  }
}

// ── UPLOAD MODAL ──────────────────────────────────────────────────────────────

let _uploadActivityId = null;
let _uploadFiles      = [];

const _escUp = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

function openUploadModal(activityId) {
  _uploadActivityId = activityId;
  _uploadFiles = [];
  const overlay = document.getElementById('upload-media-overlay');
  if (!overlay) return;
  const inp = document.getElementById('upload-media-input');
  if (inp) inp.value = '';
  const listEl  = document.getElementById('upload-file-list');
  if (listEl)  { listEl.innerHTML = ''; listEl.style.display = 'none'; }
  const prog = document.getElementById('upload-progress');
  if (prog) prog.style.display = 'none';
  const msgEl = document.getElementById('upload-msg');
  if (msgEl) msgEl.style.display = 'none';
  _setUploadSubmitEnabled(false);
  overlay.style.display = 'flex';
}

function closeUploadModal() {
  const overlay = document.getElementById('upload-media-overlay');
  if (overlay) overlay.style.display = 'none';
}

function onUploadDrop(e) {
  e.preventDefault();
  const dz = document.getElementById('upload-drop-zone');
  if (dz) dz.style.borderColor = 'var(--border2)';
  _setUploadFiles([...e.dataTransfer.files]);
}

function onUploadFilesSelected(input) {
  _setUploadFiles([...input.files]);
}

function _setUploadSubmitEnabled(on) {
  const btn = document.getElementById('upload-submit-btn');
  if (!btn) return;
  btn.disabled    = !on;
  btn.style.opacity = on ? '1' : '.5';
  btn.style.cursor  = on ? 'pointer' : 'default';
}

function _setUploadFiles(files) {
  const MAX = 100 * 1024 * 1024;
  const ok  = files.filter(f => f.size <= MAX);
  const big = files.filter(f => f.size >  MAX);
  _uploadFiles = ok;

  const listEl = document.getElementById('upload-file-list');
  if (listEl) {
    let html = '';
    for (const f of ok) {
      const mb = (f.size / (1024 * 1024)).toFixed(1);
      html += `<div style="display:flex;align-items:center;gap:6px;padding:4px 0;font-size:12px;border-bottom:1px solid var(--border)">
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_escUp(f.name)}</span>
        <span style="flex-shrink:0;color:var(--muted)">${mb} MB</span>
      </div>`;
    }
    if (big.length) {
      html += `<div style="font-size:11px;color:#f97316;margin-top:5px">${big.length} file(s) skipped — exceed 100 MB limit</div>`;
    }
    listEl.innerHTML = html;
    listEl.style.display = (ok.length || big.length) ? 'block' : 'none';
  }

  _setUploadSubmitEnabled(ok.length > 0);
}

async function submitUpload() {
  if (!_uploadActivityId || !_uploadFiles.length) return;

  const submitBtn   = document.getElementById('upload-submit-btn');
  const progressEl  = document.getElementById('upload-progress');
  const progressBar = document.getElementById('upload-progress-bar');
  const progressLbl = document.getElementById('upload-progress-label');
  const msgEl       = document.getElementById('upload-msg');
  const cancelBtn   = document.querySelector('#upload-media-overlay button:first-of-type');

  if (submitBtn)  { submitBtn.disabled = true; submitBtn.style.opacity = '.5'; }
  if (cancelBtn)  cancelBtn.disabled = true;
  if (progressEl) progressEl.style.display = 'block';
  if (msgEl)      msgEl.style.display = 'none';
  if (progressBar) progressBar.style.width = '0%';

  let uploaded = 0;
  let errors   = 0;
  const total  = _uploadFiles.length;

  for (let i = 0; i < total; i++) {
    const file = _uploadFiles[i];
    if (progressLbl) progressLbl.textContent = `Uploading ${i + 1} of ${total}…`;
    if (progressBar) progressBar.style.width = `${(i / total) * 100}%`;

    const fd = new FormData();
    fd.append('files', file);
    try {
      const resp = await fetch(`/activities/${_uploadActivityId}/upload-media`, {
        method: 'POST', body: fd
      });
      if (resp.ok) {
        uploaded++;
      } else {
        const err = await resp.json().catch(() => ({}));
        errors++;
        log && log.warn ? log.warn('Upload error', err) : console.warn('Upload error', err.detail || resp.statusText);
      }
    } catch (e) {
      errors++;
      console.error('Upload failed:', e);
    }
  }

  if (progressBar) progressBar.style.width = '100%';
  if (progressLbl) progressLbl.textContent = 'Done';

  if (errors && msgEl) {
    msgEl.textContent   = `${uploaded} uploaded, ${errors} failed.`;
    msgEl.style.color   = '#f97316';
    msgEl.style.display = 'block';
  }

  // Reload the photo panel for this activity
  if (uploaded > 0) {
    if (typeof loadStagePhotos === 'function') {
      await loadStagePhotos(_uploadActivityId);
    } else if (typeof loadPhotos === 'function') {
      await loadPhotos(_uploadActivityId);
    }
  }

  setTimeout(closeUploadModal, uploaded > 0 ? 700 : 2500);
}
