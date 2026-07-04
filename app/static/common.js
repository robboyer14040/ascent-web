'use strict';
/* common.js — helpers shared across tour.html, tour_share.html, activity_share.html.
   Must load AFTER Leaflet (defines L.divIcon markers) and BEFORE each page's inline
   <script>. Per-page bits that legitimately differ (fmtDate, the U units object) stay
   inline in each template. */

// Duration -> "H:MM:SS" / "M:SS". Rounds to the nearest second; falsy -> em dash.
function fmtHMS(s) {
  if(!s) return '—';
  s = Math.round(s);
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60;
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}` : `${m}:${String(sec).padStart(2,'0')}`;
}

// HTML-escape a string for safe insertion as text content.
function esc(s) { const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }

// Great-circle distance between two lat/lon points, in miles.
function _haversineMi(lat1, lon1, lat2, lon2) {
  const R=3958.8, dLat=(lat2-lat1)*Math.PI/180, dLon=(lon2-lon1)*Math.PI/180;
  const a=Math.sin(dLat/2)**2+Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
  return R*2*Math.asin(Math.sqrt(a));
}

// Start (green pennant) / finish (checkered) flag markers for route maps.
const _startIcon = L.divIcon({
  html: `<svg width="17" height="22" viewBox="0 0 20 26" xmlns="http://www.w3.org/2000/svg" style="filter:drop-shadow(0 1px 3px rgba(0,0,0,.9))">
    <line x1="2" y1="0" x2="2" y2="26" stroke="white" stroke-width="2"/>
    <polygon points="2,2 19,7 2,12" fill="#22c55e" stroke="#15803d" stroke-width="1"/>
  </svg>`,
  className: '', iconSize: [17,22], iconAnchor: [2,22],
});
const _endIcon = L.divIcon({
  html: `<svg width="17" height="20" viewBox="0 0 22 26" xmlns="http://www.w3.org/2000/svg" style="filter:drop-shadow(0 1px 3px rgba(0,0,0,.9))">
    <line x1="2" y1="0" x2="2" y2="26" stroke="white" stroke-width="2"/>
    <rect x="2" y="2" width="20" height="12" fill="white" stroke="#333" stroke-width="0.5"/>
    <rect x="2"  y="2"  width="5" height="4" fill="black"/>
    <rect x="12" y="2"  width="5" height="4" fill="black"/>
    <rect x="7"  y="6"  width="5" height="4" fill="black"/>
    <rect x="17" y="6"  width="5" height="4" fill="black"/>
    <rect x="2"  y="10" width="5" height="4" fill="black"/>
    <rect x="12" y="10" width="5" height="4" fill="black"/>
  </svg>`,
  className: '', iconSize: [17,20], iconAnchor: [2,20],
});

// Canonical map tile layers — the single source of truth for every page's map-style
// switcher (tour.html's setTourMapStyle and the share pages' setMapStyle both read this).
// OSM uses the no-subdomain host; OpenTopoMap requires the {s} (a/b/c) subdomains.
const MAP_TILES = {
  'osm':         { url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',                                                attr: '© OpenStreetMap contributors' },
  'topo':        { url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',                                             attr: '© OpenStreetMap contributors, © OpenTopoMap' },
  'carto-dark':  { url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',                                attr: '© OpenStreetMap contributors, © CARTO' },
  'carto-light': { url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',                               attr: '© OpenStreetMap contributors, © CARTO' },
  'esri-sat':    { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr: '© Esri' },
};
