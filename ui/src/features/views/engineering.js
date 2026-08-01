/**
 * Static markup owned by the engineering feature surface.
 *
 * Values from APIs, journals, EDDN, localStorage, and forms never belong in
 * this template; dynamic content is rendered through core/html.js.
 */
export const engineeringView = String.raw`<div class="tabpane hidden" id="tab-engineering">
    <section class="card" id="engineers-card" data-arr="engineers">
      <div class="card-head">
        <div class="label">ENGINEERS <span class="dim">who upgrades your gear · from your journal</span></div>
        <div class="value dim" id="engineers-count"></div>
      </div>
      <div id="engineers-list"></div>
      <div id="engineers-empty" class="dim empty">No engineer progress logged yet — the game reports it when you
        next launch. <b>Engineers</b> are characters who upgrade modules far beyond shop quality; you unlock them
        one by one. Unlock requirements live on
        <a class="extlink" href="https://inara.cz/elite/engineers/" target="_blank" rel="noopener">Inara · Engineers ↗</a>.</div>
    </section>

    <section class="card" id="engplan-card" data-arr="engplanner">
      <div class="card-head">
        <div class="label">ENGINEERING WORKSHOP <span class="dim">complete offline catalog &middot; one shared wishlist</span></div>
        <div class="value dim" id="ep-catalog-count"></div>
      </div>
      <details class="eng-help">
        <summary>How this planner counts materials</summary>
        <p>Choose what you own now and the grade you want. At maximum engineer
        access, ship grades G1&ndash;G5 take exactly <b>1, 2, 3, 4 and 5
        applications</b>. Experimentals, synthesis, technology-broker unlocks
        and Odyssey work use their exact recipe.</p>
        <p>The shopping list combines ship materials, Odyssey locker contents
        and required commodities already in cargo. Material-trader suggestions
        are made only within a valid raw, manufactured or encoded trader
        column; Odyssey items, Guardian components and commodities are never
        presented as material-trader exchanges.</p>
      </details>
      <form id="engplan-form" class="engplan-form">
        <label class="ep-search-field">Search all engineering
          <input id="ep-search" type="search" autocomplete="off" placeholder="module, upgrade or engineer">
        </label>
        <label>Category
          <select id="ep-kind">
            <option value="">All categories</option>
          </select>
        </label>
        <label class="ep-item-field">Recipe
          <select id="ep-blueprint"></select>
        </label>
        <label id="ep-current-wrap">Current grade
          <select id="ep-current"></select>
        </label>
        <label id="ep-target-wrap">Target grade
          <select id="ep-target"></select>
        </label>
        <label>Quantity
          <input id="ep-quantity" type="number" min="1" max="99" value="1" inputmode="numeric">
        </label>
        <button type="submit" id="ep-pin" class="hb hb-primary" title="Add or update this item in the wishlist">ADD TO WISHLIST</button>
        <button type="button" id="ep-traders" class="hb hb-utility" title="Jump to the material finder and list the nearest raw, manufactured and encoded traders">FIND TRADERS</button>
      </form>
      <div class="ep-picker-meta">
        <div class="dim" id="ep-match-count"></div>
        <div id="ep-desc"></div>
      </div>
      <div id="engplan-summary"></div>
      <div id="engplan-list"></div>
      <div id="engplan-materials"></div>
    </section>

    <section class="card" id="matfinder-card" data-arr="matfinder">
      <div class="card-head">
        <div class="label">MATERIAL FINDER <span class="dim">where to get it &middot; where to trade for it</span></div>
      </div>
      <details class="eng-help">
        <summary>How material traders work</summary>
        <p>A <b>material trader</b> swaps materials you have for materials you
        want &mdash; but only <b>within one column</b>. Phosphorus sits in the raw
        <b>category 2</b> column with Molybdenum, Chromium and Technetium, so
        those three are the only things you can spend on it.</p>
        <p>Each grade step costs you: trading <b>down</b> a grade gives you
        <b>3&times;</b> as many, trading <b>up</b> a grade costs <b>6&times;</b> as
        many. Steps compound, so two grades down is 9&times; and two grades up is
        36&times;. Trading down is nearly always the better deal.</p>
      </details>
      <form id="mf-form" class="route-form">
        <label class="mf-material-field">Material
          <input id="mf-material" type="text" list="mf-material-list" autocomplete="off"
            placeholder="e.g. Phosphorus — or leave blank for all traders">
        </label>
        <datalist id="mf-material-list"></datalist>
        <label>Near
          <input id="mf-near" type="text" autocomplete="off" placeholder="current system">
        </label>
        <label id="mf-minshare-wrap">Min concentration
          <input id="mf-minshare" type="number" min="0" max="100" step="0.5" value="10"
            inputmode="decimal" title="Only show bodies where the material is at least this percentage of the surface composition">
        </label>
        <button type="submit" id="mf-go" class="hb hb-primary">FIND</button>
      </form>
      <div id="mf-answer"></div>
      <div id="mf-status" class="dim"></div>
      <div id="mf-bodies-wrap" class="hidden">
        <div class="label mf-section-label">WHERE TO PROSPECT IT <span class="dim">landable bodies &middot; via Spansh</span></div>
        <div id="mf-bodies-status" class="dim"></div>
        <div class="table-wrap">
          <table id="mf-bodies-table" class="hidden">
            <thead><tr>
              <th class="sortable" data-sort="body">Body</th>
              <th class="sortable" data-sort="subtype">Type</th>
              <th class="num sortable" data-sort="share">Share</th>
              <th class="sortable" data-sort="system">System</th>
              <th class="num sortable" data-sort="jump">Jump</th>
              <th class="num sortable" data-sort="dist_ls">Arrival</th>
              <th></th>
            </tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div id="mf-traders-wrap" class="hidden">
        <div class="label mf-section-label">NEAREST MATERIAL TRADERS <span class="dim">via Spansh</span></div>
        <div id="mf-traders-status" class="dim"></div>
        <div class="table-wrap">
          <table id="mf-traders-table" class="hidden">
            <thead><tr>
              <th class="sortable" data-sort="station">Station</th>
              <th class="sortable" data-sort="system">System</th>
              <th class="sortable" data-sort="kind">Trades</th>
              <th class="num sortable" data-sort="jump">Jump</th>
              <th class="num sortable" data-sort="dist_ls">Arrival</th>
              <th class="sortable" data-sort="pad">Pad</th>
              <th></th>
            </tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="card" id="materials-card" data-arr="materials">
      <div class="card-head">
        <div class="label">ENGINEERING MATERIALS <span class="dim">raw · manufactured · encoded · from your journal</span></div>
        <div class="value dim" id="materials-total"></div>
      </div>
      <div id="synth-line" class="dim synth-line hidden"
        title="FSD injection ('jumponium'): synthesize a one-jump range boost from raw materials — Basic +25%, Standard +50%, Premium +100%. Craft it in the ship's right-hand panel → Inventory → Synthesis. The counts show how many of each you could make right now."></div>
      <div id="materials-groups" class="materials"></div>
      <div id="materials-empty" class="dim empty">No materials recorded yet — the game logs your full inventory when you next launch it.</div>
    </section>

    <section class="card hidden" id="odyssey-card" data-arr="odyssey">
      <div class="card-head">
        <div class="label">ODYSSEY LOCKER <span class="dim">on-foot goods · assets · data — spent at bartenders and on-foot engineers for suit &amp; weapon upgrades</span></div>
        <div class="value dim" id="odyssey-total"></div>
      </div>
      <div id="odyssey-groups" class="materials"></div>
    </section>

  </div>`;
