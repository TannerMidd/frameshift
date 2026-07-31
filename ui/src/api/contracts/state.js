/** @import {JsonObject} from "./common.js" */
/** @import {SpecialistSnapshot} from "./specialists.js" */

/**
 * @typedef {[number, number, number]} Coordinate3
 *
 * @typedef {{
 *   lat: number,
 *   lon: number,
 *   body?: string|null,
 *   radius?: number|null,
 *   heading?: number|null,
 *   altitude?: number|null,
 * }} SurfacePosition
 *
 * @typedef {{
 *   label: string,
 *   url: string,
 * }} ExternalLink
 *
 * @typedef {{
 *   system?: string|null,
 *   address?: number|null,
 *   star_class?: string|null,
 *   distance?: number|null,
 *   ts?: number|string|null,
 * }} JumpHistoryEntry
 *
 * @typedef {{
 *   symbol?: string,
 *   name?: string,
 *   count?: number,
 *   stolen?: number,
 *   mission_id?: number|null,
 * }} CargoInventoryItem
 *
 * @typedef {{
 *   id?: number|string,
 *   name?: string,
 *   symbol?: string,
 *   category?: string,
 *   buy_price?: number,
 *   sell_price?: number,
 *   demand?: number,
 *   stock?: number,
 * }} StateMarketItem
 *
 * @typedef {{
 *   market_id?: number|null,
 *   station?: string|null,
 *   system?: string|null,
 *   timestamp?: string|number|null,
 *   is_current_station?: boolean,
 *   items?: StateMarketItem[],
 * }|null} StateMarket
 *
 * @typedef {{
 *   name?: string,
 *   min_value?: number|null,
 *   max_value?: number|null,
 *   colony_m?: number|null,
 * }} BiologyGenus
 *
 * @typedef {{
 *   name?: string,
 *   genus?: string,
 *   value?: number|null,
 *   variant?: string|null,
 *   colony_m?: number|null,
 *   sites?: number,
 * }} BiologySpecies
 *
 * @typedef {{
 *   body?: string,
 *   count?: number,
 *   genuses?: BiologyGenus[],
 *   community_genuses?: BiologyGenus[],
 *   community_species?: BiologySpecies[],
 *   species_value?: number|null,
 *   predicted?: BiologyGenus[],
 *   was_discovered?: boolean,
 *   landable?: boolean,
 *   source?: string,
 *   planet_class?: string,
 *   atmosphere?: string,
 *   gravity_g?: number|null,
 *   temp_k?: number|null,
 * }} BiologySignal
 *
 * @typedef {{
 *   genus?: string|null,
 *   species?: string|null,
 *   variant?: string|null,
 *   progress?: number,
 *   value?: number|null,
 *   first?: boolean,
 *   min_dist_m?: number|null,
 *   colony_m?: number|null,
 *   clear?: boolean|null,
 * }} BiologySampling
 *
 * @typedef {{
 *   species?: string,
 *   genus?: string,
 *   variant?: string|null,
 *   value?: number,
 *   body?: string|null,
 *   first?: boolean,
 * }} BiologyVaultItem
 *
 * @typedef {{
 *   system_signals: BiologySignal[],
 *   sampling: BiologySampling|null,
 *   vault: {
 *     items: BiologyVaultItem[],
 *     total: number,
 *   },
 * }} BiologyState
 *
 * @typedef {{
 *   body?: string,
 *   class?: string,
 *   mapped?: boolean,
 *   first?: boolean,
 *   value?: number,
 * }} ExplorationBody
 *
 * @typedef {{
 *   total: number,
 *   count: number,
 *   mapped: number,
 *   firsts: number,
 *   top: ExplorationBody[],
 * }} ExplorationState
 *
 * @typedef {{
 *   start_ts?: number|null,
 *   end_ts?: number|null,
 *   start_credits?: number|null,
 *   jumps?: number,
 *   distance_ly?: number,
 *   collected_cr?: number,
 * }} SessionState
 *
 * @typedef {{
 *   kills?: number,
 *   bounty_cr?: number,
 *   bonds_cr?: number,
 *   faction_kills?: {[faction: string]: number},
 * }} CombatState
 *
 * @typedef {{
 *   route?: {system?: string, address?: number|null, star_class?: string|null}[],
 *   fuel_per_jump?: number|null,
 *   scoopable_ahead?: boolean|null,
 *   next_star_class?: string|null,
 * }} NavigationState
 *
 * @typedef {{
 *   system: string|null,
 *   star_class: string|null,
 *   scoopable: boolean,
 *   taxi: boolean,
 *   started_ms: number,
 *   elapsed_s: number,
 * }} JumpProgress
 *
 * @typedef {{
 *   id?: number|string,
 *   level?: string,
 *   code?: string,
 *   say?: string,
 *   text?: string,
 *   ts?: number,
 * }} StateAlert
 *
 * @typedef {{
 *   active: boolean,
 *   phase: string,
 *   completed: number,
 *   total: number,
 *   current: string|null,
 *   attempt: number,
 *   retrying: boolean,
 * }} JournalRebuildState
 *
 * @typedef {{
 *   commander: string|null,
 *   commander_id: string|null,
 *   ship_type: string|null,
 *   ship_name: string|null,
 *   ship_ident: string|null,
 *   cargo_capacity: number|null,
 *   max_jump_range: number|null,
 *   fuel_capacity: number|null,
 *   rebuy: number|null,
 *   horizons: boolean|null,
 *   odyssey: boolean|null,
 *   game_version: string|null,
 *   game_build: string|null,
 *   has_loadout: boolean,
 *   system: string|null,
 *   system_address: number|null,
 *   star_pos: Coordinate3|null,
 *   pos: SurfacePosition|null,
 *   body: string|null,
 *   docked: boolean,
 *   station: string|null,
 *   station_type: string|null,
 *   dist_from_star_ls: number|null,
 *   credits: number|null,
 *   fuel_main: number|null,
 *   fuel_reservoir: number|null,
 *   cargo_tons: number|null,
 *   legal_state: string|null,
 *   destination: string|null,
 *   jump_history: JumpHistoryEntry[],
 *   cargo_inventory: CargoInventoryItem[],
 *   market: StateMarket,
 *   bio: BiologyState,
 *   exploration: ExplorationState,
 *   colonisation: JsonObject[],
 *   missions: JsonObject[],
 *   materials: JsonObject,
 *   synth: JsonObject,
 *   engineers: JsonObject[],
 *   galaxy: JsonObject,
 *   stored_ships: JsonObject|null,
 *   ship_locker: JsonObject|null,
 *   carrier: JsonObject|null,
 *   specialists: SpecialistSnapshot|null,
 *   session: SessionState,
 *   combat: CombatState,
 *   nav: NavigationState,
 *   jump: JumpProgress|null,
 *   alerts: StateAlert[],
 *   last_journal_event: string|null,
 *   journal_dir_found: boolean,
 *   journal_rebuild: JournalRebuildState,
 *   game_running: boolean|null,
 *   galaxy_mode: "live"|"legacy"|"unknown",
 *   links: ExternalLink[],
 * }} ApplicationState
 *
 * @typedef {{
 *   station: string,
 *   type?: string|null,
 *   body?: string|null,
 *   dist_ls?: number|null,
 *   large_pad?: boolean,
 *   pads?: {large?: number, medium?: number, small?: number},
 *   economy?: string|null,
 *   faction?: string|null,
 *   services?: string[],
 *   market_id?: number|null,
 *   local_market?: boolean,
 *   market_updated?: number|string|null,
 * }} SystemStation
 *
 * @typedef {{
 *   system: string,
 *   stations: SystemStation[],
 *   note?: string,
 * }} SystemStationsResponse
 *
 * @typedef {{
 *   ship_type: string|null,
 *   ship_name: string|null,
 *   ship_ident: string|null,
 *   edsy_url: string,
 *   slef: JsonObject,
 * }} LoadoutExportResponse
 *
 * @typedef {{
 *   ok: true,
 *   already_running?: true,
 *   via?: string,
 * }} LaunchGameResponse
 */

export {};
