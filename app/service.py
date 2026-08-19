from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
from shapely import contains_xy

from .config import LOCAL_TZ, NWS_ZONE_VERSION, NWS_ZONES_URL, OBS_NETWORKS, URMA_BASES
from .heat_index import heat_index_from_dewpoint_f
from .observations import ASOSProvider
from .urma import URMAProvider, URMAUnavailable
from .vtec import VTECProvider, parse_dt
from .zones import ZoneStore


def month_bounds_utc(month: str) -> tuple[datetime, datetime]:
    try:
        year, mon = (int(part) for part in month.split("-"))
        start_local = datetime(year, mon, 1, tzinfo=ZoneInfo(LOCAL_TZ))
    except Exception as exc:
        raise ValueError("month must be YYYY-MM") from exc
    if mon == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=ZoneInfo(LOCAL_TZ))
    else:
        end_local = datetime(year, mon + 1, 1, tzinfo=ZoneInfo(LOCAL_TZ))
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def ceil_hour(dt: datetime) -> datetime:
    floored = floor_hour(dt)
    return floored if dt == floored else floored + timedelta(hours=1)


class VerificationService:
    def __init__(self) -> None:
        self.zones = ZoneStore()
        self.vtec = VTECProvider()
        self.urma = URMAProvider()
        self.asos = ASOSProvider()

    def verify_month(self, month: str, progress=None) -> dict:
        progress = progress or (lambda pct, msg: None)
        start_utc, end_utc = month_bounds_utc(month)
        zones = self.zones.load()
        bbox = self.zones.bounds()
        progress(0.02, "Loading LIX heat products from the VTEC archive…")
        events = self.vtec.fetch(start_utc, end_utc)
        if not events:
            return self._empty_result(month, start_utc, end_utc)

        active_by_hour: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
        trackers: dict[tuple[int, int], dict] = {}
        missing_geometry = set()
        for eidx, event in enumerate(events):
            for zidx, zone_row in enumerate(event["zones"]):
                ugc = zone_row["ugc"]
                trackers[(eidx, zidx)] = {
                    "max_hi": None,
                    "time": None,
                    "lat": None,
                    "lon": None,
                    "available_hours": 0,
                    "missing_hours": 0,
                }
                if ugc not in zones:
                    missing_geometry.add(ugc)
                    continue
                zs = parse_dt(zone_row["start_utc"])
                ze = parse_dt(zone_row["end_utc"])
                hour = floor_hour(max(zs, start_utc))
                final = ceil_hour(min(ze, end_utc))
                while hour <= final:
                    if hour >= zs and hour <= ze and hour >= start_utc and hour < end_utc:
                        active_by_hour[hour].append((eidx, zidx))
                    hour += timedelta(hours=1)

        hours = sorted(active_by_hour)
        zone_masks: dict[str, np.ndarray] = {}
        missing_urma_hours: list[str] = []
        progress(0.05, f"Processing {len(hours)} unique URMA analysis hours…")
        for hidx, hour in enumerate(hours):
            active = active_by_hour[hour]
            try:
                field = self.urma.fetch_hour(hour, bbox)
            except URMAUnavailable:
                missing_urma_hours.append(hour.isoformat())
                for key in active:
                    trackers[key]["missing_hours"] += 1
                progress(
                    0.05 + 0.70 * ((hidx + 1) / max(1, len(hours))),
                    f"URMA missing for {hour:%Y-%m-%d %HZ}; continuing…",
                )
                continue
            hi = heat_index_from_dewpoint_f(field["temp_f"], field["dewpoint_f"])
            for key in active:
                eidx, zidx = key
                zone_row = events[eidx]["zones"][zidx]
                ugc = zone_row["ugc"]
                if ugc not in zones:
                    continue
                if ugc not in zone_masks:
                    zone_masks[ugc] = contains_xy(zones[ugc].geometry, field["lon"], field["lat"])
                mask = zone_masks[ugc]
                tracker = trackers[key]
                tracker["available_hours"] += 1
                if not np.any(mask):
                    continue
                masked_hi = hi[mask]
                finite = np.isfinite(masked_hi)
                if not np.any(finite):
                    continue
                indices = np.flatnonzero(mask)
                local_idx = int(np.nanargmax(masked_hi))
                global_idx = int(indices[local_idx])
                value = float(hi[global_idx])
                if tracker["max_hi"] is None or value > tracker["max_hi"]:
                    tracker.update(
                        {
                            "max_hi": value,
                            "time": hour.isoformat(),
                            "lat": float(field["lat"][global_idx]),
                            "lon": float(field["lon"][global_idx]),
                        }
                    )
            progress(
                0.05 + 0.70 * ((hidx + 1) / max(1, len(hours))),
                f"URMA {hidx + 1}/{len(hours)} — {hour:%b %d %HZ}",
            )

        progress(0.77, "Pulling ASOS/AWOS observations for supporting evidence…")
        for eidx, event in enumerate(events):
            event_geoms = [zones[z["ugc"]].geometry for z in event["zones"] if z["ugc"] in zones]
            es = max(parse_dt(event["start_utc"]), start_utc)
            ee = min(parse_dt(event["end_utc"]), end_utc)
            try:
                obs = self.asos.fetch(es, ee)
            except Exception as exc:
                obs = None
                event["observation_error"] = str(exc)
            event["stations"] = self.asos.event_stations(obs, event_geoms) if obs is not None else []
            threshold = float(event["threshold_f"])
            for zidx, zone_row in enumerate(event["zones"]):
                ugc = zone_row["ugc"]
                tracker = trackers[(eidx, zidx)]
                zone_result = {
                    **zone_row,
                    "zone_name": zones[ugc].name if ugc in zones else ugc,
                    "threshold_f": threshold,
                    "geometry_available": ugc in zones,
                    "urma": None,
                    "urma_hours": {
                        "available": tracker["available_hours"],
                        "missing": tracker["missing_hours"],
                        "requested": tracker["available_hours"] + tracker["missing_hours"],
                    },
                    "observation": None,
                    "nearby_observation": None,
                }
                if tracker["max_hi"] is not None:
                    zone_result["urma"] = {
                        "heat_index_f": round(tracker["max_hi"], 1),
                        "valid_utc": tracker["time"],
                        "lat": tracker["lat"],
                        "lon": tracker["lon"],
                        "verified": tracker["max_hi"] >= threshold,
                        "available_hours": tracker["available_hours"],
                        "missing_hours": tracker["missing_hours"],
                    }
                if obs is not None and ugc in zones:
                    omax = self.asos.summarize_zone(obs, zones[ugc].geometry)
                    if omax:
                        omax["verified"] = omax["heat_index_f"] >= threshold
                        zone_result["observation"] = omax
                    else:
                        zone_result["nearby_observation"] = self.asos.nearest_zone_station(
                            obs, zones[ugc].geometry
                        )
                event["zones"][zidx] = zone_result
            event["zone_count"] = len(event["zones"])
            event["urma_verified_count"] = sum(
                1 for z in event["zones"] if z["urma"] and z["urma"]["verified"]
            )
            event["obs_verified_count"] = sum(
                1 for z in event["zones"] if z["observation"] and z["observation"]["verified"]
            )
            event["max_urma_hi_f"] = max(
                (z["urma"]["heat_index_f"] for z in event["zones"] if z["urma"]), default=None
            )
            event["max_obs_hi_f"] = max(
                (z["observation"]["heat_index_f"] for z in event["zones"] if z["observation"]),
                default=None,
            )
            progress(
                0.77 + 0.20 * ((eidx + 1) / max(1, len(events))),
                f"Observations {eidx + 1}/{len(events)} — {event['product']}",
            )

        result = self._base_result(month, start_utc, end_utc)
        result["events"] = events
        result["summary"] = {
            "event_count": len(events),
            "zone_entries": sum(len(e["zones"]) for e in events),
            "urma_verified_zone_entries": sum(e["urma_verified_count"] for e in events),
            "obs_verified_zone_entries": sum(e["obs_verified_count"] for e in events),
            "requested_urma_hours": len(hours),
            "available_urma_hours": len(hours) - len(missing_urma_hours),
            "missing_urma_hours": len(missing_urma_hours),
            "missing_geometry_ugcs": sorted(missing_geometry),
        }
        result["missing_urma_hours"] = missing_urma_hours
        progress(1.0, "Verification complete")
        return result

    def _base_result(self, month, start_utc, end_utc):
        return {
            "month": month,
            "window_utc": {"start": start_utc.isoformat(), "end": end_utc.isoformat()},
            "criteria": {
                "Heat Advisory": 108.0,
                "Extreme Heat Warning": 113.0,
                "legacy Excessive Heat Warning": 113.0,
            },
            "sources": {
                "zones": {"version": NWS_ZONE_VERSION, "url": NWS_ZONES_URL},
                "urma_bases": list(URMA_BASES),
                "vtec": "Iowa Environmental Mesonet VTEC archive",
                "observations": f"Iowa Environmental Mesonet ASOS/AWOS archive ({', '.join(OBS_NETWORKS)})",
            },
        }

    def _empty_result(self, month, start_utc, end_utc):
        result = self._base_result(month, start_utc, end_utc)
        result["events"] = []
        result["summary"] = {
            "event_count": 0,
            "zone_entries": 0,
            "urma_verified_zone_entries": 0,
            "obs_verified_zone_entries": 0,
            "requested_urma_hours": 0,
            "available_urma_hours": 0,
            "missing_urma_hours": 0,
            "missing_geometry_ugcs": [],
        }
        result["missing_urma_hours"] = []
        return result
