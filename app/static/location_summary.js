// location_summary.js — shared "Location Summary" modal used by the Tours,
// Tour-share, and Activity INFO panes. Turns a place name (and optional
// coordinates) into a clickable link that opens a one-page summary popup with
// a photo, a description, and links to the sources used.
//
// Data comes from Wikipedia's public, CORS-enabled APIs:
//   • REST page summary  → title, extract paragraph, lead photo, article URL
//   • query/search       → resolve a place name to the best matching article
//   • query/geosearch    → resolve coordinates to the nearest article (fallback)
//
// Public API (window.LocationSummary):
//   open({name, lat, lon, label})   open the modal for a place
//   linkHTML(label, {name,lat,lon}) → anchor HTML for a single location
//   linkifyNames(text)              → linkify an "A → B → C" trajectory string
//
// Links are wired via event delegation on `.loc-link`, so callers only need to
// drop the returned HTML into the DOM — no per-call handler wiring.
window.LocationSummary = (function () {
  'use strict';

  const WIKI_API  = 'https://en.wikipedia.org/w/api.php';
  const WIKI_REST = 'https://en.wikipedia.org/api/rest_v1/page/summary/';
  const SEP = ' → ';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function fetchJSON(url, timeoutMs) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs || 8000);
    try {
      const r = await fetch(url, { signal: ctrl.signal });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      return null;
    } finally {
      clearTimeout(t);
    }
  }

  // A Wikidata one-line description that marks an article as an actual place
  // (settlement, administrative area, or natural feature).
  // Note: no trailing \b — several entries are stems (e.g. "municipalit" must
  // also match "municipality"), which a closing word boundary would break.
  const PLACE_DESC = /\b(municipalit|cit(y|ies)|town|village|commune|count(y|ies)|region|province|prefectur|district|settlement|communit|administrativ|capital|localit|borough|hamlet|neighbourhood|neighborhood|suburb|metropolis|metropolitan|urban|human settlement|island|isle|mountain|peak|summit|hill|lake|river|valley|glacier|national park|range|plateau|massif|resort|seat of|place in|place located)/i;

  // A description (or title) that marks an article as NOT a place — an event,
  // work, organisation, or person that merely shares the name.
  const NON_PLACE_DESC = /\b(scandal|elections?|referendum|actor|actress|player|footballer|coach|singer|musician|writer|author|poet|painter|politician|film|movie|album|song|single|tv series|series|team|club|f\.?c\.?|tournament|championship|\bcup\b|league|festival|season|company|corporation|band|novel|video game|battle|war|treaty|dynasty|manuscript|species|genus|deity|mytholog|saint|monarch|king|queen|emperor|president|minister|general)\b/i;

  // A leading 4-digit year almost always means an event/edition article.
  const YEAR_TITLE = /\b(1[6-9]\d\d|20\d\d)\b/;

  // The place names come from GPS points on the ride, so the real article is
  // always close to the clicked point. A coordinate-bearing candidate farther
  // than this from the proximity hint is a same-named place on another
  // continent (e.g. "Quesada, Spain" vs "Quesada, Costa Rica") and is rejected
  // so the modal shows an honest "no summary" instead of a place 1000s of km
  // away. Generous enough to cover a long ride's spread around its midpoint hint.
  const MAX_NEAR_KM = 300;

  // Approximate great-circle distance in km (equirectangular; fine at this scale).
  function kmApart(a, b) {
    const dLat = (a.lat - b.lat) * 111;
    const dLon = (a.lon - b.lon) * 111 * Math.cos((a.lat + b.lat) * Math.PI / 360);
    return Math.hypot(dLat, dLon);
  }

  // From a result set, choose the article that best represents the clicked
  // place. An exact title match to `prefer` wins; otherwise, among the
  // place-type results, the one nearest `near` ({lat,lon}) when given, else the
  // highest-ranked. Non-places are never selected, so a search that surfaces
  // "2015 Albanian local elections" resolves to nothing here and the caller
  // falls through to a cleaner query.
  function pickPlace(pages, prefer, near) {
    if (!pages) return null;
    const cands = Object.values(pages)
      .sort((a, b) => (a.index || 0) - (b.index || 0))
      .map(p => {
        const co = p.coordinates && p.coordinates[0];
        return {
          title: p.title || '',
          desc: p.description || '',
          disambig: !!(p.pageprops && p.pageprops.disambiguation !== undefined),
          lat: co ? co.lat : null,
          lon: co ? co.lon : null,
        };
      });
    const isBad   = c => c.disambig || NON_PLACE_DESC.test(c.desc) || YEAR_TITLE.test(c.title);
    const isPlace = c => PLACE_DESC.test(c.desc);
    // With a proximity hint, a coordinate-bearing candidate beyond MAX_NEAR_KM
    // is a same-named place elsewhere on Earth — never the one that was clicked.
    const hasNear = !!(near && near.lat != null);
    const tooFar  = c => hasNear && c.lat != null && kmApart(c, near) > MAX_NEAR_KM;
    // An exact title match wins — but only if it actually looks like a place, so
    // an empty-description redirect ("San Jose" → the disambiguation page) is
    // skipped in favour of the real settlement ("San Jose, California"). A match
    // that sits far from the clicked point is a same-named place elsewhere.
    if (prefer) {
      const pl = prefer.trim().toLowerCase();
      const exact = cands.find(c => c.title.toLowerCase() === pl && !isBad(c)
        && (isPlace(c) || c.lat != null) && !tooFar(c));
      if (exact) return exact.title;
    }
    const places = cands.filter(c => isPlace(c) && !isBad(c));
    if (!places.length) return null;
    // Disambiguate same-named places by proximity to the clicked location. The
    // nearest place still has to be within MAX_NEAR_KM — otherwise the real
    // place simply isn't in the result set and we return null (honest miss).
    if (hasNear) {
      const withCoord = places.filter(c => c.lat != null);
      if (withCoord.length) {
        withCoord.sort((a, b) => kmApart(a, near) - kmApart(b, near));
        return kmApart(withCoord[0], near) <= MAX_NEAR_KM ? withCoord[0].title : null;
      }
    }
    return places[0].title;
  }

  // Title-anchored search (matches on the article title, not full text), so a
  // place name can't drift to a big unrelated article that merely mentions it.
  async function prefixSearchTitle(query, prefer, near) {
    const d = await fetchJSON(WIKI_API + '?action=query&format=json&origin=*'
      + '&generator=prefixsearch&gpslimit=6&prop=coordinates|description|pageprops&ppprop=disambiguation&colimit=max&gpssearch='
      + encodeURIComponent(query));
    return pickPlace(d && d.query && d.query.pages, prefer, near);
  }

  // Full-text search, then keep only place-type articles by description.
  async function searchGeoTitle(query, prefer, near) {
    const d = await fetchJSON(WIKI_API + '?action=query&format=json&origin=*'
      + '&generator=search&gsrnamespace=0&gsrlimit=8&prop=coordinates|description|pageprops&ppprop=disambiguation&colimit=max&gsrsearch='
      + encodeURIComponent(query));
    return pickPlace(d && d.query && d.query.pages, prefer, near);
  }

  // Nearest place-type article to a coordinate (used when a name won't resolve).
  async function geosearchTitle(lat, lon) {
    const d = await fetchJSON(WIKI_API + '?action=query&format=json&origin=*'
      + '&generator=geosearch&ggsradius=15000&ggslimit=10&prop=coordinates|description|pageprops&ppprop=disambiguation&colimit=max&ggscoord='
      + encodeURIComponent(lat + '|' + lon));
    return pickPlace(d && d.query && d.query.pages, null, null);
  }

  // Localized administrative prefixes (e.g. Greek "Dimos", Albanian "Bashkia" =
  // municipality) that reverse-geocoders prepend to a place name but that
  // derail Wikipedia search. The bare last word is also tried as a fallback, so
  // this list need not be exhaustive.
  const ADMIN_PREFIX = /^(dimos|bashkia e|bashkia|komuna e|komuna|qarku i|qarku|rrethi i|rrethi|municipality of|municipality|city of|town of|commune de|commune of|comune di|città di|gemeinde|ciudad de|concello de|freguesia de|distrito de|kommune|kommun|obshtina|op[sš]tina|gmina|powiat|comuna|municipio de|municipio|prefecture of)\s+/i;

  // Split a "Place, Region, Country" label into a clean place token (admin
  // prefix removed), the last word of that token, and a trailing country used
  // to disambiguate.
  function parseLabel(name) {
    const parts = name.split(',').map(s => s.trim()).filter(Boolean);
    const bare = parts[0] || '';
    const place = bare.replace(ADMIN_PREFIX, '').trim() || bare;
    const last = place.split(/\s+/).pop() || '';
    const country = parts.length > 1 ? parts[parts.length - 1] : '';
    return { place, last, country, bare };
  }

  // Resolve a {name, lat, lon} to a Wikipedia article title for the geographic
  // place that was clicked — never a same-named event, team, or person, never a
  // big nearby city that outranks it, and never a random article: if no place
  // matches, return null so the modal shows an honest "no summary" instead.
  async function resolveTitle(opts) {
    const name = (opts.name || '').trim();
    const hasCoord = opts.lat != null && opts.lon != null;
    // Proximity hint: the clicked point's coordinate, used to disambiguate
    // same-named places (e.g. San Jose, California vs San José, Costa Rica).
    const near = hasCoord ? { lat: opts.lat, lon: opts.lon } : null;

    if (name) {
      const { place, last, country, bare } = parseLabel(name);
      const attempts = [];
      const add = (fn, q, prefer) => { if (q && q.trim()) attempts.push([fn, q.trim(), prefer]); };
      // Title-anchored first (most reliable), then place-filtered full text.
      add(prefixSearchTitle, place, place);
      if (last && last !== place) add(prefixSearchTitle, last, last);
      add(searchGeoTitle, country ? place + ' ' + country : place, place);
      if (place !== bare) add(searchGeoTitle, place, place);
      if (last && last !== place) add(searchGeoTitle, country ? last + ' ' + country : last, last);
      for (const [fn, q, prefer] of attempts) {
        const t = await fn(q, prefer, near);
        if (t) return t;
      }
    }
    // Coordinates → nearest place-type article. Skipped for a coarse hint (a
    // shared ride-midpoint from a trajectory string): geosearching it would
    // surface some place near the middle of the ride, not the clicked one, so
    // an unresolved name honestly reports "no summary" instead.
    if (hasCoord && !opts.coarse) {
      const t = await geosearchTitle(opts.lat, opts.lon);
      if (t) return t;
    }
    return null;
  }

  async function fetchSummary(title) {
    const d = await fetchJSON(WIKI_REST + encodeURIComponent(title.replace(/ /g, '_')));
    // Reject misses and disambiguation pages ("X may refer to…") — never a
    // real location summary.
    if (!d || d.type === 'https://mediawiki.org/wiki/HyperSwitch/errors/not_found'
      || d.type === 'disambiguation') return null;
    return d;
  }

  // ── Modal DOM (injected once) ────────────────────────────────────────────
  function ensureDOM() {
    if (document.getElementById('ls-overlay')) return;

    const style = document.createElement('style');
    style.textContent = `
.loc-link{color:#f97316;cursor:pointer;text-decoration:none;border-bottom:1px dotted currentColor}
.loc-link:hover{opacity:.8}
#ls-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:10000;align-items:center;justify-content:center;padding:20px;box-sizing:border-box}
#ls-card{background:var(--surface,#1b1d22);color:var(--text,#e8e8e8);border:1px solid var(--border2,#333);border-radius:12px;max-width:560px;width:100%;max-height:88vh;overflow:auto;box-shadow:0 12px 40px rgba(0,0,0,.5)}
#ls-card::-webkit-scrollbar{width:8px}#ls-card::-webkit-scrollbar-thumb{background:var(--border2,#444);border-radius:4px}
.ls-head{display:flex;align-items:flex-start;gap:10px;padding:16px 18px 0}
.ls-title{font-size:19px;font-weight:700;line-height:1.25;flex:1}
.ls-sub{font-size:12px;color:var(--muted,#999);margin-top:2px}
.ls-close{background:none;border:none;color:var(--muted,#999);font-size:22px;line-height:1;cursor:pointer;padding:2px 4px;flex-shrink:0}
.ls-photo{width:100%;max-height:280px;object-fit:cover;display:block;margin:14px 0 0}
.ls-body{padding:14px 18px 4px;font-size:14px;line-height:1.55}
.ls-sources{padding:6px 18px 18px;font-size:12px;color:var(--muted,#999);border-top:1px solid var(--border2,#333);margin-top:12px}
.ls-sources a{color:#f97316;text-decoration:none}.ls-sources a:hover{text-decoration:underline}
.ls-state{padding:26px 18px;text-align:center;color:var(--muted,#999);font-size:14px}`;
    document.head.appendChild(style);

    const el = document.createElement('div');
    el.innerHTML = `
<div id="ls-overlay" onclick="if(event.target===this)LocationSummary.close()">
  <div id="ls-card" role="dialog" aria-modal="true">
    <div class="ls-head">
      <div style="flex:1">
        <div class="ls-title" id="ls-title"></div>
        <div class="ls-sub" id="ls-sub"></div>
      </div>
      <button class="ls-close" onclick="LocationSummary.close()" aria-label="Close">✕</button>
    </div>
    <img id="ls-photo" class="ls-photo" alt="" style="display:none">
    <div id="ls-content"></div>
  </div>
</div>`;
    document.body.appendChild(el.firstElementChild);

    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && isOpen()) close();
    });
  }

  function isOpen() {
    const o = document.getElementById('ls-overlay');
    return !!o && o.style.display === 'flex';
  }

  function close() {
    const o = document.getElementById('ls-overlay');
    if (o) o.style.display = 'none';
  }

  let _reqId = 0;

  async function open(opts) {
    opts = opts || {};
    ensureDOM();
    const overlay = document.getElementById('ls-overlay');
    const label = opts.label || opts.name || 'Location';
    const reqId = ++_reqId;

    document.getElementById('ls-title').textContent = label;
    document.getElementById('ls-sub').textContent = '';
    const photo = document.getElementById('ls-photo');
    photo.style.display = 'none';
    photo.removeAttribute('src');
    document.getElementById('ls-content').innerHTML =
      '<div class="ls-state">Loading summary…</div>';
    overlay.style.display = 'flex';

    const title = await resolveTitle(opts);
    if (reqId !== _reqId) return;                         // superseded by a newer click

    const summary = title ? await fetchSummary(title) : null;
    if (reqId !== _reqId) return;

    if (!summary || !summary.extract) {
      const q = encodeURIComponent(opts.name || label);
      document.getElementById('ls-content').innerHTML =
        `<div class="ls-state">No summary was found for this location.<br>`
        + `<a class="ls-search" href="https://en.wikipedia.org/w/index.php?search=${q}" `
        + `target="_blank" rel="noopener" style="color:#f97316">Search Wikipedia →</a></div>`;
      return;
    }

    render(summary, label, opts);
  }

  function render(summary, label, opts) {
    document.getElementById('ls-title').textContent = summary.title || label;
    document.getElementById('ls-sub').textContent =
      summary.description || (summary.title && summary.title !== label ? label : '');

    const img = (summary.originalimage && summary.originalimage.source)
             || (summary.thumbnail && summary.thumbnail.source);
    const photo = document.getElementById('ls-photo');
    if (img) {
      photo.src = img;
      photo.alt = summary.title || label;
      photo.style.display = 'block';
    }

    const sources = [];
    const page = summary.content_urls && summary.content_urls.desktop
              && summary.content_urls.desktop.page;
    if (page) sources.push(`<a href="${esc(page)}" target="_blank" rel="noopener">Wikipedia: ${esc(summary.title)}</a>`);
    if (opts.lat != null && opts.lon != null) {
      const osm = `https://www.openstreetmap.org/?mlat=${opts.lat}&mlon=${opts.lon}#map=13/${opts.lat}/${opts.lon}`;
      sources.push(`<a href="${esc(osm)}" target="_blank" rel="noopener">OpenStreetMap</a>`);
    }

    document.getElementById('ls-content').innerHTML =
      `<div class="ls-body">${esc(summary.extract)}</div>`
      + (sources.length
          ? `<div class="ls-sources"><strong>Sources:</strong> ${sources.join(' · ')}</div>`
          : '');
  }

  // ── Link helpers ─────────────────────────────────────────────────────────
  // Anchor for a single location. `opts` may carry {name, lat, lon}; when
  // omitted the visible label doubles as the lookup name.
  function linkHTML(label, opts) {
    opts = opts || {};
    const name = opts.name != null ? opts.name : label;
    const attrs = [`class="loc-link"`, `data-loc-name="${esc(name)}"`];
    if (opts.lat != null) attrs.push(`data-loc-lat="${esc(opts.lat)}"`);
    if (opts.lon != null) attrs.push(`data-loc-lon="${esc(opts.lon)}"`);
    if (opts.coarse) attrs.push(`data-loc-coarse="1"`);
    return `<a ${attrs.join(' ')}>${esc(label)}</a>`;
  }

  // Render an ordered list of locations joined by SEP. Each item is
  // {label, name?, lat?, lon?}. A given place is linked only the FIRST time it
  // appears; later repeats render as plain text (no duplicate links).
  function linkifyList(items) {
    const seen = new Set();
    return (items || []).map(it => {
      const label = it.label != null ? it.label : it.name;
      const key = String(it.name != null ? it.name : label).trim().toLowerCase();
      if (seen.has(key)) return esc(label);
      seen.add(key);
      return linkHTML(label, it);
    }).join(SEP);
  }

  // Linkify an "A → B → C" trajectory string, one link per distinct place name.
  // An optional `near` ({lat,lon}) is attached to every link as a proximity
  // hint so same-named places resolve to the right region. It is a single
  // ride-midpoint shared by every name, so it is flagged `coarse`: good enough
  // to disambiguate a region, but not a per-place coordinate to geosearch from.
  function linkifyNames(text, near) {
    if (!text) return '';
    const items = String(text).split(SEP)
      .map(p => p.trim())
      .filter(Boolean)
      .map(p => ({ label: p, name: p, coarse: !!near,
                   lat: near ? near.lat : null, lon: near ? near.lon : null }));
    return linkifyList(items);
  }

  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('.loc-link');
    if (!a) return;
    e.preventDefault();
    open({
      name: a.dataset.locName,
      label: a.textContent,
      lat: a.dataset.locLat != null ? parseFloat(a.dataset.locLat) : null,
      lon: a.dataset.locLon != null ? parseFloat(a.dataset.locLon) : null,
      coarse: a.dataset.locCoarse === '1',
    });
  });

  return {
    open, close, linkHTML, linkifyNames, linkifyList,
    // Internals exposed for the static-JS regression suite (tests/js/). Not part
    // of the public API — do not use these from application code.
    __test: { parseLabel, pickPlace, resolveTitle },
  };
})();
