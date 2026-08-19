from __future__ import annotations

import io
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
from shapely import contains_xy

from .config import HTTP_TIMEOUT, IEM_ASOS_URL, USER_AGENT
from .heat_index import heat_index_from_dewpoint_f


class ASOSProvider:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _fetch_chunk(self, start: datetime, end: datetime) -> pd.DataFrame:
        params = {"network": "LA_ASOS,MS_ASOS", "data": "tmpf,dwpf", "tz": "UTC", "format": "onlycomma", "latlon": "yes", "elev": "no", "missing": "empty", "trace": "empty", "sts": start.strftime("%Y-%m-%dT%H:%MZ"), "ets": end.strftime("%Y-%m-%dT%H:%MZ")}
        response = self.session.get(IEM_ASOS_URL, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        return pd.read_csv(io.StringIO(response.text), comment="#")

    def fetch(self, start: datetime, end: datetime) -> pd.DataFrame:
        frames = []
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + timedelta(hours=23, minutes=55))
            frames.append(self._fetch_chunk(cursor, chunk_end))
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
        frame["heat_index_f"] = heat_index_from_dewpoint_f(frame["tmpf"].to_numpy(), frame["dwpf"].to_numpy())
        return frame

    @staticmethod
    def summarize_zone(frame: pd.DataFrame, geometry) -> dict | None:
        if frame.empty:
            return None
        mask = contains_xy(geometry, frame["lon"].to_numpy(), frame["lat"].to_numpy())
        inside = frame.loc[mask]
        if inside.empty:
            return None
        idx = inside["heat_index_f"].idxmax()
        row = inside.loc[idx]
        return {"station": str(row["station"]), "heat_index_f": round(float(row["heat_index_f"]), 1), "temp_f": round(float(row["tmpf"]), 1), "dewpoint_f": round(float(row["dwpf"]), 1), "valid_utc": row["valid_dt"].isoformat(), "lat": float(row["lat"]), "lon": float(row["lon"])}

    @staticmethod
    def event_stations(frame: pd.DataFrame, geometries: list) -> list[dict]:
        if frame.empty or not geometries:
            return []
        lon = frame["lon"].to_numpy(); lat = frame["lat"].to_numpy()
        mask = np.zeros(len(frame), dtype=bool)
        for geom in geometries:
            mask |= contains_xy(geom, lon, lat)
        inside = frame.loc[mask]
        stations = []
        for station, group in inside.groupby("station"):
            idx = group["heat_index_f"].idxmax(); row = group.loc[idx]
            stations.append({"station": str(station), "heat_index_f": round(float(row["heat_index_f"]), 1), "temp_f": round(float(row["tmpf"]), 1), "dewpoint_f": round(float(row["dwpf"]), 1), "valid_utc": row["valid_dt"].isoformat(), "lat": float(row["lat"]), "lon": float(row["lon"])})
        return sorted(stations, key=lambda x: x["heat_index_f"], reverse=True)
