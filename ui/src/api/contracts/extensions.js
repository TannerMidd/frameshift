/** @import {JsonObject, JsonPrimitive, JsonValue} from "./common.js" */

/**
 * @typedef {JsonPrimitive|{
 *   exists?: boolean,
 *   eq?: JsonPrimitive,
 *   in?: JsonPrimitive[],
 *   min?: number,
 *   max?: number,
 * }} ExtensionCondition
 *
 * @typedef {{
 *   type: "alert"|"objective",
 *   level?: string,
 *   code?: string,
 *   title?: string,
 *   text?: string,
 *   say?: string,
 *   category?: string,
 *   system?: string,
 *   station?: string,
 * }} ExtensionAction
 *
 * @typedef {{
 *   event: string,
 *   when?: {[field: string]: ExtensionCondition},
 *   action: ExtensionAction,
 * }} ExtensionRule
 *
 * @typedef {{
 *   id: string,
 *   api_version: number,
 *   name: string,
 *   version: string,
 *   permissions: string[],
 *   rules: ExtensionRule[],
 *   command?: string|string[],
 *   created_with?: string,
 * }} ExtensionManifest
 *
 * @typedef {{
 *   id: string,
 *   name: string,
 *   version: string,
 *   permissions: string[],
 *   mode: "process"|"declarative",
 *   approved: boolean,
 *   approval_required: boolean,
 *   fingerprint: string|null,
 *   editable: boolean,
 *   rules: number,
 * }} ExtensionRecord
 *
 * @typedef {{
 *   id: string,
 *   error: string,
 * }} ExtensionLoadError
 *
 * @typedef {{
 *   api_version: number,
 *   directory: string,
 *   loaded: ExtensionRecord[],
 *   errors: ExtensionLoadError[],
 * }} ExtensionSnapshot
 *
 * @typedef {{
 *   rule: number,
 *   timestamp: string|null,
 *   event_type: string|null,
 *   system?: string|null,
 *   action: ExtensionAction & {extension_id?: string},
 * }} ExtensionTestMatch
 *
 * @typedef {{
 *   scanned: number,
 *   matches: ExtensionTestMatch[],
 *   truncated: boolean,
 * }} ExtensionTestResponse
 *
 * @typedef {{manifest: ExtensionManifest}} ExtensionManifestResponse
 *
 * @typedef {{
 *   version: string,
 *   time: number,
 *   python: string,
 *   platform: string,
 *   frozen: boolean,
 *   data_dir_writable: boolean,
 *   log_path: string,
 *   market_database?: JsonObject,
 *   market_database_error?: string,
 *   sqlite_integrity?: string,
 *   eddn?: JsonObject,
 *   eddn_error?: string,
 *   galaxy_watch?: {watching: string[], fired: string[], messages_checked: number},
 *   galaxy_watch_error?: string,
 *   extensions?: ExtensionSnapshot,
 *   extensions_error?: string,
 * }} DiagnosticsHealthResponse
 */

export {};
