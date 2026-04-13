// ── PHOTOS ───────────────────────────────────────────────────────────────────
// photoState.media: [{url, type:'image'|'video', hls_url?}]
const photoState = { media: [], idx: 0, activityId: null };

async function loadPhotos(activityId) {
  photoState.activityId = activityId;
  photoState.media = [];
  photoState.idx = 0;
  _panelDetach();
  showPhoto(null);

  try {
    const r = await fetch(`/activities/${activityId}/photos`);
    if (!r.ok) return;
    const d = await r.json();
    if (d.media && d.media.length) {
      photoState.media = d.media;
    } else if (d.photos && d.photos.length) {
      // backward compat
      photoState.media = d.photos.map(f => ({url: d.base_url + f, type: 'image'}));
    }
    if (photoState.media.length) showPhoto(0);
  } catch(e) {}
}

function _attachHls(videoEl, hlsUrl, autoplay = true) {
  if (videoEl._hls) { videoEl._hls.destroy(); videoEl._hls = null; }
  if (typeof Hls !== 'undefined' && Hls.isSupported()) {
    const hls = new Hls();
    videoEl._hls = hls;
    hls.loadSource(hlsUrl);
    hls.attachMedia(videoEl);
    if (autoplay) hls.on(Hls.Events.MANIFEST_PARSED, () => videoEl.play().catch(() => {}));
  } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari native HLS
    videoEl.src = hlsUrl;
    if (autoplay) videoEl.play().catch(() => {});
  }
}

function _panelDetach() {
  const v = document.getElementById('photo-vid');
  if (v._hls) { v._hls.destroy(); v._hls = null; }
  v.pause(); v.removeAttribute('src'); v.style.display = 'none';
}

function _lbDetach() {
  const v = document.getElementById('lb-vid');
  if (v._hls) { v._hls.destroy(); v._hls = null; }
  v.pause(); v.removeAttribute('src'); v.style.display = 'none';
}

async function showPhoto(idx) {
  const img   = document.getElementById('photo-img');
  const vid   = document.getElementById('photo-vid');
  const ph    = document.getElementById('photo-placeholder');
  const nav   = document.getElementById('photo-nav');
  const count = document.getElementById('photo-count');
  const dlBtn = document.getElementById('photo-dl-btn');

  if (idx === null || !photoState.media.length) {
    img.style.display = 'none';
    _panelDetach();
    ph.style.display  = '';
    nav.style.display = 'none';
    if (dlBtn) dlBtn.style.display = 'none';
    return;
  }

  photoState.idx = idx;
  const item = photoState.media[idx];
  ph.style.display = 'none';

  if (item.type === 'video' && item.hls_url) {
    img.style.display = 'none';
    vid.style.display = 'block';
    const profile  = await ensureProfile();
    const autoplay = profile.autoplay_videos !== false;
    if (autoplay) _attachHls(vid, item.hls_url);
    else { vid.poster = item.url; }  // show thumbnail without loading/playing
  } else {
    _panelDetach();
    img.src = item.url;
    img.style.display = 'block';
  }

  if (photoState.media.length > 1) {
    nav.style.display = 'flex';
    count.textContent = `${idx + 1} / ${photoState.media.length}`;
  } else {
    nav.style.display = 'none';
  }
  if (dlBtn) dlBtn.style.display = 'block';
}

function downloadCurrentMedia() {
  const item = photoState.media[photoState.idx];
  if (!item) return;
  // Use server-side /download endpoint — fetches video segments from CDN for videos,
  // serves the file directly with attachment header for images.
  const a = document.createElement('a');
  a.href = item.url + '/download';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function _lbShow(item) {
  const lbImg = document.getElementById('lb-img');
  const lbVid = document.getElementById('lb-vid');
  if (item.type === 'video' && item.hls_url) {
    lbImg.style.display = 'none';
    lbVid.style.display = 'block';
    const profile  = await ensureProfile();
    const autoplay = profile.autoplay_videos !== false;
    _attachHls(lbVid, item.hls_url, autoplay);
  } else {
    _lbDetach();
    lbImg.src = item.url;
    lbImg.style.display = 'block';
  }
}

function photoNav(delta, lightbox = false) {
  if (!photoState.media.length) return;
  const n = photoState.media.length;
  const next = (photoState.idx + delta + n) % n;
  showPhoto(next);
  if (lightbox) {
    _lbShow(photoState.media[next]);
    document.getElementById('lb-count').textContent = `${next + 1} / ${n}`;
  }
}

function photoClick() {
  if (!photoState.media.length) return;
  const lbCount = document.getElementById('lb-count');
  _lbShow(photoState.media[photoState.idx]);
  lbCount.textContent = photoState.media.length > 1
    ? `${photoState.idx + 1} / ${photoState.media.length}` : '';
  document.getElementById('lightbox').style.display = 'flex';
}

function closeLightbox() {
  _lbDetach();
  document.getElementById('lightbox').style.display = 'none';
}

// Touch swipe helper — calls onLeft/onRight when horizontal drag > threshold
function _addSwipe(el, onLeft, onRight) {
  let sx = 0, sy = 0;
  el.addEventListener('touchstart', e => {
    sx = e.touches[0].clientX;
    sy = e.touches[0].clientY;
  }, {passive: true});
  el.addEventListener('touchend', e => {
    const dx = e.changedTouches[0].clientX - sx;
    const dy = e.changedTouches[0].clientY - sy;
    if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy) * 1.5) return; // not a horizontal swipe
    if (dx < 0) onLeft(); else onRight();
  }, {passive: true});
}

document.addEventListener('DOMContentLoaded', () => {
  const panel = document.getElementById('photo-panel');
  if (panel) _addSwipe(panel,
    () => photoNav(1),
    () => photoNav(-1)
  );
  const lb = document.getElementById('lightbox');
  if (lb) _addSwipe(lb,
    () => photoNav(1, true),
    () => photoNav(-1, true)
  );
});

// Keyboard: Esc closes lightbox, ←→ navigate photos
document.addEventListener('keydown', e => {
  const lb = document.getElementById('lightbox');
  if (lb.style.display === 'flex') {
    if (e.key === 'Escape')     { closeLightbox(); return; }
    if (e.key === 'ArrowLeft')  { photoNav(-1, true); return; }
    if (e.key === 'ArrowRight') { photoNav(1, true);  return; }
  }
  // Panel arrow keys (when not in lightbox, not typing)
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (document.getElementById('coach-overlay')?.classList.contains('open')) return;
  if (photoState.photos.length > 1) {
    if (e.key === '[') { photoNav(-1); return; }
    if (e.key === ']') { photoNav(1);  return; }
  }
});

// Set active button on load
document.addEventListener('DOMContentLoaded', () => {
  const saved = _uiPrefsGet('ascent-map-style') || 'osm';
  document.querySelectorAll('.map-style-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.style === saved);
  });
});

