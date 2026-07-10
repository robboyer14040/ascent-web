// weather_forecast.js — Route/activity weather forecast modals
// Exposes: window.openWeatherForecast(source, sourceId, name)
// Reads:   window._UI_PREFS_INITIAL.use_metric for unit preference

(function () {
  'use strict';

  // ── Inject CSS ───────────────────────────────────────────────────────────────
  const _style = document.createElement('style');
  _style.textContent = `
    .thumb-wx{position:absolute;top:6px;left:34px;width:22px;height:22px;border-radius:50%;background:rgba(0,0,0,.55);color:#fff;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity .15s;padding:0}
    .route-thumb:hover .thumb-wx{opacity:1}
    .thumb-wx:hover{background:rgba(249,115,22,.8)!important}

    #wx-picker-modal,#wx-result-modal{display:none;position:fixed;inset:0;z-index:9100;background:rgba(0,0,0,.65);align-items:center;justify-content:center}
    #wx-picker-modal.open,#wx-result-modal.open{display:flex}
    #wx-result-modal{padding:max(16px,env(safe-area-inset-top)) 16px max(16px,env(safe-area-inset-bottom)) 16px;align-items:flex-start}
    @media (min-height:700px) and (min-width:600px){#wx-result-modal{align-items:center}}
    .wx-scroll{overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}

    .wx-box{background:var(--surface,#2c2c2e);border:1px solid var(--border2,#48484a);border-radius:14px}

    .wx-stat-row{display:flex;gap:6px}
    .wx-stat-card{flex:1;min-width:0;background:var(--bg,#1c1c1e);border:1px solid var(--border,#3a3a3c);border-radius:8px;padding:6px 10px;text-align:center;box-sizing:border-box}
    .wx-stat-val{font-size:14px;font-weight:700;color:var(--text,#f2f2f7)}
    .wx-stat-lbl{font-size:9px;color:var(--muted,#8e8e93);margin-top:1px;text-transform:uppercase;letter-spacing:.04em}

    .wx-chart-sec{margin-bottom:20px}
    .wx-chart-sec:last-child{margin-bottom:0}
    .wx-chart-title{font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text,#f2f2f7)}
    .wx-chart-wrap{height:160px;position:relative}

    #wx-spinner{color:var(--muted,#8e8e93);font-size:13px;padding:40px;text-align:center}
    #wx-error{color:#ff453a;font-size:13px;padding:20px;text-align:center;display:none}
    #wx-charts-area{display:none}
  `;
  document.head.appendChild(_style);

  // ── Picker modal ─────────────────────────────────────────────────────────────
  const _pickerEl = document.createElement('div');
  _pickerEl.id = 'wx-picker-modal';
  _pickerEl.innerHTML = `
    <div class="wx-box" style="padding:22px;width:320px;display:flex;flex-direction:column;gap:16px">
      <div id="wx-picker-name" style="font-size:15px;font-weight:600;color:var(--text,#f2f2f7)"></div>
      <div>
        <label style="display:block;font-size:11px;color:var(--muted,#8e8e93);margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em">Start date &amp; time</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input type="datetime-local" id="wx-datetime"
            style="flex:1;min-width:0;background:var(--bg,#1c1c1e);border:1px solid var(--border2,#48484a);border-radius:8px;padding:8px 10px;color:var(--text,#f2f2f7);font-size:13px;outline:none;box-sizing:border-box;font-family:inherit;color-scheme:dark light">
          <button id="wx-ok-btn" title="Get Forecast" style="flex-shrink:0;width:38px;height:38px;border-radius:10px;border:none;background:#f97316;color:#fff;font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;line-height:1">✓</button>
        </div>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button id="wx-cancel-btn" style="padding:7px 14px;border-radius:8px;border:1px solid var(--border2,#48484a);background:none;color:var(--text,#f2f2f7);font-size:13px;cursor:pointer">Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(_pickerEl);

  // ── Results modal ────────────────────────────────────────────────────────────
  const _resultEl = document.createElement('div');
  _resultEl.id = 'wx-result-modal';
  _resultEl.innerHTML = `
    <div class="wx-box" style="width:100%;max-width:680px;max-height:90svh;display:flex;flex-direction:column;overflow:hidden;position:relative">
      <div style="position:relative;padding:14px 18px 0;border-bottom:1px solid var(--border,#3a3a3c);flex-shrink:0">
        <button id="wx-close-btn" style="position:absolute;top:14px;right:18px;width:26px;height:26px;border-radius:50%;border:none;background:var(--surface2,#3a3a3c);color:var(--text,#f2f2f7);cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center;z-index:1">✕</button>
        <div style="display:flex;gap:10px;align-items:flex-start;min-width:0">
          <div style="flex:1;min-width:0;display:flex;flex-direction:column">
            <div id="wx-result-name" style="font-size:15px;font-weight:700;color:var(--text,#f2f2f7)"></div>
            <div id="wx-result-datetime" style="font-size:12px;font-weight:700;color:#ffffff;margin-top:4px"></div>
            <div id="wx-result-meta" style="font-size:11px;color:var(--muted,#8e8e93);margin-top:2px"></div>
          </div>
          <canvas id="wx-route-canvas" width="192" height="192" style="width:96px;height:96px;border-radius:10px;flex-shrink:0;background:#1c1c2e"></canvas>
        </div>
        <div style="display:flex;justify-content:center;padding:8px 0">
          <button id="wx-change-dt-btn" style="display:none;background:var(--surface2,#3a3a3c);border:1px solid var(--border2,#48484a);border-radius:6px;color:var(--text,#f2f2f7);font-size:11px;font-weight:600;padding:5px 9px;cursor:pointer;white-space:nowrap">Change Date/Time…</button>
        </div>
        <div id="wx-stat-row" class="wx-stat-row" style="margin-bottom:10px"></div>
      </div>
      <div class="wx-scroll" style="padding:16px 18px;flex:1">
        <div id="wx-spinner">Fetching weather data…</div>
        <div id="wx-error"></div>
        <div id="wx-charts-area">
          <div class="wx-chart-sec">
            <div class="wx-chart-title">Precipitation &amp; Cloud Cover</div>
            <div class="wx-chart-wrap"><canvas id="wx-precip-chart"></canvas></div>
          </div>
          <div class="wx-chart-sec">
            <div class="wx-chart-title">Wind Speed</div>
            <div class="wx-chart-wrap"><canvas id="wx-wind-chart"></canvas></div>
          </div>
          <div class="wx-chart-sec">
            <div class="wx-chart-title">Temperature</div>
            <div class="wx-chart-wrap"><canvas id="wx-temp-chart"></canvas></div>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(_resultEl);
  // Hold a stable reference to the wx-box — it gets moved between _resultEl and the mob tab panel
  const _wxBox = _resultEl.querySelector('.wx-box');

  // ── State ────────────────────────────────────────────────────────────────────
  let _source = null, _sourceId = null, _sourceName = null;
  let _endpoint = '/api/forecast';
  let _tChart = null, _wChart = null, _pChart = null;

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function _metric()       { return !!(window._UI_PREFS_INITIAL?.use_metric); }
  function _f2c(f)         { return (f - 32) * 5 / 9; }
  function _mph2kph(m)     { return m * 1.60934; }
  function _fmtTemp(f)     { return f == null ? '—' : (_metric() ? `${_f2c(f).toFixed(1)}°C`  : `${Math.round(f)}°F`); }
  function _fmtWind(mph)   { return mph == null ? '—' : (_metric() ? `${Math.round(_mph2kph(mph))} km/h` : `${Math.round(mph)} mph`); }
  function _fmtTime(iso)   { return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }

  function _drawRouteCanvas(canvas, points) {
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#1c1c2e';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (!points || points.length < 2) return;
    _drawTiledMap(canvas, ctx, points);
  }

  async function _drawTiledMap(canvas, ctx, points) {
    const W = canvas.width, H = canvas.height;
    const lats = points.map(p => p[0]), lons = points.map(p => p[1]);
    let minLat = Math.min(...lats), maxLat = Math.max(...lats);
    let minLon = Math.min(...lons), maxLon = Math.max(...lons);

    // Pad bounds by 15%
    const latPad = Math.max((maxLat - minLat) * 0.15, 0.004);
    const lonPad = Math.max((maxLon - minLon) * 0.15, 0.004);
    minLat -= latPad; maxLat += latPad;
    minLon -= lonPad; maxLon += lonPad;

    // Pick zoom so lon range fills ~85% of canvas width
    let zoom = Math.round(Math.log2(W * 0.85 / 256 * 360 / (maxLon - minLon)));
    zoom = Math.max(8, Math.min(15, zoom));

    // Mercator world-pixel helpers (256px per tile)
    const R = 256 * Math.pow(2, zoom);
    const lonToWX = lon => (lon + 180) / 360 * R;
    const latToWY = lat => {
      const s = Math.sin(lat * Math.PI / 180);
      return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * R;
    };

    // View bounds in world pixels
    const wx0 = lonToWX(minLon), wx1 = lonToWX(maxLon);
    const wy0 = latToWY(maxLat), wy1 = latToWY(minLat);
    const scale = Math.min(W / (wx1 - wx0), H / (wy1 - wy0));
    // Center offset
    const offX = (W - (wx1 - wx0) * scale) / 2 - wx0 * scale;
    const offY = (H - (wy1 - wy0) * scale) / 2 - wy0 * scale;

    const toX = lon => lonToWX(lon) * scale + offX;
    const toY = lat => latToWY(lat) * scale + offY;

    // Fetch & draw tiles
    const tileUrl = window._WX_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    const tx0 = Math.floor(wx0 / 256), tx1 = Math.ceil(wx1 / 256);
    const ty0 = Math.floor(wy0 / 256), ty1 = Math.ceil(wy1 / 256);
    const tileSize = 256 * scale;

    await Promise.all(
      Array.from({ length: (tx1 - tx0 + 1) * (ty1 - ty0 + 1) }, (_, k) => {
        const tx = tx0 + (k % (tx1 - tx0 + 1));
        const ty = ty0 + Math.floor(k / (tx1 - tx0 + 1));
        return new Promise(resolve => {
          const img = new Image();
          img.crossOrigin = 'anonymous';
          img.onload = () => {
            ctx.drawImage(img, tx * 256 * scale + offX, ty * 256 * scale + offY, tileSize, tileSize);
            resolve();
          };
          img.onerror = () => resolve();
          img.src = tileUrl.replace('{z}', zoom).replace('{x}', tx).replace('{y}', ty);
        });
      })
    );

    // Route line
    ctx.shadowColor = 'rgba(249,115,22,0.7)';
    ctx.shadowBlur = 4;
    ctx.strokeStyle = '#f97316';
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(toX(points[0][1]), toY(points[0][0]));
    for (let i = 1; i < points.length; i++) ctx.lineTo(toX(points[i][1]), toY(points[i][0]));
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Start / end dots
    const dot = (lat, lon, fill) => {
      ctx.fillStyle = fill;
      ctx.strokeStyle = 'rgba(255,255,255,0.9)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(toX(lon), toY(lat), 4, 0, 2 * Math.PI);
      ctx.fill();
      ctx.stroke();
    };
    dot(points[0][0], points[0][1], '#30d158');
    dot(points[points.length - 1][0], points[points.length - 1][1], '#ff453a');
  }

  function _setDefaultDT() {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    const p = n => String(n).padStart(2, '0');
    document.getElementById('wx-datetime').value =
      `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T09:00`;
  }

  function _ensureChartJs(cb) {
    if (window.Chart) { cb(); return; }
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
    s.onload = cb;
    document.head.appendChild(s);
  }

  // ── Public entry point ───────────────────────────────────────────────────────
  // endpoint (optional) overrides the POST target — used by tour stages whose
  // route lives behind a different (incl. public share-token) URL. The request
  // body shape is identical, so the same modal/renderer is reused everywhere.
  window.openWeatherForecast = function (source, sourceId, name, endpoint) {
    _source = source; _sourceId = sourceId; _sourceName = name;
    _endpoint = endpoint || '/api/forecast';
    document.getElementById('wx-picker-name').textContent = name;
    _setDefaultDT();
    _pickerEl.classList.add('open');
  };

  // ── Picker events ────────────────────────────────────────────────────────────
  document.getElementById('wx-cancel-btn').onclick = () => _pickerEl.classList.remove('open');
  _pickerEl.addEventListener('click', e => { if (e.target === _pickerEl) _pickerEl.classList.remove('open'); });

  function _isMobForecastTab() {
    const p = document.getElementById('mob-tab-forecast');
    return !!(p && (window.innerWidth <= 767 ||
      (window.innerHeight <= 500 && window.matchMedia('(orientation:landscape)').matches && navigator.maxTouchPoints > 0)));
  }

  function _embedInMobTab() {
    const panel = document.getElementById('mob-tab-forecast');
    if (!panel) return;
    document.getElementById('wx-close-btn').style.display = 'none';
    document.getElementById('wx-change-dt-btn').style.display = '';
    if (_wxBox.parentElement !== panel) {
      panel.innerHTML = '';
      panel.appendChild(_wxBox);
    }
    panel.dataset.forecastLoaded = '1';
    panel.scrollTop = 0;
  }

  // Called by mobilePushDetail so the wx-box is safely returned before the panel is cleared
  window.resetWeatherForecast = function() {
    const panel = document.getElementById('mob-tab-forecast');
    if (panel) {
      if (_wxBox.parentElement === panel) _resultEl.appendChild(_wxBox);
      panel.innerHTML = '';
      delete panel.dataset.forecastLoaded;
    }
    document.getElementById('wx-change-dt-btn').style.display = 'none';
    document.getElementById('wx-close-btn').style.display = '';
  };

  async function _submitForecast() {
    const val = document.getElementById('wx-datetime').value;
    if (!val) return;

    _pickerEl.classList.remove('open');

    document.getElementById('wx-result-name').textContent = _sourceName;
    document.getElementById('wx-result-meta').textContent = '';
    document.getElementById('wx-spinner').style.display   = '';
    document.getElementById('wx-error').style.display     = 'none';
    document.getElementById('wx-charts-area').style.display = 'none';

    if (_isMobForecastTab()) {
      _embedInMobTab();
    } else {
      // Restore wx-box to modal if it was moved to the tab panel
      if (_wxBox.parentElement !== _resultEl) {
        _resultEl.appendChild(_wxBox);
        document.getElementById('wx-close-btn').style.display = '';
        document.getElementById('wx-change-dt-btn').style.display = 'none';
      }
      _resultEl.classList.add('open');
    }

    [_tChart, _wChart, _pChart].forEach(c => c?.destroy());
    _tChart = _wChart = _pChart = null;

    try {
      const resp = await fetch(_endpoint, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          source:     _source,
          source_id:  _sourceId,
          start_time: new Date(val).toISOString(),
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      _ensureChartJs(() => _render(data));
    } catch (e) {
      document.getElementById('wx-spinner').style.display = 'none';
      const el = document.getElementById('wx-error');
      el.textContent = `Could not load forecast: ${e.message}`;
      el.style.display = 'block';
    }
  }

  document.getElementById('wx-ok-btn').onclick = () => {
    if (!document.getElementById('wx-datetime').value) { alert('Please select a date and time'); return; }
    _submitForecast();
  };

  // On touch devices the native date picker can only be dismissed via its Done
  // ("checkmark") button, so blur reliably means Done was tapped → auto-submit.
  // We skip this on desktop where blur fires on any focus loss.
  const _dtInput = document.getElementById('wx-datetime');
  _dtInput.addEventListener('blur', () => {
    if (navigator.maxTouchPoints < 1) return;
    if (_dtInput.value && _pickerEl.classList.contains('open')) _submitForecast();
  });

  document.getElementById('wx-close-btn').onclick = () => _resultEl.classList.remove('open');
  document.getElementById('wx-change-dt-btn').onclick = () => _pickerEl.classList.add('open');
  _resultEl.addEventListener('click', e => { if (e.target === _resultEl) _resultEl.classList.remove('open'); });

  // ── Render forecast ──────────────────────────────────────────────────────────
  function _render(data) {
    document.getElementById('wx-spinner').style.display = 'none';

    const metric = _metric();
    const dist = metric
      ? `${data.distance_km.toFixed(1)} km`
      : `${(data.distance_km * 0.621371).toFixed(1)} mi`;
    const h = Math.floor(data.duration_h);
    const m = Math.round((data.duration_h - h) * 60);
    const dur = h > 0 ? `${h}h ${m}m` : `${m}m`;
    const spd = metric
      ? `${data.avg_speed_kph.toFixed(1)} km/h`
      : `${(data.avg_speed_kph * 0.621371).toFixed(1)} mph`;
    document.getElementById('wx-result-meta').textContent = `${dist} · ${dur} · avg ${spd}`;

    const startDt = new Date(data.start_time_iso);
    document.getElementById('wx-result-datetime').textContent =
      'Forecast starting ' +
      startDt.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) +
      ' at ' + startDt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

    _drawRouteCanvas(document.getElementById('wx-route-canvas'), data.preview_points || []);

    const s = data.summary;
    document.getElementById('wx-stat-row').innerHTML = [
      ['Avg Temp', _fmtTemp(s.avg_temp_f)],
      ['Precip Chance', s.max_precip_prob != null ? `${s.max_precip_prob}%` : '—'],
      ['Avg Wind', _fmtWind(s.avg_wind_mph)],
    ].map(([lbl, val]) =>
      `<div class="wx-stat-card"><div class="wx-stat-val">${val}</div><div class="wx-stat-lbl">${lbl}</div></div>`
    ).join('');

    document.getElementById('wx-charts-area').style.display = 'block';

    const samples = data.samples;
    const labels  = samples.map(s => _fmtTime(s.time_iso));
    const GC = 'rgba(255,255,255,0.07)', FC = '#8e8e93';

    function _baseOpts(yTitle) {
      return {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend:  { position: 'bottom', labels: { color: '#f2f2f7', boxWidth: 12, padding: 10, font: { size: 11 } } },
          tooltip: { enabled: false },
        },
        scales: {
          x: { grid: { color: GC }, ticks: { color: FC, maxRotation: 0, autoSkipPadding: 20, font: { size: 10 } } },
          y: {
            grid:  { color: GC },
            ticks: { color: FC, font: { size: 10 } },
            ...(yTitle ? { title: { display: true, text: yTitle, color: FC, font: { size: 10 } } } : {}),
          },
        },
      };
    }

    // Temperature
    const tUnit = metric ? '°C' : '°F';
    _tChart = new Chart(document.getElementById('wx-temp-chart'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: `Temperature (${tUnit})`,
            data: samples.map(s => s.temp_f != null ? (metric ? +_f2c(s.temp_f).toFixed(1) : s.temp_f) : null),
            borderColor: '#0a84ff', backgroundColor: 'rgba(10,132,255,0.15)',
            fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2,
          },
          {
            label: `Feels Like (${tUnit})`,
            data: samples.map(s => s.feels_like_f != null ? (metric ? +_f2c(s.feels_like_f).toFixed(1) : s.feels_like_f) : null),
            borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.1)',
            fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2,
          },
        ],
      },
      options: _baseOpts(tUnit),
    });

    // Wind
    const wUnit = metric ? 'km/h' : 'mph';
    _wChart = new Chart(document.getElementById('wx-wind-chart'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: `Wind Speed (${wUnit})`,
            data: samples.map(s => s.wind_mph != null ? (metric ? +_mph2kph(s.wind_mph).toFixed(1) : s.wind_mph) : null),
            borderColor: '#30d158', backgroundColor: 'rgba(48,209,88,0.15)',
            fill: true, tension: 0.3, borderWidth: 2, pointRadius: 2,
          },
        ],
      },
      options: _baseOpts(wUnit),
    });

    // Precipitation + Cloud Cover
    _pChart = new Chart(document.getElementById('wx-precip-chart'), {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            type: 'bar',
            label: 'Precip. Probability (%)',
            data: samples.map(s => s.precip_prob),
            backgroundColor: 'rgba(10,132,255,0.5)',
            borderWidth: 0,
            yAxisID: 'y',
          },
          {
            type: 'line',
            label: 'Cloud Cover (%)',
            data: samples.map(s => s.cloud_cover),
            borderColor: '#8e8e93', backgroundColor: 'transparent',
            fill: false, tension: 0.3, borderWidth: 2, pointRadius: 0,
            yAxisID: 'y',
          },
        ],
      },
      options: {
        ..._baseOpts('%'),
        scales: {
          x: { grid: { color: GC }, ticks: { color: FC, maxRotation: 0, font: { size: 10 } } },
          y: { grid: { color: GC }, ticks: { color: FC, font: { size: 10 } }, min: 0, max: 100 },
        },
      },
    });

  }
})();
