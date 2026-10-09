'use strict';
/* stage_table_fixture.js — loaded AFTER list_cols.js but BEFORE stage_table.js
   (see run.sh). Two jobs:

   1. Supply the globals stage_table.js expects from its host page: `U` (the
      tour pages build theirs with makeUnits) and `stageColor`.
   2. Seed localStorage with preferences in the OLD per-page column ids, so that
      stage_table.js's one-time migration runs for real at load. The assertions
      live in test_stage_table.js — they can only observe the migration if the
      stored values are already in place when the module is loaded. */

var U = makeUnits(false);

function stageColor(s) { return s.completion ? '#ef4444' : '#1e40af'; }

// StageTable.render() asks the nav which layout to draw; the tests want the
// desktop column table, not the phone card list.
var TourNav = { isPhone: function () { return false; } };

// What a browser would be holding from before the columns were shared:
//   'duration' meant moving time, 'speed' meant average moving speed,
//   'num' is a long-retired column, and 'name' was stored as a flexible track.
var LEGACY_STAGE_COLS   = ['name','dist','climb','date','duration','speed','num'];
var LEGACY_STAGE_WIDTHS = { name: 'minmax(384px,1fr)', duration: '64px', speed: '70px' };

localStorage.setItem('ascent-stage-cols',       JSON.stringify(LEGACY_STAGE_COLS));
localStorage.setItem('ascent-stage-col-widths', JSON.stringify(LEGACY_STAGE_WIDTHS));
localStorage.removeItem('ascent-stage-cols-v');   // not yet migrated
