"""Top-level AnalogDiscovery driver.

Aggregates all four instrument subsystems (oscilloscope, AWG, power supply,
digital I/O) and manages the device lifecycle (connect/disconnect).
"""

from __future__ import annotations

from typing import Optional

from .transport import FtdiTransport, TransportError
from .jtag import JtagLoader, BitstreamLoadError
from .pti import PTIController
from .oscilloscope import OscilloscopeChannel
from .awg import WaveformGenerator
from .power import PowerSupply
from .digital import DigitalIO


class AnalogDiscovery:
    """Main entry point for the Analog Discovery driver.

    Manages device lifecycle and exposes instrument subsystems as attributes
    after a successful :meth:`connect` call.

    Args:
        bitstream_path: Path to the Xilinx .bit file for the Spartan-6 FPGA.
    """

    def __init__(self, bitstream_path: str) -> None:
        self._bitstream_path = bitstream_path
        self._transport: Optional[FtdiTransport] = None

        # Instrument subsystems — populated by connect()
        self.scope: OscilloscopeChannel
        self.awg: WaveformGenerator
        self.psu: PowerSupply
        self.dio: DigitalIO

    def connect(self, url: str = "ftdi://0x0403:0x6014/1") -> None:
        """Open USB, load bitstream, init PTI, expose instrument subsystems.

        Args:
            url: pyftdi device URL, e.g. ``"ftdi://0x0403:0x6014/1"``.

        Raises:
            TransportError: If the USB device cannot be opened.
            BitstreamLoadError: If FPGA bitstream loading fails.
        """
        transport = FtdiTransport()
        transport.open(url)  # may raise TransportError

        loader = JtagLoader(transport)
        loader.load(self._bitstream_path)  # may raise BitstreamLoadError

        pti = PTIController(transport)

        self.scope = OscilloscopeChannel(pti)
        self.awg = WaveformGenerator(pti)
        self.psu = PowerSupply(pti)
        self.dio = DigitalIO(pti)

        self._transport = transport

    def disconnect(self) -> None:
        """Close the USB connection. Safe to call even if not connected."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    @property
    def is_connected(self) -> bool:
        """True if the device connection is currently open."""
        return self._transport is not None
