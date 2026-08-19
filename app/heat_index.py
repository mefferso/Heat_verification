from __future__ import annotations

import numpy as np


def relative_humidity_from_dewpoint(temp_f, dewpoint_f):
    """Return RH (%) from temperature/dewpoint using a Magnus formulation."""
    t = np.asarray(temp_f, dtype=float)
    td = np.asarray(dewpoint_f, dtype=float)
    tc = (t - 32.0) * 5.0 / 9.0
    tdc = (td - 32.0) * 5.0 / 9.0
    e = np.exp((17.625 * tdc) / (243.04 + tdc))
    es = np.exp((17.625 * tc) / (243.04 + tc))
    return np.clip(100.0 * e / es, 0.0, 100.0)


def heat_index_f(temp_f, rh_percent):
    """NWS/NOAA heat-index algorithm (Rothfusz regression + adjustments)."""
    t = np.asarray(temp_f, dtype=float)
    rh = np.asarray(rh_percent, dtype=float)

    simple = 0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (rh * 0.094))
    simple = 0.5 * (simple + t)

    regression = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * rh
        - 0.22475541 * t * rh
        - 0.00683783 * t * t
        - 0.05481717 * rh * rh
        + 0.00122874 * t * t * rh
        + 0.00085282 * t * rh * rh
        - 0.00000199 * t * t * rh * rh
    )

    low_mask = (rh < 13.0) & (t >= 80.0) & (t <= 112.0)
    low_term = ((13.0 - rh) / 4.0) * np.sqrt(
        np.maximum(0.0, (17.0 - np.abs(t - 95.0)) / 17.0)
    )
    regression = np.where(low_mask, regression - low_term, regression)

    high_mask = (rh > 85.0) & (t >= 80.0) & (t <= 87.0)
    high_term = ((rh - 85.0) / 10.0) * ((87.0 - t) / 5.0)
    regression = np.where(high_mask, regression + high_term, regression)

    return np.where(simple >= 80.0, regression, simple)


def heat_index_from_dewpoint_f(temp_f, dewpoint_f):
    rh = relative_humidity_from_dewpoint(temp_f, dewpoint_f)
    return heat_index_f(temp_f, rh)
