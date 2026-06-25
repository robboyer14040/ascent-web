// ── MAP UTILITIES ──────────────────────────────────────────────────────────────
// Shared Leaflet helpers used across all pages that render maps.

const MapUtils = {
  _photoMarkers: new WeakMap(),

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
    this.clearPhotoMarkers(map);
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

  /** Remove all photo markers previously placed on this map instance. */
  clearPhotoMarkers(map) {
    if (!map) return;
    (this._photoMarkers.get(map) || []).forEach(m => map.removeLayer(m));
    this._photoMarkers.set(map, []);
  },
};
