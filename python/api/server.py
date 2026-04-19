"""FastAPI REST backend exposing the Analog Discovery driver.

Holds a single global AnalogDiscovery instance and exposes endpoints for
all four instrument subsystems: oscilloscope, AWG, power supply, and DIO.
"""

from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from drivers.analog_discovery import AnalogDiscovery, TransportError, BitstreamLoadError
from schdoc.parser import parse
from schdoc.llm import generate_understanding
from schdoc.renderer import render

# ---------------------------------------------------------------------------
# Module-level device state
# ---------------------------------------------------------------------------

_device: AnalogDiscovery | None = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # startup/shutdown hooks here if needed
    global _device
    if _device is not None:
        _device.disconnect()


app = FastAPI(title="jbhack Hardware Backend", lifespan=lifespan)

from agent.server import router as _agent_router
app.include_router(_agent_router)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_device() -> AnalogDiscovery:
    """Return the connected device or raise HTTP 503."""
    if _device is None:
        raise HTTPException(status_code=503, detail="Device not connected")
    return _device


# ---------------------------------------------------------------------------
# Pydantic models — request bodies
# ---------------------------------------------------------------------------

class ConnectRequest(BaseModel):
    bitstream_path: str
    url: Optional[str] = None


class ScopeConfigureRequest(BaseModel):
    ch: int
    range_v: float
    coupling: Optional[str] = "DC"
    offset: Optional[float] = 0.0
    num_samples: Optional[int] = 1024
    sample_rate: Optional[float] = 1_000_000.0


class ScopeCaptureRequest(BaseModel):
    ch: int
    n_samples: Optional[int] = None
    trigger_source: Optional[str] = "none"
    trigger_level: Optional[float] = 0.0
    trigger_edge: Optional[str] = "rising"


class AWGSetRequest(BaseModel):
    ch: int
    waveform: List[float]
    sample_rate: Optional[float] = 1_000_000.0


class AWGEnableRequest(BaseModel):
    ch: int
    enabled: bool


class AWGAmplitudeRequest(BaseModel):
    ch: int
    amplitude: float
    offset: Optional[float] = 0.0


class PSUSetRequest(BaseModel):
    ch: int
    voltage: float


class PSUEnableRequest(BaseModel):
    ch: int
    enabled: bool


class DIODirectionRequest(BaseModel):
    pin_mask: int
    output_mask: int


class DIOWriteRequest(BaseModel):
    pin_mask: int
    value: int


# ---------------------------------------------------------------------------
# Schematic parsing endpoint
# ---------------------------------------------------------------------------

@app.post("/schematic/parse", response_class=PlainTextResponse)
async def schematic_parse(file: UploadFile = File(...)):
    """Accept a .SchDoc upload and return a Markdown schematic summary."""
    if not file.filename or not file.filename.lower().endswith(".schdoc"):
        raise HTTPException(status_code=400, detail="File must be a .SchDoc file")
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".SchDoc", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        summary = parse(tmp_path)
        summary.understanding = generate_understanding(summary)
        return render(summary)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Parse error: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/schematic/parse.json")
async def schematic_parse_json(file: UploadFile = File(...)):
    """Structured variant of /schematic/parse — returns the JSON dict the
    agent's `POST /agent/sessions` endpoint expects as the `schematic` body.
    """
    if not file.filename or not file.filename.lower().endswith(".schdoc"):
        raise HTTPException(status_code=400, detail="File must be a .SchDoc file")
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".SchDoc", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        summary = parse(tmp_path)
        summary.understanding = generate_understanding(summary)
        return {
            "board_name": summary.board_name,
            "understanding": summary.understanding,
            "probe_points": [
                {
                    "label": p.label,
                    "net": p.net,
                    "expected_range": p.expected_range,
                    "probe_type": p.probe_type,
                    "designator": p.designator,
                    "pin_name": p.pin_name,
                    "pin_number": p.pin_number,
                }
                for p in summary.probe_points
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Parse error: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Device lifecycle endpoints
# ---------------------------------------------------------------------------

@app.post("/connect")
def connect(req: ConnectRequest):
    """Connect to the Analog Discovery device and load the FPGA bitstream."""
    global _device
    try:
        device = AnalogDiscovery(req.bitstream_path)
        kwargs = {}
        if req.url is not None:
            kwargs["url"] = req.url
        device.connect(**kwargs)
        _device = device
        return {"status": "connected"}
    except (TransportError, BitstreamLoadError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/disconnect")
def disconnect():
    """Disconnect from the device."""
    global _device
    if _device is not None:
        _device.disconnect()
        _device = None
    return {"status": "disconnected"}


@app.get("/status")
def status():
    """Return connection status."""
    return {"connected": _device is not None and _device.is_connected}


# ---------------------------------------------------------------------------
# Oscilloscope endpoints
# ---------------------------------------------------------------------------

@app.post("/scope/configure")
def scope_configure(req: ScopeConfigureRequest):
    """Configure an oscilloscope channel before acquisition."""
    device = _require_device()
    try:
        device.scope.configure_channel(
            channel=req.ch,
            voltage_range=req.range_v,
            sample_rate=req.sample_rate,
            num_samples=req.num_samples,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/scope/capture")
def scope_capture(req: ScopeCaptureRequest):
    """Arm the trigger and read samples from the oscilloscope."""
    device = _require_device()
    try:
        device.scope.arm_trigger(
            source=req.trigger_source,
            level=req.trigger_level,
            edge=req.trigger_edge,
        )
        samples: np.ndarray = device.scope.read_samples(req.ch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sample_list = samples.tolist()
    return {
        "samples": sample_list,
        "channel": req.ch,
        "n_samples": len(sample_list),
    }


# ---------------------------------------------------------------------------
# AWG endpoints
# ---------------------------------------------------------------------------

@app.post("/awg/set")
def awg_set(req: AWGSetRequest):
    """Upload an arbitrary waveform to an AWG channel."""
    device = _require_device()
    try:
        waveform_array = np.array(req.waveform, dtype=np.float64)
        device.awg.set_waveform(
            channel=req.ch,
            waveform=waveform_array,
            sample_rate=req.sample_rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/awg/enable")
def awg_enable(req: AWGEnableRequest):
    """Enable or disable an AWG output channel."""
    device = _require_device()
    try:
        device.awg.enable(channel=req.ch, enabled=req.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/awg/amplitude")
def awg_amplitude(req: AWGAmplitudeRequest):
    """Set the amplitude and DC offset of an AWG channel."""
    device = _require_device()
    try:
        device.awg.set_amplitude(channel=req.ch, amplitude=req.amplitude, offset=req.offset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# PSU endpoints
# ---------------------------------------------------------------------------

@app.post("/psu/set")
def psu_set(req: PSUSetRequest):
    """Program the output voltage for a PSU rail."""
    device = _require_device()
    try:
        device.psu.set_voltage(channel=req.ch, voltage=req.voltage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/psu/enable")
def psu_enable(req: PSUEnableRequest):
    """Enable or disable a PSU output rail."""
    device = _require_device()
    try:
        device.psu.enable(channel=req.ch, enabled=req.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Digital I/O endpoints
# ---------------------------------------------------------------------------

@app.post("/dio/direction")
def dio_direction(req: DIODirectionRequest):
    """Configure pin directions within a masked set."""
    device = _require_device()
    try:
        device.dio.set_direction(pin_mask=req.pin_mask, output_mask=req.output_mask)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/dio/write")
def dio_write(req: DIOWriteRequest):
    """Drive output pins to the given logic levels."""
    device = _require_device()
    try:
        device.dio.write(pin_mask=req.pin_mask, value_mask=req.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.get("/dio/read")
def dio_read(pin_mask: int = Query(default=0xFFFF)):
    """Sample the current logic level of selected pins."""
    device = _require_device()
    try:
        value = device.dio.read(pin_mask=pin_mask)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"value": value, "pin_mask": pin_mask}
