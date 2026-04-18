from drivers.base import InstrumentDriver

def test_instrument_driver_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        InstrumentDriver()
