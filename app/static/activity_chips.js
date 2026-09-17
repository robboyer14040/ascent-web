'use strict';
/* activity_chips.js — canonical activity stat-chips builder.
   Shared by every activity/stage detail view: the Activities INFO pane and tour
   completed-stage (via buildActivityDetailHTML in activity_detail.js), plus the
   public tour_share and activity_share pages.

   Always renders the FULL chip set; each chip's sub-value is filled in when the
   data is available (e.g. dual-unit subs, and the Avg Pwr "max N W" sub).

   Returns the inner HTML for a `.stats-grid` container (the caller supplies the
   grid wrapper, whose column sizing is page-specific). Args: activity object,
   units object U, esc(), fmtHMS(). */

function buildActivityStatChips(a, U, esc, fmtHMS) {
  function altPaceStr(mph, perMile) {
    if (!mph || mph <= 0) return null;
    const mins = perMile ? 60 / mph : 60 / (mph * 1.60934);
    const m = Math.floor(mins);
    const s = Math.round((mins - m) * 60);
    return `${m}:${String(s).padStart(2,'0')}/${perMile ? 'mi' : 'km'}`;
  }

  const chips = [
    ['Distance',  a.distance_mi       ? U.distS(a.distance_mi)                                         : null,
                  a.distance_mi       ? (U.metric ? (+a.distance_mi.toFixed(2))+' mi'
                                                  : (+(a.distance_mi*1.60934).toFixed(2))+' km') : null],
    ['Mov Time',  a.active_time       ? fmtHMS(a.active_time)                                          : null, null],
    ['Duration',  a.duration          ? fmtHMS(a.duration)                                             : null, null],
    ['Ascent',    a.total_climb_ft    ? U.climbS(a.total_climb_ft)                                     : null,
                  a.total_climb_ft    ? (U.metric ? Math.round(a.total_climb_ft)+' ft'
                                                  : Math.round(a.total_climb_ft*0.3048)+' m') : null],
    ['Descent',   a.total_descent_ft  ? U.climbS(a.total_descent_ft)                                   : null,
                  a.total_descent_ft  ? (U.metric ? Math.round(a.total_descent_ft)+' ft'
                                                  : Math.round(a.total_descent_ft*0.3048)+' m') : null],
    ['Mov Spd',   a.avg_speed_mph     ? U.speedS(a.avg_speed_mph)                                      : null,
                  a.avg_speed_mph     ? (U.metric ? (+a.avg_speed_mph.toFixed(1))+' mph'
                                                  : (+(a.avg_speed_mph*1.60934).toFixed(1))+' km/h') : null],
    ['Avg Spd',   (a.duration&&a.distance_mi) ? U.speedS(+(a.distance_mi/(a.duration/3600)).toFixed(1)) : null,
                  (a.duration&&a.distance_mi) ? (()=>{ const mph=+(a.distance_mi/(a.duration/3600)).toFixed(1);
                                                        return U.metric ? mph+' mph' : (+(mph*1.60934).toFixed(1))+' km/h'; })() : null],
    ['Avg Pace',  a.avg_speed_mph     ? altPaceStr(a.avg_speed_mph, !U.metric)                         : null,
                  a.avg_speed_mph     ? altPaceStr(a.avg_speed_mph,  U.metric)                         : null],
    ['Avg HR',    a.avg_heartrate     ? Math.round(a.avg_heartrate)+' bpm'                             : null, null],
    ['Max HR',    a.max_heartrate     ? Math.round(a.max_heartrate)+' bpm'                             : null, null],
    ['Cadence',   a.avg_cadence       ? Math.round(a.avg_cadence)+' rpm'                               : null, null],
    ['Avg Pwr',   a.avg_power         ? Math.round(a.avg_power)+' W'                                   : null,
                  a.max_power         ? 'max '+Math.round(a.max_power)+' W'                            : null],
    ['Suffer',    a.suffer_score      ? Math.round(a.suffer_score)+''                                  : null, null],
    ['Type',      a.activity_type     ? esc(a.activity_type)                                           : null, null],
    ['Equipment', a.equipment         ? esc(a.equipment)                                               : null, null],
  ].filter(([,v])=>v);

  return chips.map(([l,v,sub]) => {
    const span = l==='Equipment' ? Math.min(6, Math.max(1, Math.ceil(v.length/14))) : 1;
    const s = span>1 ? ` style="grid-column:span ${span}"` : '';
    return `<div class="stat-chip"${s}><div class="sc-label">${l}</div><div class="sc-val">${v}</div><div class="sc-sub">${sub||''}</div></div>`;
  }).join('');
}

/* ── AI summary copy button ───────────────────────────────────────────────────
   Used by the AI Stage Summary / AI Summary cards on the tour and tour-share
   stage detail. Copies the heading plus the summary text. navigator.clipboard
   needs a secure context, so plain-http (LAN testing) falls back to execCommand. */
function aiCopyBtnHtml(btnId, title, textElId, size) {
  const s = size || 16;
  return `<button id="${btnId}" title="Copy summary" onclick="copyAiSummary('${btnId}','${title}','${textElId}')" style="background:none;border:none;cursor:pointer;color:var(--text);padding:1px;line-height:0;flex-shrink:0;display:inline-flex;align-items:center" tabindex="-1">` +
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>`;
}

function copyAiSummary(btnId, title, textElId) {
  const el = document.getElementById(textElId);
  if (!el || el.classList.contains('ai-card-loading')) return;   // still generating
  const text = (el.innerText || el.textContent || '').trim();
  if (!text) return;
  const payload = `${title}\n\n${text}`;
  const btn = document.getElementById(btnId);
  const flash = ok => {
    if (!btn) return;
    const prev = btn.innerHTML;
    btn.innerHTML = `<span style="font-size:15px;line-height:1;color:${ok ? '#22c55e' : '#ef4444'}">${ok ? '\u2713' : '\u2715'}</span>`;
    setTimeout(() => { btn.innerHTML = prev; }, 1200);
  };
  const fallback = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = payload;
      ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      flash(ok);
    } catch (e) { flash(false); }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(payload).then(() => flash(true), fallback);
  else fallback();
}
