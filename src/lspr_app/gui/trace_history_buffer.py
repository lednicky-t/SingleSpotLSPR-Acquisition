from __future__ import annotations

import numpy as np


class TraceHistoryBuffer:
    def __init__(self, capacity: int) -> None:
        self.capacity = max(int(capacity), 1)
        self._times = np.empty(self.capacity, dtype=np.float64)
        self._values = np.empty(self.capacity, dtype=np.float64)
        self._start = 0
        self._size = 0

    def clear(self) -> None:
        self._start = 0
        self._size = 0

    def append(self, time_s: float, value: float) -> None:
        index = (self._start + self._size) % self.capacity
        self._times[index] = float(time_s)
        self._values[index] = float(value)
        if self._size < self.capacity:
            self._size += 1
        else:
            self._start = (self._start + 1) % self.capacity

    def __len__(self) -> int:
        return self._size

    def to_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        if self._size <= 0:
            return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
        end = self._start + self._size
        if end <= self.capacity:
            return self._times[self._start:end].copy(), self._values[self._start:end].copy()
        second_len = end % self.capacity
        times = np.concatenate((self._times[self._start :], self._times[:second_len]))
        values = np.concatenate((self._values[self._start :], self._values[:second_len]))
        return times, values
