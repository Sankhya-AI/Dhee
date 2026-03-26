from abc import ABC, abstractmethod


class MemoryBase(ABC):
    @abstractmethod
    def get(self, memory_id):
        pass

    @abstractmethod
    def get_all(self):
        pass

    @abstractmethod
    def update(self, memory_id, data):
        pass

    @abstractmethod
    def delete(self, memory_id):
        pass

    @abstractmethod
    def history(self, memory_id):
        pass
