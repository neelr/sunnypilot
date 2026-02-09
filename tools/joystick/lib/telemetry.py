"""
Telemetry parsing and interpolation.
Uses frame numbers for alignment (more reliable than videoTime).
"""

import json
import bisect
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class TelemetryFrame:
    frame_number: int
    video_time: float
    keys_left: bool
    keys_right: bool
    steer: float
    gas: bool
    brake: bool
    accel: float
    angle: float


@dataclass
class ChunkTelemetry:
    start_time: int
    chunk_index: int
    frames: list[TelemetryFrame]
    frame_offset: int = 0  # First frame number (to normalize to 0-based)

    # For fast lookup by frame number
    _frame_nums: list[int] = field(default_factory=list)

    def __post_init__(self):
        if self.frames:
            # Filter out corrupted frames (frame=0 or videoTime=0 at end)
            valid = [f for f in self.frames if f.frame_number > 0]
            if valid:
                self.frames = valid
                self.frame_offset = valid[0].frame_number
                # Normalize frame numbers to 0-based
                self._frame_nums = [f.frame_number - self.frame_offset for f in valid]


def load_telemetry(path: Path) -> ChunkTelemetry:
    """Load telemetry from JSON file."""
    with open(path) as f:
        data = json.load(f)

    frames = []
    for frame in data['frames']:
        frames.append(TelemetryFrame(
            frame_number=frame['frame'],
            video_time=frame['videoTime'],
            keys_left=frame['keys']['left'],
            keys_right=frame['keys']['right'],
            steer=frame['steer'],
            gas=frame['gas'],
            brake=frame['brake'],
            accel=frame['accel'],
            angle=frame['angle'],
        ))

    return ChunkTelemetry(
        start_time=data['startTime'],
        chunk_index=data['chunkIndex'],
        frames=frames,
    )


def interpolate_telemetry(telemetry: ChunkTelemetry, frame_index: int) -> TelemetryFrame:
    """
    Get telemetry for a given video frame index (0-based).
    Uses nearest-neighbor matching by frame number.
    """
    frame_nums = telemetry._frame_nums
    frames = telemetry.frames

    if not frames:
        raise ValueError("No telemetry frames")

    # Binary search for closest frame
    idx = bisect.bisect_left(frame_nums, frame_index)

    # Edge cases
    if idx == 0:
        return frames[0]
    if idx >= len(frames):
        return frames[-1]

    # Return closest match
    if abs(frame_nums[idx] - frame_index) < abs(frame_nums[idx-1] - frame_index):
        return frames[idx]
    return frames[idx - 1]


def get_frame_range(telemetry: ChunkTelemetry) -> tuple[int, int]:
    """Get the normalized frame range (0-based)."""
    if not telemetry._frame_nums:
        return (0, 0)
    return (0, telemetry._frame_nums[-1])
