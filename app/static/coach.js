// ── AI COACH ─────────────────────────────────────────────────────────────────

const coach = {
  hasGoal:      false,
  goalId:       null,
  goalText:     '',
  targetDate:   null,
  hasNewActs:   false,
  sending:      false,
};

function openCoach() {
  document.getElementById('root').style.display = 'none';
  const ov = document.getElementById('coach-overlay');
  ov.classList.add('open','page-mode');
  document.querySelectorAll('.nav-btn[data-page]').forEach(b => b.classList.toggle('nav-active', b.dataset.page === 'coach'));
  coachLoadState();
}

function closeCoach() {
  document.getElementById('root').style.display = '';
  const ov = document.getElementById('coach-overlay');
  ov.classList.remove('open','page-mode');
  document.querySelectorAll('.nav-btn[data-page]').forEach(b => b.classList.toggle('nav-active', b.dataset.page === 'activities'));
}

function coachOverlayClick(e) {
  if (e.target === document.getElementById('coach-overlay')) closeCoach();
}

// Auto-open coach when navigated here from another page via Coach nav button
if (sessionStorage.getItem('openCoach') === '1') {
  sessionStorage.removeItem('openCoach');
  document.addEventListener('DOMContentLoaded', () => openCoach());
}

async function coachLoadState() {
  try {
    const r = await fetch('/api/coach/state');
    const d = await r.json();

    coach.hasGoal    = d.has_goal;
    coach.hasNewActs = d.has_new_activities;

    // Update new-activity dot on toolbar button
    document.getElementById('coach-new-dot').style.display =
      (d.has_goal && d.has_new_activities) ? 'inline-block' : 'none';

    if (!d.has_goal) {
      coachShowSetup();
    } else {
      coach.goalId     = d.goal.id;
      coach.goalText   = d.goal.goal_text;
      coach.targetDate = d.goal.target_date || null;
      coachShowConversation();
      await coachLoadMessages();
      coachUpdateBanner();
    }
  } catch(e) {
    console.error('Coach state error', e);
  }
}

function coachShowSetup(editing = false) {
  document.getElementById('coach-setup').style.display          = 'flex';
  document.getElementById('coach-conversation').style.display   = 'none';
  document.getElementById('coach-change-goal-btn').style.display = 'none';
  document.getElementById('coach-new-activity-banner').classList.remove('visible');

  const cancelBtn = document.getElementById('coach-cancel-goal-btn');
  const goalEl    = document.getElementById('coach-goal-text');
  if (editing) {
    document.getElementById('coach-setup-title').textContent = 'Update Your Training Goal';
    document.getElementById('coach-setup-desc').textContent  = 'Edit your goal text or target date below. Saving will archive the current conversation and start a fresh coaching session.';
    document.getElementById('coach-set-goal-btn').textContent = 'Update Goal →';
    cancelBtn.style.display = 'inline-block';
    // Keep current goal visible in header while editing
    const dateLabel = coach.targetDate ? `  ·  Target: ${coach.targetDate}` : '';
    goalEl.textContent = (coach.goalText || '') + dateLabel;
    goalEl.style.display = 'block';
  } else {
    document.getElementById('coach-setup-title').textContent = 'Set Your Training Goal';
    document.getElementById('coach-setup-desc').textContent  = 'Tell your AI coach what you\'re working toward. Be as specific as you like — event type, date, daily distances, terrain, anything that helps.';
    document.getElementById('coach-goal-input').value        = '';
    document.getElementById('coach-target-date').value       = '';
    document.getElementById('coach-set-goal-btn').textContent = 'Start Coaching →';
    cancelBtn.style.display = 'none';
    goalEl.textContent = ''; goalEl.style.display = 'none';
  }
  document.getElementById('coach-set-goal-btn').disabled = false;
  // Restore saved model on setup screen
  const setupSel = document.getElementById('coach-setup-model');
  if (setupSel) setupSel.value = coachGetModel();
}

function coachChangeGoal() {
  // Pre-fill with current goal text and target date, then show setup in edit mode
  document.getElementById('coach-goal-input').value   = coach.goalText || '';
  document.getElementById('coach-target-date').value  = coach.targetDate || '';
  coachShowSetup(true);
}

function coachShowConversation() {
  document.getElementById('coach-setup').style.display            = 'none';
  document.getElementById('coach-conversation').style.display     = 'flex';
  document.getElementById('coach-conversation').style.flexDirection = 'column';
  document.getElementById('coach-conversation').style.overflow    = 'hidden';
  document.getElementById('coach-conversation').style.flex        = '1';
  document.getElementById('coach-change-goal-btn').style.display  = 'inline-block';
  // Show full goal text in the header card
  const goalEl = document.getElementById('coach-goal-text');
  goalEl.textContent = coach.goalText || '';
  goalEl.style.display = 'block';
  const targetDateEl = document.getElementById('coach-goal-target-date');
  if (coach.targetDate) {
    targetDateEl.textContent = 'Target Date: ' + coach.targetDate;
    targetDateEl.style.display = 'block';
  } else {
    targetDateEl.style.display = 'none';
  }
  // Restore saved model selection
  coachRestoreModel();
  // Load usage summary quietly
  coachLoadUsage();
}

function coachUpdateBanner() {
  const banner = document.getElementById('coach-new-activity-banner');
  if (coach.hasNewActs) {
    banner.classList.add('visible');
  } else {
    banner.classList.remove('visible');
  }
}

async function coachLoadMessages() {
  try {
    const r = await fetch('/api/coach/messages');
    const d = await r.json();
    const container = document.getElementById('coach-messages');
    container.innerHTML = '';
    if (!d.messages || d.messages.length === 0) return;
    d.messages.forEach(m => coachAppendMessage(m.role, m.content, m.created_at));
    coachScrollBottom();
  } catch(e) {
    console.error('Coach messages error', e);
  }
}

function coachAppendMessage(role, content, ts) {
  if (role === 'system') return;
  const container = document.getElementById('coach-messages');
  const wrap = document.createElement('div');
  wrap.className = `coach-msg ${role}`;

  const time = ts ? new Date(ts * 1000).toLocaleTimeString('en-US',
    {hour:'numeric', minute:'2-digit'}) : '';

  const avatar = role === 'assistant' ? '🧠' : '👤';

  wrap.innerHTML = `
    <div class="coach-avatar">${avatar}</div>
    <div style="display:flex;flex-direction:column;${role==='user'?'align-items:flex-end':''}">
      <div class="coach-bubble">${escHtml(content)}</div>
      ${time ? `<div class="coach-ts">${time}</div>` : ''}
    </div>`;
  container.appendChild(wrap);
}

function coachShowTyping() {
  const container = document.getElementById('coach-messages');
  const wrap = document.createElement('div');
  wrap.className = 'coach-msg assistant coach-typing';
  wrap.id = 'coach-typing-indicator';
  wrap.innerHTML = `
    <div class="coach-avatar">🧠</div>
    <div class="coach-bubble">
      <div class="coach-dots">
        <span></span><span></span><span></span>
      </div>
    </div>`;
  container.appendChild(wrap);
  coachScrollBottom();
}

function coachRemoveTyping() {
  document.getElementById('coach-typing-indicator')?.remove();
}

function coachScrollBottom() {
  const c = document.getElementById('coach-messages');
  c.scrollTop = c.scrollHeight;
}

async function coachSetGoal() {
  const text = document.getElementById('coach-goal-input').value.trim();
  if (!text) return;

  // Save selected model and sync to conversation toolbar
  const setupModel = document.getElementById('coach-setup-model').value;
  coachSaveModel(setupModel);

  const targetDate = document.getElementById('coach-target-date').value || null;

  const btn = document.getElementById('coach-set-goal-btn');
  btn.disabled = true;
  btn.textContent = 'Analysing your training…';

  try {
    const r = await fetch('/api/coach/goal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({goal_text: text, model: setupModel, target_date: targetDate}),
    });
    if (!r.ok) {
      const e = await r.json();
      alert('Error: ' + (e.detail || 'Unknown error'));
      btn.disabled = false;
      btn.textContent = 'Start Coaching →';
      return;
    }
    const d = await r.json();
    coach.goalId     = d.goal_id;
    coach.goalText   = text;
    coach.targetDate = targetDate;
    coach.hasGoal    = true;
    coach.hasNewActs = false;

    coachShowConversation();
    document.getElementById('coach-messages').innerHTML = '';
    coachAppendMessage('assistant', d.initial_message, Math.floor(Date.now()/1000));
    coachScrollBottom();
  } catch(e) {
    alert('Failed to set goal: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'Start Coaching →';
  }
}

async function coachSend() {
  if (coach.sending) return;
  const input = document.getElementById('coach-input');
  const msg   = input.value.trim();
  if (!msg) return;

  coach.sending = true;
  input.value   = '';
  coachInputResize(input);
  document.getElementById('coach-send-btn').disabled = true;

  // Optimistically append user message
  coachAppendMessage('user', msg, Math.floor(Date.now()/1000));
  coachScrollBottom();

  // Hide new-activity banner once user engages
  document.getElementById('coach-new-activity-banner').classList.remove('visible');
  document.getElementById('coach-new-dot').style.display = 'none';

  coachShowTyping();

  try {
    const r = await fetch('/api/coach/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg, model: coachGetModel()}),
    });
    coachRemoveTyping();
    if (!r.ok) {
      const e = await r.json();
      coachAppendMessage('assistant',
        '⚠️ Error: ' + (e.detail || 'Something went wrong. Please try again.'),
        Math.floor(Date.now()/1000));
    } else {
      const d = await r.json();
      coachAppendMessage('assistant', d.reply, Math.floor(Date.now()/1000));
      coach.hasNewActs = false;
    }
    coachScrollBottom();
  } catch(e) {
    coachRemoveTyping();
    coachAppendMessage('assistant',
      '⚠️ Network error — please check your connection and try again.',
      Math.floor(Date.now()/1000));
    coachScrollBottom();
  }

  coach.sending = false;
  document.getElementById('coach-send-btn').disabled = false;
  input.focus();
  // Refresh usage widget if it's open
  if (document.getElementById('coach-usage-toggle').classList.contains('open')) {
    coachLoadUsage();
  } else {
    // Quietly update the summary line without opening the panel
    coachLoadUsage();
  }
}

function coachInputKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    coachSend();
  }
}

function coachInputResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ── Usage widget ──────────────────────────────────────────────────────────────

let coachUsageData = null;

async function coachLoadUsage() {
  try {
    const r = await fetch('/api/coach/usage');
    if (!r.ok) return;
    coachUsageData = await r.json();
    coachRenderUsage();
  } catch(e) {
    console.warn('Coach usage fetch failed', e);
  }
}

function coachRenderUsage() {
  const d = coachUsageData;
  if (!d) return;

  const fmt = (n) => n < 0.01 && n > 0 ? '<$0.01' : '$' + n.toFixed(4);
  const fmtN = (n) => n >= 1000 ? (n/1000).toFixed(1) + 'k' : String(n);

  // Summary line in toggle button
  const mCost = d.this_month.cost_usd;
  document.getElementById('coach-usage-summary').textContent =
    `Coach usage: ${fmt(mCost)} this month · ${fmt(d.alltime.cost_usd)} all-time`;

  // Detail rows
  document.getElementById('u-month-cost').textContent     = fmt(mCost);
  document.getElementById('u-month-queries').textContent  = d.this_month.queries;
  document.getElementById('u-month-tokens').textContent   =
    fmtN(d.this_month.input_tokens) + ' in / ' + fmtN(d.this_month.output_tokens) + ' out';
  document.getElementById('u-alltime-cost').textContent    = fmt(d.alltime.cost_usd);
  document.getElementById('u-alltime-queries').textContent = d.alltime.queries;

  // Bar: show month spend relative to a $1 soft reference (since costs are tiny)
  // Scale: full bar = $1.00; above that bar turns warning colour
  const REF = 1.00;
  const pct  = Math.min(100, (mCost / REF) * 100);
  const fill = document.getElementById('u-bar-fill');
  fill.style.width = pct + '%';
  fill.classList.toggle('warn', mCost > REF * 0.8);

  // Monthly bar chart
  const chart  = document.getElementById('u-monthly-chart');
  const labels = document.getElementById('u-monthly-labels');
  chart.innerHTML  = '';
  labels.innerHTML = '';

  const breakdown = d.monthly_breakdown;
  if (breakdown.length === 0) {
    chart.innerHTML  = '<span style="font-size:11px;color:var(--muted2);align-self:center">No data yet</span>';
    return;
  }

  // Fill missing months so we always show 6 bars
  const allMonths = [];
  const now = new Date();
  for (let i = 5; i >= 0; i--) {
    const d2 = new Date(now.getFullYear(), now.getMonth() - i, 1);
    allMonths.push(d2.getFullYear() + '-' + String(d2.getMonth()+1).padStart(2,'0'));
  }

  const dataMap = {};
  breakdown.forEach(r => { dataMap[r.month] = r.cost_usd; });

  const values  = allMonths.map(m => dataMap[m] || 0);
  const maxVal  = Math.max(...values, 0.001);
  const curMonth = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0');

  allMonths.forEach((m, i) => {
    const bar = document.createElement('div');
    bar.className = 'u-monthly-bar' + (m === curMonth ? ' current' : '');
    const h = Math.max(2, Math.round((values[i] / maxVal) * 36));
    bar.style.height = h + 'px';
    bar.title = m + ': ' + fmt(values[i]);
    chart.appendChild(bar);

    const lbl = document.createElement('div');
    lbl.className = 'u-monthly-label';
    lbl.textContent = m.slice(5); // "MM"
    labels.appendChild(lbl);
  });
}

function coachToggleUsage(btn) {
  btn.classList.toggle('open');
  document.getElementById('coach-usage-detail').classList.toggle('open');
  // Load/refresh on first open
  if (btn.classList.contains('open')) coachLoadUsage();
}

// Refresh usage after each send — called at end of coachSend()

// ── Model selector ────────────────────────────────────────────────────────────

function coachSaveModel(val) {
  _uiPrefsSet('ascent-coach-model', val);
}

function coachGetModel() {
  return _uiPrefsGet('ascent-coach-model') || 'claude-haiku-4-5-20251001';
}

function coachRestoreModel() {
  const saved = coachGetModel();
  const sel   = document.getElementById('coach-model-select');
  if (sel) sel.value = saved;
}

// ── Training plan ─────────────────────────────────────────────────────────────

const PLAN_PROMPT = `Please generate a detailed, structured training plan based on my goal and recent training data.

The plan should include:
- A week-by-week progression from now until the event (or for the next 8–12 weeks if no specific date)
- Specific weekly targets for distance and elevation
- Key workout types each week (long ride/run, intervals, recovery, back-to-back days)
- A taper strategy in the final 1–2 weeks if applicable
- Any specific warnings or adjustments based on my current fitness level vs the goal demands

Format the plan clearly with weekly headers. Be specific with numbers — use my actual recent averages as the baseline.`;

async function coachRequestPlan() {
  const btn = document.getElementById('coach-plan-btn');
  btn.disabled = true;

  // Put the prompt in the input box visibly, then send
  const input = document.getElementById('coach-input');
  input.value = PLAN_PROMPT;
  coachInputResize(input);

  // Small delay so user sees it, then send
  await new Promise(r => setTimeout(r, 150));
  await coachSend();
  btn.disabled = false;
}

async function coachAskToday() {
  if (coach.sending) return;
  coach.sending = true;
  const btn = document.getElementById('coach-today-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Thinking…';
  coachShowTyping();
  coachScrollBottom();
  try {
    const model = coachGetModel();
    const r = await fetch(`/api/coach/today?model=${encodeURIComponent(model)}`);
    coachRemoveTyping();
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      coachAppendMessage('assistant', '⚠️ ' + (e.detail || 'Something went wrong.'), Math.floor(Date.now()/1000));
    } else {
      const d = await r.json();
      coachAppendMessage('assistant', d.advice, Math.floor(Date.now()/1000));
      if (d.activities && d.activities.length > 0) {
        _coachAppendTodayMaps(d.activities);
      }
    }
    coachScrollBottom();
  } catch(e) {
    coachRemoveTyping();
    coachAppendMessage('assistant', '⚠️ Network error — please try again.', Math.floor(Date.now()/1000));
    coachScrollBottom();
  } finally {
    coach.sending = false;
    btn.disabled = false;
    btn.textContent = '📍 What should I do today?';
  }
}

function _coachAppendTodayMaps(activities) {
  const container = document.getElementById('coach-messages');
  const wrap = document.createElement('div');
  wrap.className = 'coach-today-maps';
  activities.forEach(act => {
    const card = document.createElement('div');
    card.className = 'coach-today-card';
    card.title = act.name;
    card.onclick = () => { closeCoach(); selectActivity(act.id); };
    const mapId = `today-map-${act.id}-${Date.now()}`;
    const dist = act.distance_mi ? ` · ${(+act.distance_mi).toFixed(1)} mi` : '';
    card.innerHTML = `<div id="${mapId}" class="coach-today-map-el"></div>`
      + `<div class="coach-today-map-label">${escHtml(act.name)}${escHtml(dist)}</div>`;
    wrap.appendChild(card);
    // Init Leaflet after DOM is inserted
    requestAnimationFrame(() => {
      const el = document.getElementById(mapId);
      if (!el || !act.coords || act.coords.length < 2) return;
      const style = MAP_STYLES[_uiPrefsGet('ascent-map-style') || 'osm'] || MAP_STYLES['osm'];
      const m = L.map(el, {
        zoomControl:false, attributionControl:false,
        dragging:false, scrollWheelZoom:false, doubleClickZoom:false,
        keyboard:false, touchZoom:false, boxZoom:false,
      });
      L.tileLayer(style.url, {maxZoom:19}).addTo(m);
      const lls = act.coords.map(c => [c[1], c[0]]);
      L.polyline(lls, {color:'#ef4444', weight:2.5, opacity:.9, smoothFactor:2}).addTo(m);
      m.fitBounds(L.latLngBounds(lls), {padding:[8,8]});
    });
  });
  container.appendChild(wrap);
}


// Poll for new activities every 2 minutes while coach is open
setInterval(async () => {
  if (!document.getElementById('coach-overlay').classList.contains('open')) return;
  if (!coach.hasGoal) return;
  try {
    const r = await fetch('/api/coach/state');
    const d = await r.json();
    if (d.has_new_activities && !coach.hasNewActs) {
      coach.hasNewActs = true;
      coachUpdateBanner();
      document.getElementById('coach-new-dot').style.display = 'inline-block';
    }
  } catch(e) {}
}, 120_000);

// ── ZONES ────────────────────────────────────────────────────────────────────
const ZONE_COLORS = ['#22c55e','#86efac','#eab308','#f97316','#ef4444'];
const ZONE_NAMES  = ['Z1','Z2','Z3','Z4','Z5'];

function computeZoneTimes(valArr, timeArr, boundaries) {
  const times = [0,0,0,0,0];
  for (let i = 1; i < valArr.length; i++) {
    const v = valArr[i];
    if (!v || v <= 0) continue;
    const dt = timeArr[i] - timeArr[i-1];
    if (dt <= 0 || dt > 60) continue;
    let z = 4;
    for (let b = 0; b < boundaries.length; b++) { if (v <= boundaries[b]) { z = b; break; } }
    times[z] += dt;
  }
  return times;
}

function fmtZoneTime(s) {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = Math.floor(s%60);
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
  return `${m}:${String(ss).padStart(2,'0')}`;
}

function renderZoneChart(times, title, boundaries, unit) {
  const total = times.reduce((a,b)=>a+b,0);
  if (total === 0) return `<div class="zones-no-data">No ${title} data for this activity</div>`;
  const maxT = Math.max(...times);
  // Build range labels: [0, b0], [b0+1, b1], ..., [b3+1, ∞)
  const ranges = boundaries.map((b, i) => {
    const lo = i === 0 ? 1 : Math.round(boundaries[i-1]) + 1;
    const hi = Math.round(b);
    return `${lo} – ${hi} ${unit}`;
  });
  ranges.push(`${Math.round(boundaries[boundaries.length-1]) + 1}+ ${unit}`);

  let html = `<div><div class="zones-section-title">${title}</div>`;
  times.forEach((t, i) => {
    const pct  = total > 0 ? Math.round(t/total*100) : 0;
    const barW = maxT  > 0 ? Math.round(t/maxT*100)  : 0;
    html += `<div class="zone-row">
      <div class="zone-label" style="color:${ZONE_COLORS[i]}">${ZONE_NAMES[i]}<span class="zone-range">(${ranges[i]})</span></div>
      <div class="zone-bar-wrap">
        <div class="zone-bar" style="width:${barW}%;background:${ZONE_COLORS[i]}"></div>
      </div>
      <div class="zone-time">${fmtZoneTime(t)}</div>
      <div class="zone-pct">${pct}%</div>
    </div>`;
  });
  html += '</div>';
  return html;
}

