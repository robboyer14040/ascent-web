/**
 * training_fingerprint.js — Training Fingerprint radar chart for the Summary dashboard.
 *
 * Six training identity dimensions rendered as a radar/spider chart:
 *   Volume      – total distance vs your personal best (same period granularity)
 *   Consistency – active days / days in the period
 *   Climbing    – climb density (ft/mi) vs your personal best
 *   Speed       – avg speed vs your personal best
 *   Endurance   – Z1+Z2 time as % of total HR-zone time  (requires Max HR)
 *   Intensity   – Z4+Z5 time as % of total HR-zone time  (requires Max HR)
 *
 * Follows the dashboard period filter via getFilterParams() / getPeriodLabel().
 * Requires: Chart.js, globals getFilterParams(), getPeriodLabel() (dashboard.html).
 */
(function () {
  'use strict';

  let _fpChart = null;

  const AXES = [
    { key: 'volume',      label: 'Volume',      color: '#a855f7' },
    { key: 'consistency', label: 'Consistency',  color: '#eab308' },
    { key: 'climbing',    label: 'Climbing',     color: '#22c55e' },
    { key: 'speed',       label: 'Speed',        color: '#f97316' },
    { key: 'endurance',   label: 'Endurance',    color: '#3b82f6' },
    { key: 'intensity',   label: 'Intensity',    color: '#ef4444' },
  ];

  window.loadFingerprintChart = async function () {
    const params      = typeof getFilterParams === 'function' ? getFilterParams() : '';
    const periodLabel = typeof getPeriodLabel  === 'function' ? getPeriodLabel()  : '';

    const loadingEl = document.getElementById('fingerprintLoading');
    if (_fpChart) { _fpChart.destroy(); _fpChart = null; }
    if (loadingEl) loadingEl.style.display = 'flex';

    let data;
    try {
      const r = await fetch(`/api/stats/fingerprint${params}`);
      if (!r.ok) { if (loadingEl) loadingEl.style.display = 'none'; return; }
      data = await r.json();
    } catch (_) { if (loadingEl) loadingEl.style.display = 'none'; return; }

    const titleEl  = document.getElementById('fingerprintTitle');
    const noDataEl = document.getElementById('fingerprintNoData');
    const noHREl   = document.getElementById('fingerprintNoHR');
    const legendEl = document.getElementById('fingerprintLegend');
    const canvas   = document.getElementById('fingerprintChart');

    if (titleEl)
      titleEl.textContent = periodLabel
        ? `Training Fingerprint — ${periodLabel}`
        : 'Training Fingerprint';

    if (!data.has_data) {
      if (loadingEl) loadingEl.style.display = 'none';
      if (noDataEl) { noDataEl.textContent = 'No activity data for this period.'; noDataEl.style.display = ''; }
      if (_fpChart) { _fpChart.destroy(); _fpChart = null; }
      if (canvas)   canvas.style.display = 'none';
      if (legendEl) legendEl.innerHTML = '';
      return;
    }

    if (noDataEl) noDataEl.style.display = 'none';
    if (canvas)   canvas.style.display = '';
    if (noHREl)   noHREl.style.display = data.has_hr ? 'none' : '';

    const cs     = getComputedStyle(document.documentElement);
    const mutedC = cs.getPropertyValue('--muted').trim();
    const surfC  = cs.getPropertyValue('--surface2').trim();

    const values = AXES.map(a => data[a.key] ?? 0);

    // Legend: color swatch + label + score bar
    if (legendEl) {
      legendEl.innerHTML = AXES.map((a, i) => {
        const v     = values[i];
        const noHR  = !data.has_hr && (a.key === 'endurance' || a.key === 'intensity');
        const note  = noHR ? ' <span style="color:var(--muted);font-size:.68rem">(no HR)</span>' : '';
        const bar   = `<span style="display:inline-block;width:${Math.max(4, Math.round(v * 0.7))}px;` +
                      `height:6px;background:${a.color};border-radius:3px;` +
                      `vertical-align:middle;margin:0 6px 1px 0"></span>`;
        return `<div>${bar}<span style="color:${a.color};font-weight:600">${a.label}</span>` +
               `<span style="color:var(--muted);margin-left:4px">${v}%</span>${note}</div>`;
      }).join('');
    }

    const ctx = canvas?.getContext('2d');
    if (!ctx) { if (loadingEl) loadingEl.style.display = 'none'; return; }
    if (_fpChart) _fpChart.destroy();

    if (loadingEl) loadingEl.style.display = 'none';
    _fpChart = new Chart(ctx, {
      type: 'radar',
      data: {
        labels: AXES.map(a => a.label),
        datasets: [{
          data: values,
          backgroundColor: 'rgba(249,115,22,.1)',
          borderColor: '#f97316',
          borderWidth: 2,
          pointBackgroundColor: AXES.map(a => a.color),
          pointBorderColor:     AXES.map(a => a.color),
          pointRadius: 4,
          pointHoverRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          r: {
            min: 0,
            max: 100,
            ticks: {
              stepSize: 25,
              color: mutedC,
              backdropColor: 'transparent',
              font: { size: 9 },
              callback: v => v + '%',
            },
            grid:        { color: surfC },
            angleLines:  { color: surfC },
            pointLabels: {
              color: AXES.map(a => a.color),
              font:  { size: 11, weight: 'bold' },
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: item => {
                const axis = AXES[item.dataIndex];
                const noHR = !data.has_hr && (axis.key === 'endurance' || axis.key === 'intensity');
                return noHR ? ` ${item.raw}% (no HR data)` : ` ${item.raw}%`;
              },
            },
          },
        },
      },
    });
  };
})();

// Initial load (deferred — runs after inline dashboard script)
window.loadFingerprintChart();
