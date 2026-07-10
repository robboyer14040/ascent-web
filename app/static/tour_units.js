// Shared units formatter for the tour pages (tour.html + tour_share.html).
// Each page does `const U = makeUnits(<use_metric bool>)`. Formatting matches
// the (canonical) tour_share behavior: km to 1 decimal, plain precip suffix.
function makeUnits(metric) {
  const U = {
    metric:  metric,
    distS:   mi  => U.metric ? (mi*1.60934).toFixed(1)+' km'   : mi.toFixed(1)+' mi',
    climbS:  ft  => U.metric ? Math.round(ft*0.3048)+' m'      : Math.round(ft)+' ft',
    speedS:  mph => U.metric ? (mph*1.60934).toFixed(1)+' km/h': mph.toFixed(1)+' mph',
    tempS:   f   => U.metric ? +((f-32)*5/9).toFixed(1)+' °C'  : Math.round(f)+' °F',
    windS:   kph => U.metric ? Math.round(kph)+' km/h'         : Math.round(kph/1.60934)+' mph',
    precipS: mm  => U.metric ? mm+' mm'                        : (mm/25.4).toFixed(2)+'"',
    // Owner page refreshes the metric pref from settings; the share page never calls this.
    async load() {
      try { const p = await fetch('/api/settings/training-zones').then(r=>r.json()); U.metric=!!p.use_metric; }
      catch(e) { U.metric=false; }
    },
  };
  return U;
}
