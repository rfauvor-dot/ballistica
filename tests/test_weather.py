"""Nearest-station weather lookup (ballistica/weather.py). No network: httpx.get
is replaced. Live bug 2026-11-25: the METAR API began refusing a search box
written at a phone's full GPS precision, so every lookup failed."""
import logging

import httpx

from ballistica import weather

_STATION = {
    "icaoId": "KMAN", "name": "Nampa Muni, ID, US", "lat": 43.58, "lon": -116.52,
    "temp": 7.0, "dewp": 1.0, "altim": 1025.8, "wspd": 11, "elev": 773, "obsTime": 1795715760,
}


def _fake_get(captured, reply=None, status=200):
    def get(url, params=None, headers=None, timeout=None):
        captured.append(params)
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(status, json=[_STATION] if reply is None else reply, request=request)
    return get


def test_search_box_is_rounded_from_full_gps_precision(monkeypatch):
    captured = []
    monkeypatch.setattr(weather.httpx, "get", _fake_get(captured))
    result = weather.fetch_nearest_station_conditions(43.6205708, -116.4468167)
    assert captured[0]["bbox"] == "42.8706,-117.1968,44.3706,-115.6968"
    for part in captured[0]["bbox"].split(","):
        assert len(part.split(".")[1]) <= 4
    assert result is not None and result.station_id == "KMAN"
    assert round(result.temp_f, 1) == 44.6


def test_a_refusal_is_logged_and_still_returns_none(monkeypatch, caplog):
    captured = []
    refusal = {"status": "error", "error": "Must specify station IDs or bounding box, zoom, and density"}
    monkeypatch.setattr(weather.httpx, "get", _fake_get(captured, reply=refusal, status=400))
    with caplog.at_level(logging.WARNING, logger="ballistica.weather"):
        assert weather.fetch_nearest_station_conditions(43.62, -116.44) is None
    assert "METAR lookup failed" in caplog.text and "Must specify station IDs" in caplog.text


def test_an_error_object_with_status_200_is_not_a_station_list(monkeypatch, caplog):
    captured = []
    monkeypatch.setattr(weather.httpx, "get", _fake_get(captured, reply={"status": "error"}))
    with caplog.at_level(logging.WARNING, logger="ballistica.weather"):
        assert weather.fetch_nearest_station_conditions(43.62, -116.44) is None
    assert "unexpected reply" in caplog.text
