from abc import ABC, abstractmethod

class InstrumentDriver(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...
