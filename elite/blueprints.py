"""Complete offline engineering wishlist and material planning.

Ship engineering uses Frontier's deterministic post-rebalance maximum-access
costs: finishing G1..G5 consumes 1, 2, 3, 4 and 5 applications respectively.
Other catalog items (experimentals, synthesis, unlocks and Odyssey work) use
their exact recipe once per requested item/step.

The legacy module name and public helpers are retained so v1 settings and the
journal's ready-to-engineer callout migrate without user action.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from . import engineering_catalog as catalog


ROLLS_PER_GRADE = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}

LEGACY_PIN_IDS = {
    "FSD Increased Range": "frame-shift-drive--increased-fsd-range",
    "Thrusters Dirty Tuning": "thrusters--dirty-drive-tuning",
    "weapon--manticore-opressor": "weapon--manticore-oppressor",
}

_TRADER_FAMILIES = {
    "raw": {f"Category{i}" for i in range(1, 8)},
    "manufactured": {
        "Alloys", "Capacitors", "Chemical", "Composite", "Conductive",
        "Crystals", "Heat", "MechanicalComponents", "Shielding", "Thermic",
    },
    "encoded": {
        "DataArchives", "EmissionData", "EncodedFirmware", "EncryptionFiles",
        "ShieldData", "WakeScans",
    },
}

# A material trader exchanges within one column only, one grade step at a time.
TRADE_UP_COST = 6     # spend 6 of grade N to gain 1 of grade N+1
TRADE_DOWN_YIELD = 3  # spend 1 of grade N to gain 3 of grade N-1

TRADER_KINDS = ("raw", "manufactured", "encoded")

# Catalog family identifiers are internal; these are the column names a
# commander actually reads.  Guardian and Thargoid families are listed too, so
# the UI can name the column it is refusing to trade rather than saying nothing.
# Keyed by (kind, family): GuardianRuinsActive, ThargoidShip and ThargoidSite
# each appear under two different kinds and must not be merged.
_FAMILY_LABELS = {
    **{("raw", f"Category{i}"): f"Raw category {i}" for i in range(1, 8)},
    ("manufactured", "Alloys"): "Alloys",
    ("manufactured", "Capacitors"): "Capacitors",
    ("manufactured", "Chemical"): "Chemical",
    ("manufactured", "Composite"): "Composite",
    ("manufactured", "Conductive"): "Conductive",
    ("manufactured", "Crystals"): "Crystals",
    ("manufactured", "GuardianRuinsActive"): "Guardian ruins",
    ("manufactured", "Heat"): "Heat",
    ("manufactured", "MechanicalComponents"): "Mechanical components",
    ("manufactured", "Shielding"): "Shielding",
    ("manufactured", "ThargoidShip"): "Thargoid ships",
    ("manufactured", "ThargoidSite"): "Thargoid structures",
    ("manufactured", "Thermic"): "Thermic",
    ("encoded", "DataArchives"): "Data archives",
    ("encoded", "EmissionData"): "Emission data",
    ("encoded", "EncodedFirmware"): "Encoded firmware",
    ("encoded", "EncryptionFiles"): "Encryption files",
    ("encoded", "GuardianRuins"): "Guardian obelisk data",
    ("encoded", "GuardianRuinsActive"): "Guardian blueprint segments",
    ("encoded", "ShieldData"): "Shield data",
    ("encoded", "ThargoidShip"): "Thargoid ships",
    ("encoded", "ThargoidSite"): "Thargoid structures",
    ("encoded", "WakeScans"): "Wake scans",
}


def _all_groups():
    return catalog.load()["groups"]


def _all_materials():
    return catalog.load()["materials"]


def _display_recipes():
    out = {}
    for group in _all_groups():
        if group.get("alias_of"):
            continue
        grades = {}
        for recipe in group["recipes"]:
            grade = int(recipe.get("grade") or 0)
            grades[grade] = {ingredient["name"]: ingredient["quantity"]
                             for ingredient in recipe["ingredients"]}
        out[group["display_name"]] = grades
    return out


# Compatibility exports.  Unlike the v1 starter set these expose the complete
# 505-group bundled catalog.
BLUEPRINTS = _display_recipes()
BLUEPRINT_INFO = {
    group["display_name"]: {
        "what": f"{catalog.KIND_LABELS.get(group['kind'], group['kind'])}: {group['name']}.",
        "engineer": ("Available from " + ", ".join(
            engineer for engineer in group["engineers"] if not engineer.startswith("@")
        ) + ".") if any(not engineer.startswith("@") for engineer in group["engineers"])
        else "Created through synthesis, a merchant or a technology broker.",
    }
    for group in _all_groups() if not group.get("alias_of")
}
MATERIALS = {
    item["name"]: (item["symbol"], item["kind"], item.get("family"), item.get("grade"))
    for item in _all_materials()
}
_BY_SYMBOL = {value[0]: name for name, value in MATERIALS.items()}


def _resolve_group(value) -> dict:
    text = str(value or "")
    group_id = LEGACY_PIN_IDS.get(text, text)
    found = catalog.load()["by_group"].get(group_id)
    if found:
        return found
    folded = text.casefold()
    for group in _all_groups():
        if folded in {group["display_name"].casefold(), group["name"].casefold()}:
            return group
    raise KeyError(f"Unknown engineering catalog item: {value}")


def normalize_wishlist(items) -> tuple[list[dict], bool]:
    """Return stable v2 wishlist records and whether persistence should update.

    Old ``{"name": ..., "grade": ...}`` pins are accepted and mapped to their
    catalog IDs.  Compatibility name/grade fields remain in each record so an
    in-flight journal watcher from an upgraded process also continues safely.
    """
    normalized = []
    for raw in items or ():
        if not isinstance(raw, Mapping):
            continue
        value = raw.get("id") or raw.get("name")
        try:
            group = _resolve_group(value)
        except KeyError:
            continue
        grades = list(group["grades"])
        kind = group["kind"]
        if kind == "ship-engineering":
            default_current, default_target = 0, max(grades)
        elif kind == "odyssey-upgrade":
            default_current, default_target = max(0, min(grades) - 1), max(grades)
        elif grades:
            default_current = 0
            default_target = grades[-1]
        else:
            default_current = default_target = 0
        try:
            current = int(raw.get("current_grade", default_current))
            target = int(raw.get("target_grade", raw.get("grade", default_target)))
            quantity = int(raw.get("quantity", 1))
        except (TypeError, ValueError):
            current, target, quantity = default_current, default_target, 1
        quantity = max(1, min(99, quantity))
        if grades:
            target = min(grades, key=lambda grade: abs(grade - target))
            if kind in {"ship-engineering", "odyssey-upgrade"}:
                current = max(0, min(current, target - 1))
            else:
                current = 0
        else:
            current = target = 0
        normalized.append({
            "id": group["id"],
            "name": group["display_name"],
            "current_grade": current,
            "target_grade": target,
            "grade": target,
            "quantity": quantity,
        })
    return normalized, normalized != list(items or ())


def material_source(family):
    for item in _all_materials():
        if item.get("family") == family:
            return _source_text(item)
    return None


def _source_text(item) -> str:
    parts = list(item.get("sources") or ())
    if item.get("settlement_types"):
        parts.append("Settlements: " + ", ".join(item["settlement_types"]))
    if item.get("building_types"):
        parts.append("Buildings: " + ", ".join(item["building_types"]))
    if item.get("container_types"):
        parts.append("Containers: " + ", ".join(item["container_types"]))
    if not parts:
        if item.get("kind") == "commodity":
            parts.append("Purchase from a commodity market and keep it in ship cargo.")
        elif item.get("kind") == "odyssey":
            parts.append("Search Odyssey settlements or take it as a mission reward.")
        elif item.get("kind") == "raw":
            parts.append("Surface prospecting and mining.")
        elif item.get("kind") == "manufactured":
            parts.append("Ship salvage, signal sources and mission rewards.")
        elif item.get("kind") == "encoded":
            parts.append("Ship, wake or surface-data scanning and mission rewards.")
    return "; ".join(dict.fromkeys(parts))


def material_info(name):
    item = catalog.material(name)
    if not item:
        raise KeyError(f"Unknown engineering material: {name}")
    family = item.get("family")
    can_trade = bool(
        item.get("grade") and family in _TRADER_FAMILIES.get(item.get("kind"), set())
    )
    return {
        "name": item["name"],
        "symbol": item["symbol"],
        "kind": item["kind"],
        "family": family,
        "grade": item.get("grade"),
        "source": _source_text(item),
        "sources": list(item.get("sources") or ()),
        "tradeable": can_trade,
    }


def column_key(kind, family) -> str:
    """Identify one material-trader column.  The kind is part of the key because
    the Guardian and Thargoid families exist under more than one kind."""
    return f"{kind}/{family}"


def material_reference() -> dict:
    """Every tradeable-kind material with the trader column it belongs to.

    Sent to the browser with the catalog so 'where do I get Phosphorus' is
    answered offline, without a round trip and without reimplementing the trade
    ratios in JavaScript.
    """
    materials, columns = [], {}
    for item in _all_materials():
        kind = item.get("kind")
        if kind not in TRADER_KINDS:
            continue
        info = material_info(item["symbol"])
        family = info["family"]
        materials.append({
            "name": info["name"],
            "symbol": info["symbol"],
            "kind": kind,
            "family": family,
            "grade": info["grade"],
            "tradeable": info["tradeable"],
            "source": info["source"],
        })
        if family and column_key(kind, family) not in columns:
            columns[column_key(kind, family)] = {
                "label": _FAMILY_LABELS.get(
                    (kind, family), f"{catalog.KIND_LABELS.get(kind, kind)} {family}"),
                "tradeable": family in _TRADER_FAMILIES.get(kind, set()),
            }
    materials.sort(key=lambda row: row["name"])
    return {
        "materials": materials,
        "material_columns": columns,
        "trade_ratios": {"up": TRADE_UP_COST, "down": TRADE_DOWN_YIELD},
    }


def _recipe_steps(group, current_grade, target_grade, quantity=1, rolls=None):
    quantity = max(1, int(quantity or 1))
    kind = group["kind"]
    recipes = sorted(group["recipes"], key=lambda recipe: int(recipe.get("grade") or 0))
    steps = []
    if kind == "ship-engineering":
        costs = rolls or ROLLS_PER_GRADE
        for recipe in recipes:
            grade = int(recipe.get("grade") or 0)
            if int(current_grade or 0) < grade <= int(target_grade):
                steps.append((recipe, int(costs.get(grade, grade)) * quantity))
    elif kind == "odyssey-upgrade":
        for recipe in recipes:
            grade = int(recipe.get("grade") or 0)
            if int(current_grade or 0) < grade <= int(target_grade):
                steps.append((recipe, quantity))
    else:
        selected = recipes
        if group["grades"] and target_grade:
            exact = [recipe for recipe in recipes if int(recipe.get("grade") or 0) == int(target_grade)]
            if exact:
                selected = exact
        steps.extend((recipe, quantity) for recipe in selected)
    return steps


def _requirements_by_symbol(group, current_grade, target_grade, quantity=1, rolls=None):
    need = {}
    for recipe, applications in _recipe_steps(group, current_grade, target_grade, quantity, rolls):
        for ingredient in recipe["ingredients"]:
            symbol = ingredient["symbol"]
            need[symbol] = need.get(symbol, 0) + int(ingredient["quantity"]) * applications
    return need


def requirements(blueprint, target_grade, rolls=None, current_grade=0, quantity=1):
    """Exact material bill from ``current_grade`` to ``target_grade``.

    The legacy call form ``requirements(name, target)`` remains supported.
    Returned keys are display names for compatibility with v1 consumers.
    """
    group = _resolve_group(blueprint)
    by_symbol = _requirements_by_symbol(group, current_grade, target_grade, quantity, rolls)
    return {material_info(symbol)["name"]: count for symbol, count in by_symbol.items()}


def normalize_inventory(inventory) -> dict[str, int]:
    """Merge journal materials, Odyssey locker and cargo into catalog symbols."""
    counts = {}

    def add(identifier, count, fallback=None):
        try:
            amount = max(0, int(count or 0))
        except (TypeError, ValueError):
            return
        symbol = catalog.canonical_material_symbol(identifier)
        if not symbol and fallback:
            symbol = catalog.canonical_material_symbol(fallback)
        if symbol and amount:
            counts[symbol] = counts.get(symbol, 0) + amount

    def visit(value, key_hint=None):
        if isinstance(value, Mapping):
            if "count" in value and (value.get("symbol") or value.get("name") or key_hint):
                add(value.get("symbol") or key_hint or value.get("name"), value.get("count"), value.get("name"))
                return
            for key, nested in value.items():
                if key == "total":
                    continue
                if isinstance(nested, (int, float)):
                    add(key, nested)
                else:
                    visit(nested, key)
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            for nested in value:
                visit(nested)

    visit(inventory or {})
    return counts


def inventory_from_snapshot(snapshot) -> dict[str, int]:
    """Build one inventory from AppState's materials, locker and ship cargo."""
    snap = snapshot or {}
    return normalize_inventory({
        "materials": snap.get("materials") or {},
        "locker": snap.get("ship_locker") or {},
        "cargo": snap.get("cargo_inventory") or [],
    })


def convertible(surplus, from_grade, to_grade):
    """Same-family material-trader output: 6:1 up and 1:3 down."""
    surplus = int(surplus or 0)
    if surplus <= 0 or not from_grade or not to_grade:
        return 0
    if from_grade > to_grade:
        return surplus * TRADE_DOWN_YIELD ** (from_grade - to_grade)
    if from_grade < to_grade:
        return surplus // TRADE_UP_COST ** (to_grade - from_grade)
    return surplus


def _cost_for(wanted, from_grade, to_grade):
    if from_grade > to_grade:
        return math.ceil(wanted / (TRADE_DOWN_YIELD ** (from_grade - to_grade)))
    return wanted * TRADE_UP_COST ** (to_grade - from_grade)


def _rows(need, inventory):
    rows = []
    for symbol, required in need.items():
        info = material_info(symbol)
        have = int(inventory.get(symbol, 0))
        rows.append({**info, "need": required, "have": have,
                     "deficit": max(0, required - have), "trade": None})
    return sorted(rows, key=lambda row: (row["deficit"] == 0, -(row.get("grade") or 0), row["name"]))


def _attach_trades(rows, inventory, reserved):
    """Allocate conservative, valid same-column trades without double-spend."""
    surplus = {symbol: max(0, count - reserved.get(symbol, 0))
               for symbol, count in inventory.items()}
    materials = _all_materials()
    for row in rows:
        if not row["deficit"] or not row["tradeable"]:
            continue
        best = None
        for source in materials:
            source_symbol = source["symbol"]
            if source_symbol == row["symbol"] or source.get("kind") != row["kind"]:
                continue
            if source.get("family") != row["family"] or not source.get("grade"):
                continue
            available = surplus.get(source_symbol, 0)
            gain = convertible(available, source["grade"], row["grade"])
            if gain <= 0:
                continue
            covered = min(gain, row["deficit"])
            spend = _cost_for(covered, source["grade"], row["grade"])
            candidate = {
                "from": source["name"], "from_symbol": source_symbol,
                "spend": spend, "covers": covered,
                "direction": "down" if source["grade"] > row["grade"] else "up",
            }
            score = (covered == row["deficit"], covered, -spend)
            if best is None or score > best[0]:
                best = (score, candidate)
        if best:
            row["trade"] = best[1]
            surplus[best[1]["from_symbol"]] -= best[1]["spend"]


def _item_plan(entry, inventory, rolls=None, attach_trades=True):
    group = _resolve_group(entry["id"])
    need = _requirements_by_symbol(
        group, entry["current_grade"], entry["target_grade"], entry["quantity"], rolls
    )
    rows = _rows(need, inventory)
    if attach_trades:
        _attach_trades(rows, inventory, need)
    total = sum(row["need"] for row in rows)
    covered = sum(min(row["have"], row["need"]) for row in rows)
    return {
        **entry,
        "blueprint": group["display_name"],
        "module": group["module"],
        "upgrade": group["name"],
        "kind": group["kind"],
        "kind_label": catalog.KIND_LABELS.get(group["kind"], group["kind"]),
        "engineers": [e for e in group["engineers"] if not e.startswith("@")],
        "engineer_access": catalog.engineer_access(group),
        "materials": rows,
        "applications": sum(applications for _recipe, applications in _recipe_steps(
            group, entry["current_grade"], entry["target_grade"], entry["quantity"], rolls
        )),
        "progress": round(covered * 100 / total) if total else 100,
        "craftable": all(row["deficit"] == 0 for row in rows),
    }


def plan(blueprint, target_grade, inventory, rolls=None, current_grade=0, quantity=1):
    """Compatibility single-item material plan."""
    group = _resolve_group(blueprint)
    normalized, _changed = normalize_wishlist([{
        "id": group["id"], "current_grade": current_grade,
        "target_grade": target_grade, "quantity": quantity,
    }])
    return _item_plan(normalized[0], normalize_inventory(inventory), rolls)


def plan_wishlist(items, inventory) -> dict:
    """Plan many items against one shared inventory without double-counting."""
    wishlist, changed = normalize_wishlist(items)
    held = normalize_inventory(inventory)
    total_need = {}
    for entry in wishlist:
        group = _resolve_group(entry["id"])
        for symbol, count in _requirements_by_symbol(
            group, entry["current_grade"], entry["target_grade"], entry["quantity"]
        ).items():
            total_need[symbol] = total_need.get(symbol, 0) + count

    material_rows = _rows(total_need, held)
    _attach_trades(material_rows, held, total_need)

    # Allocate direct holdings in list order for honest per-item readiness.  The
    # aggregate rows remain the authoritative shopping/trader list.
    remaining = dict(held)
    plans = []
    for entry in wishlist:
        item = _item_plan(entry, remaining, attach_trades=False)
        for row in item["materials"]:
            remaining[row["symbol"]] = max(0, remaining.get(row["symbol"], 0) - row["need"])
        plans.append(item)

    total = sum(row["need"] for row in material_rows)
    covered = sum(min(row["have"], row["need"]) for row in material_rows)
    trade_covered = sum((row.get("trade") or {}).get("covers", 0) for row in material_rows)
    return {
        "entries": wishlist,
        "items": plans,
        "materials": material_rows,
        "progress": round(covered * 100 / total) if total else 100,
        "craftable": all(row["deficit"] == 0 for row in material_rows),
        "obtainable_with_suggested_trades": all(
            row["deficit"] == 0 or (row.get("trade") or {}).get("covers", 0) >= row["deficit"]
            for row in material_rows
        ),
        "direct_units": covered,
        "required_units": total,
        "trade_covered_units": trade_covered,
        "migrated": changed,
    }
