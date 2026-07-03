// lightbox.js — Unified photo/video lightbox used by all pages.
//
// Usage:
//   Lightbox.open(mediaArray, startIndex, opts)
//
// opts:
//   download     {bool}     show Download button (default true)
//   captionEdit  {bool}     show Add/Edit Description button (default false)
//   onNav        {fn(idx)}  called after navigating to a new item (e.g. sync a panel)
//   onCaptionSave {async fn(item, caption)} called to persist a caption; throws on failure
//   onClose      {fn}       called when lightbox closes

const Lightbox = (() => {
  let _media = [], _idx = 0, _opts = {};

  // ── HLS attachment (used here and exported for the photo panel in photos.js) ──
  function attachHls(videoEl, hlsUrl, autoplay = true) {
    if (videoEl._hls) { videoEl._hls.destroy(); videoEl._hls = null; }
    if (typeof Hls !== 'undefined' && Hls.isSupported()) {
      const hls = new Hls();
      videoEl._hls = hls;
      hls.loadSource(hlsUrl);
      hls.attachMedia(videoEl);
      if (autoplay) hls.on(Hls.Events.MANIFEST_PARSED, () => videoEl.play().catch(() => {}));
    } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
      videoEl.src = hlsUrl;
      if (autoplay) videoEl.play().catch(() => {});
    }
  }

  // ── Inject DOM once ────────────────────────────────────────────────────────
  function _ensureDOM() {
    if (document.getElementById('lb-overlay')) return;

    const el = document.createElement('div');
    el.innerHTML = `
<div id="lb-overlay" style="display:none;position:fixed;inset:0;background:var(--lb-bg);z-index:9999;align-items:center;justify-content:center;flex-direction:column" onclick="if(event.target===this)Lightbox.close()">
  <button onclick="Lightbox.close()" style="position:absolute;top:14px;right:18px;font-size:22px;color:var(--lb-text);background:none;border:none;cursor:pointer;line-height:1;padding:4px">✕</button>
  <img id="lb-img" alt="" style="display:none;max-width:90vw;max-height:80vh;object-fit:contain;border-radius:6px">
  <video id="lb-vid" controls playsinline style="display:none;max-width:90vw;max-height:80vh;border-radius:6px;background:#000" onclick="event.stopPropagation()"></video>
  <div id="lb-cap" style="display:none;color:var(--lb-text);font-size:13px;margin-top:10px;max-width:min(90vw,600px);text-align:center;line-height:1.4;opacity:.9" onclick="event.stopPropagation()"></div>
  <div id="lb-cap-edit" style="display:none;margin-top:8px;max-width:min(90vw,520px);width:100%;padding:0 8px;box-sizing:border-box" onclick="event.stopPropagation()">
    <textarea id="lb-cap-inp" rows="2" placeholder="Add a description…" style="width:100%;box-sizing:border-box;padding:6px 8px;font-size:13px;border-radius:6px;border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.1);color:#fff;resize:none;outline:none;line-height:1.4"></textarea>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:5px">
      <button onclick="event.stopPropagation();Lightbox.cancelCaptionEdit()" style="font-size:12px;padding:4px 12px;border-radius:5px;border:1px solid rgba(255,255,255,.3);background:transparent;color:#fff;cursor:pointer">Cancel</button>
      <button onclick="event.stopPropagation();Lightbox.saveCaptionEdit()" style="font-size:12px;padding:4px 12px;border-radius:5px;border:none;background:#f97316;color:#fff;cursor:pointer;font-weight:600">Save</button>
    </div>
  </div>
  <div id="lb-nav" style="display:none;align-items:center;gap:1rem;margin-top:10px">
    <button onclick="event.stopPropagation();Lightbox.nav(-1)" style="background:var(--lb-btn);border:none;color:var(--lb-text);border-radius:6px;padding:6px 18px;cursor:pointer;font-size:18px">‹</button>
    <span id="lb-cnt" style="color:var(--lb-muted);font-size:13px;min-width:50px;text-align:center"></span>
    <button onclick="event.stopPropagation();Lightbox.nav(1)" style="background:var(--lb-btn);border:none;color:var(--lb-text);border-radius:6px;padding:6px 18px;cursor:pointer;font-size:18px">›</button>
  </div>
  <div id="lb-acts" style="display:flex;align-items:center;gap:1rem;margin-top:8px" onclick="event.stopPropagation()">
    <button id="lb-dl" style="display:none;background:var(--lb-btn);border:none;color:var(--lb-text);border-radius:6px;padding:5px 18px;cursor:pointer;font-size:13px" onclick="Lightbox.download()">Download</button>
    <button id="lb-cap-btn" style="display:none;background:var(--lb-btn);border:none;color:var(--lb-text);border-radius:6px;padding:5px 14px;cursor:pointer;font-size:13px" onclick="Lightbox.openCaptionEdit()">Add Description…</button>
  </div>
  <div style="color:var(--lb-hint);font-size:11px;margin-top:6px">Press Esc or click background to close · ← → to navigate</div>
</div>`;
    document.body.appendChild(el.firstElementChild);

    document.addEventListener('keydown', e => {
      if (!isOpen()) return;
      if (e.key === 'Escape')     { close();    return; }
      if (e.key === 'ArrowLeft')  { nav(-1);    return; }
      if (e.key === 'ArrowRight') { nav(1);     return; }
    });

    const overlay = document.getElementById('lb-overlay');
    let _tx = 0, _ty = 0;
    overlay.addEventListener('touchstart', e => {
      _tx = e.touches[0].clientX; _ty = e.touches[0].clientY;
    }, { passive: true });
    overlay.addEventListener('touchend', e => {
      const dx = e.changedTouches[0].clientX - _tx;
      const dy = e.changedTouches[0].clientY - _ty;
      if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.5) nav(dx < 0 ? 1 : -1);
    }, { passive: true });
  }

  // ── Show a specific index ──────────────────────────────────────────────────
  function _show(raw) {
    _idx = ((raw % _media.length) + _media.length) % _media.length;
    const m   = _media[_idx];
    const img = document.getElementById('lb-img');
    const vid = document.getElementById('lb-vid');
    const cap = document.getElementById('lb-cap');
    const cnt = document.getElementById('lb-cnt');
    const nav = document.getElementById('lb-nav');
    const dl  = document.getElementById('lb-dl');
    const capBtn = document.getElementById('lb-cap-btn');
    const acts   = document.getElementById('lb-acts');

    _cancelEdit();

    // ── Media ──
    if (m.type === 'video' && m.hls_url) {
      img.style.display = 'none';
      vid.style.display = '';
      attachHls(vid, m.hls_url, true);
    } else if (m.type === 'video') {
      img.style.display = 'none';
      vid.style.display = '';
      if (vid._hls) { vid._hls.destroy(); vid._hls = null; }
      vid.src      = m.url;
      vid.controls = true;
      vid.play().catch(() => {});
    } else {
      if (vid._hls) { vid._hls.destroy(); vid._hls = null; }
      vid.pause(); vid.removeAttribute('src'); vid.style.display = 'none';
      img.src           = m.url;
      img.style.display = '';
    }

    // ── Caption ──
    if (m.caption) { cap.textContent = m.caption; cap.style.display = ''; }
    else            { cap.textContent = ''; cap.style.display = 'none'; }

    // ── Nav ──
    cnt.textContent   = _media.length > 1 ? `${_idx + 1} / ${_media.length}` : '';
    nav.style.display = _media.length > 1 ? 'flex' : 'none';

    // ── Action buttons ──
    const showDl  = _opts.download !== false;
    const showCap = !!_opts.captionEdit;
    if (dl)     dl.style.display     = showDl  ? '' : 'none';
    if (capBtn) {
      capBtn.style.display = showCap ? '' : 'none';
      if (showCap) capBtn.textContent = m.caption ? 'Edit Description…' : 'Add Description…';
    }
    if (acts) acts.style.display = (showDl || showCap) ? 'flex' : 'none';

    if (_opts.onNav) _opts.onNav(_idx);
  }

  // ── Caption edit ──────────────────────────────────────────────────────────
  function _cancelEdit() {
    const editEl = document.getElementById('lb-cap-edit');
    if (!editEl || editEl.style.display === 'none') return;
    editEl.style.display = 'none';
    const m   = _media[_idx];
    const cap = document.getElementById('lb-cap');
    if (!cap || !m) return;
    if (m.caption) { cap.textContent = m.caption; cap.style.display = ''; }
    else            { cap.style.display = 'none'; }
  }

  function openCaptionEdit() {
    const m = _media[_idx];
    if (!m || !_opts.captionEdit) return;
    document.getElementById('lb-cap').style.display = 'none';
    const inp = document.getElementById('lb-cap-inp');
    inp.value = m.caption || '';
    document.getElementById('lb-cap-edit').style.display = 'block';
    inp.focus();
  }

  function cancelCaptionEdit() { _cancelEdit(); }

  async function saveCaptionEdit() {
    const m = _media[_idx];
    if (!m) return;
    const inp     = document.getElementById('lb-cap-inp');
    const capBtn  = document.getElementById('lb-cap-btn');
    const caption = inp.value.trim();
    try {
      if (_opts.onCaptionSave) await _opts.onCaptionSave(m, caption);
      m.caption = caption || undefined;
      const cap = document.getElementById('lb-cap');
      if (caption) { cap.textContent = caption; cap.style.display = ''; }
      else          { cap.textContent = ''; cap.style.display = 'none'; }
      if (capBtn) capBtn.textContent = caption ? 'Edit Description…' : 'Add Description…';
      document.getElementById('lb-cap-edit').style.display = 'none';
    } catch (e) {
      alert('Failed to save: ' + e.message);
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  function open(media, idx, opts = {}) {
    _media = media;
    _opts  = opts;
    _ensureDOM();
    _show(idx);
    document.getElementById('lb-overlay').style.display = 'flex';
  }

  function nav(delta) {
    if (!_media.length) return;
    _show(_idx + delta);
  }

  function close() {
    _cancelEdit();
    const overlay = document.getElementById('lb-overlay');
    if (!overlay) return;
    overlay.style.display = 'none';
    const vid = document.getElementById('lb-vid');
    if (vid) {
      if (vid._hls) { vid._hls.destroy(); vid._hls = null; }
      vid.pause(); vid.removeAttribute('src');
      vid.style.display = 'none';
    }
    if (_opts.onClose) _opts.onClose();
  }

  function download() {
    const m = _media[_idx];
    if (!m) return;
    const a = document.createElement('a');
    a.href = m.url + '/download';
    a.setAttribute('download', '');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  function isOpen() {
    return document.getElementById('lb-overlay')?.style.display === 'flex';
  }

  return { open, nav, close, isOpen, download, openCaptionEdit, cancelCaptionEdit, saveCaptionEdit, attachHls };
})();
