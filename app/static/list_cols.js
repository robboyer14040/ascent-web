'use strict';
/* list_cols.js — THE definition of the activity/stage table columns.

   This list exists in exactly one place. Every page that shows a column table
   (Activities, Tours, Tour_shared) builds its columns from it via
   buildListCols(), so the "Visible Columns" picker offers the same choices, in
   the same order, with the same labels everywhere.

   What is shared: which columns exist, their order, labels, alignment, and the
   unit each is measured in. What stays per-page: the default width (a tour's
   sidebar is far narrower than the Activities pane), the sort key, and the cell
   renderer (an activity row and a tour stage hold different shapes of data).

   A page that cannot supply a value for a column still offers it — the default
   renderer prints an em-dash. That keeps the picker identical rather than
   quietly differing per page.

   Depends on a global `U` (units) exposing distUnit() / climbUnit() /
   speedUnit(); both main.js and tour_units.js provide it. */

const LIST_COLS = [
  {id:'user',     label:'User',      align:'left'},
  {id:'date',     label:'Date/Time', align:'left'},
  {id:'name',     label:'Title',     align:'left'},
  {id:'dist',     label:'Dist',      align:'right', unit:'dist'},
  {id:'active',   label:'MovTime',   align:'right'},
  {id:'duration', label:'Duration',  align:'right'},
  {id:'climb',    label:'Ascent',    align:'right', unit:'climb'},
  {id:'mvspd',    label:'MvSpd',     align:'right', unit:'speed'},
  {id:'avgspd',   label:'AvgSpd',    align:'right', unit:'speed'},
  {id:'hr',       label:'HR',        align:'right'},
  {id:'maxhr',    label:'MaxHR',     align:'right'},
  {id:'power',    label:'Power',     align:'right'},
  {id:'cadence',  label:'Cadence',   align:'right'},
  {id:'calories', label:'Cal',       align:'right'},
  {id:'suffer',   label:'Suffer',    align:'right'},
  {id:'pace',     label:'Pace',      align:'right'},
];

const LIST_COL_IDS = LIST_COLS.map(c => c.id);

/* Build a page's column array from the canonical list.

   spec = {
     w:      {id: '78px', ...},   // per-page default width (required per id)
     sort:   {id: 'start_time'},  // sort key; omitted ids are unsortable
     render: {id: row => html},   // omitted ids render an em-dash
     label:  {name: 'Stage'},     // optional per-page label override
     fallbackW: '60px',           // width for ids missing from spec.w
   } */
function buildListCols(spec) {
  const w = spec.w || {}, sort = spec.sort || {}, render = spec.render || {}, label = spec.label || {};
  return LIST_COLS.map(c => ({
    id:        c.id,
    label:     label[c.id] || c.label,
    align:     c.align,
    titleCell: c.id === 'name',
    unitLabel: c.unit ? () => U[c.unit + 'Unit']() : undefined,
    w:         w[c.id] || spec.fallbackW || '60px',
    sort:      sort[c.id] || '',
    render:    render[c.id] || (() => '—'),
  }));
}
