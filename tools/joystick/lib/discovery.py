"""
File discovery and pairing for replay rendering.
Groups files by timestamp, pairs road/wide/telemetry by chunk.
"""

import re
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Chunk:
    timestamp: int
    chunk_index: int
    road_video: Path
    wide_video: Path | None
    telemetry: Path


@dataclass
class Session:
    timestamp: int
    chunks: list[Chunk]


# Patterns: road_<timestamp>_chunk<NNN>.webm, telemetry_<timestamp>_chunk<NNN>.json
FILE_PATTERN = re.compile(r'^(road|wide|telemetry)_(\d+)_chunk(\d+)\.(webm|json)$')


def discover_sessions(input_dir: Path) -> list[Session]:
    """
    Discover and pair files by timestamp.
    Returns sessions sorted latest-to-earliest.
    """
    input_dir = Path(input_dir)

    # Group files by (timestamp, chunk_index)
    groups: dict[tuple[int, int], dict[str, Path]] = {}

    for f in input_dir.iterdir():
        if not f.is_file():
            continue
        match = FILE_PATTERN.match(f.name)
        if not match:
            continue

        file_type, timestamp_str, chunk_str, _ = match.groups()
        timestamp = int(timestamp_str)
        chunk_index = int(chunk_str)
        key = (timestamp, chunk_index)

        if key not in groups:
            groups[key] = {}
        groups[key][file_type] = f

    # Build chunks, requiring at least road + telemetry
    chunks_by_session: dict[int, list[Chunk]] = {}

    for (timestamp, chunk_index), files in groups.items():
        if 'road' not in files or 'telemetry' not in files:
            continue

        chunk = Chunk(
            timestamp=timestamp,
            chunk_index=chunk_index,
            road_video=files['road'],
            wide_video=files.get('wide'),
            telemetry=files['telemetry'],
        )

        if timestamp not in chunks_by_session:
            chunks_by_session[timestamp] = []
        chunks_by_session[timestamp].append(chunk)

    # Build sessions, sort chunks by index
    sessions = []
    for timestamp, chunks in chunks_by_session.items():
        chunks.sort(key=lambda c: c.chunk_index)
        sessions.append(Session(timestamp=timestamp, chunks=chunks))

    # Sort sessions latest-to-earliest
    sessions.sort(key=lambda s: s.timestamp, reverse=True)

    return sessions
