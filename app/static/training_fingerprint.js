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
 * Two-phase loading:
 *   Phase 1  – fast activity-table query; chart renders and animates from 0.
 *   Phase 2  – zone data fetched sub-period by sub-period (month-by-month for a
 *               year view, week-by-week for a month view, etc.).  The chart updates
 *               after every sub-period so the user sees endurance/intensity fill in.
 *
 * Requires: Chart.js, dashboard globals getFilterParams(), getPeriodLabel(),
 *           currentPeriod, selVal(), getWeeksInMonth().
 */
(function () {
  'use strict';

  let _fpChart  = null;
  let _lastData = null;

  const AXES = [
    { key: 'volume',      label: 'Volume',      color: '#a855f7' },
    { key: 'consistency', label: 'Consistency',  color: '#eab308' },
    { key: 'climbing',    label: 'Climbing',     color: '#22c55e' },
    { key: 'speed',       label: 'Speed',        color: '#f97316' },
    { key: 'endurance',   label: 'Endurance',    color: '#3b82f6' },
    { key: 'intensity',   label: 'Intensity',    color: '#ef4444' },
  ];

  // ── helpers ──────────────────────────────────────────────────────────────────

  function setLoading(msg) {
    const el = document.getElementById('fingerprintLoading');
    if (!el) return;
    if (msg) { el.textContent = msg; el.style.display = ''; }
    else      { el.style.display = 'none'; }
  }

  function updateLegend(data) {
    const legendEl = document.getElementById('fingerprintLegend');
    if (!legendEl) return;
    legendEl.innerHTML = AXES.map(a => {
      const v    = data[a.key] ?? 0;
      const noHR = !data.has_hr && (a.key === 'endurance' || a.key === 'intensity');
      const note = noHR ? ' <span style="color:var(--muted);font-size:.68rem">(no HR)</span>' : '';
      const bar  = `<span style="display:inline-block;width:${Math.max(4, Math.round(v * 0.7))}px;` +
                   `height:6px;background:${a.color};border-radius:3px;` +
                   `vertical-align:middle;margin:0 6px 1px 0"></span>`;
      return `<div>${bar}<span style="color:${a.color};font-weight:600">${a.label}</span>` +
             `<span style="color:var(--muted);margin-left:4px">${v}%</span>${note}</div>`;
    }).join('');
  }

  function createChart(ctx, data) {
    const cs     = getComputedStyle(document.documentElement);
    const mutedC = cs.getPropertyValue('--muted').trim();
    const surfC  = cs.getPropertyValue('--surface2').trim();
    return new Chart(ctx, {
      type: 'radar',
      data: {
        labels: AXES.map(a => a.label),
        datasets: [{
          data: AXES.map(a => data[a.key] ?? 0),
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
        layout: { padding: { top: 15, bottom: 0, left: 0, right: 0 } },
        animation: { duration: 600, easing: 'easeOutQuart' },
        scales: {
          r: {
            min: 0, max: 100,
            ticks: {
              stepSize: 25, color: mutedC, backdropColor: 'transparent',
              font: { size: 9 }, callback: v => v + '%',
            },
            grid:        { color: surfC },
            angleLines:  { color: surfC },
            pointLabels: { color: AXES.map(a => a.color), font: { size: 11, weight: 'bold' } },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: item => {
                const axis = AXES[item.dataIndex];
                const noHR = !_lastData?.has_hr && (axis.key === 'endurance' || axis.key === 'intensity');
                return noHR ? ` ${item.raw}% (no HR data)` : ` ${item.raw}%`;
              },
            },
          },
        },
      },
    });
  }

  // Build a list of /api/stats/zones query strings to fetch incrementally.
  // For week→one call; month→weeks; year→months; all-time→years.
  function zoneSubPeriods() {
    const period = typeof currentPeriod !== 'undefined' ? currentPeriod : 'year';
    const _sel   = id => (typeof selVal === 'function' ? selVal(id) : '') || '';

    if (period === 'week') {
      const week = _sel('weekSel');
      return week ? [`?week_start=${week}`] : [];
    }

    if (period === 'month') {
      const y = parseInt(_sel('yearSel'))  || new Date().getFullYear();
      const m = parseInt(_sel('monthSel')) || (new Date().getMonth() + 1);
      if (typeof getWeeksInMonth === 'function') {
        return getWeeksInMonth(y, m).map(w => `?week_start=${w.value}`);
      }
      return [`?year=${y}&month=${m}`];
    }

    // year or all-time
    const year = _sel('yearSel');
    if (year) {
      return Array.from({ length: 12 }, (_, i) => `?year=${year}&month=${i + 1}`);
    }
    // all-time: read available years from the select
    const yearSel = document.getElementById('yearSel');
    const years   = yearSel
      ? [...yearSel.options].map(o => o.value).filter(v => v)
      : [];
    return years.length ? years.map(y => `?year=${y}`) : [''];
  }

  // ── main ────────────────────────────────────────────────────────────────────

  window.loadFingerprintChart = async function () {
    const params      = typeof getFilterParams === 'function' ? getFilterParams() : '';
    const periodLabel = typeof getPeriodLabel  === 'function' ? getPeriodLabel()  : '';

    const titleEl  = document.getElementById('fingerprintTitle');
    const noDataEl = document.getElementById('fingerprintNoData');
    const noHREl   = document.getElementById('fingerprintNoHR');
    const canvas   = document.getElementById('fingerprintChart');

    if (titleEl)
      titleEl.textContent = periodLabel
        ? `Training Fingerprint — ${periodLabel}`
        : 'Training Fingerprint';

    if (_fpChart) { _fpChart.destroy(); _fpChart = null; }
    if (noHREl)   noHREl.style.display = 'none';
    setLoading('Building chart…');

    // ── Phase 1: fast — activity-table query, no zone join ───────────────────
    let phase1;
    try {
      const sep = params ? '&' : '?';
      const r   = await fetch(`/api/stats/fingerprint${params}${sep}skip_zones=true`);
      if (!r.ok) { setLoading(null); return; }
      phase1 = await r.json();
    } catch (_) { setLoading(null); return; }

    if (!phase1.has_data) {
      setLoading(null);
      if (noDataEl) { noDataEl.textContent = 'No activity data for this period.'; noDataEl.style.display = ''; }
      if (canvas)   canvas.style.display = 'none';
      const legendEl = document.getElementById('fingerprintLegend');
      if (legendEl) legendEl.innerHTML = '';
      return;
    }

    if (noDataEl) noDataEl.style.display = 'none';
    if (canvas)   canvas.style.display = '';

    _lastData = phase1;
    updateLegend(phase1);

    const ctx = canvas?.getContext('2d');
    if (!ctx) { setLoading(null); return; }

    // Render immediately — animates from 0
    _fpChart = createChart(ctx, phase1);

    // ── Phase 2: progressive zone loading ────────────────────────────────────
    const subPeriods = zoneSubPeriods();
    if (!subPeriods.length) { setLoading(null); return; }

    const total     = subPeriods.length;
    const accZones  = [0, 0, 0, 0, 0];
    let   hasHR     = false;
    let   maxHRKnown = null;   // null = not yet checked

    for (let i = 0; i < total; i++) {
      setLoading(`Loading HR zones… ${i + 1} / ${total}`);
      let zd;
      try {
        const r = await fetch(`/api/stats/zones${subPeriods[i]}`);
        if (!r.ok) continue;
        zd = await r.json();
      } catch (_) { continue; }

      // If max_hr not set on first call, bail — no HR data available
      if (maxHRKnown === null) {
        maxHRKnown = !!zd.max_hr;
        if (!maxHRKnown) {
          if (noHREl) noHREl.style.display = '';
          break;
        }
      }

      // Accumulate zone minutes
      (zd.hr_zones_min || []).forEach((v, j) => { accZones[j] += v; });

      const zTotal    = accZones.reduce((a, b) => a + b, 0);
      if (zTotal > 0) {
        hasHR = true;
        const endurance = Math.round((accZones[0] + accZones[1]) / zTotal * 100);
        const intensity = Math.round((accZones[3] + accZones[4]) / zTotal * 100);

        const updated = { ...phase1, has_hr: true, endurance, intensity };
        _lastData = updated;

        if (_fpChart) {
          _fpChart.data.datasets[0].data = AXES.map(a => updated[a.key] ?? 0);
          _fpChart.update('active');
        }
        updateLegend(updated);
      }
    }

    if (noHREl && hasHR) noHREl.style.display = 'none';
    setLoading(null);
  };
})();

// Initial load (deferred — runs after inline dashboard script)
window.loadFingerprintChart();
