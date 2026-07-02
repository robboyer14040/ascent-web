// ── ELEVATION GRADIENT FILL ───────────────────────────────────────────────────
// Shared by altitude_chart.js (Analysis page) and stage_elev_chart.js (Tour/Tour_share).

// Gradient % → color: blue (downhill) → green (flat) → yellow → orange → red (steep)
function gradientColor(pct, alpha, light) {
  alpha = (alpha == null) ? 1 : alpha;
  light = !!light;
  const stops = light
    ? [[-20,29,78,216],[-5,96,165,250],[0,21,128,61],[5,161,98,7],[10,194,65,12],[20,185,28,28]]
    : [[-20,37,99,235],[-5,96,165,250],[0,34,197,94],[5,234,179,8],[10,249,115,22],[20,239,68,68]];
  const clamped = Math.max(stops[0][0], Math.min(stops[stops.length - 1][0], pct));
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0,r0,g0,b0] = stops[i], [t1,r1,g1,b1] = stops[i + 1];
    if (clamped <= t1) {
      const t = (clamped - t0) / (t1 - t0);
      const r = Math.round(r0 + (r1 - r0) * t);
      const g = Math.round(g0 + (g1 - g0) * t);
      const b = Math.round(b0 + (b1 - b0) * t);
      return alpha < 1 ? `rgba(${r},${g},${b},${alpha})` : `rgb(${r},${g},${b})`;
    }
  }
  return light ? `rgba(185,28,28,${alpha})` : `rgba(239,68,68,${alpha})`;
}

// Build a Chart.js beforeDatasetsDraw plugin that fills the elevation area with
// gradient-colored trapezoids instead of a solid fill.
//
// pts       [{x, y}]  display-unit points (same array used for the Chart dataset)
// grads     [number]  gradient % at each point, parallel to pts
// yScaleKey string    Chart.js y-axis key: 'yElev' (Analysis) or 'y' (Tour/Tour_share)
// light     bool      true = light theme
function makeElevFillPlugin(pts, grads, yScaleKey, light) {
  return {
    id: 'elevGradFill',
    beforeDatasetsDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      const xSc = scales.x;
      const ySc = scales[yScaleKey || 'y'];
      if (!xSc || !ySc || !chartArea) return;
      const n = pts.length;
      ctx.save();
      ctx.beginPath();
      ctx.rect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, chartArea.bottom - chartArea.top);
      ctx.clip();
      for (let i = 0; i < n - 1; i++) {
        const x0 = xSc.getPixelForValue(pts[i].x);
        const x1 = xSc.getPixelForValue(pts[i + 1].x);
        if (x1 <= chartArea.left || x0 >= chartArea.right) continue;
        const y0 = ySc.getPixelForValue(pts[i].y);
        const y1 = ySc.getPixelForValue(pts[i + 1].y);
        ctx.fillStyle = gradientColor(grads[i] || 0, 0.72, light);
        ctx.beginPath();
        ctx.moveTo(x0, chartArea.bottom);
        ctx.lineTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.lineTo(x1, chartArea.bottom);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
    }
  };
}
