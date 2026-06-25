"""Camera and zone seed data for development and integration tests.

Keep camera_ids in sync with edge/config/cameras/ — the ids here are matched
to the edge YAML camera definitions by convention (not by file import).

Zones are upserted with ON CONFLICT (id) DO NOTHING.
Cameras are upserted with ON CONFLICT (id) DO UPDATE to refresh name/whep_url/zone_id.
Re-running is safe (idempotent).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

# Keep camera_ids in sync with edge/config/cameras/
_DEMO_CAMERAS = [
    {"id": "cam_01", "zone_id": "zone_weld_bay",       "name": "Weld Bay Cam 1"},
    {"id": "cam_02", "zone_id": "zone_weld_bay",       "name": "Weld Bay Cam 2"},
    {"id": "cam_03", "zone_id": "zone_assembly",       "name": "Assembly Cam 1"},
    {"id": "cam_04", "zone_id": "zone_assembly",       "name": "Assembly Cam 2"},
    {"id": "cam_05", "zone_id": "zone_loading_dock",   "name": "Loading Dock Cam 1"},
    {"id": "cam_06", "zone_id": "zone_loading_dock",   "name": "Loading Dock Cam 2"},
]

_SITE_ID = "plant-01"

# One zone row per unique zone_id
_DEMO_ZONES = [
    {"id": "zone_weld_bay",     "name": "Weld Bay"},
    {"id": "zone_assembly",     "name": "Assembly"},
    {"id": "zone_loading_dock", "name": "Loading Dock"},
]


async def seed_cameras(session_maker: async_sessionmaker) -> None:
    """Upsert demo Zone rows then Camera rows for plant-01.

    Uses ON CONFLICT so restarts and re-seeding are always safe.
    """
    async with session_maker() as s:
        # --- zones first (cameras.zone_id FK) ---
        for zone in _DEMO_ZONES:
            # Find a camera that belongs to this zone to set camera_id
            cam_id = next(
                c["id"] for c in _DEMO_CAMERAS if c["zone_id"] == zone["id"]
            )
            await s.execute(
                text(
                    "INSERT INTO zones (id, site_id, camera_id, name, kind, polygon)"
                    " VALUES (:id, :site_id, :camera_id, :name, :kind, CAST(:polygon AS jsonb))"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": zone["id"],
                    "site_id": _SITE_ID,
                    "camera_id": cam_id,
                    "name": zone["name"],
                    "kind": "required_ppe",
                    "polygon": "[]",
                },
            )

        # --- cameras ---
        for cam in _DEMO_CAMERAS:
            cam_id = cam["id"]
            whep_url = f"/whep/{cam_id}/whep"
            rtsp_path = f"rtsp://mediamtx:8554/{cam_id}"
            await s.execute(
                text(
                    "INSERT INTO cameras (id, site_id, name, rtsp_path, whep_url, zone_id)"
                    " VALUES (:id, :site_id, :name, :rtsp_path, :whep_url, :zone_id)"
                    " ON CONFLICT (id) DO UPDATE"
                    "   SET name = EXCLUDED.name,"
                    "       whep_url = EXCLUDED.whep_url,"
                    "       zone_id = EXCLUDED.zone_id"
                ),
                {
                    "id": cam_id,
                    "site_id": _SITE_ID,
                    "name": cam["name"],
                    "rtsp_path": rtsp_path,
                    "whep_url": whep_url,
                    "zone_id": cam["zone_id"],
                },
            )

        await s.commit()
