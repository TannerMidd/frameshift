/** @import {BuilderModel, BuilderRule} from "./model.js" */
import { clear, html, render } from "../../core/html.js";
import { XB_EVENTS, XB_OPS } from "./catalog.js";

/**
 * @param {HTMLElement} wrap
 * @param {BuilderModel} model
 */
export function renderBuilderRules(wrap, model) {
  clear(wrap);
  model.rules.forEach((rule, ruleIndex) => {
    const block = document.createElement("div");
    block.className = "xb-rule";
    const known = XB_EVENTS.some((event) => event.id === rule.event);
    const catalogEntry = XB_EVENTS.find((event) => event.id === (known ? rule.event : "")) || null;
    const fields = catalogEntry?.fields || [];
    const isAlert = rule.action.type === "alert";
    const eventOptions = XB_EVENTS.map(
      (event) =>
        html`<option value="${event.id}" ${known && event.id === rule.event ? html`selected` : ""}>
          ${event.label}
        </option>`,
    );
    const conditions = rule.conditions.map(
      (condition, conditionIndex) =>
        html`<div class="xb-row xb-cond">
          <span class="xb-kw">${conditionIndex === 0 ? "IF" : "AND"}</span>
          <input
            type="text"
            data-xb="cond-field"
            data-ri="${ruleIndex}"
            data-ci="${conditionIndex}"
            list="xb-fields-${ruleIndex}"
            placeholder="Field"
            value="${condition.field}"
          />
          <select data-xb="cond-op" data-ri="${ruleIndex}" data-ci="${conditionIndex}">
            ${XB_OPS.map(
              (operator) =>
                html`<option
                  value="${operator.id}"
                  ${operator.id === condition.op ? html`selected` : ""}
                >
                  ${operator.label}
                </option>`,
            )}
          </select>
          ${
            ["exists", "absent"].includes(condition.op)
              ? ""
              : html`<input
                  type="text"
                  data-xb="cond-value"
                  data-ri="${ruleIndex}"
                  data-ci="${conditionIndex}"
                  placeholder="Value"
                  value="${condition.value}"
                />`
          }
          <button
            type="button"
            class="hb hb-utility hb-sm"
            data-xb="cond-remove"
            data-ri="${ruleIndex}"
            data-ci="${conditionIndex}"
            title="Remove condition"
          >
            ✕
          </button>
        </div>`,
    );
    render(
      block,
      html`<div class="xb-rule-head">
          <span class="xb-rule-n">RULE ${ruleIndex + 1}</span>
          ${
            model.rules.length > 1
              ? html`<button
                  type="button"
                  class="hb hb-utility hb-sm"
                  data-xb="rule-remove"
                  data-ri="${ruleIndex}"
                >
                  ✕ REMOVE
                </button>`
              : ""
          }
        </div>
        <div class="xb-row">
          <span class="xb-kw">WHEN</span>
          <select data-xb="event" data-ri="${ruleIndex}">
            ${eventOptions}
            <option value="__custom__" ${known ? "" : html`selected`}>Custom event…</option>
          </select>
          ${
            known
              ? ""
              : html`<input
                  type="text"
                  data-xb="custom-event"
                  data-ri="${ruleIndex}"
                  placeholder="Journal event name"
                  value="${rule.customEvent || rule.event}"
                />`
          }
        </div>
        <div class="xb-conditions">${conditions}</div>
        <datalist id="xb-fields-${ruleIndex}">
          ${fields.map((field) => html`<option value="${field}"></option>`)}
        </datalist>
        <button type="button" class="hb hb-utility hb-sm" data-xb="cond-add" data-ri="${ruleIndex}">
          ＋ CONDITION
        </button>
        <div class="xb-row">
          <span class="xb-kw">THEN</span>
          <select data-xb="action-type" data-ri="${ruleIndex}">
            <option value="alert" ${isAlert ? html`selected` : ""}>Show a cockpit alert</option>
            <option value="objective" ${isAlert ? "" : html`selected`}>Suggest an objective</option>
          </select>
          ${
            isAlert
              ? html`<select data-xb="action-level" data-ri="${ruleIndex}">
                  ${["info", "warn", "critical"].map(
                    (level) =>
                      html`<option
                        value="${level}"
                        ${rule.action.level === level ? html`selected` : ""}
                      >
                        ${level.toUpperCase()}
                      </option>`,
                  )}
                </select>`
              : html`<input
                  type="text"
                  data-xb="action-category"
                  data-ri="${ruleIndex}"
                  placeholder="Category (optional)"
                  value="${rule.action.category}"
                />`
          }
        </div>
        <div class="xb-row xb-msgrow">
          ${
            isAlert
              ? html`<input
                  type="text"
                  data-xb="action-text"
                  data-ri="${ruleIndex}"
                  maxlength="500"
                  placeholder="Alert text — {FieldName} inserts a value from the event"
                  value="${rule.action.text}"
                />`
              : html`<input
                  type="text"
                  data-xb="action-title"
                  data-ri="${ruleIndex}"
                  maxlength="240"
                  placeholder="Objective title — {FieldName} inserts a value from the event"
                  value="${rule.action.title}"
                />`
          }
        </div>
        ${
          isAlert
            ? html`<label class="check xb-voice"
                ><input
                  type="checkbox"
                  data-xb="action-voice"
                  data-ri="${ruleIndex}"
                  ${rule.action.voice ? html`checked` : ""}
                />
                Also speak it (voice callout)</label
              >`
            : ""
        }
        ${
          fields.length
            ? html`<div class="xb-chips dim">
                Insert:
                ${fields.map(
                  (field) =>
                    html`<button
                      type="button"
                      class="chip"
                      data-xb="chip"
                      data-ri="${ruleIndex}"
                      data-field="${field}"
                    >
                      {${field}}
                    </button>`,
                )}
              </div>`
            : ""
        }`,
    );
    wrap.appendChild(block);
  });
}

/**
 * @param {HTMLElement} target
 * @param {BuilderModel} model
 * @param {() => void} rerender
 */
export function handleBuilderRuleClick(target, model, rerender) {
  const kind = target.dataset.xb;
  const ruleIndex = Number(target.dataset.ri);
  const rule = model.rules[ruleIndex];
  if (!kind || !rule) return false;
  if (kind === "rule-remove") {
    model.rules.splice(ruleIndex, 1);
    rerender();
  } else if (kind === "cond-add") {
    rule.conditions.push({ field: "", op: "eq", value: "" });
    rerender();
  } else if (kind === "cond-remove") {
    rule.conditions.splice(Number(target.dataset.ci), 1);
    rerender();
  } else if (kind === "chip") {
    const selector = `[data-xb="${
      rule.action.type === "alert" ? "action-text" : "action-title"
    }"][data-ri="${ruleIndex}"]`;
    const input = target.closest(".xb-rule")?.querySelector(selector);
    if (input instanceof HTMLInputElement) {
      input.value += `{${target.dataset.field || ""}}`;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
  } else {
    return false;
  }
  return true;
}

/**
 * @param {HTMLElement} target
 * @param {BuilderModel} model
 * @param {() => void} rerender
 */
export function handleBuilderRuleInput(target, model, rerender) {
  const kind = target.dataset.xb;
  const rule = model.rules[Number(target.dataset.ri)];
  if (!kind || !rule) return;
  const condition = rule.conditions[Number(target.dataset.ci)];
  const value =
    target instanceof HTMLInputElement || target instanceof HTMLSelectElement ? target.value : "";
  if (kind === "event") {
    if (value === "__custom__") {
      rule.event = "";
      rule.customEvent = "";
    } else {
      rule.event = value;
    }
    rerender();
  } else if (kind === "custom-event") {
    rule.customEvent = value;
    rule.event = "";
  } else if (kind === "cond-field" && condition) {
    condition.field = value.trim();
  } else if (kind === "cond-op" && condition) {
    condition.op = /** @type {import("./model.js").BuilderOperator} */ (value);
    rerender();
  } else if (kind === "cond-value" && condition) {
    condition.value = value;
  } else if (kind === "action-type") {
    rule.action.type = value === "objective" ? "objective" : "alert";
    rerender();
  } else if (kind === "action-level") {
    rule.action.level = value;
  } else if (kind === "action-text") {
    rule.action.text = value;
  } else if (kind === "action-title") {
    rule.action.title = value;
  } else if (kind === "action-category") {
    rule.action.category = value;
  } else if (kind === "action-voice" && target instanceof HTMLInputElement) {
    rule.action.voice = target.checked;
  }
}
