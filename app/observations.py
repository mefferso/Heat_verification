from __future__ import annotations

import io
import math
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
from shapely import Point, contains_xy
from shapely.ops import nearest_points

from .config import HTTP_TIMEOUT, IEM_ASOS_URL, OBS_NETWORKS, USER_AGENT
from .heat_index import heat_index_from_dewpoint_f


class ASOSProvider:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _fetch_chunk(self, network: str, start: datetime, end: datetime) -> pd.DataFrame:
        params = {
            "network": network,
            "data": "tmpf,dwpf",
            "tz": "UTC",
            "format": "onlycomma",
            "latlon": "yes",
            "elev": "no",
            "missing": "empty",
            "trace": "empty",
            "sts": start.strftime("%Y-%m-%dT%H:%MZ"),
            "ets": end.strftime("%Y-%m-%dT%H:%MZ"),
        }
        response = self.session.get(IEM_ASOS_URL, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text), comment="#")
        if not frame.empty:
            frame["network"] = network
        return frame

    def fetch(self, start: datetime, end: datetime) -> pd.DataFrame:
        frames = []
        for network in OBS_NETWORKS:
            cursor = start
            while cursor < end:
                chunk_end = min(end, cursor + timedelta(hours=23, minutes=55))
                try:
                    frame = self._fetch_chunk(network, cursor, chunk_end)
                    if not frame.empty:
                        frames.append(frame)
                finally:
                    cursor = chunk_end
                if cursor < end:
                    time.sleep(1.05)
        if not frames:
            return pd.DataFrame()
        frame = pd.concat(frames, ignore_index=True).drop_duplicates()
        required = {"station", "valid", "lon", "lat", "tmpf", "dwpf"}
        if not required.issubset(frame.columns):
            return pd.DataFrame()
        for col in ("lon", "lat", "tmpf", "dwpf"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame["valid_dt"] = pd.to_datetime(frame["valid"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["lon", "lat", "tmpf", "dwpf", "valid_dt"]).copy()
        frame["heat_index_f"] = heat_index_from_dewpoint_f(
            frame["tmpf"].to_numpy(), frame["dwpf"].to_numpy()
        )
        return frame

    @staticmethod
    def _row_dict(row) -> dict:
        return {
            "station": str(row["station"]),
            "network": str(row.get("network", "ASOS/AWOS")),
            "heat_index_f": round(float(row["heat_index_f"]), 1),
            "temp_f": round(float(row["tmpf"]), 1),
            "dewpoint_f": round(float(row["dwpf"]), 1),
            "valid_utc": row["valid_dt"].isoformat(),
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
        }

    @staticmethod
    def summarize_zone(frame: pd.DataFrame, geometry) -> dict | None:
        if frame.empty:
            return None
        mask = contains_xy(geometry, frame["lon"].to_numpy(), frame["lat"].to_numpy())
        inside = frame.loc[mask]
        if inside.empty:
            return None
        idx = inside["heat_index_f"].idxmax()
        return ASOSProvider._row_dict(inside.loc[idx])

    @staticmethod
    def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 3958.7613
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def nearest_zone_station(frame: pd.DataFrame, geometry, max_miles: float = 35.0) -> dict | None:
        """Return the hottest report from the nearest station outside a zone.

        This is context only and is never counted as strict zone verification.
        """
        if frame.empty:
            return None
        best = None
        # Reduce repeated sub-hourly reports to one hottest row per station first.
        for _, group in frame.groupby("station"):
            idx = group["heat_index_f"].idxmax()
            row = group.loc[idx]
            station_point = Point(float(row["lon"]), float(row["lat"]))
            if geometry.contains(station_point):
                continue
            edge_point, _ = nearest_points(geometry, station_point)
            miles = ASOSProvider._haversine_miles(
                edge_point.y, edge_point.x, float(row["lat"]), float(row["lon"])
            )
            if miles <= max_miles and (best is None or miles < best[0]):
                best = (miles, row)
        if best is None:
            return None
        result = ASOSProvider._row_dict(best[1])
        result["distance_miles_outside"] = round(float(best[0]), 1)
        result["context_only"] = True
        return result

    @staticmethod
    def event_stations(frame: pd.DataFrame, geometries: list) -> list[dict]:
        if frame.empty or not geometries:
            return []
        lon = frame["lon"].to_numpy()
        lat = frame["lat"].to_numpy()
        mask = np.zeros(len(frame), dtype=bool)
        for geom in geometries:
            mask |= contains_xy(geom, lon, lat)
        inside = frame.loc[mask]
        stations = []
        for _, group in inside.groupby("station"):
            idx = group["heat_index_f"].idxmax()
            stations.append(ASOSProvider._row_dict(group.loc[idx]))
        return sorted(stations, key=lambda x: x["heat_index_f"], reverse=True)
