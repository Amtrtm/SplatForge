# SPLATFORGE — Automated Video-to-Gaussian-Splat Pipeline
## Build Instructions for Claude Code

**What this is:** A local web application that takes a drone MP4 video and fully automates the creation of a `.ply` / `.ksplat` Gaussian Splat file. The user drops in a video, watches a beautiful dashboard track every stage, optionally watches the 3D model build in real-time, and downloads the final file.

**Stack:** Python 3.10+ · FastAPI backend · HTML/CSS/JS frontend (no React — keep it simple, single page) · Nerfstudio (splatfacto) · COLMAP (via nerfstudio) · FFmpeg

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    BROWSER (localhost:8080)                   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              SPLATFORGE DASHBOARD                     │   │
│  │                                                       │   │
│  │  ┌─────────────┐  ┌──────────────────────────────┐   │   │
│  │  │  VIDEO DROP  │  │     PIPELINE STAGES          │   │   │
│  │  │  ZONE        │  │  ☑ Extract Frames   2:14     │   │   │
│  │  │  .mp4        │  │  ☑ COLMAP SfM       8:32     │   │   │
│  │  │              │  │  ▶ Training Splat  12:01     │   │   │
│  │  └─────────────┘  │  ○ Export .ply                │   │   │
│  │                    │  ○ Convert .ksplat            │   │   │
│  │                    └──────────────────────────────┘   │   │
│  │                                                       │   │
│  │  ┌──────────────────────────────────────────────────┐ │   │
│  │  │            LIVE STATS PANEL                       │ │   │
│  │  │  Iteration: 12,450 / 30,000  ████████░░  41%    │ │   │
│  │  │  PSNR: 27.3 dB   Loss: 0.0042   Gaussians: 1.2M│ │   │
│  │  │  GPU Mem: 6.2 GB   ETA: 8 min 22s               │ │   │
│  │  └──────────────────────────────────────────────────┘ │   │
│  │                                                       │   │
│  │  ┌──────────────────────────────────────────────────┐ │   │
│  │  │         LIVE 3D VIEWER (iframe)                   │ │   │
│  │  │    ← nerfstudio viewer @ localhost:7007 →        │ │   │
│  │  │    (shows splat building in real-time)            │ │   │
│  │  └──────────────────────────────────────────────────┘ │   │
│  │                                                       │   │
│  │  ┌──────────────────────────────────────────────────┐ │   │
│  │  │  [⬇ Download .ply]  [⬇ Download .ksplat]        │ │   │
│  │  └──────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
        │ SSE (Server-Sent Events for live stats)
        │ REST (upload, status, download)
┌───────▼─────────────────────────────────────────────────────┐
│                 FASTAPI BACKEND (:8080)                       │
│                                                              │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ /upload   │  │ /status (SSE)│  │ /download/{filename}   │ │
│  └──────────┘  └──────────────┘  └────────────────────────┘ │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              PIPELINE ORCHESTRATOR                    │   │
│  │  Runs subprocess commands, parses stdout in real      │   │
│  │  time, extracts stats, broadcasts via SSE             │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  Subprocess calls:                                           │
│    1. ffprobe (video metadata)                               │
│    2. ns-process-data video (COLMAP)                         │
│    3. ns-train splatfacto (training)                         │
│    4. ns-export gaussian-splat (export .ply)                 │
│    5. ksplat converter (optional, .ply → .ksplat)            │
└──────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
splatforge/
├── backend/
│   ├── main.py                  # FastAPI app — routes, SSE, CORS
│   ├── pipeline.py              # Pipeline orchestrator — runs stages
│   ├── log_parser.py            # Parses nerfstudio stdout for stats
│   ├── config.py                # Paths, defaults, quality presets
│   └── ksplat_converter.py      # PLY → ksplat conversion (optional)
│
├── frontend/
│   ├── index.html               # Single-page app — the whole UI
│   ├── style.css                # Dark theme, military-grade aesthetic
│   └── app.js                   # SSE listener, upload handler, UI updates
│
├── jobs/                        # Working directory for pipeline runs
│   └── (created at runtime)
│
├── requirements.txt             # Python deps (fastapi, uvicorn, etc.)
├── setup.sh                     # One-shot install script
└── README.md
```

---

## CRITICAL IMPLEMENTATION DETAILS

### 1. The Pipeline Stages

The pipeline has exactly 5 stages. Each is a subprocess call. The backend runs them sequentially, parsing stdout/stderr in real time.

```
STAGE 1 — VIDEO ANALYSIS (ffprobe)
  Input:  user's .mp4 file
  Output: duration, resolution, fps, codec info
  Time:   instant (<1s)
  Stats to extract: video duration, resolution, frame count, file size

STAGE 2 — FRAME EXTRACTION + COLMAP (ns-process-data)
  Input:  .mp4 file path
  Output: scene_data/ directory with images/, sparse/, transforms.json
  Time:   5-30 minutes depending on video length
  Command:
    ns-process-data video \
      --data {input_mp4} \
      --output-dir {job_dir}/scene_data \
      --num-frames-target {num_frames} \
      --matching-method exhaustive
  Stats to extract from stdout:
    - "Extracting X frames" → frame extraction progress
    - COLMAP feature extraction progress (image counts)
    - COLMAP matching progress
    - "Registered X images" → registration count (want >90%)
    - "num_points3D = X" → initial point cloud size
    - "mean_reprojection_error = X" → quality metric (want <1.5)

STAGE 3 — GAUSSIAN SPLAT TRAINING (ns-train)
  Input:  scene_data/ directory
  Output: trained model checkpoint in outputs/
  Time:   10-40 minutes depending on GPU and iteration count
  Command:
    ns-train splatfacto \
      --data {job_dir}/scene_data \
      --output-dir {job_dir}/outputs \
      --max-num-iterations {iterations} \
      --pipeline.model.cull_alpha_thresh 0.005 \
      --pipeline.model.continue_cull_post_densification False \
      --pipeline.model.use_scale_regularization True \
      --vis viewer \
      --viewer.websocket-port 7007
  Stats to extract from stdout (nerfstudio prints these per step):
    - "Step: XXXXX" or iteration number
    - "loss: X.XXXX"
    - "psnr: XX.XX"
    - "num_gaussians: XXXXXX" (or similar gaussian count)
    - "train_rays_per_sec: XXXXXX"
    - ETA / time elapsed
  IMPORTANT: The --vis viewer flag launches the nerfstudio web viewer
  on port 7007. This is the LIVE 3D PREVIEW that shows the splat
  building in real time. We embed this as an iframe in the frontend.

STAGE 4 — EXPORT TO PLY (ns-export)
  Input:  trained model config.yml
  Output: .ply file in exports/
  Time:   10-60 seconds
  Command:
    ns-export gaussian-splat \
      --load-config {job_dir}/outputs/splatfacto/{timestamp}/config.yml \
      --output-dir {job_dir}/exports
  The output file is typically: {job_dir}/exports/splat.ply

STAGE 5 — CONVERT TO KSPLAT (optional)
  Input:  .ply file
  Output: .ksplat file
  Time:   5-30 seconds
  This step uses the mkkellogg/GaussianSplats3D converter.
  If the Node.js converter is not available, skip this stage
  and just provide the .ply (SuperSplat can convert it in-browser).
  Command (if available):
    node {gsplat3d_path}/util/create-ksplat.js \
      {job_dir}/exports/splat.ply \
      {job_dir}/exports/terrain.ksplat \
      1 10 1
  Alternatively, provide a Python-based PLY-to-splat converter
  as a fallback.
```

### 2. Server-Sent Events (SSE) for Live Stats

The frontend connects to `GET /api/status/stream` which is an SSE endpoint. The backend broadcasts stats as the pipeline runs.

**SSE Message Format:**
```json
{
  "stage": "training",
  "stage_index": 2,
  "total_stages": 5,
  "stage_progress": 0.41,
  "overall_progress": 0.55,
  "stats": {
    "iteration": 12450,
    "max_iterations": 30000,
    "loss": 0.0042,
    "psnr": 27.3,
    "num_gaussians": 1200000,
    "train_rays_per_sec": 450000,
    "elapsed_seconds": 721,
    "eta_seconds": 502
  },
  "message": "Training iteration 12,450 / 30,000",
  "viewer_ready": true,
  "viewer_url": "http://localhost:7007"
}
```

**Stage names for UI:**
```
"analyzing"   → Stage 1: Analyzing Video
"processing"  → Stage 2: Extracting Frames & COLMAP
"training"    → Stage 3: Training Gaussian Splat
"exporting"   → Stage 4: Exporting .ply
"converting"  → Stage 5: Converting to .ksplat
"complete"    → Done!
"error"       → Pipeline failed
```

### 3. Log Parsing Strategy

Nerfstudio prints training progress to stdout in a specific format. The `log_parser.py` module must parse this in real time from the subprocess stdout pipe.

**Nerfstudio stdout patterns to match:**

```python
# During ns-process-data:
# Look for lines like:
#   "Processing frames..."
#   "Running COLMAP..."  
#   "Registered 287 images"
#   "num_points3D = 45231"
#   "mean_reprojection_error = 0.8432"
#   "COLMAP found poses for X images" or "CONGRATS"

# During ns-train splatfacto:
# Nerfstudio uses rich console output. Key patterns:
#   Step lines contain iteration, loss values
#   Look for patterns like:
#     "Step ... loss=... psnr=..."  
#   Or table-formatted output with columns
#   The exact format varies by nerfstudio version,
#   so be flexible with regex patterns.

TRAINING_PATTERNS = {
    # Pattern: "Iteration XXXXX / XXXXX"
    'iteration': r'(?:Step|Iter(?:ation)?)\s*[:=]?\s*(\d+)',
    # Pattern: "loss: 0.0042" or "loss=0.0042"
    'loss': r'loss\s*[:=]\s*([\d.]+)',
    # Pattern: "psnr: 27.3" or "PSNR: 27.3"
    'psnr': r'psnr\s*[:=]\s*([\d.]+)',
    # Gaussian count
    'num_gaussians': r'(?:num[_\s]?gaussians|splats)\s*[:=]\s*([\d,]+)',
    # Max iterations from config
    'max_iter': r'max[_-]num[_-]iterations\s*[:=]\s*(\d+)',
}
```

### 4. Frontend Design Spec

**Theme:** Dark background (#0a0a0f), accent color electric blue (#00aaff) with amber (#ff8800) for warnings. Military/tech aesthetic matching PROJECT AEGIS. Monospace fonts for stats. Clean, minimal, professional.

**Layout (single page, no scroll needed on 1080p+):**

```
┌─────────────────────────────────────────────────────────┐
│  SPLATFORGE          [Quality: ▼ Standard]   [GPU: RTX] │  ← Header bar
├─────────────────────┬───────────────────────────────────┤
│                     │                                    │
│   VIDEO INPUT       │    PIPELINE PROGRESS               │
│   (drag & drop or   │    ═══════════════                 │
│    file picker)     │    ✓ Analyze Video      0:02       │
│                     │    ✓ COLMAP Processing   8:14       │
│   ┌─────────────┐  │    ▶ Training Splat     12:33       │
│   │   📹        │  │      ████████████░░░░  67%         │
│   │  drop .mp4  │  │    ○ Export .ply                    │
│   │   here      │  │    ○ Convert .ksplat                │
│   └─────────────┘  │                                    │
│                     │    ─────────────────────           │
│   Video Info:       │    TRAINING STATS                  │
│   Duration: 2:34    │    Iteration:  20,100 / 30,000     │
│   Resolution: 4K    │    PSNR:       28.4 dB             │
│   Frames: 300       │    Loss:       0.0031              │
│   Size: 1.2 GB      │    Gaussians:  1,450,000           │
│                     │    Speed:      512k rays/s          │
│   [▶ START]         │    ETA:        4 min 12s            │
│                     │                                    │
├─────────────────────┴───────────────────────────────────┤
│                                                          │
│              LIVE 3D VIEWER                               │
│   ┌──────────────────────────────────────────────────┐   │
│   │                                                   │   │
│   │    (nerfstudio viewer iframe — localhost:7007)    │   │
│   │    Shows splat building in real-time!             │   │
│   │    Mouse orbit / zoom to inspect                  │   │
│   │                                                   │   │
│   └──────────────────────────────────────────────────┘   │
│   [👁 Toggle Viewer]  [⬇ Download .ply]  [⬇ Download .ksplat] │
└──────────────────────────────────────────────────────────┘
```

**Key UI behaviors:**
- The drag & drop zone accepts `.mp4`, `.mov`, `.avi`, `.mkv` files
- File is uploaded via `POST /api/upload` (multipart form)
- On upload completion, the START button becomes active
- Clicking START triggers `POST /api/start` which begins the pipeline
- The SSE stream at `GET /api/status/stream` provides all live updates
- The 3D viewer iframe appears only during training stage (stage 3) when `viewer_ready: true`
- The viewer iframe src is `http://localhost:7007` (nerfstudio's built-in viewer)
- Download buttons appear only after pipeline completes
- Quality preset dropdown controls iteration count and other params:
  - **Draft:** 7,000 iterations (~5 min training)
  - **Standard:** 30,000 iterations (~20 min training) ← default
  - **High Quality:** 50,000 iterations (~35 min training)
  - **Ultra:** 100,000 iterations (~60+ min training)
- An animated PSNR chart that plots quality over training iterations (simple canvas or SVG line chart)

**CSS Animations:**
- Stage checkmarks animate in with a satisfying pop
- Active stage has a pulsing glow effect
- Progress bar has a subtle shimmer/gradient animation
- Stats numbers use a rolling counter animation when they update
- The overall progress ring/arc at the top animates smoothly

### 5. Quality Presets

```python
PRESETS = {
    "draft": {
        "num_frames": 150,
        "max_iterations": 7000,
        "cull_alpha_thresh": 0.01,
        "description": "Quick preview — ~5-10 min total"
    },
    "standard": {
        "num_frames": 300,
        "max_iterations": 30000,
        "cull_alpha_thresh": 0.005,
        "description": "Good quality — ~20-40 min total"
    },
    "high": {
        "num_frames": 500,
        "max_iterations": 50000,
        "cull_alpha_thresh": 0.005,
        "description": "High quality — ~40-60 min total"
    },
    "ultra": {
        "num_frames": 800,
        "max_iterations": 100000,
        "cull_alpha_thresh": 0.003,
        "description": "Maximum quality — 60+ min"
    }
}
```

---

## BACKEND IMPLEMENTATION DETAILS

### `main.py` — FastAPI Application

```python
# Key routes:
#
# POST /api/upload
#   - Accepts multipart file upload
#   - Saves to jobs/{job_id}/input.mp4
#   - Returns job_id and video metadata (from ffprobe)
#
# POST /api/start
#   - Body: { "job_id": "xxx", "preset": "standard" }
#   - Starts pipeline in background task (asyncio)
#   - Returns immediately with { "status": "started" }
#
# GET /api/status/stream
#   - SSE endpoint
#   - Streams pipeline progress as JSON events
#   - Client connects with EventSource
#
# GET /api/status/{job_id}
#   - Polling fallback — returns current status JSON
#
# GET /api/download/{job_id}/{filename}
#   - Serves the output .ply or .ksplat file
#   - FileResponse with appropriate headers
#
# POST /api/cancel/{job_id}
#   - Kills the running subprocess
#   - Cleans up state
#
# GET /api/gpu-info
#   - Returns GPU name, VRAM, CUDA version via nvidia-smi
#
# Static files:
#   - Mount frontend/ directory at /

# CORS: Allow localhost origins for development

# IMPORTANT: Use asyncio.create_subprocess_exec for all subprocesses
# so we can read stdout line-by-line without blocking the event loop.
# Pipe both stdout and stderr and merge them for parsing.
```

### `pipeline.py` — Pipeline Orchestrator

This is the core engine. It runs each stage as an async subprocess, reads output line by line, parses stats, and broadcasts them via a shared state object that the SSE endpoint reads.

```python
# Pseudocode structure:

class PipelineOrchestrator:
    def __init__(self, job_id, input_path, preset, status_callback):
        self.job_id = job_id
        self.input_path = input_path
        self.preset = PRESETS[preset]
        self.status_callback = status_callback  # callable to broadcast SSE
        self.current_stage = None
        self.cancelled = False
        
    async def run(self):
        try:
            await self._stage_analyze()
            await self._stage_process_data()
            await self._stage_train()
            await self._stage_export()
            await self._stage_convert()
            self._broadcast("complete", 1.0, "Pipeline complete!")
        except asyncio.CancelledError:
            self._broadcast("cancelled", None, "Pipeline cancelled")
        except Exception as e:
            self._broadcast("error", None, str(e))
    
    async def _stage_analyze(self):
        """Run ffprobe to get video metadata."""
        self._broadcast("analyzing", 0.0, "Analyzing video...")
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            self.input_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        metadata = json.loads(stdout)
        # Extract: duration, resolution, fps, codec, file size
        # Store in self.video_info
        self._broadcast("analyzing", 1.0, "Video analysis complete", 
                        stats=self.video_info)
    
    async def _stage_process_data(self):
        """Run ns-process-data video — extracts frames + COLMAP."""
        self._broadcast("processing", 0.0, "Extracting frames & running COLMAP...")
        cmd = [
            "ns-process-data", "video",
            "--data", self.input_path,
            "--output-dir", f"{self.job_dir}/scene_data",
            "--num-frames-target", str(self.preset["num_frames"]),
        ]
        await self._run_subprocess(cmd, self._parse_colmap_output)
    
    async def _stage_train(self):
        """Run ns-train splatfacto — the main training loop."""
        self._broadcast("training", 0.0, "Starting Gaussian Splat training...")
        
        # Find the scene_data directory
        cmd = [
            "ns-train", "splatfacto",
            "--data", f"{self.job_dir}/scene_data",
            "--output-dir", f"{self.job_dir}/outputs",
            "--max-num-iterations", str(self.preset["max_iterations"]),
            "--pipeline.model.cull_alpha_thresh", str(self.preset["cull_alpha_thresh"]),
            "--pipeline.model.continue_cull_post_densification", "False",
            "--pipeline.model.use_scale_regularization", "True",
            "--vis", "viewer",
            "--viewer.websocket-port", "7007",
        ]
        
        # After process starts and viewer initializes, broadcast viewer_ready
        await self._run_subprocess(cmd, self._parse_training_output)
    
    async def _stage_export(self):
        """Run ns-export gaussian-splat."""
        self._broadcast("exporting", 0.0, "Exporting .ply file...")
        
        # Find the config.yml from the most recent training run
        config_path = self._find_latest_config()
        cmd = [
            "ns-export", "gaussian-splat",
            "--load-config", config_path,
            "--output-dir", f"{self.job_dir}/exports",
        ]
        await self._run_subprocess(cmd, None)
        self._broadcast("exporting", 1.0, "Export complete")
    
    async def _stage_convert(self):
        """Convert .ply to .ksplat (optional)."""
        ply_path = f"{self.job_dir}/exports/splat.ply"
        if not os.path.exists(ply_path):
            # Try point_cloud.ply as alternative name
            ply_path = f"{self.job_dir}/exports/point_cloud.ply"
        
        # Try Node.js converter first, fall back to skipping
        # If skipped, just note that .ply is available
        self._broadcast("converting", 1.0, "Conversion complete")
    
    async def _run_subprocess(self, cmd, line_parser):
        """
        Run a command as an async subprocess.
        Read stdout+stderr line by line.
        Call line_parser(line) for each line to extract stats.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
        )
        
        async for line_bytes in proc.stdout:
            if self.cancelled:
                proc.kill()
                break
            line = line_bytes.decode('utf-8', errors='replace').strip()
            if line and line_parser:
                line_parser(line)
        
        await proc.wait()
        if proc.returncode != 0 and not self.cancelled:
            raise RuntimeError(f"Command failed with code {proc.returncode}")
    
    def _parse_training_output(self, line):
        """
        Parse a line of nerfstudio training output.
        Extract iteration, loss, PSNR, gaussian count.
        Broadcast via SSE.
        """
        stats = parse_training_line(line)  # from log_parser.py
        if stats:
            progress = stats.get('iteration', 0) / self.preset['max_iterations']
            self._broadcast("training", progress, 
                           f"Training iteration {stats.get('iteration', '?')}",
                           stats=stats,
                           viewer_ready=True,
                           viewer_url="http://localhost:7007")
        
        # Detect viewer ready message
        if "viewer" in line.lower() and "7007" in line:
            self._broadcast("training", None, None,
                           viewer_ready=True,
                           viewer_url="http://localhost:7007")
```

### `log_parser.py` — Stdout Parser

```python
# This module contains regex patterns to extract stats from
# nerfstudio's console output.
#
# Nerfstudio uses the `rich` library for formatted console output.
# When piped to a subprocess, rich may output ANSI codes or
# simplified text. We need to handle both.
#
# IMPORTANT: Strip ANSI escape codes before parsing:
#   import re
#   ANSI_PATTERN = re.compile(r'\x1b\[[0-9;]*m')
#   clean_line = ANSI_PATTERN.sub('', raw_line)
#
# Key patterns to match:
#
# 1. Training step:
#    Nerfstudio typically prints a table row per N steps.
#    Look for step/iteration numbers, loss, PSNR metrics.
#    Example outputs (format varies by version):
#      "Step 1000/30000 (3.3%): loss=0.0234 psnr=21.2"
#      Table format with columns: Step | Loss | PSNR | ...
#
# 2. COLMAP progress:
#    "Extracting features..."
#    "Matching features..."  
#    "Running bundle adjustment..."
#    "Registered 245 / 300 images"
#
# 3. Viewer ready:
#    "Starting viewer on port 7007"
#    Or URL containing "7007"
#
# Strategy: Be FLEXIBLE with patterns. Use multiple regex attempts.
# If a line doesn't match any pattern, ignore it.
# Always return a dict or None.

def parse_training_line(line: str) -> dict | None:
    """Parse a single line of ns-train output. Returns stats dict or None."""
    line = strip_ansi(line)
    
    result = {}
    
    # Try to extract iteration
    iter_match = re.search(r'(?:Step|Iter)\D*(\d[\d,]*)', line, re.IGNORECASE)
    if iter_match:
        result['iteration'] = int(iter_match.group(1).replace(',', ''))
    
    # Try to extract loss
    loss_match = re.search(r'loss\D*([\d.]+(?:e[+-]?\d+)?)', line, re.IGNORECASE)
    if loss_match:
        result['loss'] = float(loss_match.group(1))
    
    # Try to extract PSNR
    psnr_match = re.search(r'psnr\D*([\d.]+)', line, re.IGNORECASE)
    if psnr_match:
        result['psnr'] = float(psnr_match.group(1))
    
    # Try to extract gaussian count
    gauss_match = re.search(r'(?:gaussians?|splats?|num_points)\D*([\d,]+)', line, re.IGNORECASE)
    if gauss_match:
        val = gauss_match.group(1).replace(',', '')
        if val.isdigit() and int(val) > 1000:  # sanity check
            result['num_gaussians'] = int(val)
    
    return result if result else None
```

---

## FRONTEND IMPLEMENTATION DETAILS

### `index.html`

Single HTML file. No build step, no bundler, no framework. Pure HTML + CSS + vanilla JS. This is a tool, not a web app — keep it lean.

**Structure:**
- Header bar with logo, preset selector, GPU info badge
- Two-column layout: left = input panel, right = progress/stats
- Full-width section below: 3D viewer (iframe, togglable)
- Footer: download buttons

**Key elements with IDs:**
```
#drop-zone          — drag & drop area
#file-input         — hidden file input
#preset-select      — quality preset dropdown
#start-btn          — start pipeline button
#cancel-btn         — cancel button
#stage-list         — pipeline stages list (ul)
#stats-panel        — training stats container
#psnr-chart         — canvas element for PSNR chart
#viewer-container   — iframe container (hidden initially)
#viewer-iframe      — the actual iframe for nerfstudio viewer
#viewer-toggle      — toggle button for viewer
#download-section   — download buttons container
#overall-progress   — circular progress indicator
#log-output         — collapsible raw log output area
```

### `style.css`

```
Theme:
  --bg-primary:    #0a0a0f
  --bg-secondary:  #12121a
  --bg-card:       #1a1a2e
  --accent-blue:   #00aaff
  --accent-amber:  #ff8800
  --accent-green:  #22ff88
  --accent-red:    #ff4444
  --text-primary:  #e0e0e0
  --text-secondary:#888899
  --text-mono:     'JetBrains Mono', 'Fira Code', 'Consolas', monospace
  --text-sans:     'Inter', -apple-system, sans-serif
  --border:        #2a2a3e
  --glow-blue:     0 0 20px rgba(0, 170, 255, 0.3)

Key styles:
  - Cards: bg-card with 1px border, subtle border-radius (8px)
  - Progress bars: rounded, with gradient shimmer animation
  - Stage items: flex row, icon + label + time, checkmark animates in
  - Active stage: left border accent-blue, subtle pulse glow
  - Stats: grid of stat cards, each with label + big number
  - Drop zone: dashed border, hover effect, drag-over highlight
  - Buttons: filled accent-blue, hover brighten, disabled state
  - The iframe viewer: border with accent glow
  - Numbers/stats: monospace font, tabular-nums for alignment
  
Animations:
  @keyframes shimmer — progress bar gradient slide
  @keyframes pulse-glow — active stage border pulse
  @keyframes pop-in — checkmark scale bounce
  @keyframes fade-up — stats number entrance
  @keyframes spin — loading spinner for stages
```

### `app.js`

```javascript
// Core state
let currentJobId = null;
let eventSource = null;
let psnrHistory = [];  // [{iteration, psnr}] for chart

// ── File Upload ─────────────────────────────────────────
// Drag & drop + click-to-browse
// On file selected:
//   1. Show file info immediately (name, size)
//   2. Upload via fetch() to POST /api/upload (FormData)
//   3. On response: show video metadata, enable START button
//   4. Store job_id from response

// ── Pipeline Control ────────────────────────────────────
// START button: POST /api/start with { job_id, preset }
//   Then connect SSE: new EventSource('/api/status/stream?job_id=...')
//   
// CANCEL button: POST /api/cancel/{job_id}
//   Close SSE connection

// ── SSE Handler ─────────────────────────────────────────
// eventSource.onmessage = (event) => {
//   const data = JSON.parse(event.data);
//   updateStages(data.stage, data.stage_index);
//   updateProgress(data.overall_progress);
//   updateStats(data.stats);
//   if (data.viewer_ready) showViewer(data.viewer_url);
//   if (data.stage === 'complete') showDownloads();
//   if (data.stage === 'error') showError(data.message);
// }

// ── PSNR Chart ──────────────────────────────────────────
// Simple canvas line chart.
// On each stats update during training, push to psnrHistory.
// Redraw chart: X axis = iteration, Y axis = PSNR (dB).
// Use a smooth line (bezier interpolation between points).
// Show grid lines at 5 dB increments (15, 20, 25, 30, 35).
// Current PSNR value shown as a tooltip at the latest point.
// Chart colors: line = accent-blue, grid = border color, bg = bg-card.

// ── Viewer Toggle ───────────────────────────────────────
// The nerfstudio viewer runs at http://localhost:7007
// We embed it in an iframe.
// IMPORTANT: The viewer may not load immediately when training starts.
// Poll or wait for the SSE `viewer_ready` flag before showing.
// If iframe fails to load (nerfstudio viewer not reachable), 
// show a message: "Viewer loading... The 3D preview will appear 
// shortly after training begins."
//
// The viewer toggle button shows/hides the viewer section
// to save screen space. Default: SHOWN during training.

// ── Download Buttons ────────────────────────────────────
// After pipeline completes:
//   Show download buttons for .ply and .ksplat (if available)
//   Links: /api/download/{job_id}/splat.ply
//          /api/download/{job_id}/terrain.ksplat
//   Also show file sizes next to buttons

// ── Formatting Helpers ──────────────────────────────────
// formatNumber(1234567) → "1,234,567"
// formatTime(seconds) → "12:34" or "1:02:34"
// formatBytes(bytes) → "1.2 GB"
// animateNumber(element, from, to, duration) → rolling counter
```

---

## NERFSTUDIO VIEWER INTEGRATION (LIVE 3D PREVIEW)

This is the "wow factor" feature. Nerfstudio includes a built-in web viewer that shows the splat building during training. It works like this:

1. When `ns-train` runs with `--vis viewer`, it starts a WebSocket server on port 7007
2. The viewer at `http://localhost:7007` connects to this WebSocket
3. As training progresses, the viewer updates the 3D scene in real-time
4. The user can orbit, zoom, and pan to inspect the model as it trains

**Integration approach:**
- Embed as an `<iframe src="http://localhost:7007">` in the frontend
- Show it only during the training stage
- It will NOT work if the training process isn't running
- On training completion, the viewer freezes on the last state (still viewable)
- The viewer adds ~5-10% overhead to training speed (acceptable)

**Fallback if viewer doesn't load:**
- Show a placeholder with a message and a retry button
- Some nerfstudio versions use `viewer.nerf.studio` as a relay — 
  in that case the iframe URL would be something like 
  `https://viewer.nerf.studio/versions/24-05?websocket_url=ws://localhost:7007`
- Test both approaches and use whichever works

**IMPORTANT GOTCHA — Cross-Origin:**
The FastAPI app runs on :8080 and the nerfstudio viewer on :7007.
Embedding cross-origin iframes is fine for localhost.
But if COOP/COEP headers are set (for SharedArrayBuffer), they
may block the iframe. Solution: do NOT set COOP/COEP headers on
the SplatForge FastAPI server. Those headers are only needed
for the AEGIS frontend that loads GaussianSplats3D, not here.

---

## SETUP & INSTALLATION

### Prerequisites

```bash
# System requirements:
# - NVIDIA GPU with 8GB+ VRAM (RTX 3060+ recommended)
# - CUDA 11.8 or 12.x installed
# - Python 3.10 or 3.11
# - FFmpeg installed and in PATH
# - ~20 GB free disk space per job

# Check GPU:
nvidia-smi

# Check CUDA:
nvcc --version

# Check FFmpeg:
ffmpeg -version

# Check Python:
python --version
```

### Installation Script (`setup.sh`)

```bash
#!/bin/bash
set -e

echo "╔══════════════════════════════════════════╗"
echo "║       SPLATFORGE — Setup Script          ║"
echo "╚══════════════════════════════════════════╝"

# 1. Create conda environment (or use existing)
echo "[1/5] Creating Python environment..."
conda create -n splatforge python=3.10 -y 2>/dev/null || true
conda activate splatforge

# 2. Install nerfstudio (includes COLMAP, gsplat, torch)
echo "[2/5] Installing nerfstudio..."
pip install nerfstudio

# 3. Install FastAPI and other Python deps
echo "[3/5] Installing SplatForge dependencies..."
pip install fastapi uvicorn python-multipart aiofiles

# 4. Verify nerfstudio installation
echo "[4/5] Verifying nerfstudio..."
ns-train --help > /dev/null 2>&1 && echo "  ✓ nerfstudio OK" || echo "  ✗ nerfstudio FAILED"
ns-process-data --help > /dev/null 2>&1 && echo "  ✓ ns-process-data OK" || echo "  ✗ ns-process-data FAILED"

# 5. Verify COLMAP
echo "[5/5] Verifying COLMAP..."
colmap --help > /dev/null 2>&1 && echo "  ✓ COLMAP OK" || echo "  ✗ COLMAP not found (ns-process-data will try to install)"

echo ""
echo "Setup complete! Run with:"
echo "  cd splatforge && python backend/main.py"
echo ""
echo "Then open http://localhost:8080 in your browser."
```

### `requirements.txt`

```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-multipart>=0.0.6
aiofiles>=23.2.0
```

(nerfstudio and its dependencies are installed separately via pip install nerfstudio)

---

## RUNNING THE APP

```bash
# Start the backend (serves frontend too):
cd splatforge
python backend/main.py

# This starts:
#   - FastAPI on http://localhost:8080 (dashboard)
#   - nerfstudio viewer will auto-start on :7007 during training

# Open browser to http://localhost:8080
# Drop in an MP4, select quality, hit Start.
```

The `main.py` entry point should:
1. Create the `jobs/` directory if it doesn't exist
2. Mount `frontend/` as static files at `/`
3. Start uvicorn on port 8080
4. Print a banner with the URL

```python
if __name__ == "__main__":
    import uvicorn
    print("\n  ╔══════════════════════════════════════╗")
    print("  ║  SPLATFORGE — Video to Splat Engine  ║")
    print("  ║  Open: http://localhost:8080          ║")
    print("  ╚══════════════════════════════════════╝\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
```

---

## EDGE CASES & ERROR HANDLING

### Video Validation
- Reject files that aren't video (check MIME type and extension)
- Reject videos shorter than 5 seconds (not enough frames)
- Reject videos larger than 10 GB (configurable limit)
- Warn if resolution is below 1080p

### COLMAP Failure
- Most common failure point in the pipeline
- If `ns-process-data` fails or registers <70% of frames:
  - Show clear error: "COLMAP could not find enough camera poses. 
    This usually means the video has too much motion blur, 
    insufficient overlap between frames, or repetitive textures.
    Try re-recording with slower drone movement."
- If COLMAP takes >30 minutes, show a warning but don't timeout

### Training Failure
- If `ns-train` crashes (OOM, CUDA error):
  - Parse the error from stderr
  - Common fix: "GPU out of memory. Try the Draft preset or 
    reduce resolution."
  - Offer to retry with lower settings

### nerfstudio Not Installed
- If `ns-train` command not found:
  - Show setup instructions prominently
  - Link to nerfstudio installation guide
  - The /api/gpu-info endpoint should also check for nerfstudio

### Viewer Not Loading
- The iframe may fail if nerfstudio viewer takes time to start
- Implement a retry mechanism: try loading the iframe every 3 seconds
- After 30 seconds of failure, show "Viewer unavailable" message
- Training still continues even if viewer fails

---

## IMPORTANT NOTES FOR CLAUDE CODE

1. **This is a LOCAL tool** — everything runs on the user's machine. No cloud, no uploads to external servers. All processing is local via nerfstudio.

2. **The frontend must be a SINGLE HTML file** with inline or separate CSS/JS — no React, no Tailwind build step, no npm. Pure vanilla. It's served as a static file by FastAPI.

3. **SSE (Server-Sent Events)** is the communication channel for live stats. NOT WebSocket. SSE is simpler — it's just a GET endpoint that keeps the connection open and sends `data: {...}\n\n` formatted events. FastAPI supports this via `StreamingResponse`.

4. **The nerfstudio viewer is NOT something we build** — it's built into nerfstudio and auto-starts on port 7007 during training. We just embed it in an iframe. That's it.

5. **The biggest risk is log parsing** — nerfstudio's output format varies between versions. The parser must be resilient: if it can't parse a line, skip it silently. Never crash on unparseable output.

6. **File paths** — use `pathlib.Path` everywhere. Jobs go in `jobs/{uuid}/`. Create `scene_data/`, `outputs/`, `exports/` subdirectories within each job.

7. **Process management** — store the subprocess PID so we can kill it on cancel. Use `asyncio.create_subprocess_exec` NOT `subprocess.run`.

8. **GPU info** — run `nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader` to get GPU info for the header badge.

9. **The .ksplat conversion is OPTIONAL** — if the node converter isn't available, that's fine. The .ply file works everywhere (SuperSplat, nerfstudio viewer, etc). Don't block on this.

10. **Testing without nerfstudio** — for UI development, implement a mock mode that simulates the pipeline with fake stats at 10x speed. Activate with `?mock=true` query param or `SPLATFORGE_MOCK=1` env var. This way the frontend can be developed and tested without a GPU.

---

## MOCK MODE (for development)

When `SPLATFORGE_MOCK=1` env var is set or `?mock=true` is in the URL:

- Skip all real subprocess calls
- Simulate each stage with realistic timing:
  - Analyze: 1 second
  - COLMAP: 10 seconds (with progress updates)
  - Training: 30 seconds (with iteration/loss/psnr updates that improve over time)
  - Export: 2 seconds
  - Convert: 1 second
- Generate fake but realistic stats:
  - PSNR: starts at ~18, climbs to ~28-30 logarithmically
  - Loss: starts at ~0.05, drops to ~0.003
  - Gaussians: starts at 100k, grows to ~1.5M
- Do NOT show the viewer iframe in mock mode (it won't exist)
- Create a dummy .ply file (empty or tiny) for download testing

This is ESSENTIAL for rapid frontend iteration.

---

## SUMMARY — What Claude Code Should Build

1. **Backend (`backend/main.py`)**: FastAPI app with upload, start, SSE stream, download, cancel, and GPU info endpoints. Serves the frontend as static files.

2. **Pipeline (`backend/pipeline.py`)**: Async orchestrator that runs 5 stages sequentially via subprocess, parses stdout for stats, broadcasts via shared state.

3. **Log Parser (`backend/log_parser.py`)**: Regex-based parser for nerfstudio and COLMAP output. Resilient to format variations.

4. **Config (`backend/config.py`)**: Quality presets, paths, defaults.

5. **Frontend (`frontend/index.html`)**: Single-page dashboard with drag-drop upload, pipeline stage tracker, live stats panel, PSNR chart, nerfstudio viewer iframe, and download buttons. Dark military-tech theme.

6. **Frontend Styles (`frontend/style.css`)**: Dark theme with electric blue/amber accents, animations for progress, monospace stats.

7. **Frontend Logic (`frontend/app.js`)**: SSE connection, upload handling, UI updates, chart rendering, viewer management.

8. **Mock Mode**: Simulated pipeline for development without GPU/nerfstudio.

9. **Setup Script (`setup.sh`)**: One-command install of nerfstudio + FastAPI deps.

The end result: user opens `localhost:8080`, drops an MP4, selects quality, hits Start, watches the pipeline progress with live stats, sees the 3D model build in real-time, and downloads the final `.ply` file. Total development estimate: ~4-6 hours of Claude Code time.
