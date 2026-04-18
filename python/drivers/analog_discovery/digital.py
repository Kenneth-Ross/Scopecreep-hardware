"""Digital I/O instrument module for the Digilent Analog Discovery.

Implements :class:`~python.drivers.base.DigitalIOBase` using PTI commands.

The Analog Discovery exposes 16 DIO pins (3.3 V LVCMOS) controlled by
bitmask registers for direction, output value, and read-back.
"""

from __future__ import annotations

import struct

from ..base import DigitalIOBase
from .pti import PTIController, dio_set_dir, dio_write, dio_read

_PIN_MASK_16 = 0xFFFF  # 16-bit pin universe


class DigitalIO(DigitalIOBase):
    """Digital I/O driver for the Analog Discovery (16-pin, 3.3 V LVCMOS).

    Direction and output registers are maintained in software and sent to
    the device on every change.  Only bits covered by *pin_mask* are
    modified; other bits retain their current state.
    """

    def __init__(self, pti: PTIController) -> None:
        self._pti = pti
        self._direction: int = 0x0000   # 0 = input, 1 = output (per bit)
        self._output_value: int = 0x0000

    # ------------------------------------------------------------------
    # DigitalIOBase implementation
    # ------------------------------------------------------------------

    def set_direction(self, pin_mask: int, output_mask: int) -> None:
        """Configure pin directions within a masked set.

        Args:
            pin_mask: Bitmask selecting which pins to configure.
            output_mask: Bitmask where 1 = output, 0 = input (within pin_mask).
        """
        # Clear selected pins, then OR in the new direction bits
        self._direction = (self._direction & ~pin_mask) | (output_mask & pin_mask)
        self._direction &= _PIN_MASK_16
        self._pti.send(dio_set_dir(mask=self._direction))

    def write(self, pin_mask: int, value_mask: int) -> None:
        """Drive output pins to the given logic levels.

        Args:
            pin_mask: Bitmask selecting which output pins to drive.
            value_mask: Bitmask of logic levels to write (1 = high, 0 = low).
        """
        # Clear selected pins, then OR in the new values
        self._output_value = (self._output_value & ~pin_mask) | (value_mask & pin_mask)
        self._output_value &= _PIN_MASK_16
        self._pti.send(dio_write(value=self._output_value))

    def read(self, pin_mask: int) -> int:
        """Sample the current logic level of selected pins.

        Args:
            pin_mask: Bitmask selecting which pins to read.

        Returns:
            Integer whose bits reflect the sampled pin levels (within pin_mask).
        """
        cmd = dio_read()
        raw = self._pti.send(cmd)
        # Response is 2 bytes big-endian unsigned
        (pin_state,) = struct.unpack(">H", raw[:2])
        return pin_state & pin_mask
