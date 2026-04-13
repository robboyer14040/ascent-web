// ── Activity editing ──────────────────────────────────────────────────────────

async function startEditActivity(id) {
  const a = currentAct;
  if (!a || a.id !== id) return;
  const _nameVal = a.name || '';
  document.getElementById('edit-act-name').value       = _nameVal;
  document.getElementById('edit-act-name').style.display = 'none';
  document.getElementById('edit-act-name-display').textContent = _nameVal;
  document.getElementById('edit-act-name-display').style.display = '';
  document.getElementById('edit-act-textarea').value = a.notes || '';
  document.getElementById('edit-act-status').textContent = '';
  // Populate RPE slider
  const rpeSlider = document.getElementById('rpe-slider');
  const rpeLabel  = document.getElementById('rpe-val-label');
  if (a.perceived_exertion != null) {
    rpeSlider.value = a.perceived_exertion;
    rpeSlider.classList.remove('rpe-unset');
    rpeLabel.textContent = _rpeText(a.perceived_exertion);
  } else {
    rpeSlider.value = 5;
    rpeSlider.classList.add('rpe-unset');
    rpeLabel.textContent = '';
  }
  rpeSlider._rpeSet = (a.perceived_exertion != null);
  const helpEl = document.getElementById('suggest-help-text');
  if (helpEl) helpEl.style.display = 'none';

  // Populate activity type dropdown
  const typeEl = document.getElementById('edit-act-type');
  if (typeEl) {
    const current = a.local_sport_type || a.activity_type || '';
    let opts = STRAVA_SPORT_TYPES.includes(current) ? '' :
      (current ? `<option value="${escHtml(current)}">${escHtml(current)}</option>` : '<option value="">— Unknown —</option>');
    opts += STRAVA_SPORT_TYPES.map(t =>
      `<option value="${t}"${t === current ? ' selected' : ''}>${t}</option>`
    ).join('');
    typeEl.innerHTML = opts;
    if (current) typeEl.value = current;
  }

  // Open overlay immediately so user isn't waiting
  document.getElementById('edit-activity-overlay').classList.add('open');
  _updateSuggestBtn();

  // Populate gear dropdown async (shows "Loading…" until ready)
  const gearEl = document.getElementById('edit-act-gear');
  if (gearEl) {
    const gear = await fetchGear();
    const allGear = [...(gear.bikes || []), ...(gear.shoes || [])];
    const pendingId   = a.local_gear_id;   // null=not set, ""=clear, "bXXX"=set
    const currentName = a.equipment || '';
    let matched = false;
    let opts = `<option value="__keep__">— No change —</option><option value="">— None (clear) —</option>`;
    for (const g of allGear) {
      const sel = (pendingId != null ? pendingId === g.id : g.name === currentName);
      if (sel) matched = true;
      opts += `<option value="${escHtml(g.id)}"${sel ? ' selected' : ''}>${escHtml(g.name)}</option>`;
    }
    gearEl.innerHTML = opts;
    if (pendingId === '') {
      gearEl.value = '';           // pending "clear gear"
    } else if (matched) {
      // already selected in loop above
    } else {
      gearEl.value = '__keep__';   // no match — don't touch gear on save
    }
  }
}

function _updateSuggestBtn() {
  const btn = document.getElementById('suggest-title-btn');
  if (!btn) return;
  btn.disabled     = !_hasAnthropicKey;
  btn.style.opacity = _hasAnthropicKey ? '1' : '0.4';
  btn.style.cursor  = _hasAnthropicKey ? 'pointer' : 'not-allowed';
  btn.title = _hasAnthropicKey
    ? 'Generate a humorous title with AI'
    : 'Requires an Anthropic API key — see Settings';
}

function toggleSuggestHelp(e) {
  e.stopPropagation();
  const el = document.getElementById('suggest-help-text');
  if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
}

async function suggestTitle() {
  if (!_hasAnthropicKey) return;
  const id = currentAct && currentAct.id;
  if (!id) return;
  const btn    = document.getElementById('suggest-title-btn');
  const status = document.getElementById('edit-act-status');
  btn.disabled = true; btn.textContent = '⏳ Thinking…';
  status.textContent = '';
  try {
    const r = await fetch(`/api/activities/${id}/suggest-title`, { method: 'POST' });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      status.textContent = '✗ ' + (err.detail || 'AI error');
      status.style.color = '#ef4444';
      return;
    }
    const data = await r.json();
    document.getElementById('edit-act-name').value = data.title;
    document.getElementById('edit-act-name-display').textContent = data.title;
    activateTitleEdit();
    status.textContent = '✨ Title suggested — edit or save as-is';
    status.style.color = 'var(--muted)';
  } catch(e) {
    status.textContent = '✗ ' + e.message;
    status.style.color = '#ef4444';
  } finally {
    btn.disabled = !_hasAnthropicKey; btn.textContent = '✨ Suggest';
  }
}

function cancelEditActivity() {
  document.getElementById('edit-activity-overlay').classList.remove('open');
}

function activateTitleEdit() {
  const display = document.getElementById('edit-act-name-display');
  const input   = document.getElementById('edit-act-name');
  display.style.display = 'none';
  input.style.display   = '';
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

function deactivateTitleEdit() {
  const display = document.getElementById('edit-act-name-display');
  const input   = document.getElementById('edit-act-name');
  display.textContent   = input.value;
  display.style.display = '';
  input.style.display   = 'none';
}

function _rpeText(v) {
  v = parseInt(v);
  if (v <= 2)  return 'Easy (' + v + ')';
  if (v <= 4)  return 'Light (' + v + ')';
  if (v <= 6)  return 'Moderate (' + v + ')';
  if (v <= 8)  return 'Hard (' + v + ')';
  return 'Max Effort (' + v + ')';
}

function onRpeInput(val) {
  const slider = document.getElementById('rpe-slider');
  const label  = document.getElementById('rpe-val-label');
  slider._rpeSet = true;
  slider.classList.remove('rpe-unset');
  label.textContent = _rpeText(val);
}

function clearRpe() {
  const slider = document.getElementById('rpe-slider');
  const label  = document.getElementById('rpe-val-label');
  slider._rpeSet = false;
  slider.value = 5;
  slider.classList.add('rpe-unset');
  label.textContent = '';
}

async function saveEditActivity() {
  const id   = currentAct && currentAct.id;
  if (!id) return;
  const name = (document.getElementById('edit-act-name').value || '').trim();
  if (!name) {
    const st = document.getElementById('edit-act-status');
    st.textContent = 'Title is required'; st.style.color = '#ef4444';
    return;
  }
  const desc      = document.getElementById('edit-act-textarea').value;
  const status    = document.getElementById('edit-act-status');
  const saveBtn   = document.getElementById('edit-save-btn');
  saveBtn.disabled = true; saveBtn.textContent = 'Saving…';

  const typeEl    = document.getElementById('edit-act-type');
  const gearEl    = document.getElementById('edit-act-gear');
  const rpeSlider = document.getElementById('rpe-slider');
  const body = { name, description: desc };
  if (typeEl && typeEl.value) body.sport_type = typeEl.value;
  if (gearEl && gearEl.value !== '__keep__') {
    body.gear_id   = gearEl.value;   // "" = clear, "bXXX" = set
    body.gear_name = gearEl.value
      ? (gearEl.options[gearEl.selectedIndex]?.text || '')
      : '';
  }
  // Always include perceived_exertion (null clears it)
  body.perceived_exertion = rpeSlider._rpeSet ? parseInt(rpeSlider.value) : null;

  try {
    const r = await fetch(`/api/activities/${id}/update`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      status.textContent = '✗ ' + (err.detail || r.statusText);
      status.style.color = '#ef4444';
      return;
    }
    const updated = await r.json();

    // Update cached state
    const idx  = state.all.findIndex(a => a.id === id);
    if (idx  >= 0) state.all[idx]      = updated;
    const fidx = state.filtered.findIndex(a => a.id === id);
    if (fidx >= 0) state.filtered[fidx] = updated;
    currentAct = updated;

    cancelEditActivity();  // close overlay
    const infoDiv = document.getElementById('act-detail');
    if (infoDiv) { infoDiv.innerHTML = buildDetailHTML(updated); loadWeatherLocation(id); }
    renderVirtualList();
  } catch(e) {
    status.textContent = '✗ ' + e.message;
    status.style.color = '#ef4444';
  } finally {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
  }
}

let _saveRouteActId = null;

function openSaveRouteDialog(actId, actName) {
  const id   = actId   != null ? actId   : (currentAct ? currentAct.id   : null);
  const name = actName != null ? actName : (currentAct ? currentAct.name : 'Route');
  if (!id) return;
  _saveRouteActId = id;
  document.getElementById('save-route-name').value = name || 'Route';
  const msg = document.getElementById('save-route-msg');
  msg.style.display = 'none'; msg.textContent = '';
  const btn = document.getElementById('save-route-confirm');
  btn.disabled = false; btn.textContent = 'Save';
  document.getElementById('save-route-overlay').style.display = 'flex';
  setTimeout(() => document.getElementById('save-route-name').select(), 50);
}

function closeSaveRouteDialog() {
  document.getElementById('save-route-overlay').style.display = 'none';
  _saveRouteActId = null;
}

async function confirmSaveAsRoute() {
  if (!_saveRouteActId) return;
  const name          = document.getElementById('save-route-name').value.trim() || 'Route';
  const local_starred = document.getElementById('save-route-favorite').checked;
  const confirmBtn = document.getElementById('save-route-confirm');
  const msg        = document.getElementById('save-route-msg');
  confirmBtn.disabled = true; confirmBtn.textContent = 'Saving…';
  msg.style.display = 'none';
  try {
    const resp = await fetch(`/api/activities/${_saveRouteActId}/save-as-route`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, local_starred }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || resp.status);
    closeSaveRouteDialog();
    // Flash the button green
    const routeBtn = document.getElementById('save-route-btn');
    if (routeBtn) {
      routeBtn.textContent = '✓ Saved';
      routeBtn.style.color = '#22c55e';
      routeBtn.style.borderColor = '#22c55e';
    }
  } catch(e) {
    msg.style.display = 'block';
    msg.style.color = '#ef4444';
    msg.textContent = `Error: ${e.message}`;
    confirmBtn.disabled = false; confirmBtn.textContent = 'Save';
  }
}

async function resyncActivity(activityId) {
  const btn = document.getElementById('resync-btn');
  if (!btn || btn.disabled) return;
  const hadPendingEdits = btn.classList.contains('dirty');
  btn.disabled = true;
  btn.classList.add('spinning');

  try {
    const resp = await fetch(`/api/activities/${activityId}/resync`, { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      btn.disabled = false;
      btn.classList.remove('spinning');
      alert('Sync failed: ' + (err.detail || resp.statusText));
      return;
    }
    const updated = await resp.json();

    // Update the cached activity in state.all and state.filtered
    const idx = state.all.findIndex(a => a.id === activityId);
    if (idx >= 0) state.all[idx] = updated;
    const fidx = state.filtered.findIndex(a => a.id === activityId);
    if (fidx >= 0) state.filtered[fidx] = updated;

    // Re-render the detail panel and list row
    currentAct = updated;
    const infoDiv = document.getElementById('act-detail');
    if (infoDiv) {
      infoDiv.innerHTML = buildDetailHTML(updated);
      loadWeatherLocation(activityId);
      loadAISummary(activityId, true);
    }
    renderVirtualList();


    // Flash the new button green briefly to confirm
    const newBtn = document.getElementById('resync-btn');
    if (newBtn) {
      newBtn.classList.remove('spinning');
      newBtn.disabled = false;
      newBtn.innerHTML = hadPendingEdits ? '✓ Pushed & Synced' : '✓ Synced';
      newBtn.style.color = '#30d158';
      newBtn.style.borderColor = '#30d158';
      setTimeout(() => {
        newBtn.innerHTML = '<span class="resync-icon">↻</span> Sync';
        newBtn.style.color = '';
        newBtn.style.borderColor = '';
      }, 2000);
    }
  } catch(e) {
    alert('Sync error: ' + e.message);
    const b = document.getElementById('resync-btn');
    if (b) { b.disabled = false; b.classList.remove('spinning'); }
  }
}

async function openZones() {
  const overlay = document.getElementById('zones-overlay');
  const body    = document.getElementById('zones-body');
  const title   = document.getElementById('zones-title');
  const act     = currentAct;
  const charts  = currentCharts;
  if (!act) return;

  title.textContent = act.name || 'Zones';
  body.innerHTML = '<div style="color:var(--muted);padding:1rem;text-align:center">Loading…</div>';
  overlay.classList.add('open');

  // Fetch stored profile for real max_hr / ftp
  let profile = {};
  try { const r = await fetch('/api/settings/training-zones'); profile = await r.json(); } catch(e) {}

  let html = '';

  if (charts && charts.hr && charts.time && charts.hr.some(v=>v>0)) {
    const maxHR = profile.max_hr || act.max_heartrate || 190;
    const hrBounds = [Math.round(maxHR*.60), Math.round(maxHR*.70),
                      Math.round(maxHR*.80), Math.round(maxHR*.90)];
    const hrTimes  = computeZoneTimes(charts.hr, charts.time, hrBounds);
    html += renderZoneChart(hrTimes, `Heart Rate Zones (max ${maxHR} bpm)`, hrBounds, 'bpm');
  }

  if (charts && charts.power && charts.time && charts.power.some(v=>v>0)) {
    const ftp = profile.ftp_watts || (act.avg_power ? Math.round(act.avg_power / 0.75) : 0);
    if (ftp > 0) {
      const pwrBounds = [Math.round(ftp*.55), Math.round(ftp*.75),
                         Math.round(ftp*.90), Math.round(ftp*1.05)];
      const pwrTimes  = computeZoneTimes(charts.power, charts.time, pwrBounds);
      html += renderZoneChart(pwrTimes, `Power Zones (FTP ${ftp} W)`, pwrBounds, 'W');
    } else {
      html += '<div><div class="zones-section-title">Power Zones</div><div class="zones-no-data">Set your FTP in Settings → Training Profile to enable power zones</div></div>';
    }
  }

  if (!html) html = '<div class="zones-no-data">No heart rate or power data for this activity</div>';
  body.innerHTML = html;
}

function closeZones() {
  document.getElementById('zones-overlay').classList.remove('open');
}



