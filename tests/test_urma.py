import pytest

from app.urma import parse_grib2_message_length


def test_parse_grib2_message_length():
    header = b"GRIB" + b"\x00\x00" + b"\x00" + b"\x02" + (123456).to_bytes(8, "big")
    assert parse_grib2_message_length(header) == 123456


def test_rejects_non_grib():
    with pytest.raises(ValueError):
        parse_grib2_message_length(b"nope" * 4)
