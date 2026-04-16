/**
 * hre_chart.js — Heart Rate Efficiency trend chart for the Summary dashboard.
 *
 * Two modes:
 *   power  — watts ÷ HR (W/bpm). Rising = more power per heartbeat.
 *   speed  — speed ÷ HR (×100). Rising = faster per heartbeat.
 *
 * Follows the dashboard period filter (week / month / year) via getFilterParams()
 * and currentPeriod, which are defined in the inline dashboard script.
 *
 * Requires: Chart.js, globals selVal(), getFilterParams(), getPeriodLabel(),
 *           currentPeriod, _uiPrefs  (all in dashboard.html).
 */

(function () {
  'use strict';

  let _hreChart = null;
  const DAY_S = 86400;

  // ── color per activity type ───────────────────────────────────────────────
  function typeColor(type, alpha) {
    const t = (type || '').toLowerCase();
    if (t.includes('run') || t.includes('hike') || t.includes('walk'))
      return `rgba(48,209,88,${alpha})`;
    if (t.includes('ride') || t.includes('cycl'))
      return `rgba(249,115,22,${alpha})`;
    if (t.includes('swim'))
      return `rgba(59,130,246,${alpha})`;
    return `rgba(142,142,147,${alpha})`;
  }

  // ── centered moving average ───────────────────────────────────────────────
  function movingAvg(values, win) {
    const half = Math.floor(win / 2);
    return values.map((_, i) => {
      const s = Math.max(0, i - half);
      const e = Math.min(values.length, i + half + 1);
      const sl = values.slice(s, e);
      return sl.reduce((a, b) => a + b, 0) / sl.length;
    });
  }

  function fmtDate(ts) {
    return new Date(ts * 1000).toLocaleDateString('en-US',
      { month: 'short', day: 'numeric', year: 'numeric' });
  }

  // ── populate type selector; preserve user selection across period changes ─
  function populateTypeSelector(raw) {
    const sel = document.getElementById('hreTypeSel');
    if (!sel) return;
    const isFirst  = !sel.dataset.populated;
    sel.dataset.populated = '1';
    const current  = sel.value;

    const counts = new Map();
    raw.forEach(d => {
      const t = d.activity_type || '';
      if (t) counts.set(t, (counts.get(t) || 0) + 1);
    });
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    const types  = sorted.map(([t]) => t);

    // First load: prefer "Ride", else most common; after that: restore or fall back to All
    const selected = isFirst
      ? (types.includes('Ride') ? 'Ride' : (sorted[0]?.[0] || ''))
      : ((current && types.includes(current)) ? current : '');

    sel.innerHTML =
      '<option value="">All types</option>' +
      sorted.map(([t]) =>
        `<option value="${t}"${t === selected ? ' selected' : ''}>${t}</option>`
      ).join('');
  }

  // ── build HRE-specific query params, mirroring getFilterParams() ──────────
  function hreParams() {
    // getFilterParams() and currentPeriod are globals from the inline dashboard script
    if (typeof getFilterParams === 'function') return getFilterParams();
    // Fallback if somehow not available
    const year = selVal('yearSel');
    return year ? `?year=${year}` : '';
  }

  // ── main entry point ──────────────────────────────────────────────────────
  window.loadHreChart = async function () {
    const params = hreParams();
    const metric = selVal('hreMetricSel') || 'power';

    let raw;
    try {
      const r = await fetch(`/api/stats/hre${params}`);
      if (!r.ok) return;
      raw = await r.json();
    } catch (_) { return; }

    populateTypeSelector(raw);

    const activeType = selVal('hreTypeSel');
    // Filter by type, then by whether the chosen metric has data
    const hreKey = metric === 'power' ? 'hre_power' : 'hre_speed';
    const data = (activeType ? raw.filter(d => d.activity_type === activeType) : raw)
      .filter(d => d[hreKey] != null);

    const titleEl  = document.getElementById('hreSectionTitle');
    const noDataEl = document.getElementById('hreNoData');
    const descEl   = document.getElementById('hreDesc');

    // Section title: include period label (matches zone chart style)
    const periodLabel = typeof getPeriodLabel === 'function' ? getPeriodLabel() : '';
    const typeLabel   = activeType ? ` · ${activeType}` : '';
    const modeLabel   = metric === 'power' ? 'Power÷HR' : 'Speed÷HR';
    if (titleEl)
      titleEl.textContent =
        `HR Efficiency — ${modeLabel}${typeLabel}${periodLabel ? ' — ' + periodLabel : ''}`;

    if (descEl) {
      descEl.textContent = metric === 'power'
        ? 'Watts ÷ HR — a rising trend means more power output per heartbeat (fitness improving). Dots are clickable.'
        : 'Speed ÷ HR ×100 — a rising trend means faster pace per heartbeat (fitness improving). Dots are clickable.';
    }

    const noDataMsg = !data.length
      ? (metric === 'power'
          ? 'No activities with power meter data found. Switch to Speed÷HR or check your power meter data.'
          : 'No activities with both heart rate and speed data found for this period.')
      : '';

    if (noDataMsg) {
      if (noDataEl) { noDataEl.textContent = noDataMsg; noDataEl.style.display = ''; }
      if (_hreChart) { _hreChart.destroy(); _hreChart = null; }
      const canvas = document.getElementById('hreChart');
      if (canvas) canvas.style.display = 'none';
      return;
    }
    if (noDataEl) noDataEl.style.display = 'none';
    const canvas = document.getElementById('hreChart');
    if (canvas) canvas.style.display = '';

    data.sort((a, b) => a.ts - b.ts);

    const useMetric = window._uiPrefs?.use_metric;

    const yVals     = data.map(d => d[hreKey]);
    const scatterPts = data.map((d, i) => ({
      x: Math.floor(d.ts / DAY_S),
      y: yVals[i],
      _d: d,
    }));

    const win    = Math.min(12, Math.max(3, Math.round(data.length / 6)));
    const maVals = movingAvg(yVals, win);
    const maPts  = data.map((d, i) => ({
      x: Math.floor(d.ts / DAY_S),
      y: maVals[i],
    }));

    const cs     = getComputedStyle(document.documentElement);
    const textC  = cs.getPropertyValue('--text').trim();
    const mutedC = cs.getPropertyValue('--muted').trim();
    const gridC  = cs.getPropertyValue('--border').trim();

    const ctx = document.getElementById('hreChart')?.getContext('2d');
    if (!ctx) return;
    if (_hreChart) _hreChart.destroy();

    // X-axis tick formatter (days-since-epoch → date string)
    const spanDays = scatterPts.length > 1
      ? scatterPts[scatterPts.length - 1].x - scatterPts[0].x : 30;
    function tickFmt(v) {
      const d = new Date(v * DAY_S * 1000);
      return spanDays > 300
        ? d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' })
        : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }

    // Y-axis formatting
    const yTickFmt = metric === 'power'
      ? v => v.toFixed(2)
      : v => (v * 100).toFixed(1);
    const yTitle = metric === 'power'
      ? 'W÷HR (watts per bpm)'
      : 'Speed÷HR ×100';

    // Tooltip label builder
    function tooltipLabel(d) {
      const lines = [` ${d.name}`];
      if (metric === 'power' && d.power_w != null) {
        lines.push(` Power: ${Math.round(d.power_w)} W`);
        lines.push(` HR: ${Math.round(d.hr)} bpm`);
        lines.push(` Efficiency: ${d.hre_power.toFixed(3)}`);
      } else if (d.speed_mph != null) {
        const spd = useMetric
          ? `${(d.speed_mph * 1.60934).toFixed(1)} km/h`
          : `${d.speed_mph.toFixed(1)} mph`;
        lines.push(` Speed: ${spd}`);
        lines.push(` HR: ${Math.round(d.hr)} bpm`);
        lines.push(` Efficiency: ${(d.hre_speed * 100).toFixed(2)}`);
      }
      return lines;
    }

    _hreChart = new Chart(ctx, {
      type: 'scatter',
      data: {
        datasets: [
          {
            label: 'Activities',
            data: scatterPts,
            backgroundColor: scatterPts.map(p => typeColor(p._d.activity_type, 0.7)),
            pointRadius: 4,
            pointHoverRadius: 7,
            order: 2,
          },
          {
            label: 'Trend',
            data: maPts,
            type: 'line',
            borderColor: textC,
            borderWidth: 2.5,
            pointRadius: 0,
            tension: 0.4,
            fill: false,
            order: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            filter: item => item.datasetIndex === 0,
            callbacks: {
              title: items => fmtDate(items[0]?.raw?._d?.ts),
              label: item => tooltipLabel(item.raw._d),
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            ticks: { color: mutedC, maxTicksLimit: 10, callback: tickFmt },
            grid: { color: gridC },
          },
          y: {
            ticks: { color: mutedC, callback: yTickFmt },
            grid: { color: gridC },
            title: { display: true, text: yTitle, color: mutedC, font: { size: 10 } },
          },
        },
        onClick: (evt, elements) => {
          const el = elements.find(e => e.datasetIndex === 0);
          if (!el) return;
          const id = scatterPts[el.index]?._d?.id;
          if (id) window.location.href = `/activities/${id}`;
        },
        onHover: (evt, elements) => {
          const c = evt.native?.target;
          if (c) c.style.cursor =
            elements.some(e => e.datasetIndex === 0) ? 'pointer' : 'default';
        },
      },
    });
  };
})();

// Auto-load once the script is ready (defer runs after the init IIFE has set selectors)
window.loadHreChart();
