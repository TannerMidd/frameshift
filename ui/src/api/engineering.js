import { http } from "../core/http.js";
import { withQuery } from "./query.js";

/** @import {EngineeringPinRequest, EngineeringPinResponse, EngineeringWorkshopResponse, MaterialBodiesResponse, MaterialTradersResponse} from "./contracts/engineering.js" */

/** @param {typeof http} [client] */
export function createEngineeringApi(client = http) {
  /** @returns {Promise<EngineeringWorkshopResponse>} */
  function getWorkshop() {
    return client.json("/api/engineering", { scope: "commander" });
  }

  /** @param {EngineeringPinRequest} request @returns {Promise<EngineeringPinResponse>} */
  function setPinnedBlueprint(request) {
    return client.json("/api/engineering/pin", {
      method: "POST",
      json: request,
      scope: "commander",
    });
  }

  /**
   * @param {string} kind
   * @param {string} [system]
   * @returns {Promise<MaterialTradersResponse>}
   */
  function findMaterialTraders(kind, system) {
    return client.json(withQuery("/api/material-traders", { kind, system }), {
      scope: "commander",
    });
  }

  /**
   * @param {string} material
   * @param {string} [system]
   * @param {string|number} [minShare]
   * @returns {Promise<MaterialBodiesResponse>}
   */
  function findMaterialBodies(material, system, minShare) {
    return client.json(
      withQuery("/api/material-bodies", { material, system, min_share: minShare }),
      { scope: "commander" },
    );
  }

  return Object.freeze({
    getWorkshop,
    setPinnedBlueprint,
    findMaterialTraders,
    findMaterialBodies,
  });
}

export const engineeringApi = createEngineeringApi();
