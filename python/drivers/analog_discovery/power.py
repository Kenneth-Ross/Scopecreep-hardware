"""Power supply instrument module for the Digilent Analog Discovery.

Implements :class:`~python.drivers.base.PowerSupplyBase` using PTI commands.

The Analog Discovery exposes two programmable supply rails:
- Channel 0 → V+: 0 to +5 V, 12-bit DAC (0–4095)
- Channel 1 → V−: 0 to −5 V, 12-bit DAC (0–4095, maps to magnitude)
"""

from __future__ import annotations

from ..base import PowerSupplyBase
from .pti import PTIController, psu_vpos, psu_vneg

PSU_V_MAX = 5.0   # V+ maximum output voltage
PSU_V_MIN = -5.0  # V− minimum output voltage (most negative)
_DAC_FULL_SCALE = 4095  # 12-bit DAC


def _vpos_code(voltage: float) -> int:
    """Convert a V+ voltage [0, +5 V] to a 12-bit DAC code."""
    code = int(voltage / PSU_V_MAX * _DAC_FULL_SCALE)
    return max(0, min(_DAC_FULL_SCALE, code))


def _vneg_code(voltage: float) -> int:
    """Convert a V− voltage [−5 V, 0] to a 12-bit DAC code (magnitude)."""
    code = int(abs(voltage) / PSU_V_MAX * _DAC_FULL_SCALE)
    return max(0, min(_DAC_FULL_SCALE, code))


class PowerSupply(PowerSupplyBase):
    """Programmable PSU driver for the Analog Discovery V+ / V− rails.

    ``enable(ch, False)`` drives the rail to 0 V; ``enable(ch, True)``
    restores the last programmed voltage.
    """

    def __init__(self, pti: PTIController) -> None:
        self._pti = pti
        # Track last set voltage and enabled state per channel
        self._voltage: dict[int, float] = {0: 0.0, 1: 0.0}
        self._enabled: dict[int, bool] = {0: True, 1: True}

    # ------------------------------------------------------------------
    # PowerSupplyBase implementation
    # ------------------------------------------------------------------

    def set_voltage(self, channel: int, voltage: float) -> None:
        """Program the output voltage for a supply rail.

        Args:
            channel: 0 for V+ (0–+5 V), 1 for V− (0–−5 V).
            voltage: Target output voltage in volts.

        Raises:
            ValueError: If *channel* is unknown or *voltage* is out of range.
        """
        if channel == 0:
            if not (0.0 <= voltage <= PSU_V_MAX):
                raise ValueError(
                    f"V+ voltage {voltage!r} V out of range [0, {PSU_V_MAX}]"
                )
            self._voltage[channel] = voltage
            if self._enabled.get(channel, True):
                self._pti.send(psu_vpos(_vpos_code(voltage)))
        elif channel == 1:
            if not (PSU_V_MIN <= voltage <= 0.0):
                raise ValueError(
                    f"V− voltage {voltage!r} V out of range [{PSU_V_MIN}, 0]"
                )
            self._voltage[channel] = voltage
            if self._enabled.get(channel, True):
                self._pti.send(psu_vneg(_vneg_code(voltage)))
        else:
            raise ValueError(f"Unknown PSU channel {channel!r}. Valid: 0 (V+), 1 (V−)")

    def enable(self, channel: int, enabled: bool) -> None:
        """Enable or disable a supply output rail.

        When disabled, the rail is driven to 0 V.  When re-enabled, the
        last programmed voltage is restored.

        Args:
            channel: 0 for V+, 1 for V−.
            enabled: True to enable the rail, False to disable.

        Raises:
            ValueError: If *channel* is unknown.
        """
        if channel not in (0, 1):
            raise ValueError(f"Unknown PSU channel {channel!r}. Valid: 0 (V+), 1 (V−)")

        self._enabled[channel] = enabled

        if channel == 0:
            code = _vpos_code(self._voltage[0]) if enabled else 0
            self._pti.send(psu_vpos(code))
        else:
            code = _vneg_code(self._voltage[1]) if enabled else 0
            self._pti.send(psu_vneg(code))
