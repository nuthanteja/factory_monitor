/**
 * Shared constants for the anomaly domain used by k6 scripts.
 *
 * The k6 scenarios (api_read / api_write / ws_live) operate on the HTTP and
 * WebSocket side and do not produce Kafka events directly, so this file is
 * kept as a thin constant library for referencing valid enum values and camera
 * IDs if a future k6 scenario needs to build an event-like payload.
 */

export const CAMERAS = ["cam_01", "cam_02", "cam_03", "cam_04", "cam_05", "cam_06"];

export const ANOMALY_TYPES = [
  "ppe_no_hardhat",
  "ppe_no_vest",
  "zone_intrusion",
  "loitering",
  "forklift_in_pedestrian_zone",
  "duty_zone_absence",
  "density_threshold",
];

export const SEVERITIES = ["low", "medium", "high", "critical"];

export const OBJECT_CLASSES = ["person", "forklift"];

export const ZONES = [
  "zone_weld_bay",
  "zone_assembly",
  "zone_loading_dock",
  "zone_paint_shop",
];

/**
 * Return a random element from an array.
 * @param {Array} arr
 * @returns {*}
 */
export function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}
