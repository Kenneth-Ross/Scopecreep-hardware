"""Tests for the Analog Discovery FastAPI backend (api/server.py)."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_device() -> MagicMock:
    """Return a fully-mocked AnalogDiscovery instance."""
    device = MagicMock()
    device.is_connected = True

    # scope
    device.scope = MagicMock()
    device.scope.read_samples.return_value = np.array([0.1, 0.2, 0.3], dtype=np.float64)

    # awg
    device.awg = MagicMock()

    # psu
    device.psu = MagicMock()

    # dio
    device.dio = MagicMock()
    device.dio.read.return_value = 0xAB

    return device


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_device():
    """Reset the module-level _device to None before/after each test."""
    import api.server as server_mod
    server_mod._device = None
    yield
    server_mod._device = None


@pytest.fixture()
def client():
    """TestClient with AnalogDiscovery class mocked at the module level."""
    with patch("api.server.AnalogDiscovery") as mock_cls:
        mock_device = _make_mock_device()
        mock_cls.return_value = mock_device

        from api.server import app
        yield TestClient(app), mock_cls, mock_device


@pytest.fixture()
def connected_client(client):
    """TestClient with a device already connected."""
    tc, mock_cls, mock_device = client
    tc.post("/connect", json={"bitstream_path": "/fake/bitstream.bit"})
    return tc, mock_cls, mock_device


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

def test_status_not_connected():
    """GET /status returns connected=false before connect."""
    from api.server import app
    with TestClient(app) as tc:
        r = tc.get("/status")
    assert r.status_code == 200
    assert r.json() == {"connected": False}


# ---------------------------------------------------------------------------
# /connect
# ---------------------------------------------------------------------------

def test_connect_calls_driver(client):
    """POST /connect with valid bitstream_path calls AnalogDiscovery.connect()."""
    tc, mock_cls, mock_device = client
    r = tc.post("/connect", json={"bitstream_path": "/path/to/bitstream.bit"})
    assert r.status_code == 200
    assert r.json() == {"status": "connected"}
    mock_cls.assert_called_once_with("/path/to/bitstream.bit")
    mock_device.connect.assert_called_once()


def test_connect_with_url(client):
    """POST /connect passes url keyword argument when provided."""
    tc, mock_cls, mock_device = client
    r = tc.post(
        "/connect",
        json={"bitstream_path": "/path/to/bitstream.bit", "url": "ftdi://0x0403:0x6014/2"},
    )
    assert r.status_code == 200
    mock_device.connect.assert_called_once_with(url="ftdi://0x0403:0x6014/2")


def test_connect_transport_error(client):
    """POST /connect returns 500 when TransportError is raised."""
    from drivers.analog_discovery import TransportError
    tc, mock_cls, mock_device = client
    mock_device.connect.side_effect = TransportError("USB device not found")
    r = tc.post("/connect", json={"bitstream_path": "/bad/path.bit"})
    assert r.status_code == 500


def test_connect_bitstream_error(client):
    """POST /connect returns 500 when BitstreamLoadError is raised."""
    from drivers.analog_discovery import BitstreamLoadError
    tc, mock_cls, mock_device = client
    mock_device.connect.side_effect = BitstreamLoadError("file not found")
    r = tc.post("/connect", json={"bitstream_path": "/bad/path.bit"})
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# /disconnect
# ---------------------------------------------------------------------------

def test_disconnect(connected_client):
    """POST /disconnect calls device.disconnect() and returns disconnected."""
    tc, _, mock_device = connected_client
    r = tc.post("/disconnect")
    assert r.status_code == 200
    assert r.json() == {"status": "disconnected"}
    mock_device.disconnect.assert_called_once()


def test_disconnect_when_not_connected():
    """POST /disconnect is safe even if no device is connected."""
    from api.server import app
    with TestClient(app) as tc:
        r = tc.post("/disconnect")
    assert r.status_code == 200
    assert r.json() == {"status": "disconnected"}


# ---------------------------------------------------------------------------
# /scope/configure
# ---------------------------------------------------------------------------

def test_scope_configure_503_if_not_connected():
    """POST /scope/configure returns 503 if not connected."""
    from api.server import app
    with TestClient(app) as tc:
        r = tc.post("/scope/configure", json={"ch": 0, "range_v": 5.0})
    assert r.status_code == 503
    assert r.json()["detail"] == "Device not connected"


def test_scope_configure_calls_driver(connected_client):
    """POST /scope/configure calls scope.configure_channel() with correct args."""
    tc, _, mock_device = connected_client
    r = tc.post(
        "/scope/configure",
        json={
            "ch": 0,
            "range_v": 5.0,
            "coupling": "DC",
            "offset": 0.0,
            "num_samples": 512,
            "sample_rate": 500_000.0,
        },
    )
    assert r.status_code == 200
    mock_device.scope.configure_channel.assert_called_once_with(
        channel=0,
        voltage_range=5.0,
        sample_rate=500_000.0,
        num_samples=512,
    )


def test_scope_configure_value_error_returns_400(connected_client):
    """POST /scope/configure returns 400 on ValueError from driver."""
    tc, _, mock_device = connected_client
    mock_device.scope.configure_channel.side_effect = ValueError("Unsupported voltage_range")
    r = tc.post("/scope/configure", json={"ch": 0, "range_v": 999.0})
    assert r.status_code == 400
    assert "Unsupported voltage_range" in r.json()["detail"]


# ---------------------------------------------------------------------------
# /scope/capture
# ---------------------------------------------------------------------------

def test_scope_capture_returns_samples(connected_client):
    """POST /scope/capture returns samples list in response."""
    tc, _, mock_device = connected_client
    mock_device.scope.read_samples.return_value = np.array([1.0, 2.0, 3.0], dtype=np.float64)
    r = tc.post(
        "/scope/capture",
        json={"ch": 0},
    )
    assert r.status_code == 200
    body = r.json()
    assert "samples" in body
    assert body["samples"] == [1.0, 2.0, 3.0]
    assert body["channel"] == 0
    assert body["n_samples"] == 3


def test_scope_capture_calls_arm_trigger(connected_client):
    """POST /scope/capture calls arm_trigger with provided params."""
    tc, _, mock_device = connected_client
    r = tc.post(
        "/scope/capture",
        json={
            "ch": 1,
            "trigger_source": "ch0",
            "trigger_level": 1.5,
            "trigger_edge": "falling",
        },
    )
    assert r.status_code == 200
    mock_device.scope.arm_trigger.assert_called_once_with(
        source="ch0",
        level=1.5,
        edge="falling",
    )


def test_scope_capture_503_if_not_connected():
    """POST /scope/capture returns 503 if not connected."""
    from api.server import app
    with TestClient(app) as tc:
        r = tc.post("/scope/capture", json={"ch": 0})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# /awg/*
# ---------------------------------------------------------------------------

def test_awg_set_converts_waveform(connected_client):
    """POST /awg/set converts list to ndarray and calls set_waveform."""
    tc, _, mock_device = connected_client
    waveform = [0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5]
    r = tc.post("/awg/set", json={"ch": 0, "waveform": waveform, "sample_rate": 8000.0})
    assert r.status_code == 200
    call_args = mock_device.awg.set_waveform.call_args
    assert call_args.kwargs["channel"] == 0
    assert call_args.kwargs["sample_rate"] == 8000.0
    np.testing.assert_array_almost_equal(call_args.kwargs["waveform"], np.array(waveform))


def test_awg_enable(connected_client):
    """POST /awg/enable calls awg.enable() correctly."""
    tc, _, mock_device = connected_client
    r = tc.post("/awg/enable", json={"ch": 1, "enabled": True})
    assert r.status_code == 200
    mock_device.awg.enable.assert_called_once_with(channel=1, enabled=True)


def test_awg_amplitude(connected_client):
    """POST /awg/amplitude calls awg.set_amplitude() correctly."""
    tc, _, mock_device = connected_client
    r = tc.post("/awg/amplitude", json={"ch": 0, "amplitude": 2.5, "offset": 0.5})
    assert r.status_code == 200
    mock_device.awg.set_amplitude.assert_called_once_with(channel=0, amplitude=2.5, offset=0.5)


def test_awg_503_if_not_connected():
    """POST /awg/set returns 503 if not connected."""
    from api.server import app
    with TestClient(app) as tc:
        r = tc.post("/awg/set", json={"ch": 0, "waveform": [0.0]})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# /psu/*
# ---------------------------------------------------------------------------

def test_psu_set_calls_driver(connected_client):
    """POST /psu/set calls psu.set_voltage() with correct args."""
    tc, _, mock_device = connected_client
    r = tc.post("/psu/set", json={"ch": 0, "voltage": 3.3})
    assert r.status_code == 200
    mock_device.psu.set_voltage.assert_called_once_with(channel=0, voltage=3.3)


def test_psu_set_value_error_returns_400(connected_client):
    """POST /psu/set with out-of-range voltage that causes ValueError returns 400."""
    tc, _, mock_device = connected_client
    mock_device.psu.set_voltage.side_effect = ValueError("V+ voltage 99 V out of range")
    r = tc.post("/psu/set", json={"ch": 0, "voltage": 99.0})
    assert r.status_code == 400
    assert "out of range" in r.json()["detail"]


def test_psu_enable(connected_client):
    """POST /psu/enable calls psu.enable() correctly."""
    tc, _, mock_device = connected_client
    r = tc.post("/psu/enable", json={"ch": 0, "enabled": False})
    assert r.status_code == 200
    mock_device.psu.enable.assert_called_once_with(channel=0, enabled=False)


def test_psu_503_if_not_connected():
    """POST /psu/set returns 503 if not connected."""
    from api.server import app
    with TestClient(app) as tc:
        r = tc.post("/psu/set", json={"ch": 0, "voltage": 1.0})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# /dio/*
# ---------------------------------------------------------------------------

def test_dio_direction(connected_client):
    """POST /dio/direction calls dio.set_direction() correctly."""
    tc, _, mock_device = connected_client
    r = tc.post("/dio/direction", json={"pin_mask": 0xFF, "output_mask": 0x0F})
    assert r.status_code == 200
    mock_device.dio.set_direction.assert_called_once_with(pin_mask=0xFF, output_mask=0x0F)


def test_dio_write(connected_client):
    """POST /dio/write calls dio.write() correctly."""
    tc, _, mock_device = connected_client
    r = tc.post("/dio/write", json={"pin_mask": 0x0F, "value": 0x05})
    assert r.status_code == 200
    mock_device.dio.write.assert_called_once_with(pin_mask=0x0F, value_mask=0x05)


def test_dio_read_with_pin_mask(connected_client):
    """GET /dio/read?pin_mask=255 returns masked value."""
    tc, _, mock_device = connected_client
    mock_device.dio.read.return_value = 0xAB & 255  # 0xAB = 171
    r = tc.get("/dio/read", params={"pin_mask": 255})
    assert r.status_code == 200
    body = r.json()
    assert "value" in body
    assert "pin_mask" in body
    assert body["pin_mask"] == 255
    mock_device.dio.read.assert_called_once_with(pin_mask=255)


def test_dio_read_default_pin_mask(connected_client):
    """GET /dio/read without pin_mask defaults to 0xFFFF."""
    tc, _, mock_device = connected_client
    tc.get("/dio/read")
    mock_device.dio.read.assert_called_once_with(pin_mask=0xFFFF)


def test_dio_503_if_not_connected():
    """GET /dio/read returns 503 if not connected."""
    from api.server import app
    with TestClient(app) as tc:
        r = tc.get("/dio/read")
    assert r.status_code == 503
