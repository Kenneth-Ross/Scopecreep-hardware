"""Unit tests for the top-level AnalogDiscovery driver class.

All dependencies (FtdiTransport, JtagLoader, PTIController, instrument
subsystems) are mocked so no hardware is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest

from python.drivers.analog_discovery.transport import TransportError
from python.drivers.analog_discovery.jtag import BitstreamLoadError

# Module path prefix for patching
_MOD = "python.drivers.analog_discovery.driver"


def _make_driver(bitstream_path: str = "/fake/top.bit"):
    from python.drivers.analog_discovery.driver import AnalogDiscovery
    return AnalogDiscovery(bitstream_path)


@pytest.fixture()
def mock_deps():
    """Patch all dependencies inside driver.py and return the mock objects."""
    with (
        patch(f"{_MOD}.FtdiTransport") as MockTransport,
        patch(f"{_MOD}.JtagLoader") as MockLoader,
        patch(f"{_MOD}.PTIController") as MockPTI,
        patch(f"{_MOD}.OscilloscopeChannel") as MockScope,
        patch(f"{_MOD}.WaveformGenerator") as MockAWG,
        patch(f"{_MOD}.PowerSupply") as MockPSU,
        patch(f"{_MOD}.DigitalIO") as MockDIO,
    ):
        transport_instance = MockTransport.return_value
        loader_instance = MockLoader.return_value
        pti_instance = MockPTI.return_value

        yield {
            "MockTransport": MockTransport,
            "MockLoader": MockLoader,
            "MockPTI": MockPTI,
            "MockScope": MockScope,
            "MockAWG": MockAWG,
            "MockPSU": MockPSU,
            "MockDIO": MockDIO,
            "transport": transport_instance,
            "loader": loader_instance,
            "pti": pti_instance,
        }


class TestConnect:
    def test_transport_open_called_with_url(self, mock_deps):
        """connect() must call transport.open() with the given URL."""
        drv = _make_driver()
        drv.connect("ftdi://0x0403:0x6014/1")
        mock_deps["transport"].open.assert_called_once_with("ftdi://0x0403:0x6014/1")

    def test_loader_load_called_with_bitstream_path(self, mock_deps):
        """connect() must call loader.load() with the stored bitstream path."""
        drv = _make_driver("/path/to/spartan6.bit")
        drv.connect()
        mock_deps["loader"].load.assert_called_once_with("/path/to/spartan6.bit")

    def test_jtag_loader_created_with_transport(self, mock_deps):
        """JtagLoader must be instantiated with the FtdiTransport instance."""
        drv = _make_driver()
        drv.connect()
        mock_deps["MockLoader"].assert_called_once_with(mock_deps["transport"])

    def test_pti_controller_created_with_transport(self, mock_deps):
        """PTIController must be instantiated with the FtdiTransport instance."""
        drv = _make_driver()
        drv.connect()
        mock_deps["MockPTI"].assert_called_once_with(mock_deps["transport"])

    def test_instruments_created_with_pti(self, mock_deps):
        """All four instrument subsystems must be created with the PTIController."""
        drv = _make_driver()
        drv.connect()
        pti = mock_deps["pti"]
        mock_deps["MockScope"].assert_called_once_with(pti)
        mock_deps["MockAWG"].assert_called_once_with(pti)
        mock_deps["MockPSU"].assert_called_once_with(pti)
        mock_deps["MockDIO"].assert_called_once_with(pti)

    def test_subsystems_accessible_after_connect(self, mock_deps):
        """scope, awg, psu, dio attributes must be set after connect()."""
        drv = _make_driver()
        drv.connect()
        assert drv.scope is mock_deps["MockScope"].return_value
        assert drv.awg is mock_deps["MockAWG"].return_value
        assert drv.psu is mock_deps["MockPSU"].return_value
        assert drv.dio is mock_deps["MockDIO"].return_value

    def test_raises_transport_error_if_open_fails(self, mock_deps):
        """connect() must propagate TransportError from transport.open()."""
        mock_deps["transport"].open.side_effect = TransportError("device not found")
        drv = _make_driver()
        with pytest.raises(TransportError):
            drv.connect()

    def test_raises_bitstream_error_if_load_fails(self, mock_deps):
        """connect() must propagate BitstreamLoadError from loader.load()."""
        mock_deps["loader"].load.side_effect = BitstreamLoadError("DONE timeout")
        drv = _make_driver()
        with pytest.raises(BitstreamLoadError):
            drv.connect()

    def test_default_url_used_when_none_given(self, mock_deps):
        """connect() with no args must pass the default FTDI URL."""
        drv = _make_driver()
        drv.connect()
        mock_deps["transport"].open.assert_called_once_with("ftdi://0x0403:0x6014/1")


class TestIsConnected:
    def test_false_before_connect(self, mock_deps):
        drv = _make_driver()
        assert drv.is_connected is False

    def test_true_after_connect(self, mock_deps):
        drv = _make_driver()
        drv.connect()
        assert drv.is_connected is True

    def test_false_after_disconnect(self, mock_deps):
        drv = _make_driver()
        drv.connect()
        drv.disconnect()
        assert drv.is_connected is False


class TestDisconnect:
    def test_calls_transport_close(self, mock_deps):
        """disconnect() must call transport.close()."""
        drv = _make_driver()
        drv.connect()
        drv.disconnect()
        mock_deps["transport"].close.assert_called_once()

    def test_idempotent_called_twice(self, mock_deps):
        """disconnect() called twice must not raise."""
        drv = _make_driver()
        drv.connect()
        drv.disconnect()
        drv.disconnect()  # should not raise

    def test_safe_before_connect(self, mock_deps):
        """disconnect() before connect() must not raise."""
        drv = _make_driver()
        drv.disconnect()  # should not raise
