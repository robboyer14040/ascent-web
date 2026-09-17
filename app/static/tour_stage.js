'use strict';
/* tour_stage.js — pure stage-route helpers shared by the tour pages
   (tour.html, tour_share.html). Loads after common.js (biggestClimbInfo uses _haversineMi). */

// Returns {gainFt, startKm} for the biggest climb in a [[lat,lon,alt_ft],...] array.
// A climb begins when gradient exceeds 3% and ends when gradient is flat/negative
// for more than 250 ft of horizontal distance. Altitude is smoothed (5-pt moving
// average) before gradient calculation to suppress GPS noise.
function biggestClimbInfo(pts) {
  if(!pts?.length || pts[0].length < 3) return {gainFt:0, startKm:0};

  // 5-point moving-average altitude smoothing
  const raw  = pts.map(p => p[2] || 0);
  const half = 2;
  const alts = raw.map((_, i) => {
    let s = 0, c = 0;
    for(let j = Math.max(0,i-half); j <= Math.min(raw.length-1,i+half); j++) { s+=raw[j]; c++; }
    return s/c;
  });

  // Cumulative distance in km, one entry per point
  const cumKm = [0];
  for(let i = 1; i < pts.length; i++)
    cumKm.push(cumKm[i-1] + _haversineMi(pts[i-1][0],pts[i-1][1],pts[i][0],pts[i][1]) * 1.60934);

  const START_GRAD  = 3;    // % gradient required to begin a climb
  const END_DIST_FT = 250;  // ft of flat/negative gradient before climb is considered over

  let bestGain = 0, bestStartKm = 0;
  let inClimb = false;
  let climbStartKm = 0, climbStartAlt = 0, climbMaxAlt = 0;
  let flatFt = 0;

  for(let i = 1; i < pts.length; i++) {
    const dAltFt  = alts[i] - alts[i-1];
    const dDistFt = (cumKm[i] - cumKm[i-1]) * 3280.84;  // km → ft
    const grad    = dDistFt > 0 ? (dAltFt / dDistFt) * 100 : 0;

    if(!inClimb) {
      if(grad > START_GRAD) {
        inClimb       = true;
        climbStartKm  = cumKm[i-1];
        climbStartAlt = alts[i-1];
        climbMaxAlt   = Math.max(alts[i-1], alts[i]);
        flatFt        = 0;
      }
    } else {
      if(grad > 0) {
        flatFt      = 0;
        climbMaxAlt = Math.max(climbMaxAlt, alts[i]);
      } else {
        flatFt += dDistFt;
        if(flatFt > END_DIST_FT) {
          const gain = climbMaxAlt - climbStartAlt;
          if(gain > bestGain) { bestGain = gain; bestStartKm = climbStartKm; }
          inClimb = false; flatFt = 0;
        }
      }
    }
  }
  // Flush a climb that runs all the way to the end of the route
  if(inClimb) {
    const gain = climbMaxAlt - climbStartAlt;
    if(gain > bestGain) { bestGain = gain; bestStartKm = climbStartKm; }
  }

  return {gainFt: bestGain, startKm: bestStartKm};
}

// Reverse-geocode a lat/lon to a "City, Country" string (or null on failure/empty).
async function reverseGeocode(lat, lon) {
  try {
    const r = await fetch(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=en`);
    const d = await r.json();
    return [d.city||d.locality||d.principalSubdivision, d.countryName].filter(Boolean).slice(0,2).join(', ') || null;
  } catch(e) { return null; }
}

// ── Alternate-route grouping ──────────────────────────────────────────────────
// Group adjacent stages that share BOTH their start and end location AND retrace
// at least half of one another — i.e. alternate versions of the same segment.
// Returns an array of groups (each a list of stages, in order); most groups hold
// a single stage. Start comes from start_lat/lon, end + full path from `cache`
// ({stageId: [[lat,lon,alt],...]}). A stage's `alt_override` forces the call:
// 0 = always its own segment, 1 = always an alternate of the preceding stage.
// Terminal-point helpers, shared with _riddenTwinIds below.
const _SEG_WIN = 15;       // points at each end to scan for convergence
const _segAllPts   = (s, cache) => cache[String(s.id)] || [];
const _segFirstPts = (s, cache) => _segAllPts(s, cache).slice(0, _SEG_WIN);
const _segLastPts  = (s, cache) => _segAllPts(s, cache).slice(-_SEG_WIN);
const _segStartPt = (s, cache) => (s.start_lat != null && s.start_lon != null)
  ? [s.start_lat, s.start_lon] : (_segFirstPts(s, cache)[0] || null);
const _segEndPt = (s, cache) => { const lp = _segLastPts(s, cache); return lp.length ? lp[lp.length - 1] : null; };
const _segNear = (a, b) => {
  if (!a || !b) return false;
  const dLat = (a[0] - b[0]) * 111000;
  const dLon = (a[1] - b[1]) * 111000 * Math.cos(a[0] * Math.PI / 180);
  return Math.hypot(dLat, dLon) < 150;   // metres
};
const _segNearAny = (pt, arr) => !!pt && arr.some(q => _segNear(pt, q));
// Alternate routes may diverge briefly at the start/finish before rejoining the
// shared path, so match a terminal point against a window of the other route's
// early/late points rather than comparing single endpoints.
const _segStartsMatch = (a, b, cache) =>
  _segNearAny(_segStartPt(a, cache), _segFirstPts(b, cache)) ||
  _segNearAny(_segStartPt(b, cache), _segFirstPts(a, cache));
const _segEndsMatch = (a, b, cache) =>
  _segNearAny(_segEndPt(a, cache), _segLastPts(b, cache)) ||
  _segNearAny(_segEndPt(b, cache), _segLastPts(a, cache));

// Mirrors the backend `_stage_segment_groups` — keep the two in sync.
function _stageSegmentGroups(stages, cache) {
  const OVERLAP_MIN = 0.5; // alternate must retrace >= this fraction of the original
  // Names usually mark an alternate by suffixing the original's stage number
  // ("BB24" / "BB24G"), so the pair resolves to the SAME display number. Sharing
  // a number plus both endpoints identifies an alternate that the overlap test
  // above misses, e.g. a gravel variant that leaves the paved route for most of
  // the day. The endpoint check keeps a same-numbered add-on ("35x Athens hotel")
  // from being swallowed by the stage it follows.
  const numMap = _stageNumMap(stages);
  const sameNamedSeg = (a, b) => {
    if (!numMap) return false;
    const na = numMap[a.stage_num];
    return na != null && na === numMap[b.stage_num]
        && _segStartsMatch(a, b, cache) && _segEndsMatch(a, b, cache);
  };
  const allPts = s => _segAllPts(s, cache);
  const nearAny = _segNearAny;
  // Cap the pairwise overlap work by sampling to ~150 points, like the backend.
  const sample = arr => {
    const step = Math.max(1, Math.floor(arr.length / 150));
    const out = [];
    for (let i = 0; i < arr.length; i += step) out.push(arr[i]);
    return out;
  };
  const overlapFrac = (orig, cand) => {   // fraction of orig's points that lie on cand
    const o = sample(allPts(orig)), c = sample(allPts(cand));
    if (!o.length || !c.length) return 0;
    let covered = 0;
    for (const p of o) if (nearAny(p, c)) covered++;
    return covered / o.length;
  };
  // a is the later candidate, b the anchor (original): the alternate must
  // retrace at least half of the original.
  const sameSeg = (a, b) =>
    _segStartsMatch(a, b, cache) && _segEndsMatch(a, b, cache) && overlapFrac(b, a) >= OVERLAP_MIN;
  // Anchor each group on the first occurrence of its [start, end]; a later stage
  // matching that anchor (and retracing it) is an alternate. `alt_override`
  // forces it: 0 → own group, 1 → join the previous stage's group.
  const groups = [];
  const groupOf = new Map();
  let prev = null;
  for (const s of stages) {
    let g;
    if (s.alt_override === 0) g = null;
    else if (s.alt_override === 1 && prev) g = groupOf.get(String(prev.id));
    else if (prev && sameNamedSeg(s, groupOf.get(String(prev.id))[0])) g = groupOf.get(String(prev.id));
    else g = groups.find(g => sameSeg(s, g[0]));
    if (g) g.push(s);
    else { g = [s]; groups.push(g); }
    groupOf.set(String(s.id), g);
    prev = s;
  }
  return groups;
}

// Collapse each same-segment group to its first (original) stage — for tour-wide
// stats we count such a group once, using the first-listed route.
function _dedupeStatStages(stages, cache) {
  return _stageSegmentGroups(stages, cache).map(g => g[0]);
}

// IDs (as strings) of the "alternative" routes — every stage after the first
// occurrence of its [start, end]. Excluded from stats and drawn dashed on the map.
function _altStageIds(stages, cache) {
  const alt = new Set();
  for (const grp of _stageSegmentGroups(stages, cache))
    for (const s of grp.slice(1)) alt.add(String(s.id));
  return alt;
}

// Stage numbering: when ONE naming scheme matches EVERY stage name in the tour,
// the number embedded in the name is the stage number shown everywhere (map
// badges, titles, nav, AI summaries); otherwise we fall back to list order
// (stage_num). Schemes, tried in order: (1) a leading number ("3 Alpe d'Huez");
// (2) a "Stage N" reference anywhere ("Stage 3"); (3) the SAME 1-4 letter code +
// a number at the start of every name ("CdP 1", "TdF15" — mixed codes don't
// qualify). Mirrors _stage_display_num() in tours.py — keep the two in sync.
const _LEADING_NUM_RE = /^\s*(\d+)/;
const _STAGE_WORD_RE  = /\bstage\s*(\d+)/i;
const _CODE_PREFIX_RE = /^\s*([A-Za-z]{1,4})\s*(\d+)/;

// scheme: every name matches `pat`; number is capture group 1. Returns a
// stage_num→number map, or null if any name fails to match.
function _mapByRegex(stages, pat) {
  const ms = stages.map(s => pat.exec(s.name || ''));
  if (!ms.every(Boolean)) return null;
  const map = {};
  stages.forEach((s, i) => { map[s.stage_num] = parseInt(ms[i][1], 10); });
  return map;
}
// scheme: every name starts with the SAME 1-4 letter code + a number.
function _mapByCodePrefix(stages) {
  const ms = stages.map(s => _CODE_PREFIX_RE.exec(s.name || ''));
  if (!ms.every(Boolean)) return null;
  if (new Set(ms.map(m => m[1].toLowerCase())).size !== 1) return null;
  const map = {};
  stages.forEach((s, i) => { map[s.stage_num] = parseInt(ms[i][2], 10); });
  return map;
}
// stage_num -> number embedded in the name, or null when no scheme fits them all.
function _stageNumMap(stages) {
  if (!Array.isArray(stages) || !stages.length) return null;
  return _mapByRegex(stages, _LEADING_NUM_RE)
      || _mapByRegex(stages, _STAGE_WORD_RE)
      || _mapByCodePrefix(stages);
}
function stageDisplayNum(stage, stages, fallback) {
  const map = _stageNumMap(stages);
  if (map && map[stage.stage_num] != null) return map[stage.stage_num];
  return fallback != null ? fallback : stage.stage_num;
}
