import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

@pytest.fixture
def client():
    with patch("api.routes.psu.DPS150") as mock_cls:
        mock_driver = MagicMock()
        mock_driver.is_connected = True
        mock_driver.get_measurements.return_value = {
            "voltage": 5.0, "current": 1.0, "power": 5.0
        }
        mock_driver.get_all_state.return_value = {
            "voltage": 5.0, "current": 1.0, "power": 5.0,
            "voltage_set": 5.0, "current_set": 1.5,
            "output_enabled": True, "mode": "CV",
            "temperature": 25.0, "input_voltage": 12.0,
            "protection": 0, "voltage_max": 30.0, "current_max": 5.0,
        }
        mock_cls.return_value = mock_driver

        from api.main import app
        yield TestClient(app)

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200

def test_connect(client):
    r = client.post("/psu/connect", json={"port": "/dev/ttyACM0"})
    assert r.status_code == 200

def test_get_measurements(client):
    client.post("/psu/connect", json={"port": "/dev/ttyACM0"})
    r = client.get("/psu/measurements")
    assert r.status_code == 200
    data = r.json()
    assert "voltage" in data and "current" in data

def test_set_voltage(client):
    client.post("/psu/connect", json={"port": "/dev/ttyACM0"})
    r = client.post("/psu/voltage", json={"volts": 5.0})
    assert r.status_code == 200

def test_set_current(client):
    client.post("/psu/connect", json={"port": "/dev/ttyACM0"})
    r = client.post("/psu/current", json={"amps": 1.5})
    assert r.status_code == 200

def test_enable_output(client):
    client.post("/psu/connect", json={"port": "/dev/ttyACM0"})
    r = client.post("/psu/output", json={"enabled": True})
    assert r.status_code == 200

def test_disconnect(client):
    client.post("/psu/connect", json={"port": "/dev/ttyACM0"})
    r = client.delete("/psu/disconnect")
    assert r.status_code == 200
