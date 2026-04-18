"""Abstract base classes for hardware instrument drivers.

Each concrete driver (e.g. Analog Discovery) implements one or more of these
interfaces. The FastAPI layer programs exclusively against these abstractions.
"""

from abc import ABC, abstractmethod

import numpy as np


class OscilloscopeBase(ABC):
    """Interface contract for oscilloscope / digitizer instruments."""

    @abstractmethod
    def configure_channel(
        self,
        channel: int,
        voltage_range: float,
        sample_rate: float,
        num_samples: int,
    ) -> None:
        """Configure an input channel before acquisition.

        Args:
            channel: Zero-based channel index.
            voltage_range: Full-scale input range in volts (peak-to-peak).
            sample_rate: Desired sample rate in Hz.
            num_samples: Number of samples to acquire per trigger.
        """

    @abstractmethod
    def arm_trigger(
        self,
        source: str = "none",
        level: float = 0.0,
        edge: str = "rising",
    ) -> None:
        """Arm the trigger and prepare for acquisition.

        Args:
            source: Trigger source identifier (e.g. "ch0", "ext", "none").
            level: Trigger threshold in volts.
            edge: Edge polarity — "rising" or "falling".
        """

    @abstractmethod
    def read_samples(self, channel: int) -> np.ndarray:
        """Block until acquisition completes and return sample data.

        Args:
            channel: Zero-based channel index to read.

        Returns:
            1-D float64 array of voltage samples.
        """


class AWGBase(ABC):
    """Interface contract for arbitrary waveform generator outputs."""

    @abstractmethod
    def set_waveform(self, channel: int, waveform: np.ndarray, sample_rate: float) -> None:
        """Upload an arbitrary waveform to a channel.

        Args:
            channel: Zero-based AWG channel index.
            waveform: Normalized float64 array in [-1.0, 1.0]; scaled to ±5 V by hardware.
            sample_rate: Playback sample rate in Hz.
        """

    @abstractmethod
    def enable(self, channel: int, enabled: bool) -> None:
        """Enable or disable an AWG output channel.

        Args:
            channel: Zero-based AWG channel index.
            enabled: True to enable the output, False to disable.
        """

    @abstractmethod
    def set_amplitude(self, channel: int, amplitude: float, offset: float = 0.0) -> None:
        """Set the output amplitude and DC offset for a channel.

        Args:
            channel: Zero-based AWG channel index.
            amplitude: Peak amplitude in volts.
            offset: DC offset in volts (default 0.0).
        """


class PowerSupplyBase(ABC):
    """Interface contract for programmable power supply outputs."""

    @abstractmethod
    def set_voltage(self, channel: int, voltage: float) -> None:
        """Program the output voltage for a supply rail.

        Args:
            channel: Zero-based supply channel index.
            voltage: Target output voltage in volts.
        """

    @abstractmethod
    def enable(self, channel: int, enabled: bool) -> None:
        """Enable or disable a supply output rail.

        Args:
            channel: Zero-based supply channel index.
            enabled: True to enable the rail, False to disable (output off).
        """


class DigitalIOBase(ABC):
    """Interface contract for digital I/O pin banks."""

    @abstractmethod
    def set_direction(self, pin_mask: int, output_mask: int) -> None:
        """Configure pin directions within a masked set.

        Args:
            pin_mask: Bitmask selecting which pins to configure.
            output_mask: Bitmask where 1 = output, 0 = input (within pin_mask).
        """

    @abstractmethod
    def write(self, pin_mask: int, value_mask: int) -> None:
        """Drive output pins to the given logic levels.

        Args:
            pin_mask: Bitmask selecting which output pins to drive.
            value_mask: Bitmask of logic levels to write (1 = high, 0 = low).
        """

    @abstractmethod
    def read(self, pin_mask: int) -> int:
        """Sample the current logic level of selected pins.

        Args:
            pin_mask: Bitmask selecting which pins to read.

        Returns:
            Integer whose bits reflect the sampled pin levels (within pin_mask).
        """
