'use strict';
/* share_common.js — shared by the public read-only share pages
   (tour_share.html, activity_share.html): map style switcher + zoom reset.
   Reads the shared MAP_TILES table from common.js; depends on Leaflet (L) and the per-page
   globals `shareMap` / `tileLayer` / `_lastBounds`,
   which are declared in each page's inline <script>. Those are only touched at call time
   (setMapStyle/resetZoom run after boot), so load order relative to the inline script is fine. */

function setMapStyle(key, init=false) {
  document.querySelectorAll('.map-style-btn').forEach(b => b.classList.toggle('active', b.dataset.style===key));
  const t = MAP_TILES[key] || MAP_TILES['osm'];
  if(tileLayer) shareMap.removeLayer(tileLayer);
  tileLayer = L.tileLayer(t.url, {attribution:t.attr, maxZoom:19}).addTo(shareMap);
}
function resetZoom() {
  if(_lastBounds) shareMap.fitBounds(_lastBounds, {padding:[24,24]});
}
