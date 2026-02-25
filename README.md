# SplatForge

Local web application that takes a drone MP4 video and fully automates the creation of a Gaussian Splat file (`.ply` / `.ksplat`). Drop in a video, watch a live dashboard track every stage, optionally watch the 3D model build in real-time, and download the final file.

![Stack](https://img.shields.io/badge/Python-3.10+-blue) ![Stack](https://img.shields.io/badge/FastAPI-0.104+-green) ![Stack](https://img.shields.io/badge/Nerfstudio-splatfacto-orange)

## Features

- **Drag & drop** video upload (MP4, MOV, AVI, MKV)
- **5-stage automated pipeline**: video analysis, COLMAP processing, Gaussian Splat training, PLY export, ksplat conversion
- **Live dashboard** with real-time stats via Server-Sent Events (SSE)
- **PSNR chart** plotting quality over training iterations
- **Live 3D viewer** embedded from nerfstudio (port 7007) during training
- **4 quality presets**: Draft, Standard, High, Ultra
- **Mock mode** for UI development without GPU/nerfstudio
- **Cancel** running pipelines at any time
- Dark military-tech themed UI

## Prerequisites

| Requirement | Check command |
|---|---|
| NVIDIA GPU (8GB+ VRAM, RTX 3060+) | `nvidia-smi` |
| CUDA 11.8 or 12.x | `nvcc --version` |
| Python 3.10+ | `python --version` |
| FFmpeg | `ffmpeg -version` |
| ~20 GB free disk space per job | |

## Quick Start

### 1. Install dependencies

**Option A: Using the setup script (recommended)**

```bash
bash setup.sh
```

This creates a conda environment, installs nerfstudio + FastAPI dependencies, and verifies the installation.

**Option B: Manual install**

```bash
# Install nerfstudio (includes COLMAP, gsplat, PyTorch)
pip install nerfstudio

# Install SplatForge dependencies
pip install -r requirements.txt
```

### 2. Run the server

```bash
python -m backend.main
```

### 3. Open the dashboard

Navigate to **http://localhost:8080** in your browser.

### 4. Process a video

1. Drag & drop a drone video onto the upload zone
2. Select a quality preset (Standard is a good default)
3. Click **START**
4. Watch the pipeline progress through all 5 stages
5. Download the `.ply` file when complete

## Mock Mode

For testing the UI without a GPU or nerfstudio installed:

```bash
SPLATFORGE_MOCK=1 python -m backend.main
```

On Windows (PowerShell):

```powershell
$env:SPLATFORGE_MOCK="1"; python -m backend.main
```

Mock mode simulates all 5 pipeline stages with realistic training stats (PSNR climbing from 18 to 30 dB, loss decaying exponentially) in about 45 seconds.

## Quality Presets

| Preset | Frames | Iterations | Approx. Time |
|--------|--------|------------|--------------|
| Draft | 150 | 7,000 | 5-10 min |
| Standard | 300 | 30,000 | 20-40 min |
| High | 500 | 50,000 | 40-60 min |
| Ultra | 800 | 100,000 | 60+ min |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload` | Upload a video file |
| POST | `/api/start` | Start the pipeline |
| GET | `/api/status/stream` | SSE stream of pipeline progress |
| GET | `/api/status/{job_id}` | Polling fallback for current status |
| GET | `/api/download/{job_id}/{filename}` | Download exported files |
| POST | `/api/cancel/{job_id}` | Cancel a running pipeline |
| GET | `/api/gpu-info` | GPU information |
| GET | `/api/presets` | Available quality presets |

## Project Structure

```
SplatForge/
├── backend/
│   ├── main.py              # FastAPI app, routes, SSE
│   ├── pipeline.py          # 5-stage async pipeline orchestrator
│   ├── log_parser.py        # Regex parser for nerfstudio/COLMAP output
│   ├── config.py            # Quality presets, paths, constants
│   └── ksplat_converter.py  # Optional PLY-to-ksplat conversion
├── frontend/
│   ├── index.html           # Single-page dashboard
│   ├── style.css            # Dark theme with animations
│   └── app.js               # SSE, upload, chart, viewer logic
├── jobs/                    # Pipeline working directories (runtime)
├── tests/
│   └── test_log_parser.py   # 29 unit tests for log parsing
├── requirements.txt
└── setup.sh
```

## Pipeline Stages

1. **Analyze Video** — `ffprobe` extracts duration, resolution, FPS, codec
2. **COLMAP Processing** — `ns-process-data video` extracts frames and runs structure-from-motion
3. **Train Gaussian Splat** — `ns-train splatfacto` trains the 3D model (live viewer on port 7007)
4. **Export PLY** — `ns-export gaussian-splat` exports the trained model
5. **Convert ksplat** — Optional conversion to `.ksplat` format

## Troubleshooting

**COLMAP fails / low registration count**
Your video may have too much motion blur, insufficient frame overlap, or repetitive textures. Try re-recording with slower drone movement.

**GPU out of memory**
Try the Draft preset or reduce video resolution before uploading.

**nerfstudio not found**
Run `bash setup.sh` or `pip install nerfstudio` to install.

**Port 8080 already in use**
Change `SERVER_PORT` in `backend/config.py` or kill the existing process.

## License

MIT
