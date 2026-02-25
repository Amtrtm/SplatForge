# SplatForge Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local web app that takes a drone MP4 video and automates Gaussian Splat creation with a live dashboard.

**Architecture:** FastAPI backend runs 5 pipeline stages as async subprocesses, parsing stdout for stats and broadcasting via SSE. Vanilla HTML/CSS/JS frontend connects to SSE for live updates. Mock mode baked in for development without GPU/nerfstudio.

**Tech Stack:** Python 3.10+ / FastAPI / uvicorn / asyncio subprocesses / vanilla HTML+CSS+JS / nerfstudio / COLMAP / FFmpeg

---

## Task 1: Project Scaffolding

**Files:**
- Create: `backend/__init__.py`
- Create: `frontend/` (directory)
- Create: `jobs/` (directory with `.gitkeep`)
- Create: `requirements.txt`
- Create: `setup.sh`
- Create: `.gitignore`

**Step 1: Initialize git repo and create directory structure**

```bash
cd C:/Users/amtrt/Documents/JDD/SplatForge
git init
mkdir -p backend frontend jobs
touch backend/__init__.py
touch jobs/.gitkeep
```

**Step 2: Create requirements.txt**

```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-multipart>=0.0.6
aiofiles>=23.2.0
```

**Step 3: Create .gitignore**

```
__pycache__/
*.pyc
jobs/*/
!jobs/.gitkeep
.env
*.egg-info/
dist/
build/
.venv/
```

**Step 4: Create setup.sh**

The one-shot install script per the spec. Creates conda env, installs nerfstudio + FastAPI deps, verifies installation.

**Step 5: Commit**

```bash
git add -A
git commit -m "chore: scaffold project structure"
```

---

## Task 2: Backend — config.py

**Files:**
- Create: `backend/config.py`

**Step 1: Write config.py**

Contains:
- `PRESETS` dict with 4 quality levels (draft/standard/high/ultra), each having `num_frames`, `max_iterations`, `cull_alpha_thresh`, `description`
- `BASE_DIR` — project root via `Path(__file__).resolve().parent.parent`
- `JOBS_DIR` — `BASE_DIR / "jobs"`
- `FRONTEND_DIR` — `BASE_DIR / "frontend"`
- `DEFAULT_PRESET` = `"standard"`
- `MAX_UPLOAD_SIZE` = `10 * 1024 * 1024 * 1024` (10 GB)
- `MIN_VIDEO_DURATION` = `5.0` seconds
- `ALLOWED_EXTENSIONS` = `{".mp4", ".mov", ".avi", ".mkv"}`
- `VIEWER_PORT` = `7007`
- `SERVER_PORT` = `8080`

Preset values from the spec:
```python
PRESETS = {
    "draft":    {"num_frames": 150, "max_iterations": 7000,   "cull_alpha_thresh": 0.01,  "description": "Quick preview — ~5-10 min total"},
    "standard": {"num_frames": 300, "max_iterations": 30000,  "cull_alpha_thresh": 0.005, "description": "Good quality — ~20-40 min total"},
    "high":     {"num_frames": 500, "max_iterations": 50000,  "cull_alpha_thresh": 0.005, "description": "High quality — ~40-60 min total"},
    "ultra":    {"num_frames": 800, "max_iterations": 100000, "cull_alpha_thresh": 0.003, "description": "Maximum quality — 60+ min"},
}
```

**Step 2: Commit**

```bash
git add backend/config.py
git commit -m "feat: add config with quality presets and paths"
```

---

## Task 3: Backend — log_parser.py

**Files:**
- Create: `backend/log_parser.py`
- Create: `tests/test_log_parser.py`

**Step 1: Write the failing tests**

Test `strip_ansi()`, `parse_training_line()`, and `parse_colmap_line()` with realistic nerfstudio/COLMAP output samples. Key test cases:

```python
# test_strip_ansi - removes ANSI escape codes
# test_parse_training_iteration - "Step 1000/30000" → {"iteration": 1000}
# test_parse_training_loss - "loss=0.0234" → {"loss": 0.0234}
# test_parse_training_psnr - "psnr=21.2" → {"psnr": 21.2}
# test_parse_training_combined - full line with all stats
# test_parse_training_gaussians - "num_gaussians: 1200000" → {"num_gaussians": 1200000}
# test_parse_training_junk - random line → None
# test_parse_colmap_registered - "Registered 287 images" → {"registered_images": 287}
# test_parse_colmap_points - "num_points3D = 45231" → {"num_points3d": 45231}
# test_parse_colmap_error - "mean_reprojection_error = 0.8432" → {"reprojection_error": 0.8432}
```

**Step 2: Run tests to verify they fail**

```bash
cd C:/Users/amtrt/Documents/JDD/SplatForge
python -m pytest tests/test_log_parser.py -v
```
Expected: FAIL (module not found)

**Step 3: Write log_parser.py**

Contains:
- `ANSI_PATTERN = re.compile(r'\x1b\[[0-9;]*m')`
- `strip_ansi(text: str) -> str`
- `parse_training_line(line: str) -> dict | None` — extracts iteration, loss, psnr, num_gaussians from a single line. Uses flexible regex. Returns dict with found keys or None if nothing matched.
- `parse_colmap_line(line: str) -> dict | None` — extracts registered_images, num_points3d, reprojection_error, and progress messages.
- All parsers: strip ANSI first, try multiple regex patterns, return None on no match. Never raise exceptions.

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_log_parser.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add backend/log_parser.py tests/test_log_parser.py
git commit -m "feat: add log parser for nerfstudio and COLMAP output"
```

---

## Task 4: Backend — pipeline.py

**Files:**
- Create: `backend/pipeline.py`

**Step 1: Write pipeline.py**

The `PipelineOrchestrator` class. This is the core engine. Key structure:

```python
class PipelineOrchestrator:
    def __init__(self, job_id, input_path, preset_name, broadcast_fn, mock=False):
        # Store params, resolve job_dir from config.JOBS_DIR / job_id
        # Create subdirs: scene_data/, outputs/, exports/

    async def run(self):
        # Sequential: analyze → process_data → train → export → convert
        # Wrap in try/except for CancelledError and generic Exception
        # Broadcast "complete" or "error" stage

    async def cancel(self):
        # Set self.cancelled = True
        # Kill current subprocess if running

    # --- Stage methods ---
    async def _stage_analyze(self):
        # Real: run ffprobe, parse JSON output for duration/resolution/fps/codec
        # Mock: sleep 1s, return fake video_info dict
        # Broadcast: stage="analyzing"

    async def _stage_process_data(self):
        # Real: run ns-process-data video with preset num_frames
        # Mock: sleep 10s with periodic progress updates
        # Broadcast: stage="processing"

    async def _stage_train(self):
        # Real: run ns-train splatfacto with preset params + viewer on port 7007
        # Mock: sleep 30s with iteration/loss/psnr that improve over time
        # Broadcast: stage="training", include viewer_ready and viewer_url

    async def _stage_export(self):
        # Real: find config.yml, run ns-export gaussian-splat
        # Mock: sleep 2s, create dummy splat.ply
        # Broadcast: stage="exporting"

    async def _stage_convert(self):
        # Real: try node converter, skip if unavailable
        # Mock: sleep 1s, create dummy terrain.ksplat
        # Broadcast: stage="converting"

    # --- Helpers ---
    async def _run_subprocess(self, cmd, line_parser):
        # asyncio.create_subprocess_exec with stdout PIPE, stderr merged
        # Read line by line, call line_parser for each
        # Check self.cancelled, kill proc if true
        # Raise RuntimeError on non-zero exit

    def _broadcast(self, stage, stage_progress, message, **kwargs):
        # Build the SSE event dict with: stage, stage_index, total_stages,
        # stage_progress, overall_progress, stats, message, viewer_ready, viewer_url
        # Call self.broadcast_fn(event_dict)

    def _find_latest_config(self):
        # Glob for outputs/splatfacto/*/config.yml, return most recent

    # --- Mock data generators ---
    def _mock_training_stats(self, elapsed_fraction):
        # PSNR: 18 + 12*log(1+fraction*9)/log(10) — logarithmic climb to ~30
        # Loss: 0.05 * exp(-3*fraction) — exponential decay to ~0.003
        # Gaussians: 100000 + int(1400000 * fraction)
        # Iteration: int(max_iterations * fraction)
```

**Step 2: Commit**

```bash
git add backend/pipeline.py
git commit -m "feat: add pipeline orchestrator with mock mode"
```

---

## Task 5: Backend — ksplat_converter.py

**Files:**
- Create: `backend/ksplat_converter.py`

**Step 1: Write ksplat_converter.py**

Simple module with:
- `async def convert_ply_to_ksplat(ply_path, output_path) -> bool` — tries Node.js `create-ksplat.js` first, returns False if not available. Never raises.
- `def is_converter_available() -> bool` — checks if node + create-ksplat.js exist.

This is intentionally minimal — the spec says ksplat conversion is optional.

**Step 2: Commit**

```bash
git add backend/ksplat_converter.py
git commit -m "feat: add optional ksplat converter"
```

---

## Task 6: Backend — main.py (FastAPI Application)

**Files:**
- Create: `backend/main.py`

**Step 1: Write main.py**

The FastAPI app with all routes. Key structure:

```python
# --- State ---
jobs: dict[str, dict] = {}          # job_id -> {status, pipeline, task, ...}
subscribers: dict[str, list[asyncio.Queue]] = {}  # job_id -> [Queue, ...]

# --- Mock mode detection ---
MOCK_MODE = os.environ.get("SPLATFORGE_MOCK", "0") == "1"

# --- Routes ---

@app.post("/api/upload")
# Accept multipart file upload
# Validate: extension in ALLOWED_EXTENSIONS, size < MAX_UPLOAD_SIZE
# Generate job_id = uuid4().hex[:12]
# Save to jobs/{job_id}/input{ext}
# Run ffprobe (or mock) to get video metadata
# Validate: duration >= MIN_VIDEO_DURATION
# Store job in jobs dict
# Return: {job_id, filename, video_info}

@app.post("/api/start")
# Body: {job_id, preset}
# Validate job exists and isn't already running
# Create PipelineOrchestrator with broadcast_fn that pushes to subscriber queues
# Create asyncio.Task wrapping orchestrator.run()
# Store task in jobs dict
# Return: {status: "started"}

@app.get("/api/status/stream")
# SSE endpoint — StreamingResponse with media_type="text/event-stream"
# Query param: job_id
# Create Queue, add to subscribers[job_id]
# Yield "data: {json}\n\n" for each event from queue
# On disconnect: remove queue from subscribers
# Send keepalive comment every 15s to prevent timeout

@app.get("/api/status/{job_id}")
# Polling fallback — return current status dict for job

@app.get("/api/download/{job_id}/{filename}")
# Serve file from jobs/{job_id}/exports/{filename}
# FileResponse with Content-Disposition header

@app.post("/api/cancel/{job_id}")
# Call orchestrator.cancel()
# Cancel the asyncio task

@app.get("/api/gpu-info")
# Run nvidia-smi to get GPU name, VRAM, CUDA version
# Return as JSON (or mock data if MOCK_MODE)

@app.get("/api/presets")
# Return PRESETS dict for frontend to display

# --- Static files ---
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True))

# --- Entry point ---
if __name__ == "__main__":
    # Print banner, start uvicorn on port 8080
```

Important details:
- CORS middleware allowing localhost origins
- No COOP/COEP headers (would break nerfstudio iframe)
- `jobs/` directory created on startup
- The mock query param: check `request.query_params.get("mock")` on `/api/start` to override MOCK_MODE per-job
- The broadcast function: iterate `subscribers[job_id]`, put event dict into each queue

**Step 2: Verify server starts**

```bash
cd C:/Users/amtrt/Documents/JDD/SplatForge
SPLATFORGE_MOCK=1 python backend/main.py
# Should print banner and start on :8080
# Ctrl+C to stop
```

**Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: add FastAPI app with all API routes and SSE"
```

---

## Task 7: Frontend — style.css

**Files:**
- Create: `frontend/style.css`

**Step 1: Write style.css**

Dark military-tech theme. Key sections:

```
CSS Custom Properties:
  --bg-primary: #0a0a0f
  --bg-secondary: #12121a
  --bg-card: #1a1a2e
  --accent-blue: #00aaff
  --accent-amber: #ff8800
  --accent-green: #22ff88
  --accent-red: #ff4444
  --text-primary: #e0e0e0
  --text-secondary: #888899
  --border: #2a2a3e

Typography:
  --text-mono: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace
  --text-sans: 'Inter', -apple-system, sans-serif
  Stats use monospace + font-variant-numeric: tabular-nums

Layout:
  - body: full viewport, no scroll on 1080p+, grid layout
  - .header: flex row, logo left, preset selector + GPU badge right
  - .main-content: two-column grid (left: input panel, right: progress+stats)
  - .viewer-section: full-width below main content
  - .footer: download buttons

Components:
  - .drop-zone: dashed 2px border, hover highlight, drag-over glow
  - .stage-item: flex row with icon + label + timer
  - .stage-item.active: left border blue, pulse-glow animation
  - .stage-item.complete: checkmark with pop-in animation
  - .progress-bar: rounded, gradient shimmer animation
  - .stat-card: bg-card, label + large number, monospace
  - .btn-primary: accent-blue fill, hover brighten
  - .btn-primary:disabled: dimmed, no hover
  - .viewer-frame: border with accent glow
  - .log-output: collapsible, monospace, dark bg, max-height with scroll

Animations:
  @keyframes shimmer — linear gradient sliding right on progress bar
  @keyframes pulse-glow — border-color + box-shadow pulse on active stage
  @keyframes pop-in — scale 0→1.2→1 for checkmarks
  @keyframes fade-up — opacity 0→1, translateY 10px→0 for stat entries
  @keyframes spin — 360deg rotation for loading spinner
```

**Step 2: Commit**

```bash
git add frontend/style.css
git commit -m "feat: add dark theme CSS with animations"
```

---

## Task 8: Frontend — index.html

**Files:**
- Create: `frontend/index.html`

**Step 1: Write index.html**

Single-page structure. Key sections:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SplatForge</title>
  <link rel="stylesheet" href="style.css">
  <!-- Google Fonts: Inter + JetBrains Mono -->
</head>
<body>
  <!-- HEADER: logo, preset selector, GPU badge -->
  <header class="header">
    <div class="logo">SPLATFORGE</div>
    <div class="header-controls">
      <select id="preset-select">
        <option value="draft">Draft (~5-10 min)</option>
        <option value="standard" selected>Standard (~20-40 min)</option>
        <option value="high">High Quality (~40-60 min)</option>
        <option value="ultra">Ultra (60+ min)</option>
      </select>
      <div id="gpu-badge" class="gpu-badge">GPU: detecting...</div>
    </div>
  </header>

  <!-- MAIN: two columns -->
  <main class="main-content">
    <!-- LEFT: Video input -->
    <section class="input-panel">
      <div id="drop-zone" class="drop-zone">
        <div class="drop-zone-content">
          <div class="drop-icon">&#x1F4F9;</div>  <!-- or SVG -->
          <p>Drop .mp4 here</p>
          <p class="drop-hint">or click to browse</p>
        </div>
        <input type="file" id="file-input" accept=".mp4,.mov,.avi,.mkv" hidden>
      </div>
      <div id="video-info" class="video-info" hidden>
        <!-- Filled by JS: duration, resolution, frames, size -->
      </div>
      <div class="input-actions">
        <button id="start-btn" class="btn-primary" disabled>START</button>
        <button id="cancel-btn" class="btn-secondary" hidden>CANCEL</button>
      </div>
    </section>

    <!-- RIGHT: Pipeline progress + stats -->
    <section class="progress-panel">
      <div class="overall-progress-container">
        <svg id="overall-progress" class="progress-ring" ...></svg>
      </div>
      <ul id="stage-list" class="stage-list">
        <li class="stage-item" data-stage="analyzing">
          <span class="stage-icon"></span>
          <span class="stage-label">Analyze Video</span>
          <span class="stage-time"></span>
        </li>
        <!-- ... 4 more stages -->
      </ul>
      <div id="stats-panel" class="stats-panel" hidden>
        <!-- Grid of stat cards: iteration, PSNR, loss, gaussians, speed, ETA -->
      </div>
      <canvas id="psnr-chart" class="psnr-chart" hidden></canvas>
    </section>
  </main>

  <!-- VIEWER: full width iframe -->
  <section id="viewer-container" class="viewer-section" hidden>
    <div class="viewer-header">
      <span>LIVE 3D PREVIEW</span>
      <button id="viewer-toggle" class="btn-icon">Hide</button>
    </div>
    <iframe id="viewer-iframe" class="viewer-frame" src=""></iframe>
  </section>

  <!-- DOWNLOADS -->
  <section id="download-section" class="download-section" hidden>
    <a id="download-ply" class="btn-download" href="#">Download .ply</a>
    <a id="download-ksplat" class="btn-download" href="#">Download .ksplat</a>
  </section>

  <!-- LOG OUTPUT (collapsible) -->
  <details class="log-details">
    <summary>Raw Log Output</summary>
    <pre id="log-output" class="log-output"></pre>
  </details>

  <script src="app.js"></script>
</body>
</html>
```

**Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add single-page dashboard HTML"
```

---

## Task 9: Frontend — app.js

**Files:**
- Create: `frontend/app.js`

**Step 1: Write app.js**

This is the largest frontend file. Key sections:

```javascript
// === STATE ===
let currentJobId = null;
let eventSource = null;
let psnrHistory = [];  // [{iteration, psnr}]
let stageStartTimes = {};

// === INITIALIZATION ===
// On DOMContentLoaded:
//   - Fetch /api/gpu-info → update #gpu-badge
//   - Set up drag & drop on #drop-zone
//   - Set up file input change handler
//   - Set up start/cancel button handlers

// === FILE UPLOAD ===
// handleFile(file):
//   - Validate extension
//   - Show uploading state
//   - POST /api/upload with FormData
//   - On success: store job_id, show video-info panel, enable START
//   - On error: show error message

// === PIPELINE CONTROL ===
// startPipeline():
//   - POST /api/start with {job_id, preset}
//   - Connect SSE: new EventSource(`/api/status/stream?job_id=${currentJobId}`)
//   - Disable start, show cancel
//   - Set up eventSource.onmessage handler

// cancelPipeline():
//   - POST /api/cancel/{job_id}
//   - Close eventSource

// === SSE HANDLER ===
// handleSSEMessage(data):
//   - updateStageList(data.stage, data.stage_index)
//   - updateOverallProgress(data.overall_progress)
//   - updateStats(data.stats)
//   - appendLog(data.message)
//   - if data.viewer_ready: showViewer(data.viewer_url)
//   - if data.stage === "complete": showDownloads(data)
//   - if data.stage === "error": showError(data.message)

// === STAGE LIST ===
// updateStageList(currentStage, stageIndex):
//   - Mark completed stages with checkmark + pop-in animation
//   - Mark current stage as active with pulse-glow
//   - Update stage timers

// === STATS PANEL ===
// updateStats(stats):
//   - Show stats panel if hidden
//   - Animate number transitions for: iteration, psnr, loss, num_gaussians, speed, eta
//   - Push to psnrHistory if training stage
//   - Redraw PSNR chart

// === PSNR CHART ===
// drawPSNRChart():
//   - Canvas 2D context
//   - Grid lines at 5 dB increments (15, 20, 25, 30, 35)
//   - X axis: iteration, Y axis: PSNR (dB)
//   - Smooth bezier line through psnrHistory points
//   - Current value tooltip at latest point
//   - Colors: line=accent-blue, grid=border, bg=bg-card

// === VIEWER ===
// showViewer(url):
//   - Set iframe src to url
//   - Show viewer-container
//   - Toggle button shows/hides

// === DOWNLOADS ===
// showDownloads():
//   - Set download hrefs to /api/download/{job_id}/splat.ply and terrain.ksplat
//   - Show download-section

// === PROGRESS RING ===
// updateOverallProgress(fraction):
//   - SVG circle stroke-dashoffset animation

// === HELPERS ===
// formatNumber(n) → "1,234,567"
// formatTime(seconds) → "12:34" or "1:02:34"
// formatBytes(bytes) → "1.2 GB"
// animateNumber(element, from, to, duration) → rolling counter
```

**Step 2: Verify the full stack runs in mock mode**

```bash
cd C:/Users/amtrt/Documents/JDD/SplatForge
SPLATFORGE_MOCK=1 python backend/main.py
# Open http://localhost:8080 in browser
# Drop any .mp4 file, click START
# Watch mock pipeline run through all stages
# Verify: stages animate, stats update, chart draws, downloads appear
```

**Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "feat: add frontend JS with SSE, upload, chart, and viewer"
```

---

## Task 10: Integration Smoke Test & Polish

**Files:**
- Modify: any files that need fixes from smoke testing

**Step 1: Run full mock pipeline end-to-end**

```bash
SPLATFORGE_MOCK=1 python backend/main.py
```

Test checklist:
- [ ] Page loads with dark theme, all elements visible
- [ ] GPU badge shows (mock or real)
- [ ] Drag & drop accepts .mp4
- [ ] File info displays after upload
- [ ] START button enables after upload
- [ ] All 5 stages animate through
- [ ] Training stats update in real time
- [ ] PSNR chart draws and updates
- [ ] Progress bar and ring animate
- [ ] Download buttons appear on completion
- [ ] Cancel button stops pipeline
- [ ] Error states display correctly
- [ ] Log output shows raw messages

**Step 2: Fix any issues found**

**Step 3: Final commit**

```bash
git add -A
git commit -m "fix: polish UI and resolve integration issues"
```

---

## Build Order Summary

```
Task 1: Scaffolding          (5 min)
Task 2: config.py            (3 min)
Task 3: log_parser.py + test (10 min)
Task 4: pipeline.py          (15 min)
Task 5: ksplat_converter.py  (3 min)
Task 6: main.py              (15 min)
Task 7: style.css            (15 min)
Task 8: index.html           (10 min)
Task 9: app.js               (20 min)
Task 10: Integration + polish (15 min)
```

Dependencies: Task 1 first → Tasks 2-5 (backend, in order) → Task 6 (needs 2-5) → Tasks 7-8 (parallel, no deps on backend) → Task 9 (needs 8 for IDs) → Task 10 (needs all).
