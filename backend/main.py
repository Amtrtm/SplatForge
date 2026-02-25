"""
SplatForge -- FastAPI application.

Central server that serves the frontend SPA and exposes the REST / SSE API
for uploading videos, launching the Gaussian-splatting pipeline, streaming
progress events, downloading exports, and querying GPU information.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.config import (
    PRESETS, JOBS_DIR, FRONTEND_DIR, SERVER_PORT,
    MAX_UPLOAD_SIZE, MIN_VIDEO_DURATION, ALLOWED_EXTENSIONS, DEFAULT_PRESET,
)
from backend.pipeline import PipelineOrchestrator

# ── In-memory state ─────────────────────────────────────────────────────────

jobs: dict[str, dict] = {}            # job_id -> {status, pipeline, task, video_info, ...}
subscribers: dict[str, list[asyncio.Queue]] = {}  # job_id -> [Queue, ...]

# Mock mode (global default, can be overridden per-request)
MOCK_MODE: bool = os.environ.get("SPLATFORGE_MOCK", "0") == "1"

# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    yield


# ── FastAPI application ──────────────────────────────────────────────────────

app = FastAPI(title="SplatForge", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── POST /api/upload ─────────────────────────────────────────────────────────


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """Accept a video upload, validate it, and return a new job ID."""

    # -- Validate extension
    original_name: str = file.filename or "video.mp4"
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # -- Create job directory
    job_id: str = uuid4().hex[:12]
    job_dir: Path = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # -- Save uploaded file (preserve original extension)
    input_path: Path = job_dir / f"input{suffix}"
    try:
        contents = await file.read()
        if len(contents) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum allowed size is {MAX_UPLOAD_SIZE // (1024**3)} GB.",
            )
        input_path.write_bytes(contents)
    finally:
        await file.close()

    # -- Probe video metadata
    if MOCK_MODE:
        video_info: dict = {
            "duration": 154,
            "resolution": "3840x2160",
            "fps": 30,
            "codec": "h264",
            "file_size": len(contents),
        }
    else:
        video_info = await _probe_video(input_path)
        if video_info["duration"] < MIN_VIDEO_DURATION:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Video is too short ({video_info['duration']:.1f}s). "
                    f"Minimum duration is {MIN_VIDEO_DURATION}s."
                ),
            )

    # -- Store in application state
    jobs[job_id] = {
        "status": "uploaded",
        "input_path": str(input_path),
        "video_info": video_info,
    }

    return {"job_id": job_id, "filename": original_name, "video_info": video_info}


async def _probe_video(path: Path) -> dict:
    """Run ffprobe on *path* and return a metadata dict."""

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, _ = await proc.communicate()

    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail="ffprobe failed -- is FFmpeg installed?")

    probe = json.loads(stdout_bytes.decode("utf-8", errors="replace"))

    # Locate first video stream
    video_stream: dict = {}
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    # Parse FPS from r_frame_rate (e.g. "30/1")
    fps: float = 0.0
    rfr: str = video_stream.get("r_frame_rate", "0/1")
    if "/" in rfr:
        num, den = rfr.split("/")
        fps = round(float(num) / float(den), 2) if float(den) else 0.0
    else:
        fps = float(rfr)

    fmt = probe.get("format", {})
    return {
        "duration": float(fmt.get("duration", 0)),
        "resolution": f"{video_stream.get('width', 0)}x{video_stream.get('height', 0)}",
        "fps": fps,
        "codec": video_stream.get("codec_name", "unknown"),
        "file_size": int(fmt.get("size", 0)),
    }


# ── POST /api/start ─────────────────────────────────────────────────────────


@app.post("/api/start")
async def start_pipeline(request: Request):
    """Start (or retry) the training pipeline for a previously uploaded job."""

    body = await request.json()
    job_id: str = body.get("job_id", "")
    preset_name: str = body.get("preset", DEFAULT_PRESET)

    # -- Validate job
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    if job["status"] not in ("uploaded", "error"):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already in state '{job['status']}' -- cannot start.",
        )

    # -- Validate preset
    if preset_name not in PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{preset_name}'. Available: {', '.join(PRESETS)}",
        )

    # -- Determine mock mode (global OR per-request ?mock=true)
    is_mock: bool = MOCK_MODE or request.query_params.get("mock", "").lower() == "true"

    # -- Synchronous broadcast closure
    def broadcast(event: dict) -> None:
        job["last_event"] = event
        job["status"] = event.get("stage", "unknown")
        for queue in subscribers.get(job_id, []):
            queue.put_nowait(event)

    # -- Create pipeline and launch as a background task
    pipeline = PipelineOrchestrator(
        job_id=job_id,
        input_path=job["input_path"],
        preset_name=preset_name,
        broadcast_fn=broadcast,
        mock=is_mock,
    )
    task = asyncio.create_task(pipeline.run())
    job["pipeline"] = pipeline
    job["task"] = task

    return {"status": "started"}


# ── GET /api/status/stream (SSE) ────────────────────────────────────────────


@app.get("/api/status/stream")
async def status_stream(request: Request):
    """Server-Sent Events endpoint for real-time pipeline progress."""

    job_id: str = request.query_params.get("job_id", "")
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    queue: asyncio.Queue = asyncio.Queue()
    subscribers.setdefault(job_id, []).append(queue)

    async def event_generator():
        try:
            # Send the most recent event so late-connecting clients
            # immediately know the current state.
            last = jobs[job_id].get("last_event")
            if last is not None:
                yield f"data: {json.dumps(last)}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"

                    stage = event.get("stage", "")
                    if stage in ("complete", "error", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    # SSE keepalive comment (not a data frame)
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        except GeneratorExit:
            pass
        finally:
            # Clean up: remove this queue from subscribers
            sub_list = subscribers.get(job_id, [])
            if queue in sub_list:
                sub_list.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /api/status/{job_id} (polling fallback) ─────────────────────────────


@app.get("/api/status/{job_id}")
async def status_poll(job_id: str):
    """Polling fallback for clients that cannot use SSE."""

    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    last_event = job.get("last_event")
    if last_event:
        return last_event
    return {"status": job["status"]}


# ── GET /api/download/{job_id}/{filename} ────────────────────────────────────


@app.get("/api/download/{job_id}/{filename}")
async def download_export(job_id: str, filename: str):
    """Download an exported file (PLY, ksplat, etc.) from a completed job."""

    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    # Path-traversal protection
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path: Path = JOBS_DIR / job_id / "exports" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /api/cancel/{job_id} ───────────────────────────────────────────────


@app.post("/api/cancel/{job_id}")
async def cancel_pipeline(job_id: str):
    """Cancel a running pipeline."""

    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    pipeline: PipelineOrchestrator | None = job.get("pipeline")
    if pipeline is None:
        raise HTTPException(status_code=409, detail="No running pipeline for this job")

    # Request graceful cancellation, then cancel the asyncio task
    await pipeline.cancel()
    task: asyncio.Task | None = job.get("task")
    if task is not None and not task.done():
        task.cancel()

    return {"status": "cancelled"}


# ── GET /api/gpu-info ────────────────────────────────────────────────────────


@app.get("/api/gpu-info")
async def gpu_info():
    """Return GPU information (nvidia-smi) or a mock response."""

    if MOCK_MODE:
        return {
            "name": "NVIDIA RTX 4090 (Mock)",
            "memory_total": "24 GB",
            "memory_used": "0 GB",
            "driver_version": "555.42",
            "cuda_version": "12.4",
        }

    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,driver_version",
            "--format=csv,noheader",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await proc.communicate()

        if proc.returncode != 0:
            return {"name": "No GPU detected", "error": "nvidia-smi returned an error"}

        line = stdout_bytes.decode("utf-8", errors="replace").strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]

        result: dict = {
            "name": parts[0] if len(parts) > 0 else "Unknown",
            "memory_total": parts[1] if len(parts) > 1 else "Unknown",
            "memory_used": parts[2] if len(parts) > 2 else "Unknown",
            "driver_version": parts[3] if len(parts) > 3 else "Unknown",
            "cuda_version": "Unknown",
        }

        # Attempt to get CUDA version via nvcc
        try:
            nvcc_proc = await asyncio.create_subprocess_exec(
                "nvcc", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            nvcc_stdout, _ = await nvcc_proc.communicate()
            nvcc_output = nvcc_stdout.decode("utf-8", errors="replace")
            # Look for "release X.Y" in nvcc output
            for nvcc_line in nvcc_output.split("\n"):
                if "release" in nvcc_line.lower():
                    # e.g. "Cuda compilation tools, release 12.4, V12.4.99"
                    m = re.search(r"release\s+([\d.]+)", nvcc_line)
                    if m:
                        result["cuda_version"] = m.group(1)
                    break
        except Exception:
            pass

        return result

    except FileNotFoundError:
        return {"name": "No GPU detected", "error": "nvidia-smi not found"}
    except Exception as exc:
        return {"name": "No GPU detected", "error": str(exc)}


# ── GET /api/presets ─────────────────────────────────────────────────────────


@app.get("/api/presets")
async def get_presets():
    """Return available quality presets."""
    return PRESETS


# ── Static files (MUST be last) ─────────────────────────────────────────────
# Mount the frontend SPA after all API routes so /api/* routes take precedence.

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import uvicorn

    # Ensure stdout can handle UTF-8 on Windows
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("\n  ╔══════════════════════════════════════╗")
    print("  ║  SPLATFORGE — Video to Splat Engine  ║")
    print("  ║  Open: http://localhost:8080          ║")
    if MOCK_MODE:
        print("  ║  ⚠  MOCK MODE ENABLED                ║")
    print("  ╚══════════════════════════════════════╝\n")

    uvicorn.run("backend.main:app", host="0.0.0.0", port=SERVER_PORT, reload=False)
