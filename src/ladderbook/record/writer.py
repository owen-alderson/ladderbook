"""Append-only raw tape: (recv_ns, source, raw) rows, rotated into closed Parquet files.

Each file is written in one go when it rotates, so a crash loses at most one rotation
interval and never leaves a half-written (unreadable) Parquet file behind.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema([("recv_ns", pa.int64()), ("source", pa.string()), ("raw", pa.string())])


class RawWriter:
    def __init__(self, root: Path, rotate_seconds: float = 300.0, clock=time.time_ns):
        self.root = Path(root)
        self.rotate_ns = int(rotate_seconds * 1e9)
        self._clock = clock
        self._rows: list[tuple[int, str, str]] = []
        self._opened_ns: int | None = None
        self.files_written = 0

    def write(self, source: str, raw: str, recv_ns: int | None = None) -> None:
        now = self._clock() if recv_ns is None else recv_ns
        if self._opened_ns is None:
            self._opened_ns = now
        self._rows.append((now, source, raw))
        if now - self._opened_ns >= self.rotate_ns:
            self.flush()

    def flush(self) -> Path | None:
        if not self._rows:
            return None
        first_ns = self._rows[0][0]
        stamp = datetime.fromtimestamp(first_ns / 1e9, tz=timezone.utc)
        directory = self.root / "raw" / stamp.strftime("%Y-%m-%d")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stamp.strftime('%H%M%S')}-{first_ns}.parquet"
        recv, source, raw = zip(*self._rows)
        table = pa.table({"recv_ns": recv, "source": source, "raw": raw}, schema=SCHEMA)
        tmp = path.with_suffix(".tmp")
        pq.write_table(table, tmp, compression="zstd")
        tmp.rename(path)  # atomic: readers never see a partial file
        self._rows.clear()
        self._opened_ns = None
        self.files_written += 1
        return path


def read_raw(root: Path) -> pa.Table:
    """Every raw row under root/raw, sorted by receive time."""
    files = sorted((Path(root) / "raw").glob("*/*.parquet"))
    if not files:
        return SCHEMA.empty_table()
    return pa.concat_tables(pq.read_table(f) for f in files).sort_by("recv_ns")
