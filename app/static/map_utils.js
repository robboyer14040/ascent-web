// ── MAP UTILITIES ──────────────────────────────────────────────────────────────
// Shared Leaflet helpers used across all pages that render maps.

const MapUtils = {
  _photoMarkers: new WeakMap(),
  _lastMedia: new WeakMap(),
  _toggleEl: new WeakMap(),
  _PHOTOS_KEY: 'ascent-map-photos',

  /** Whether photo markers should be shown on maps (shared across all maps, persisted). Default on. */
  photosEnabled() {
    return localStorage.getItem(this._PHOTOS_KEY) !== '0';
  },

  /** Persist the shared show-photos preference. */
  setPhotosEnabled(on) {
    localStorage.setItem(this._PHOTOS_KEY, on ? '1' : '0');
  },

  /**
   * Wire a "photos" camera-icon button to the shared preference for a given map.
   * The button's highlighted state reflects the current preference and toggles it on click.
   * @param {HTMLButtonElement} btn
   * @param {L.Map} map
   */
  wirePhotoToggle(btn, map) {
    if (!btn) return;
    this._toggleEl.set(map, btn);
    this._showToggle(map, false);   // hidden until geotagged photos are placed
    this._reflectToggle(map);
    btn.addEventListener('click', () => {
      this.setPhotosEnabled(!this.photosEnabled());
      this._reflectToggle(map);
      this.refreshPhotoMarkers(map);
    });
  },

  /** Show/hide the "photos" toggle button for a map. */
  _showToggle(map, visible) {
    const btn = this._toggleEl.get(map);
    if (btn) btn.style.display = visible ? 'flex' : 'none';
  },

  /** Update the toggle button's appearance to reflect the current photos-on state. */
  _reflectToggle(map) {
    const btn = this._toggleEl.get(map);
    if (!btn) return;
    const on = this.photosEnabled();
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.style.background  = on ? 'rgba(10,132,255,.85)' : 'rgba(0,0,0,.6)';
    btn.style.borderColor = on ? 'rgba(10,132,255,.9)'  : 'rgba(255,255,255,.15)';
    btn.style.color       = on ? '#fff' : '#999';
    btn.title = on ? 'Photos shown — click to hide' : 'Photos hidden — click to show';
  },

  /** Re-render photo markers for a map from the last media passed to placePhotoMarkers. */
  refreshPhotoMarkers(map) {
    const saved = this._lastMedia.get(map);
    if (saved) this.placePhotoMarkers(map, saved.media, saved.onClickFn);
  },

  /**
   * Add a distance scale bar to the bottom-right corner of a Leaflet map.
   * @param {L.Map} map - Leaflet map instance
   * @param {boolean|null} isMetric - true=metric only, false=imperial only, null=both
   * @returns {L.Control.Scale} the added control (store the ref to remove/recreate it)
   */
  addScale(map, isMetric) {
    const metric   = isMetric == null || !!isMetric;
    const imperial = isMetric == null || !isMetric;
    return L.control.scale({
      position: 'bottomright',
      metric,
      imperial,
      maxWidth: 120,
    }).addTo(map);
  },

  /**
   * Place photo thumbnail markers on a map for media items that have a location.
   * Replaces any previously placed markers for this map instance.
   * @param {L.Map} map - Leaflet map instance
   * @param {Array} media - array of media objects from the /photos API
   * @param {Function} onClickFn - called with the media index when a marker is clicked
   */
  placePhotoMarkers(map, media, onClickFn) {
    this._lastMedia.set(map, { media, onClickFn });
    this.clearPhotoMarkers(map);
    // Only offer the toggle when there's at least one geotagged photo to place.
    this._showToggle(map, media.some(item => item.location));
    if (!this.photosEnabled()) return;
    const markers = [];
    media.forEach((item, idx) => {
      if (!item.location) return;
      const [lat, lng] = item.location;
      const m = L.marker([lat, lng], {
        icon: L.divIcon({
          html: `<img src="${item.url}" style="width:48px;height:48px;object-fit:cover;border:2px solid white;border-radius:4px;box-shadow:0 2px 6px rgba(0,0,0,.6);cursor:pointer;display:block">`,
          className: '',
          iconSize: [52, 52],
          iconAnchor: [26, 26],
        }),
        zIndexOffset: 100,
      }).addTo(map);
      m.on('click', () => onClickFn(idx));
      markers.push(m);
    });
    this._photoMarkers.set(map, markers);
  },

  /**
   * Pan the map the minimum amount needed to keep a point clear of the edges.
   * No-op when the point already sits inside the padded viewport. Used to follow
   * the elevation-chart position puck so it never hides behind (or hugs) the map
   * edge as the puck is scrubbed, dragged, or seeked from the region-info modal.
   * @param {L.Map} map
   * @param {L.LatLngExpression} latLng - map-dot centre
   * @param {number} margin - min pixels between the dot's edge and every map edge
   */
  keepPointVisible(map, latLng, margin = 50) {
    if (!map || !latLng) return;
    const pad = margin + 5;   // dot is 10px, anchored centre → +5 keeps its edge `margin` px clear
    map.panInside(latLng, { padding: [pad, pad], animate: false });
  },

  /**
   * Zoom/pan the map to frame a selected region, keeping a fixed pixel margin
   * around it. Shared by the Activity and Tour elevation-profile region selection,
   * so both zoom identically when a range is dragged on the elevation strip.
   * @param {L.Map} map
   * @param {Array<[number, number]>} latLngs - [lat, lon] pairs of the selected region
   * @param {number} margin - min pixels between the region and every map edge
   */
  fitRegion(map, latLngs, margin = 100) {
    if (!map || !latLngs || latLngs.length < 2) return;
    const bounds = L.latLngBounds(latLngs);
    if (!bounds.isValid()) return;
    map.fitBounds(bounds, { padding: [margin, margin] });
  },

  /** Remove all photo markers previously placed on this map instance. */
  clearPhotoMarkers(map) {
    if (!map) return;
    (this._photoMarkers.get(map) || []).forEach(m => map.removeLayer(m));
    this._photoMarkers.set(map, []);
    this._showToggle(map, false);
  },
};
