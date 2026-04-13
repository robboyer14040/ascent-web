// ── MAP UTILITIES ──────────────────────────────────────────────────────────────
// Shared Leaflet helpers used across all pages that render maps.

const MapUtils = {
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
};
