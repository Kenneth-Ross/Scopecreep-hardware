"""WaveForms-SDK backend for the Analog Discovery.

Drop-in replacement for the custom-bitstream :class:`AnalogDiscovery`
driver. Uses Digilent's stock firmware via pydwf, so no .bit file is
required. Exposes the same ``.scope`` and ``.psu`` attributes the agent
tools consume, with the same lifecycle (``connect`` / ``disconnect``).

Requirements:
  - WaveForms runtime installed (``dwf.framework`` on macOS, ``libdwf.so``
    on Linux). Install the WaveForms package from digilent.com.
  - ``pip install pydwf``.

Only one process may own the device at a time; close the WaveForms GUI
before connecting.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

from ..base import OscilloscopeBase, PowerSupplyBase


def _pydwf():
    """Lazy import so the module loads even when pydwf isn't installed."""
    from pydwf import (
        DwfAcquisitionMode,
        DwfLibrary,
        DwfState,
        DwfTriggerSlope,
        DwfTriggerSource,
    )
    from pydwf.utilities import openDwfDevice
    return (
        DwfAcquisitionMode,
        DwfLibrary,
        DwfState,
        DwfTriggerSlope,
        DwfTriggerSource,
        openDwfDevice,
    )


# ---------------------------------------------------------------------------
# Oscilloscope
# ---------------------------------------------------------------------------

_CAPTURE_TIMEOUT_S = 5.0


class WaveFormsOscilloscope(OscilloscopeBase):
    """Analog Discovery oscilloscope via pydwf's AnalogIn API."""

    def __init__(self, hdwf) -> None:
        (
            self._DwfAcquisitionMode,
            _,
            self._DwfState,
            self._DwfTriggerSlope,
            self._DwfTriggerSource,
            _,
        ) = _pydwf()
        self._scope = hdwf.analogIn
        self._num_samples: dict[int, int] = {}

    def configure_channel(
        self,
        channel: int,
        voltage_range: float,
        sample_rate: float,
        num_samples: int,
    ) -> None:
        self._scope.channelEnableSet(channel, True)
        self._scope.channelRangeSet(channel, voltage_range)
        self._scope.channelOffsetSet(channel, 0.0)
        self._scope.frequencySet(sample_rate)
        self._scope.bufferSizeSet(num_samples)
        self._scope.acquisitionModeSet(self._DwfAcquisitionMode.Single)
        self._num_samples[channel] = num_samples

    def arm_trigger(
        self,
        source: str = "none",
        level: float = 0.0,
        edge: str = "rising",
    ) -> None:
        TrigSrc = self._DwfTriggerSource
        Slope = self._DwfTriggerSlope
        if source == "none":
            self._scope.triggerSourceSet(TrigSrc.None_)
        elif source in ("ch0", "ch1"):
            trig_ch = 0 if source == "ch0" else 1
            self._scope.triggerSourceSet(TrigSrc.DetectorAnalogIn)
            self._scope.triggerChannelSet(trig_ch)
            self._scope.triggerTypeSet(0)  # edge
            self._scope.triggerLevelSet(level)
            self._scope.triggerConditionSet(
                Slope.Fall if edge == "falling" else Slope.Rise
            )
        elif source == "ext":
            self._scope.triggerSourceSet(TrigSrc.External1)
        else:
            raise ValueError(
                f"Unknown trigger source {source!r}. Valid: none, ch0, ch1, ext"
            )
        self._scope.configure(False, True)  # arm + start acquisition

    def read_samples(self, channel: int) -> np.ndarray:
        if channel not in self._num_samples:
            raise ValueError(
                f"Channel {channel} not configured; call configure_channel() first"
            )
        Done = self._DwfState.Done
        deadline = time.monotonic() + _CAPTURE_TIMEOUT_S
        while self._scope.status(True) != Done:
            if time.monotonic() > deadline:
                raise TimeoutError("Timed out waiting for scope acquisition to complete")
            time.sleep(0.005)
        return self._scope.statusData(channel, self._num_samples[channel])


# ---------------------------------------------------------------------------
# Power supply (V+ / V-)
# ---------------------------------------------------------------------------
#
# pydwf exposes the fixed supplies through the AnalogIO subsystem. On the
# original Analog Discovery and AD2, the node layout is:
#   channel 0 = master power enable
#   channel 1 = V+  (node 0 = enable, node 1 = voltage setpoint)
#   channel 2 = V-  (node 0 = enable, node 1 = voltage setpoint)
# AD3 uses the same layout. If a future device reorders these, adjust the
# constants below.
_MASTER_CH = 0
_MASTER_NODE_ENABLE = 0
_VPOS_CH, _VNEG_CH = 1, 2
_NODE_ENABLE, _NODE_VOLTAGE = 0, 1

_V_MAX = 5.0   # V+ ceiling
_V_MIN = -5.0  # V- floor


class WaveFormsPowerSupply(PowerSupplyBase):
    """Fixed V+/V- supplies on the Analog Discovery via AnalogIO."""

    def __init__(self, hdwf) -> None:
        self._aio = hdwf.analogIO
        self._aio.enableSet(True)
        self._voltage: dict[int, float] = {0: 0.0, 1: 0.0}
        self._enabled: dict[int, bool] = {0: False, 1: False}
        self._aio.configure()

    def set_voltage(self, channel: int, voltage: float) -> None:
        if channel == 0:
            if not (0.0 <= voltage <= _V_MAX):
                raise ValueError(f"V+ voltage {voltage} out of range [0, {_V_MAX}]")
            self._voltage[0] = voltage
            self._aio.channelNodeSet(_VPOS_CH, _NODE_VOLTAGE, voltage)
        elif channel == 1:
            if not (_V_MIN <= voltage <= 0.0):
                raise ValueError(f"V- voltage {voltage} out of range [{_V_MIN}, 0]")
            self._voltage[1] = voltage
            self._aio.channelNodeSet(_VNEG_CH, _NODE_VOLTAGE, voltage)
        else:
            raise ValueError(f"Unknown PSU channel {channel!r}. Valid: 0 (V+), 1 (V-)")
        self._aio.configure()

    def enable(self, channel: int, enabled: bool) -> None:
        if channel not in (0, 1):
            raise ValueError(f"Unknown PSU channel {channel!r}. Valid: 0 (V+), 1 (V-)")
        self._enabled[channel] = enabled
        rail_ch = _VPOS_CH if channel == 0 else _VNEG_CH
        self._aio.channelNodeSet(rail_ch, _NODE_ENABLE, 1.0 if enabled else 0.0)
        # Master supply enable tracks whether either rail is on.
        any_on = any(self._enabled.values())
        self._aio.channelNodeSet(_MASTER_CH, _MASTER_NODE_ENABLE, 1.0 if any_on else 0.0)
        self._aio.configure()


# ---------------------------------------------------------------------------
# Top-level device
# ---------------------------------------------------------------------------


class WaveFormsAnalogDiscovery:
    """Mirror of :class:`AnalogDiscovery`'s public surface.

    Exposes ``.scope`` and ``.psu`` after :meth:`connect`. Safe to
    substitute into :class:`HardwareContext` in place of the custom-
    bitstream driver.

    Args:
        bitstream_path: Accepted but ignored; kept for API compatibility
            with :class:`AnalogDiscovery`.
    """

    def __init__(self, bitstream_path: str = "") -> None:
        self._hdwf = None
        self._cm = None
        self.scope: WaveFormsOscilloscope
        self.psu: WaveFormsPowerSupply

    def connect(self, url: str = "") -> None:
        """Open the first Analog Discovery found on USB.

        Args:
            url: Ignored; kept for API compatibility with the custom-
                bitstream driver. pydwf does not use pyftdi URLs.
        """
        _, DwfLibrary, _, _, _, openDwfDevice = _pydwf()
        cm = openDwfDevice(DwfLibrary())
        self._hdwf = cm.__enter__()
        self._cm = cm

        self.scope = WaveFormsOscilloscope(self._hdwf)
        self.psu = WaveFormsPowerSupply(self._hdwf)

    def disconnect(self) -> None:
        if self._hdwf is None:
            return
        try:
            self._cm.__exit__(None, None, None)
        finally:
            self._hdwf = None
            self._cm = None

    @property
    def is_connected(self) -> bool:
        return self._hdwf is not None
