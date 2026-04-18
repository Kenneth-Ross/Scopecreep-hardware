from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from drivers.dps150 import DPS150
from config import PSU_BAUD, PSU_TIMEOUT

router = APIRouter(prefix="/psu")
_driver: DPS150 | None = None


class ConnectRequest(BaseModel):
    port: str

class VoltageRequest(BaseModel):
    volts: float

class CurrentRequest(BaseModel):
    amps: float

class OutputRequest(BaseModel):
    enabled: bool


def _require_connection() -> DPS150:
    if _driver is None or not _driver.is_connected:
        raise HTTPException(status_code=503, detail="PSU not connected")
    return _driver


@router.post("/connect")
def connect(req: ConnectRequest):
    global _driver
    import serial as _serial
    if _driver and _driver.is_connected:
        _driver.disconnect()
    _driver = DPS150(port=req.port, baud=PSU_BAUD, timeout=PSU_TIMEOUT)
    try:
        _driver.connect()
    except _serial.SerialException as e:
        _driver = None
        raise HTTPException(status_code=503, detail=f"Cannot open port {req.port}: {e}")
    return {"status": "connected", "port": req.port}


@router.delete("/disconnect")
def disconnect():
    global _driver
    if _driver:
        _driver.disconnect()
        _driver = None
    return {"status": "disconnected"}


@router.get("/measurements")
def measurements():
    return _require_connection().get_measurements()


@router.get("/state")
def state():
    return _require_connection().get_all_state()


@router.post("/voltage")
def set_voltage(req: VoltageRequest):
    _require_connection().set_voltage(req.volts)
    return {"status": "ok", "voltage_set": req.volts}


@router.post("/current")
def set_current(req: CurrentRequest):
    _require_connection().set_current(req.amps)
    return {"status": "ok", "current_set": req.amps}


@router.post("/output")
def set_output(req: OutputRequest):
    _require_connection().enable_output(req.enabled)
    return {"status": "ok", "output_enabled": req.enabled}
