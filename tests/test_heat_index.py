import numpy as np

from app.heat_index import heat_index_f, heat_index_from_dewpoint_f, relative_humidity_from_dewpoint


def test_heat_index_known_example():
    value = float(heat_index_f(90.0, 70.0))
    assert 105.0 <= value <= 107.0


def test_dewpoint_roundtrip_behavior():
    rh = float(relative_humidity_from_dewpoint(90.0, 79.0))
    assert 65.0 < rh < 75.0
    hi = float(heat_index_from_dewpoint_f(90.0, 79.0))
    assert hi > 100.0


def test_vectorized():
    values = heat_index_f(np.array([90.0, 100.0]), np.array([50.0, 50.0]))
    assert values.shape == (2,)
    assert values[1] > values[0]
