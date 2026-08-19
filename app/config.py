from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "WFO LIX Heat Verification"
WFO = "LIX"
LOCAL_TZ = "America/Chicago"

HEAT_ADVISORY_THRESHOLD_F = float(os.getenv("HEAT_ADVISORY_THRESHOLD_F", "108"))
EXTREME_HEAT_WARNING_THRESHOLD_F = float(os.getenv("EXTREME_HEAT_WARNING_THRESHOLD_F", "113"))

# Latest official NWS public forecast-zone shapefile as of 2026-08-19.
NWS_ZONE_VERSION = os.getenv("NWS_ZONE_VERSION", "2026-04-16")
NWS_ZONES_URL = os.getenv(
    "NWS_ZONES_URL",
    "https://www.weather.gov/source/gis/Shapefiles/WSOM/z_16ap26.zip",
)

IEM_VTEC_URL = os.getenv(
    "IEM_VTEC_URL",
    "https://mesonet.agron.iastate.edu/json/vtec_events_bywfo.py",
)
IEM_ASOS_URL = os.getenv(
    "IEM_ASOS_URL",
    "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
)
OBS_NETWORKS = tuple(
    x.strip()
    for x in os.getenv("OBS_NETWORKS", "LA_ASOS,MS_ASOS").split(",")
    if x.strip()
)

# NOAA NODD's public URMA archive is available from 2019-present.  Prefer it
# for retrospective work; retain operational NOMADS endpoints as fallbacks.
DEFAULT_URMA_BASES = (
    "https://noaa-urma-pds.s3.amazonaws.com",
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com/urma/prod",
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com/urma/v2.10",
)
URMA_BASES = tuple(
    x.strip().rstrip("/")
    for x in os.getenv("URMA_BASES", ",".join(DEFAULT_URMA_BASES)).split(",")
    if x.strip()
)

CACHE_DIR = Path(os.getenv("CACHE_DIR", "data/cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "45"))
USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "WFO-LIX-Heat-Verification/1.1 (https://github.com/mefferso/Heat_verification)",
)
