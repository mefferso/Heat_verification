from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from .config import EXTREME_HEAT_WARNING_THRESHOLD_F, HEAT_ADVISORY_THRESHOLD_F, HTTP_TIMEOUT, IEM_VTEC_URL, USER_AGENT, WFO

PRODUCTS = {
    ("HT", "Y"): ("Heat Advisory", HEAT_ADVISORY_THRESHOLD_F),
    ("XH", "W"): ("Extreme Heat Warning", EXTREME_HEAT_WARNING_THRESHOLD_F),
    ("EH", "W"): ("Excessive Heat Warning", EXTREME_HEAT_WARNING_THRESHOLD_F),
}


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def group_vtec_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple, dict] = {}
    for row in rows:
        ph = row.get("phenomena")
        sig = row.get("significance")
        if (ph, sig) not in PRODUCTS:
            continue
        name, threshold = PRODUCTS[(ph, sig)]
        key = (int(row.get("vtec_year") or parse_dt(row["issue"]).year), int(row["eventid"]), ph, sig)
        event = grouped.setdefault(key, {"key": f"{key[0]}-{ph}-{sig}-{key[1]:04d}", "vtec_year": key[0], "eventid": key[1], "phenomena": ph, "significance": sig, "product": name, "threshold_f": threshold, "zones": {}, "source_url": row.get("url")})
        ugc = row["ugc"]
        issue = parse_dt(row["issue"])
        expire = parse_dt(row["expire"])
        prior = event["zones"].get(ugc)
        if prior:
            issue = min(issue, parse_dt(prior["start_utc"]))
            expire = max(expire, parse_dt(prior["end_utc"]))
        event["zones"][ugc] = {"ugc": ugc, "start_utc": issue.isoformat(), "end_utc": expire.isoformat(), "product_id": row.get("product_id"), "last_product_id": row.get("last_product_id")}
    result = []
    for event in grouped.values():
        zone_rows = list(event["zones"].values())
        event["zones"] = sorted(zone_rows, key=lambda x: x["ugc"])
        event["start_utc"] = min(z["start_utc"] for z in zone_rows)
        event["end_utc"] = max(z["end_utc"] for z in zone_rows)
        result.append(event)
    return sorted(result, key=lambda x: (x["start_utc"], x["product"], x["eventid"]))


class VTECProvider:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch(self, start_utc: datetime, end_utc: datetime) -> list[dict]:
        rows: list[dict] = []
        for ph, sig in PRODUCTS:
            response = self.session.get(IEM_VTEC_URL, params={"wfo": WFO, "start": start_utc.isoformat().replace("+00:00", "Z"), "end": end_utc.isoformat().replace("+00:00", "Z"), "phenomena": ph, "significance": sig}, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            rows.extend(response.json().get("events", []))
        return group_vtec_rows(rows)
