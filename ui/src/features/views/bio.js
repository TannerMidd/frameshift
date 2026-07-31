/**
 * Static markup owned by the bio feature surface.
 *
 * Values from APIs, journals, EDDN, localStorage, and forms never belong in
 * this template; dynamic content is rendered through core/html.js.
 */
export const bioView = String.raw`<div class="tabpane hidden" id="tab-bio">
    <section class="card" data-arr="biosignals">
      <div class="label">BIO SIGNALS — CURRENT SYSTEM <span class="dim">◆ = exact species someone already scanned, so its value is known · ◇ = genus only, so the value is a range · ★ = undiscovered body, logs there pay 5×</span></div>
      <div class="table-wrap">
        <table id="bio-table" class="hidden">
          <thead>
            <tr>
              <th>Body</th><th class="num">Signals</th><th>Species / genuses (value · sample spacing)</th>
              <th>Body type</th><th class="num">Gravity</th><th class="num">Temp</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
        <div id="bio-empty" class="dim empty">No biological signals detected in this system yet — honk (FSS discovery scan) and scan bodies to populate this.</div>
      </div>
    </section>

    <section class="card hidden" id="bio-sampling-card" data-arr="sampling">
      <div class="label">SAMPLING IN PROGRESS</div>
      <div id="bio-sampling"></div>
    </section>

    <section class="card" data-arr="explodata">
      <div class="card-head">
        <div class="label">EXPLORATION DATA <span class="dim">unsold cartographic value · estimates; first discovery ×2.6, mapping ×3.3</span></div>
        <div class="value orange" id="explo-total"></div>
      </div>
      <div class="commodities" id="explo-summary"></div>
      <ul id="explo-top" class="cargo-list"></ul>
      <div id="explo-empty" class="dim empty">No unsold scans — FSS some bodies and this fills in.</div>
    </section>

    <section class="card" data-arr="vault">
      <div class="card-head">
        <div class="label">UNSOLD SAMPLES <span class="dim">estimated Vista Genomics value · ★ = likely first log, pays 5× (confirmed when you sell)</span></div>
        <div class="value orange" id="bio-vault-total"></div>
      </div>
      <ul id="bio-vault" class="cargo-list"></ul>
      <div id="bio-vault-empty" class="dim empty">No completed samples on board.</div>
    </section>

    <section class="card" id="selldata-card" data-arr="selldata">
      <div class="label">WHERE TO SELL YOUR DATA <span class="dim">nearest ports with Universal Cartographics (map data) and Vista Genomics (bio samples) · via Spansh</span></div>
      <form id="sd-form" class="route-form">
        <label class="check" title="Fleet carriers can fit both services and often sit far out in the black — but they move, so a listed position may be stale. Untick to see only fixed starports.">
          <input type="checkbox" id="sd-carriers" checked> Include fleet carriers
        </label>
        <button type="submit" id="sd-go" class="hb hb-primary">FIND NEAREST</button>
      </form>
      <div id="sd-status" class="dim">Deep in the black and done exploring? This finds the closest place to turn your scans and samples into credits.</div>
      <div id="sd-results"></div>
    </section>
  </div>`;
