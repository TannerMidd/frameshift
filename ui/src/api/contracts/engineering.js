/** @import {JsonObject} from "./common.js" */

/**
 * @typedef {{
 *   name: string,
 *   max_grade?: number|null,
 * }} EngineerAccess
 *
 * @typedef {{
 *   id: string,
 *   display_name: string,
 *   module: string,
 *   name: string,
 *   kind: string,
 *   kind_label: string,
 *   grades: number[],
 *   engineers: string[],
 *   engineer_access: EngineerAccess[],
 *   alias_of?: string|null,
 * }} EngineeringCatalogGroup
 *
 * @typedef {{
 *   recipes?: number,
 *   groups?: number,
 *   categories?: {[kind: string]: number},
 * }} EngineeringCatalogStats
 *
 * @typedef {{
 *   name: string,
 *   symbol: string,
 *   kind: string,
 *   family?: string|null,
 *   grade?: number|null,
 *   tradeable: boolean,
 *   source?: string,
 * }} TradeableMaterial
 *
 * @typedef {{
 *   label: string,
 *   tradeable: boolean,
 * }} MaterialColumn
 *
 * @typedef {{
 *   up: number,
 *   down: number,
 * }} TradeRatios
 *
 * @typedef {{
 *   schema_version?: number,
 *   source?: string,
 *   stats: EngineeringCatalogStats,
 *   kind_labels: {[kind: string]: string},
 *   groups: EngineeringCatalogGroup[],
 *   materials?: TradeableMaterial[],
 *   material_columns?: {[key: string]: MaterialColumn},
 *   trade_ratios?: TradeRatios,
 * }} EngineeringCatalog
 *
 * @typedef {{
 *   spend: number,
 *   from: string,
 *   covers: number,
 * }} MaterialTrade
 *
 * @typedef {{
 *   symbol: string,
 *   name: string,
 *   kind: string,
 *   grade?: number|null,
 *   source?: string|null,
 *   have: number,
 *   need: number,
 *   deficit: number,
 *   trade?: MaterialTrade|null,
 * }} EngineeringMaterial
 *
 * @typedef {{
 *   id: string,
 *   current_grade?: number,
 *   target_grade?: number,
 *   quantity: number,
 *   blueprint: string,
 *   module: string,
 *   upgrade?: string,
 *   kind: string,
 *   kind_label: string,
 *   engineers?: string[],
 *   engineer_access: EngineerAccess[],
 *   materials?: EngineeringMaterial[],
 *   applications?: number,
 *   progress: number,
 *   craftable: boolean,
 * }} EngineeringWishlistItem
 *
 * @typedef {{
 *   entries?: JsonObject[],
 *   items: EngineeringWishlistItem[],
 *   materials: EngineeringMaterial[],
 *   progress?: number,
 *   craftable?: boolean,
 *   obtainable_with_suggested_trades?: boolean,
 *   direct_units?: number,
 *   required_units?: number,
 *   trade_covered_units?: number,
 *   migrated?: boolean,
 * }} EngineeringWishlist
 *
 * @typedef {{
 *   commander_id: string,
 *   catalog: EngineeringCatalog,
 *   wishlist: EngineeringWishlist,
 *   info?: JsonObject,
 *   rolls_per_grade?: {[grade: string]: number},
 *   blueprints?: {[name: string]: number[]},
 *   pinned?: EngineeringWishlistItem[],
 * }} EngineeringWorkshopResponse
 *
 * @typedef {{
 *   id?: string,
 *   name?: string,
 *   action?: "unpin",
 *   current_grade?: number,
 *   target_grade?: number,
 *   quantity?: number,
 *   blueprint_id?: string,
 *   blueprint?: string,
 *   grade?: number,
 *   pinned?: boolean,
 * }} EngineeringPinRequest
 *
 * @typedef {{
 *   ok: true,
 *   commander_id: string,
 *   pinned: EngineeringWishlistItem[],
 * }} EngineeringPinResponse
 *
 * @typedef {{
 *   station: string,
 *   system: string,
 *   distance?: number,
 *   dist_ls?: number|null,
 *   type?: string|null,
 *   carrier?: boolean,
 *   large_pad?: boolean,
 *   updated_at?: string|null,
 * }} MaterialTrader
 *
 * @typedef {{
 *   kind: string,
 *   reference: string|null,
 *   traders: MaterialTrader[],
 * }} MaterialTradersResponse
 *
 * @typedef {{
 *   system: string,
 *   body: string,
 *   subtype?: string|null,
 *   share: number,
 *   distance?: number,
 *   dist_ls?: number|null,
 *   gravity?: number|null,
 * }} MaterialBody
 *
 * @typedef {{
 *   material: string,
 *   reference: string|null,
 *   min_share: number,
 *   bodies: MaterialBody[],
 * }} MaterialBodiesResponse
 */

export {};
