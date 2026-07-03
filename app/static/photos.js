// ── PHOTOS ───────────────────────────────────────────────────────────────────
// photoState.media: [{url, type:'image'|'video', hls_url?, caption?, user_upload?}]
const photoState = { media: [], idx: 0, activityId: null };

async function loadPhotos(activityId) {
  photoState.activityId = activityId;
  photoState.media = [];
  photoState.idx = 0;
  _panelDetach();
  showPhoto(null);
  if (typeof placePhotoMarkers === 'function') placePhotoMarkers([]);

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
    if (photoState.media.length) {
      showPhoto(0);
      if (typeof placePhotoMarkers === 'function') placePhotoMarkers(photoState.media);
    }
  } catch(e) {}
}

function _panelDetach() {
  const v = document.getElementById('photo-vid');
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
  const cap   = document.getElementById('photo-caption');

  if (idx === null || !photoState.media.length) {
    img.style.display = 'none';
    _panelDetach();
    ph.style.display  = '';
    nav.style.display = 'none';
    if (dlBtn) dlBtn.style.display = 'none';
    if (cap) { cap.textContent = ''; cap.style.display = 'none'; }
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
    if (autoplay) Lightbox.attachHls(vid, item.hls_url);
    else { vid.poster = item.url; }
  } else if (item.type === 'video') {
    img.style.display = 'none';
    vid.style.display = 'block';
    vid.src           = item.url;
    vid.controls      = true;
    vid.play().catch(() => {});
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
  if (cap) {
    if (item.caption) { cap.textContent = item.caption; cap.style.display = ''; }
    else { cap.textContent = ''; cap.style.display = 'none'; }
  }
}

function downloadCurrentMedia() {
  const item = photoState.media[photoState.idx];
  if (!item) return;
  const a = document.createElement('a');
  a.href = item.url + '/download';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function photoNav(delta) {
  if (!photoState.media.length) return;
  const n    = photoState.media.length;
  const next = (photoState.idx + delta + n) % n;
  showPhoto(next);
}

function photoClick() {
  if (!photoState.media.length) return;
  Lightbox.open(photoState.media, photoState.idx, {
    download:    true,
    captionEdit: true,
    onNav: idx => { showPhoto(idx); },
    onCaptionSave: async (item, caption) => {
      await _saveCaption(item.url, caption, !!item.user_upload);
      const panelCap = document.getElementById('photo-caption');
      if (panelCap) {
        if (caption) { panelCap.textContent = caption; panelCap.style.display = ''; }
        else          { panelCap.textContent = ''; panelCap.style.display = 'none'; }
      }
    },
  });
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
    if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    if (dx < 0) onLeft(); else onRight();
  }, {passive: true});
}

document.addEventListener('DOMContentLoaded', () => {
  const panel = document.getElementById('photo-panel');
  if (panel) _addSwipe(panel,
    () => photoNav(1),
    () => photoNav(-1)
  );
});

// Panel arrow keys [ ] (not in lightbox, not typing in a field)
document.addEventListener('keydown', e => {
  if (Lightbox.isOpen()) return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (document.getElementById('coach-overlay')?.classList.contains('open')) return;
  if (photoState.media.length > 1) {
    if (e.key === '[') { photoNav(-1); return; }
    if (e.key === ']') { photoNav(1);  return; }
  }
});

// Set active map style button on load
document.addEventListener('DOMContentLoaded', () => {
  const saved = _uiPrefsGet('ascent-map-style') || 'osm';
  document.querySelectorAll('.map-style-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.style === saved);
  });
});
