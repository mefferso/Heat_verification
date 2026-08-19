from __future__ import annotations

import tempfile
from datetime import datetime

import numpy as np
import requests
import xarray as xr

from .config import CACHE_DIR, HTTP_TIMEOUT, URMA_BASES, USER_AGENT


class URMAUnavailable(RuntimeError):
    pass


def parse_grib2_message_length(header: bytes) -> int:
    if len(header) < 16 or header[:4] != b"GRIB":
        raise ValueError("Not a GRIB2 section-0 header")
    if header[7] != 2:
        raise ValueError(f"Expected GRIB edition 2, got {header[7]}")
    length = int.from_bytes(header[8:16], byteorder="big", signed=False)
    if length < 16:
        raise ValueError("Invalid GRIB2 message length")
    return length


class URMAProvider:
    """Fetch only URMA GRIB records 3 (2-m T) and 4 (2-m Td) via HTTP ranges."""
    def __init__(self) -> None:
        self.session = requests.Session(); self.session.headers.update({"User-Agent": USER_AGENT})
        self.cache_dir = CACHE_DIR / "urma"; self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _url(base: str, valid: datetime) -> str:
        return f"{base}/urma2p5.{valid:%Y%m%d}/urma2p5.t{valid:%H}z.2dvaranl_ndfd.grb2_wexp"

    def _range(self, url: str, start: int, end: int, full_blob: bytes | None):
        if full_blob is not None:
            return full_blob[start:end + 1], full_blob
        response = self.session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=HTTP_TIMEOUT)
        if response.status_code == 404:
            raise URMAUnavailable("404")
        response.raise_for_status()
        if response.status_code == 206:
            return response.content, None
        blob = response.content
        return blob[start:end + 1], blob

    def _extract_records_3_4(self, url: str) -> tuple[bytes, bytes]:
        offset = 0; full_blob = None; records: dict[int, bytes] = {}
        for record_number in range(1, 5):
            header, full_blob = self._range(url, offset, offset + 15, full_blob)
            length = parse_grib2_message_length(header)
            if record_number in (3, 4):
                payload, full_blob = self._range(url, offset, offset + length - 1, full_blob)
                if len(payload) != length:
                    raise URMAUnavailable(f"Truncated GRIB record {record_number}: {len(payload)} of {length} bytes")
                records[record_number] = payload
            offset += length
        return records[3], records[4]

    @staticmethod
    def _decode_single_message(payload: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with tempfile.NamedTemporaryFile(suffix=".grb2") as tmp:
            tmp.write(payload); tmp.flush()
            try:
                ds = xr.open_dataset(tmp.name, engine="cfgrib", backend_kwargs={"indexpath": ""})
            except Exception as exc:
                raise RuntimeError("Unable to decode URMA GRIB2. Install cfgrib and eccodes as listed in requirements.txt.") from exc
            try:
                if not ds.data_vars:
                    raise RuntimeError("Decoded URMA message had no data variable")
                da = ds[next(iter(ds.data_vars))]
                values = np.asarray(da.values, dtype=float); lat = np.asarray(ds["latitude"].values, dtype=float); lon = np.asarray(ds["longitude"].values, dtype=float)
            finally:
                ds.close()
        if lat.ndim == 1 and lon.ndim == 1:
            lon, lat = np.meshgrid(lon, lat)
        lon = np.where(lon > 180.0, lon - 360.0, lon)
        return values, lat, lon

    def fetch_hour(self, valid: datetime, bbox: tuple[float, float, float, float]) -> dict[str, np.ndarray]:
        cache_path = self.cache_dir / f"{valid:%Y%m%d%H}_lix.npz"
        if cache_path.exists():
            with np.load(cache_path) as cached:
                return {key: cached[key] for key in cached.files}
        last_error = None; records = None
        for base in URMA_BASES:
            try:
                records = self._extract_records_3_4(self._url(base, valid)); break
            except (URMAUnavailable, requests.RequestException, ValueError) as exc:
                last_error = exc
        if records is None:
            raise URMAUnavailable(f"URMA unavailable for {valid.isoformat()}: {last_error}")
        temp_k, lat_t, lon_t = self._decode_single_message(records[0]); dew_k, lat_d, lon_d = self._decode_single_message(records[1])
        if temp_k.shape != dew_k.shape or lat_t.shape != lat_d.shape:
            raise RuntimeError("URMA temperature/dewpoint grids did not match")
        minx, miny, maxx, maxy = bbox; pad = 0.15
        mask = (lon_t >= minx - pad) & (lon_t <= maxx + pad) & (lat_t >= miny - pad) & (lat_t <= maxy + pad)
        result = {"lat": lat_t[mask].astype(np.float32), "lon": lon_t[mask].astype(np.float32), "temp_f": ((temp_k[mask] - 273.15) * 9.0 / 5.0 + 32.0).astype(np.float32), "dewpoint_f": ((dew_k[mask] - 273.15) * 9.0 / 5.0 + 32.0).astype(np.float32)}
        if result["lat"].size == 0:
            raise RuntimeError("URMA LIX bbox subset was empty")
        np.savez_compressed(cache_path, **result)
        return result
