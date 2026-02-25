"""
SplatForge -- Pipeline orchestrator.

Runs the five-stage Gaussian-splatting pipeline as async subprocesses,
parses stdout for progress metrics, and broadcasts events to the frontend
via a caller-supplied callback.

Stages: analyzing -> processing -> training -> exporting -> converting
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from pathlib import Path
from uuid import uuid4

from backend.config import PRESETS, JOBS_DIR, VIEWER_PORT
from backend.log_parser import parse_training_line, parse_colmap_line


class PipelineOrchestrator:
    """Drive video -> 3-D Gaussian Splat conversion through five stages."""

    STAGE_NAMES = ["analyzing", "processing", "training", "exporting", "converting"]

    # Relative weight of each stage for the overall progress bar.
    _STAGE_WEIGHTS: dict[str, float] = {
        "analyzing":  0.02,
        "processing": 0.25,
        "training":   0.60,
        "exporting":  0.08,
        "converting": 0.05,
    }

    # ------------------------------------------------------------------ init
    def __init__(
        self,
        job_id: str,
        input_path: str,
        preset_name: str,
        broadcast_fn,
        mock: bool = False,
    ):
        self.job_id = job_id
        self.input_path = Path(input_path)
        self.preset = PRESETS[preset_name]
        self.broadcast_fn = broadcast_fn  # callable(event_dict) -- synchronous
        self.mock = mock
        self.cancelled = False
        self.current_process: asyncio.subprocess.Process | None = None

        # Job directory layout
        self.job_dir = JOBS_DIR / job_id
        self.job_dir.mkdir(parents=True, exist_ok=True)
        (self.job_dir / "scene_data").mkdir(exist_ok=True)
        (self.job_dir / "outputs").mkdir(exist_ok=True)
        (self.job_dir / "exports").mkdir(exist_ok=True)

        self.video_info: dict = {}
        self.stage_start_time: float | None = None

    # --------------------------------------------------------- public: run
    async def run(self) -> None:
        """Execute the full pipeline sequentially."""
        try:
            await self._stage_analyze()
            await self._stage_process_data()
            await self._stage_train()
            await self._stage_export()
            await self._stage_convert()

            self._broadcast("complete", 1.0, "Pipeline complete")
        except asyncio.CancelledError:
            self._broadcast("cancelled", 0.0, "Pipeline cancelled")
        except Exception as exc:
            self._broadcast("error", 0.0, f"Pipeline error: {exc}")
            raise

    # ------------------------------------------------------- public: cancel
    async def cancel(self) -> None:
        """Request cancellation of the running pipeline."""
        self.cancelled = True
        if self.current_process is not None:
            try:
                self.current_process.kill()
            except ProcessLookupError:
                pass

    # ----------------------------------------------------------- broadcast
    def _broadcast(self, stage: str, stage_progress: float, message: str, **kwargs) -> None:
        event = {
            "stage": stage,
            "stage_index": (
                self.STAGE_NAMES.index(stage) if stage in self.STAGE_NAMES else -1
            ),
            "total_stages": len(self.STAGE_NAMES),
            "stage_progress": stage_progress,
            "overall_progress": self._calc_overall_progress(stage, stage_progress),
            "stats": kwargs.get("stats", {}),
            "message": message,
            "viewer_ready": kwargs.get("viewer_ready", False),
            "viewer_url": kwargs.get("viewer_url", ""),
            "elapsed_seconds": (
                time.time() - self.stage_start_time if self.stage_start_time else 0
            ),
        }
        self.broadcast_fn(event)

    # ------------------------------------------------ overall progress math
    def _calc_overall_progress(self, stage: str, stage_progress: float) -> float:
        """Map per-stage progress to a single 0-1 overall value."""
        if stage not in self.STAGE_NAMES:
            # Terminal pseudo-stages: complete -> 1.0, others -> 0.0
            return 1.0 if stage == "complete" else 0.0

        stage_idx = self.STAGE_NAMES.index(stage)
        completed = sum(
            self._STAGE_WEIGHTS[self.STAGE_NAMES[i]]
            for i in range(stage_idx)
        )
        current = self._STAGE_WEIGHTS[stage] * stage_progress
        return min(completed + current, 1.0)

    # --------------------------------------------------- subprocess helper
    async def _run_subprocess(self, cmd: list[str], line_parser=None) -> None:
        """Run *cmd* as an async subprocess, feeding lines to *line_parser*."""
        self.current_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert self.current_process.stdout is not None

        async for line_bytes in self.current_process.stdout:
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line_parser:
                line_parser(line)
            if self.cancelled:
                self.current_process.kill()
                break

        await self.current_process.wait()

        if self.current_process.returncode != 0 and not self.cancelled:
            raise RuntimeError(
                f"Subprocess exited with code {self.current_process.returncode}: "
                + " ".join(cmd[:3])
            )

    # ============================================================ STAGE 1
    async def _stage_analyze(self) -> None:
        """Probe the input video for metadata (duration, resolution, etc.)."""
        self.stage_start_time = time.time()
        self._broadcast("analyzing", 0.0, "Analyzing input video...")

        if self.mock:
            await asyncio.sleep(1)
            self.video_info = {
                "duration": 154,
                "resolution": "3840x2160",
                "fps": 30,
                "codec": "h264",
                "file_size": 1_288_490_188,
            }
        else:
            # Run ffprobe and capture JSON output
            cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(self.input_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError("ffprobe failed -- is FFmpeg installed?")

            probe = json.loads(stdout_bytes.decode("utf-8", errors="replace"))

            # Locate first video stream
            video_stream: dict = {}
            for stream in probe.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break

            # Parse FPS from r_frame_rate (e.g. "30/1")
            fps = 0.0
            rfr = video_stream.get("r_frame_rate", "0/1")
            if "/" in rfr:
                num, den = rfr.split("/")
                fps = round(float(num) / float(den), 2) if float(den) else 0.0
            else:
                fps = float(rfr)

            fmt = probe.get("format", {})
            self.video_info = {
                "duration": float(fmt.get("duration", 0)),
                "resolution": f"{video_stream.get('width', 0)}x{video_stream.get('height', 0)}",
                "fps": fps,
                "codec": video_stream.get("codec_name", "unknown"),
                "file_size": int(fmt.get("size", 0)),
            }

        self._broadcast(
            "analyzing", 1.0,
            "Video analysis complete",
            stats=self.video_info,
        )

    # ============================================================ STAGE 2
    async def _stage_process_data(self) -> None:
        """Extract frames and run COLMAP via *ns-process-data*."""
        self.stage_start_time = time.time()
        self._broadcast("processing", 0.0, "Processing video data...")

        if self.mock:
            for i in range(1, 11):
                if self.cancelled:
                    return
                await asyncio.sleep(1)
                progress = i / 10.0
                stats = {
                    "registered_images": int(self.preset["num_frames"] * progress),
                    "num_points3d": int(45000 * progress),
                }
                self._broadcast(
                    "processing", progress,
                    f"COLMAP: registered {stats['registered_images']} images",
                    stats=stats,
                )
        else:
            scene_dir = self.job_dir / "scene_data"
            cmd = [
                "ns-process-data", "video",
                "--data", str(self.input_path),
                "--output-dir", str(scene_dir),
                "--num-frames-target", str(self.preset["num_frames"]),
                "--matching-method", "exhaustive",
            ]

            num_frames_target = self.preset["num_frames"]

            def _parse(line: str) -> None:
                parsed = parse_colmap_line(line)
                if parsed:
                    registered = parsed.get("registered_images", 0)
                    progress = min(registered / max(num_frames_target, 1), 1.0)
                    self._broadcast(
                        "processing", progress,
                        f"COLMAP: {line}",
                        stats=parsed,
                    )

            await self._run_subprocess(cmd, _parse)

        self._broadcast("processing", 1.0, "Data processing complete")

    # ============================================================ STAGE 3
    async def _stage_train(self) -> None:
        """Train a splatfacto model via *ns-train*."""
        self.stage_start_time = time.time()
        self._broadcast("training", 0.0, "Starting training...")

        max_iter = self.preset["max_iterations"]

        if self.mock:
            steps = 60  # 30 seconds at 0.5 s interval
            for i in range(1, steps + 1):
                if self.cancelled:
                    return
                await asyncio.sleep(0.5)
                fraction = i / steps
                stats = self._mock_training_stats(fraction)
                self._broadcast(
                    "training", fraction,
                    f"Training iteration {stats['iteration']}/{max_iter}",
                    stats=stats,
                    viewer_ready=False,
                )
        else:
            scene_dir = self.job_dir / "scene_data"
            output_dir = self.job_dir / "outputs"

            cmd = [
                "ns-train", "splatfacto",
                "--data", str(scene_dir),
                "--output-dir", str(output_dir),
                "--max-num-iterations", str(max_iter),
                "--pipeline.model.cull-alpha-thresh",
                str(self.preset["cull_alpha_thresh"]),
                "--vis", "viewer",
                "--viewer.websocket-port", str(VIEWER_PORT),
            ]

            viewer_ready = False

            def _parse(line: str) -> None:
                nonlocal viewer_ready
                parsed = parse_training_line(line)
                stats = parsed if parsed else {}

                # Detect viewer readiness
                if not viewer_ready and "viewer" in line.lower() and str(VIEWER_PORT) in line:
                    viewer_ready = True

                iteration = stats.get("iteration", 0)
                progress = min(iteration / max(max_iter, 1), 1.0)

                self._broadcast(
                    "training", progress,
                    f"Training: {line}",
                    stats=stats,
                    viewer_ready=viewer_ready,
                    viewer_url=f"http://localhost:{VIEWER_PORT}" if viewer_ready else "",
                )

            await self._run_subprocess(cmd, _parse)

        self._broadcast("training", 1.0, "Training complete")

    # ============================================================ STAGE 4
    async def _stage_export(self) -> None:
        """Export a Gaussian Splat PLY from the trained model."""
        self.stage_start_time = time.time()
        self._broadcast("exporting", 0.0, "Exporting splat...")

        exports_dir = self.job_dir / "exports"

        if self.mock:
            await asyncio.sleep(2)
            ply_path = exports_dir / "splat.ply"
            ply_path.write_text("ply\n")
        else:
            config_path = self._find_latest_config()
            cmd = [
                "ns-export", "gaussian-splat",
                "--load-config", str(config_path),
                "--output-dir", str(exports_dir),
            ]
            await self._run_subprocess(cmd, None)

        self._broadcast("exporting", 1.0, "Export complete")

    # ============================================================ STAGE 5
    async def _stage_convert(self) -> None:
        """Convert PLY to ksplat for the web viewer."""
        self.stage_start_time = time.time()
        self._broadcast("converting", 0.0, "Converting to ksplat...")

        exports_dir = self.job_dir / "exports"

        if self.mock:
            await asyncio.sleep(1)
            ksplat_path = exports_dir / "terrain.ksplat"
            ksplat_path.write_text("ksplat\n")
        else:
            ply_path = exports_dir / "splat.ply"
            ksplat_path = exports_dir / "terrain.ksplat"
            try:
                from backend.ksplat_converter import convert_ply_to_ksplat

                success = await convert_ply_to_ksplat(ply_path, ksplat_path)
                if not success:
                    self._broadcast(
                        "converting", 0.5,
                        "ksplat converter unavailable -- skipping",
                    )
            except Exception as exc:
                self._broadcast(
                    "converting", 0.5,
                    f"ksplat conversion skipped: {exc}",
                )

        self._broadcast("converting", 1.0, "Conversion complete")

    # ------------------------------------------------ find latest config
    def _find_latest_config(self) -> Path:
        """Locate the most recently written *config.yml* under outputs/."""
        outputs_dir = self.job_dir / "outputs"
        configs = list(outputs_dir.glob("splatfacto/*/config.yml"))
        if not configs:
            raise RuntimeError(
                f"No config.yml found in {outputs_dir / 'splatfacto'}"
            )
        return max(configs, key=lambda p: p.stat().st_mtime)

    # ------------------------------------------------ mock training stats
    def _mock_training_stats(self, fraction: float) -> dict:
        """Generate realistic-looking training metrics for mock mode."""
        max_iter = self.preset["max_iterations"]
        return {
            "iteration": int(max_iter * fraction),
            "max_iterations": max_iter,
            "loss": round(0.05 * math.exp(-3 * fraction), 6),
            "psnr": round(18 + 12 * math.log10(1 + fraction * 9), 2),
            "num_gaussians": 100_000 + int(1_400_000 * fraction),
            "train_rays_per_sec": 450_000 + int(50_000 * (fraction - 0.5)),
            "eta_seconds": max(0, int(30 * (1 - fraction))),
            "elapsed_seconds": int(30 * fraction),
        }
