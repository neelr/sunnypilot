#!/usr/bin/env python3
"""
Replay Render - Render driving recordings with customizable HTML overlay.

Renders video frames through an HTML template and outputs final video files.
Uses parallel Playwright instances for faster-than-realtime rendering.

Usage:
    python replay_render.py /path/to/recordings --workers 8
    python replay_render.py ~/Documents/realdrive_southpark/ --template custom.html

Requirements:
    pip install playwright opencv-python numpy tqdm
    playwright install chromium
"""

import argparse
import json
import random
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

from lib.discovery import discover_sessions, Chunk
from lib.telemetry import load_telemetry, interpolate_telemetry, get_frame_range
from lib.renderer import BatchRenderer


def compress_events(events: list[dict]) -> list[dict]:
    """
    Compress events by merging quick press+release pairs into KeyTyped events.
    A press followed by release within 5 events becomes KeyTyped.
    """
    result = []
    i = 0
    while i < len(events):
        event = events[i]

        # Look for Keyboard press events
        if event.get('type') == 'Keyboard' and event.get('is_press') is True:
            keycode = event['keycode']

            # Look ahead for matching release within 5 events
            found_release = False
            for j in range(i + 1, min(i + 6, len(events))):
                candidate = events[j]
                if (candidate.get('type') == 'Keyboard' and
                    candidate.get('keycode') == keycode and
                    candidate.get('is_press') is False):
                    # Found matching release - merge into KeyTyped
                    result.append({'type': 'KeyTyped', 'keycode': keycode})
                    # Skip to after the release, but include events in between
                    for k in range(i + 1, j):
                        result.append(events[k])
                    i = j + 1
                    found_release = True
                    break

            if not found_release:
                result.append(event)
                i += 1
        else:
            result.append(event)
            i += 1

    return result


def extract_frames(video_path: Path, fps: float = 20.0) -> list[tuple[float, np.ndarray]]:
    """
    Extract all frames from a video file.
    Returns list of (timestamp_seconds, frame_bgr) tuples.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS) or fps
    frames = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        timestamp = frame_idx / video_fps
        frames.append((timestamp, frame))
        frame_idx += 1

    cap.release()
    return frames


def render_chunk(
    chunk: Chunk,
    template_path: Path,
    output_dir: Path,
    num_workers: int,
    fps: float = 20.0,
) -> tuple[Path, str]:
    """
    Render a single chunk and return (output_dir_path, relative_path).
    Output structure: {uid}/video.mp4 + events.json
    """
    # Generate unique ID for this chunk
    chunk_id = str(uuid.uuid4())
    chunk_output_dir = output_dir / chunk_id
    chunk_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  Processing chunk {chunk.chunk_index} -> {chunk_id}")

    # Create temp dir for frames
    frames_dir = output_dir / f"frames_{chunk.timestamp}_{chunk.chunk_index:03d}"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Load telemetry
    print("    Loading telemetry...")
    telemetry = load_telemetry(chunk.telemetry)
    frame_start, frame_end = get_frame_range(telemetry)
    print(f"    Telemetry covers frames {frame_start}-{frame_end} (offset: {telemetry.frame_offset})")

    # Extract road frames
    print("    Extracting road video frames...")
    road_frames = extract_frames(chunk.road_video, fps)

    # Extract wide frames if available
    wide_frames = None
    if chunk.wide_video:
        print("    Extracting wide video frames...")
        wide_frames = extract_frames(chunk.wide_video, fps)

    print(f"    Rendering {len(road_frames)} frames with {num_workers} workers...")

    # Track key states for event generation
    events = []
    prev_keys_left = False
    prev_keys_right = False

    # Use renderer with context manager for proper cleanup
    with BatchRenderer(template_path, frames_dir, num_workers) as renderer:
        # Process all frames - workers are persistent so no need for small batches
        batch = []

        for i, (timestamp, road_frame) in enumerate(road_frames):
            # Get wide frame at same index
            wide_frame = None
            if wide_frames and i < len(wide_frames):
                wide_frame = wide_frames[i][1]

            # Get telemetry for this frame index
            telem = interpolate_telemetry(telemetry, i)

            # Generate keyboard events for key state changes
            if telem.keys_left != prev_keys_left:
                events.append({'type': 'Keyboard', 'keycode': 'Left', 'is_press': telem.keys_left})
                prev_keys_left = telem.keys_left
            if telem.keys_right != prev_keys_right:
                events.append({'type': 'Keyboard', 'keycode': 'Right', 'is_press': telem.keys_right})
                prev_keys_right = telem.keys_right

            # Add frame event
            events.append({'type': 'Frame', 'frame_counter': i})

            telem_dict = {
                'keys_left': telem.keys_left,
                'keys_right': telem.keys_right,
                'steer': telem.steer,
                'gas': telem.gas,
                'brake': telem.brake,
                'accel': telem.accel,
                'angle': telem.angle,
            }

            batch.append((i, road_frame, wide_frame, telem_dict))

        # Render all frames (progress shown via tqdm in render_batch)
        print("    Queuing frames to workers...")
        all_frame_paths = renderer.render_batch(batch)

    # Apply compression to events (merge quick press+release -> KeyTyped)
    events = compress_events(events)

    # Write events.json
    events_path = chunk_output_dir / 'events.json'
    with open(events_path, 'w') as f:
        json.dump(events, f)
    print(f"    Wrote {len(events)} events to events.json")

    # Stitch with FFmpeg - 720p H.264 with profile:high per spec
    video_path = chunk_output_dir / 'video.mp4'

    print(f"    Encoding video with FFmpeg (720p)...")
    ffmpeg_cmd = [
        'ffmpeg', '-y',
        '-framerate', str(fps),
        '-i', str(frames_dir / 'frame_%06d.png'),
        '-vf', 'scale=-2:720',
        '-c:v', 'libx264',
        '-profile:v', 'high',
        '-preset', 'medium',
        '-crf', '23',
        '-pix_fmt', 'yuv420p',
        str(video_path)
    ]

    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    FFmpeg error: {result.stderr}")
        raise RuntimeError("FFmpeg encoding failed")

    # Cleanup frame images
    print("    Cleaning up temporary frames...")
    shutil.rmtree(frames_dir)

    print(f"    Output: {chunk_output_dir}")
    return chunk_output_dir, chunk_id


def main():
    parser = argparse.ArgumentParser(
        description='Render driving recordings with customizable HTML overlay.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s ~/Documents/realdrive_southpark/
    %(prog)s ~/recordings/ --workers 8 --output ~/rendered/
    %(prog)s ~/recordings/ --template my_template.html
        """
    )
    parser.add_argument('input_dir', type=Path, help='Input folder with video and telemetry files')
    parser.add_argument('--output', '-o', type=Path, default=None,
                        help='Output directory (default: input_dir/rendered)')
    parser.add_argument('--template', '-t', type=Path, default=None,
                        help='Custom HTML template (default: overlay_template.html)')
    parser.add_argument('--workers', '-w', type=int, default=4,
                        help='Number of parallel render workers (default: 4)')
    parser.add_argument('--fps', type=float, default=20.0,
                        help='Output video FPS (default: 20)')
    parser.add_argument('--session', type=int, default=None,
                        help='Render only this session timestamp')
    parser.add_argument('--chunk', type=int, default=None,
                        help='Render only this chunk index')

    args = parser.parse_args()

    # Validate input
    if not args.input_dir.is_dir():
        parser.error(f"Input directory not found: {args.input_dir}")

    # Setup paths
    output_dir = args.output or args.input_dir / 'rendered'
    output_dir.mkdir(parents=True, exist_ok=True)

    template_path = args.template or Path(__file__).parent / 'overlay_template.html'
    if not template_path.exists():
        parser.error(f"Template not found: {template_path}")

    # Check dependencies
    if shutil.which('ffmpeg') is None:
        parser.error("FFmpeg not found. Please install FFmpeg.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        parser.error("Playwright not installed. Run: pip install playwright && playwright install chromium")

    # Discover sessions
    print(f"Scanning {args.input_dir}...")
    sessions = discover_sessions(args.input_dir)

    if not sessions:
        print("No valid sessions found (need road_*.webm + telemetry_*.json pairs)")
        return

    print(f"Found {len(sessions)} session(s)")

    # Filter by session timestamp if specified
    if args.session:
        sessions = [s for s in sessions if s.timestamp == args.session]
        if not sessions:
            print(f"Session {args.session} not found")
            return

    # Track output paths for index.txt
    all_chunk_ids = []

    # Process each session
    for session in sessions:
        print(f"\nSession {session.timestamp} ({len(session.chunks)} chunks)")

        chunks = session.chunks
        if args.chunk:
            chunks = [c for c in chunks if c.chunk_index == args.chunk]
            if not chunks:
                print(f"  Chunk {args.chunk} not found")
                continue

        for chunk in chunks:
            try:
                _, chunk_id = render_chunk(
                    chunk, template_path, output_dir, args.workers, args.fps
                )
                all_chunk_ids.append(chunk_id)
            except Exception as e:
                print(f"  Error rendering chunk {chunk.chunk_index}: {e}")
                continue

    # Generate shuffled index.txt
    if all_chunk_ids:
        random.shuffle(all_chunk_ids)
        index_path = output_dir / 'index.txt'
        with open(index_path, 'w') as f:
            for chunk_id in all_chunk_ids:
                f.write(f"{chunk_id}\n")
        print(f"\nWrote {len(all_chunk_ids)} paths to index.txt")

    print(f"\nDone! Output files in: {output_dir}")


if __name__ == '__main__':
    main()
