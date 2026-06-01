from __future__ import annotations

import numpy as np


class MetricHistoryBuffer:
    def __init__(self, capacity: int) -> None:
        self.capacity = max(int(capacity), 1)
        self._times = np.empty(self.capacity, dtype=np.float64)
        self._values = np.empty(self.capacity, dtype=np.float64)
        self._start = 0
        self._size = 0
        self._revision = 0

    def clear(self) -> None:
        self._revision += 1
        self._start = 0
        self._size = 0

    def append(self, time_s: float, value: float) -> None:
        self._revision += 1
        index = (self._start + self._size) % self.capacity
        self._times[index] = float(time_s)
        self._values[index] = float(value)
        if self._size < self.capacity:
            self._size += 1
        else:
            self._start = (self._start + 1) % self.capacity

    def __len__(self) -> int:
        return self._size

    @property
    def revision(self) -> int:
        return self._revision

    def to_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        if self._size <= 0:
            return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
        end = self._start + self._size
        if end <= self.capacity:
            # Return contiguous views when the ring buffer has not wrapped; this keeps
            # live redraws from copying the entire retained history every refresh.
            return self._times[self._start:end], self._values[self._start:end]
        second_len = end % self.capacity
        times = np.concatenate((self._times[self._start :], self._times[:second_len]))
        values = np.concatenate((self._values[self._start :], self._values[:second_len]))
        return times, values


class AppendOnlyMetricHistoryBuffer:
    def __init__(self, capacity: int) -> None:
        self.capacity = max(int(capacity), 1)
        self._times = np.empty(self.capacity, dtype=np.float64)
        self._values = np.empty(self.capacity, dtype=np.float64)
        self._size = 0
        self._revision = 0

    def clear(self) -> None:
        self._revision += 1
        self._size = 0

    def append(self, time_s: float, value: float) -> None:
        self._revision += 1
        if self._size >= self.capacity:
            new_capacity = max(self.capacity * 2, self.capacity + 1)
            new_times = np.empty(new_capacity, dtype=np.float64)
            new_values = np.empty(new_capacity, dtype=np.float64)
            if self._size > 0:
                new_times[: self._size] = self._times[: self._size]
                new_values[: self._size] = self._values[: self._size]
            self._times = new_times
            self._values = new_values
            self.capacity = new_capacity
        self._times[self._size] = float(time_s)
        self._values[self._size] = float(value)
        self._size += 1

    def __len__(self) -> int:
        return self._size

    @property
    def revision(self) -> int:
        return self._revision

    def to_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        if self._size <= 0:
            return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
        return self._times[: self._size], self._values[: self._size]


TraceHistoryBuffer = MetricHistoryBuffer
