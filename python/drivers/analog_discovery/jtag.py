"""Spartan-6 FPGA bitstream loader via JTAG.

Loads a Xilinx .bit file into the FPGA connected through an FtdiTransport.
The sequence follows the Spartan-6 configuration specification:
  1. Assert PROGRAM_B to initiate reconfiguration
  2. Wait for INIT_B to go low (FPGA clearing config memory)
  3. Release PROGRAM_B
  4. Wait for INIT_B to go high (FPGA ready)
  5. Shift the bitstream payload via JTAG
  6. Wait for DONE to go high (configuration complete)
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .transport import FtdiTransport, TransportError

# ---------------------------------------------------------------------------
# Bit-reversal lookup table (precomputed for performance)
# Xilinx JTAG bitstream bytes must be sent bit-reversed (MSB of file byte →
# LSB of JTAG shift byte).
# ---------------------------------------------------------------------------
_BIT_REVERSE_TABLE: list[int] = [
    int(f"{b:08b}"[::-1], 2) for b in range(256)
]

# Sync word that marks the start of the Spartan-6 configuration bitstream
# payload within a .bit file.
_SYNC_WORD: bytes = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xAA, 0x99, 0x55, 0x66])

# Poll interval between GPIO reads (seconds)
_POLL_INTERVAL_S: float = 0.001  # 1 ms


class BitstreamLoadError(Exception):
    """Raised when FPGA bitstream loading fails for any reason."""


class JtagLoader:
    """Loads a Xilinx Spartan-6 bitstream via JTAG using an FtdiTransport.

    Args:
        transport: An open (or soon-to-be-opened) FtdiTransport instance.
    """

    def __init__(self, transport: FtdiTransport) -> None:
        self._transport = transport

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, bitstream_path: str, timeout_s: float = 5.0) -> None:
        """Load a Xilinx .bit file into the FPGA.

        Args:
            bitstream_path: Filesystem path to the .bit file.
            timeout_s: Maximum seconds to wait for each GPIO polling phase.

        Raises:
            BitstreamLoadError: On any timeout, I/O error, or parse failure.
        """
        # Step 1: Read and parse the bitstream before touching hardware,
        # so a bad file fails fast without disturbing the FPGA.
        payload = self._load_bitstream(bitstream_path)

        t = self._transport
        try:
            # Step 2: Assert PROGRAM_B low — FPGA enters reconfiguration mode.
            t.assert_program_b()

            # Step 3: Wait for INIT_B to go low (FPGA clearing config RAM).
            self._poll_gpio(
                read_fn=t.read_init_b,
                expected=False,
                timeout_s=timeout_s,
                error_msg=(
                    f"Timed out after {timeout_s:.1f}s waiting for INIT_B to go low "
                    "(FPGA did not enter reconfiguration mode)"
                ),
            )

            # Step 4: Release PROGRAM_B — FPGA begins initialisation.
            t.release_program_b()

            # Step 5: Wait for INIT_B to go high (FPGA ready to receive bitstream).
            self._poll_gpio(
                read_fn=t.read_init_b,
                expected=True,
                timeout_s=timeout_s,
                error_msg=(
                    f"Timed out after {timeout_s:.1f}s waiting for INIT_B to go high "
                    "(FPGA did not become ready for configuration)"
                ),
            )

            # Step 6: Shift the bit-reversed payload.
            self._shift_payload(payload)

            # Step 7: Poll DONE until high.
            self._poll_gpio(
                read_fn=t.read_done,
                expected=True,
                timeout_s=timeout_s,
                error_msg=(
                    f"Timed out after {timeout_s:.1f}s waiting for DONE to go high "
                    "(bitstream may be corrupt or FPGA configuration failed)"
                ),
            )

        except TransportError as exc:
            raise BitstreamLoadError(f"Transport error during bitstream load: {exc}") from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_bitstream(self, path: str) -> bytes:
        """Read and parse a Xilinx .bit file, returning the payload bytes.

        Scans forward in the file to find the sync word
        ``FF FF FF FF AA 99 55 66`` and returns everything from that
        offset onward (bit-reversed, ready for JTAG shifting).

        Args:
            path: Path to the .bit file.

        Returns:
            Bit-reversed bitstream payload bytes.

        Raises:
            BitstreamLoadError: If the file cannot be read or the sync word
                is not found.
        """
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise BitstreamLoadError(
                f"Cannot read bitstream file {path!r}: {exc}"
            ) from exc

        offset = raw.find(_SYNC_WORD)
        if offset == -1:
            raise BitstreamLoadError(
                f"Sync word not found in {path!r}; file may not be a valid "
                "Xilinx .bit bitstream"
            )

        payload_raw = raw[offset:]
        # Bit-reverse every byte for Xilinx JTAG convention.
        return bytes(_BIT_REVERSE_TABLE[b] for b in payload_raw)

    def _shift_payload(self, payload: bytes) -> None:
        """Send the bitstream payload to the FPGA via jtag_shift().

        Args:
            payload: Bit-reversed bitstream bytes.

        Raises:
            BitstreamLoadError: If the payload is empty.
        """
        if not payload:
            raise BitstreamLoadError("Bitstream payload is empty after sync word")
        num_bits = len(payload) * 8
        self._transport.jtag_shift(payload, num_bits, last=False)  # last=False: FPGA exits JTAG cfg mode autonomously via DONE; no TAP exit needed

    @staticmethod
    def _poll_gpio(
        read_fn: Callable[[], bool],
        expected: bool,
        timeout_s: float,
        error_msg: str,
    ) -> None:
        """Poll a GPIO read function until it returns *expected* or timeout.

        Args:
            read_fn: Callable returning a bool GPIO level.
            expected: The level to wait for.
            timeout_s: Maximum wait time in seconds.
            error_msg: Message for the raised exception on timeout.

        Raises:
            BitstreamLoadError: If the expected level is not seen within
                *timeout_s* seconds.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            if time.monotonic() >= deadline:
                raise BitstreamLoadError(error_msg)
            if read_fn() == expected:
                return
            time.sleep(_POLL_INTERVAL_S)
