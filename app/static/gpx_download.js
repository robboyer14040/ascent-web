'use strict';
/* gpx_download.js — save an export URL without leaving the page.

   A plain <a href> to an attachment navigates the whole window to the download.
   In the standalone (home-screen) app there is no browser chrome, so that lands
   the user on the download screen with no way back. Fetching the file and
   saving it from a blob keeps the current page put. */

async function downloadGpx(href, btn) {
  const orig = btn ? btn.innerHTML : null;
  if (btn) { btn.innerHTML = '↓ …'; btn.style.opacity = '.6'; btn.style.pointerEvents = 'none'; }
  try {
    const resp = await fetch(href);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const m    = /filename="([^"]+)"/.exec(resp.headers.get('Content-Disposition') || '');
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = m ? m[1] : 'route.gpx';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    if (btn) btn.innerHTML = orig;
    return true;
  } catch(e) {
    console.error('GPX download failed:', e);
    if (btn) {
      btn.innerHTML = '↓ failed';
      setTimeout(() => { btn.innerHTML = orig; }, 2500);
    }
    return false;
  } finally {
    if (btn) { btn.style.opacity = ''; btn.style.pointerEvents = ''; }
  }
}
