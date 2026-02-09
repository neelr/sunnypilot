"""
Parallel frame renderer using Playwright.
Optimized: Each worker maintains a persistent browser instance.
"""

import base64
import json
import multiprocessing as mp
from pathlib import Path
from dataclasses import dataclass
from queue import Empty

import cv2
import numpy as np


@dataclass
class FrameTask:
    frame_index: int
    road_frame: np.ndarray
    wide_frame: np.ndarray | None
    telemetry: dict
    output_path: Path


def _frame_to_base64(frame: np.ndarray, quality: int = 85) -> str:
    """Convert numpy BGR frame to base64 JPEG data URL."""
    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    b64 = base64.b64encode(buffer).decode('utf-8')
    return f'data:image/jpeg;base64,{b64}'


def _worker_process(worker_id: int, template_path: str, task_queue: mp.Queue, result_queue: mp.Queue, stop_event):
    """
    Worker process that maintains a persistent browser.
    Renders frames from task_queue until stop_event is set.
    """
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # 14" MacBook Pro scaled resolution
            page = browser.new_page(viewport={'width': 1512, 'height': 982})

            # Load template once
            page.goto(f'file://{template_path}')
            page.wait_for_load_state('domcontentloaded')

            while not stop_event.is_set():
                try:
                    # Get task with timeout so we can check stop_event
                    task = task_queue.get(timeout=0.1)
                except Empty:
                    continue

                if task is None:  # Poison pill
                    break

                frame_index, road_path, wide_path, telemetry_json, output_path = task

                try:
                    # Read frames from disk and convert to base64
                    road_frame = cv2.imread(road_path)
                    _, road_buf = cv2.imencode('.jpg', road_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    road_b64 = f'data:image/jpeg;base64,{base64.b64encode(road_buf).decode()}'

                    wide_b64 = 'null'
                    if wide_path:
                        wide_frame = cv2.imread(wide_path)
                        _, wide_buf = cv2.imencode('.jpg', wide_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        wide_b64 = f'"{base64.b64encode(wide_buf).decode()}"'
                        wide_b64 = f'"data:image/jpeg;base64,{base64.b64encode(wide_buf).decode()}"'

                    # Update page content (no reload needed!)
                    page.evaluate(f'''
                        window.setFrame(
                            "{road_b64}",
                            {wide_b64},
                            {telemetry_json}
                        );
                    ''')

                    # Brief wait for render
                    page.wait_for_timeout(5)

                    # Screenshot
                    page.screenshot(path=output_path, type='png')

                    result_queue.put((frame_index, True, ""))

                except Exception as e:
                    result_queue.put((frame_index, False, str(e)[:200]))

            browser.close()

    except Exception as e:
        # Worker crashed
        result_queue.put((-1, False, f"Worker {worker_id} crashed: {e}"))


class BatchRenderer:
    """
    Renders frames using a pool of persistent browser workers.
    Much faster than launching a new browser per frame.
    """

    def __init__(self, template_path: Path, output_dir: Path, num_workers: int = 4):
        self.template_path = template_path
        self.output_dir = output_dir
        self.num_workers = num_workers
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir = output_dir / 'temp_inputs'
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Worker pool
        self.task_queue = mp.Queue()
        self.result_queue = mp.Queue()
        self.stop_event = mp.Event()
        self.workers = []

        # Start workers
        for i in range(num_workers):
            w = mp.Process(
                target=_worker_process,
                args=(i, str(template_path.absolute()), self.task_queue, self.result_queue, self.stop_event)
            )
            w.start()
            self.workers.append(w)

    def render_batch(
        self,
        frames: list[tuple[int, np.ndarray, np.ndarray | None, dict]],
        progress_callback=None,
    ) -> list[Path]:
        """
        Render a batch of frames.
        """
        # Write input frames to disk and queue tasks
        for frame_index, road_frame, wide_frame, telemetry in frames:
            road_path = str(self.temp_dir / f'input_road_{frame_index:06d}.jpg')
            cv2.imwrite(road_path, road_frame, [cv2.IMWRITE_JPEG_QUALITY, 90])

            wide_path = None
            if wide_frame is not None:
                wide_path = str(self.temp_dir / f'input_wide_{frame_index:06d}.jpg')
                cv2.imwrite(wide_path, wide_frame, [cv2.IMWRITE_JPEG_QUALITY, 90])

            output_path = str(self.output_dir / f'frame_{frame_index:06d}.png')
            telemetry_json = json.dumps(telemetry)

            self.task_queue.put((frame_index, road_path, wide_path, telemetry_json, output_path))

        # Collect results with progress bar
        from tqdm import tqdm

        results = []
        success_count = 0
        fail_count = 0

        for i in tqdm(range(len(frames)), desc="    Rendering"):
            result = self.result_queue.get()
            results.append(result)

            if result[1]:
                success_count += 1
            else:
                fail_count += 1
                if fail_count <= 3:
                    print(f"\n      Frame {result[0]} failed: {result[2]}")

            if progress_callback:
                progress_callback(i + 1, len(frames))

        if fail_count > 0:
            print(f"\n    Warning: {fail_count} frames failed")

        return [self.output_dir / f'frame_{idx:06d}.png' for idx, success, _ in results if success]

    def cleanup(self):
        """Stop workers and clean up."""
        # Send poison pills
        for _ in self.workers:
            self.task_queue.put(None)

        # Wait for workers to finish
        self.stop_event.set()
        for w in self.workers:
            w.join(timeout=5)
            if w.is_alive():
                w.terminate()

        # Clean temp files
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cleanup()
