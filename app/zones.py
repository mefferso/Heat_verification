from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests
import shapefile
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from .config import CACHE_DIR, HTTP_TIMEOUT, NWS_ZONE_VERSION, NWS_ZONES_URL, USER_AGENT, WFO


@dataclass(frozen=True)
class Zone:
    ugc: str
    name: str
    state: str
    zone: str
    geometry: object


class ZoneStore:
    def __init__(self) -> None:
        self.cache_dir = CACHE_DIR / "zones" / NWS_ZONE_VERSION
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._zones: dict[str, Zone] | None = None

    def _download(self) -> Path:
        shp_files = list(self.cache_dir.glob("*.shp"))
        if shp_files:
            return shp_files[0]
        response = requests.get(NWS_ZONES_URL, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            safe_members = [m for m in zf.namelist() if Path(m).suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg"}]
            for member in safe_members:
                (self.cache_dir / Path(member).name).write_bytes(zf.read(member))
        shp_files = list(self.cache_dir.glob("*.shp"))
        if not shp_files:
            raise RuntimeError("NWS forecast-zone ZIP did not contain a shapefile")
        return shp_files[0]

    def load(self) -> dict[str, Zone]:
        if self._zones is not None:
            return self._zones
        reader = shapefile.Reader(str(self._download()))
        zones: dict[str, Zone] = {}
        for sr in reader.iterShapeRecords():
            props = sr.record.as_dict()
            if str(props.get("CWA", "")).strip().upper() != WFO:
                continue
            state = str(props.get("STATE", "")).strip().upper()
            zone_num = str(props.get("ZONE", "")).strip().zfill(3)
            if not state or not zone_num:
                continue
            ugc = f"{state}Z{zone_num}"
            geom = shape(sr.shape.__geo_interface__)
            if not geom.is_valid:
                geom = geom.buffer(0)
            zones[ugc] = Zone(ugc=ugc, name=str(props.get("NAME", ugc)).strip(), state=state, zone=zone_num, geometry=geom)
        if not zones:
            raise RuntimeError("No LIX zones found in the official NWS shapefile")
        self._zones = zones
        return zones

    def bounds(self) -> tuple[float, float, float, float]:
        union = unary_union([z.geometry for z in self.load().values()])
        return tuple(float(x) for x in union.bounds)

    def geojson(self) -> dict:
        features = []
        for z in self.load().values():
            features.append({"type": "Feature", "id": z.ugc, "properties": {"ugc": z.ugc, "name": z.name, "state": z.state, "zone": z.zone}, "geometry": mapping(z.geometry)})
        return {"type": "FeatureCollection", "features": features, "metadata": {"wfo": WFO, "source": NWS_ZONES_URL, "version": NWS_ZONE_VERSION}}
