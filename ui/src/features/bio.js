/** @import {ApplicationState} from "../api/contracts/state.js" */
import { requireById } from "../core/dom.js";
import { fmtUnknownNumber } from "../core/fmt.js";
import { clear, html, render } from "../core/html.js";
import { appStore } from "../core/store.js";
import { fmtRange } from "./market.js";

/** @typedef {ApplicationState} BiologyApplicationState */

/** @param {string} id @returns {HTMLElement} */
const $ = (id) => /** @type {HTMLElement} */ (requireById(id));
const fmtNum = fmtUnknownNumber;

export const BIO_FIRST_TIP =
  "You're almost certainly the first commander to log this species here " +
  "(the body was undiscovered and nobody has reported it) — Vista Genomics pays 5× for a first log. " +
  "Confirmed only when you sell.";

export const BIO_FIRST_BODY_TIP =
  "Undiscovered body — you were first here, so any species you log " +
  "is almost certainly a first log (5× at Vista Genomics).";

/** @param {BiologyApplicationState|null} [snapshot] */
export function renderBio(
  snapshot = /** @type {BiologyApplicationState|null} */ (appStore.getSnapshot()),
) {
  if (!snapshot) return;
  const bio = snapshot.bio || {};

  // Exploration data card
  const ex = snapshot.exploration || { total: 0, count: 0, top: [] };
  $("explo-total").textContent = ex.count ? "≈" + fmtNum(ex.total) + " cr" : "";
  $("explo-summary").textContent = ex.count
    ? `${ex.count} ${ex.count === 1 ? "body" : "bodies"} scanned · ${ex.mapped} mapped` +
      ` · ${ex.firsts} first ${ex.firsts === 1 ? "discovery" : "discoveries"}`
    : "";
  $("explo-empty").classList.toggle("hidden", (ex.count || 0) > 0);
  const exUl = $("explo-top");
  const exSig = JSON.stringify(ex.top);
  if (exUl.dataset.sig !== exSig) {
    exUl.dataset.sig = exSig;
    clear(exUl);
    for (const b of ex.top || []) {
      const li = document.createElement("li");
      render(
        li,
        html`<span
            >${b.body}
            <span class="sub"
              >${b.class || ""}${b.mapped ? " · mapped" : ""}${
                b.first ? " · first discovery" : ""
              }</span
            ></span
          >
          <span class="count">≈${fmtNum(b.value)} cr</span>`,
      );
      exUl.appendChild(li);
    }
  }

  // Sampling progress
  const sampCard = $("bio-sampling-card");
  const samp = bio.sampling;
  if (samp) {
    sampCard.classList.remove("hidden");
    const pct = Math.round((100 * (samp.progress || 0)) / 3);
    const sampPay = samp.value != null ? samp.value * (samp.first ? 5 : 1) : null;
    // Live clonal-colony distance: how far you've moved from the nearest
    // previous sample vs. the genus's required spacing. Green = clear.
    let distTemplate = html``;
    if (samp.min_dist_m != null) {
      const need = samp.colony_m;
      const clear = samp.clear === true;
      const pctd = need ? Math.min(100, Math.round((100 * samp.min_dist_m) / need)) : 0;
      distTemplate = html`<div class="samp-dist${clear ? " samp-ok" : ""}">
          <span class="samp-dist-num">${fmtNum(samp.min_dist_m)} m</span>
          ${need ? html`<span class="dim">of ${fmtNum(need)} m needed</span>` : ""}
          ${
            clear
              ? html`<span class="samp-badge">✓ CLEAR TO SAMPLE</span>`
              : samp.clear === false
                ? html`<span class="samp-badge samp-wait">KEEP MOVING</span>`
                : ""
          }
        </div>
        ${
          need
            ? html`<div class="seedbar">
                <div
                  style="height:100%;width:${pctd}%;background:${
                    clear ? "var(--good)" : "var(--orange)"
                  }"
                ></div>
              </div>`
            : ""
        }`;
    }
    render(
      $("bio-sampling"),
      html`<div class="route-line">
          <b>${samp.species}</b>
          ${samp.variant ? html`<span class="dim">${samp.variant}</span>` : ""}
          ${
            samp.first
              ? html`<span class="bio-first" title="${BIO_FIRST_TIP}">★ FIRST LOG ×5</span>`
              : ""
          }
          <span class="profit">${sampPay != null ? "+" + fmtNum(sampPay) + " cr" : ""}</span>
        </div>
        <div class="commodities">
          sample
          ${samp.progress}/3${samp.colony_m ? ` · move ≥ ${samp.colony_m} m between samples` : ""}
        </div>
        <div class="seedbar">
          <div style="height:100%;width:${pct}%;background:var(--good)"></div>
        </div>
        ${distTemplate}`,
    );
  } else {
    sampCard.classList.add("hidden");
  }

  // Vault
  const vault = bio.vault || { items: [], total: 0 };
  $("bio-vault-total").textContent = vault.items.length ? "≈" + fmtNum(vault.total) + " cr" : "";
  $("bio-vault-empty").classList.toggle("hidden", vault.items.length > 0);
  const ul = $("bio-vault");
  const vsig = JSON.stringify(vault.items);
  if (ul.dataset.sig !== vsig) {
    ul.dataset.sig = vsig;
    clear(ul);
    for (const s of vault.items) {
      const pay = (s.value || 0) * (s.first ? 5 : 1);
      const li = document.createElement("li");
      render(
        li,
        html`<span
            >${s.species}${
              s.first
                ? html` <span class="bio-first" title="${BIO_FIRST_TIP}">★ FIRST LOG ×5</span>`
                : ""
            }${s.body ? html` <span class="sub">${s.body}</span>` : ""}</span
          >
          <span class="count">+${fmtNum(pay)} cr</span>`,
      );
      ul.appendChild(li);
    }
  }

  // System signals table
  const rows = bio.system_signals || [];
  $("bio-empty").classList.toggle("hidden", rows.length > 0);
  const table = $("bio-table");
  table.classList.toggle("hidden", rows.length === 0);
  const tbody = /** @type {HTMLTableSectionElement} */ (table.querySelector("tbody"));
  const bsig = JSON.stringify(rows);
  if (tbody.dataset.sig === bsig) return;
  tbody.dataset.sig = bsig;
  clear(tbody);
  for (const b of rows) {
    const known = (b.genuses || []).map(
      (g) =>
        html`<div>
          ${g.name}
          <span class="sub"
            >${fmtRange(g.min_value, g.max_value)}${g.colony_m ? ` · ${g.colony_m} m` : ""}</span
          >
        </div>`,
    );
    // Exact species recorded by commanders who landed and scanned. This beats
    // both your own map and the community genus list: a surface scan only ever
    // reveals the genus, so a value range is the best either of those can do.
    const sampledList = b.community_species || [];
    // Only species somebody actually scanned are known. A body can carry more
    // signals than that, so say so rather than imply the list is the whole body.
    const unidentified = Math.max(0, (b.count || 0) - sampledList.length);
    const sampled = sampledList.length
      ? [
          ...sampledList.map(
            (s) =>
              html`<div class="community">
                ◆ ${s.name}
                <span class="sub"
                  >${fmtUnknownNumber(s.value)} cr${s.variant ? ` · ${s.variant}` : ""}${
                    s.colony_m ? ` · ${s.colony_m} m` : ""
                  } · ${s.sites} site${s.sites === 1 ? "" : "s"}</span
                >
              </div>`,
          ),
          unidentified
            ? html`<div class="sub">+${unidentified} signal${
                unidentified === 1 ? "" : "s"
              } nobody has identified yet</div>`
            : "",
          b.species_value && sampledList.length > 1
            ? html`<div class="sub">full survey ≈${fmtUnknownNumber(b.species_value)} cr</div>`
            : "",
        ]
      : [];
    // Community-mapped genuses (Spansh) for bodies you haven't DSS'd yourself.
    const community =
      !sampled.length && !known.length && (b.community_genuses || []).length
        ? (b.community_genuses?.map(
            (g) =>
              html`<div class="community">
                ◇ ${g.name}
                <span class="sub"
                  >${fmtRange(g.min_value, g.max_value)}${g.colony_m ? ` · ${g.colony_m} m` : ""} ·
                  community</span
                >
              </div>`,
          ) ?? [])
        : [];
    const predicted =
      !sampled.length && !known.length && !community.length && (b.predicted || []).length
        ? html`<div class="sub">
            predicted:
            ${b.predicted?.map(
              (g, index) =>
                html`${index ? ", " : ""}${g.name} (${fmtRange(g.min_value, g.max_value)})`,
            )}
          </div>`
        : "";
    const genuses =
      (sampled.length && sampled) ||
      (known.length && known) ||
      (community.length && community) ||
      predicted ||
      html`<span class="dim">${b.count ? "not mapped yet" : ""}</span>`;
    const tr = document.createElement("tr");
    render(
      tr,
      html`<td>
          ${b.body}${
            b.was_discovered === false
              ? html` <span class="bio-first" title="${BIO_FIRST_BODY_TIP}">★</span>`
              : ""
          }${b.landable === false ? html` <span class="sub">not landable</span>` : ""}${
            b.source === "community" ? html` <span class="sub">◇ community</span>` : ""
          }
        </td>
        <td class="num">${b.count || "?"}</td>
        <td>${genuses}</td>
        <td>
          ${b.planet_class || "?"}
          <div class="sub">${b.atmosphere || ""}</div>
        </td>
        <td class="num">${b.gravity_g != null ? b.gravity_g + " g" : "?"}</td>
        <td class="num">${b.temp_k != null ? b.temp_k + " K" : "?"}</td>`,
    );
    tbody.appendChild(tr);
  }
}
