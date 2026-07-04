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
