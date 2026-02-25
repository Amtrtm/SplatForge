# SplatForge Design Document

## Overview
Local web app: drop in a drone MP4 video, automate Gaussian Splat creation, watch live dashboard, download `.ply`/`.ksplat`.

## Stack
- **Backend:** Python 3.10+ / FastAPI on port 8080
- **Frontend:** Vanilla HTML/CSS/JS (no framework, no build step)
- **Processing:** Nerfstudio (splatfacto), COLMAP, FFmpeg
- **Communication:** SSE (Server-Sent Events) for live stats, REST for upload/download

## Architecture Decisions

### Mock mode
Baked into `PipelineOrchestrator` via a `mock: bool` parameter. When true, subprocess calls are replaced with `asyncio.sleep` + synthetic stat generation. The SSE broadcast path is identical — the frontend is unaware of the difference. Activated via `SPLATFORGE_MOCK=1` env var or `?mock=true` URL param.

### Job state
In-memory `dict[str, JobState]` keyed by job_id. No database — single-user local tool. Job artifacts persist on disk under `jobs/{uuid}/`.

### SSE implementation
One `asyncio.Queue` per connected SSE client. Pipeline pushes events to all queues. SSE endpoint yields from its queue. Clean producer/consumer decoupling.

### PSNR chart
Canvas-based line chart with bezier smoothing in `app.js`. No chart library.

### Nerfstudio viewer
Embedded as `<iframe src="http://localhost:7007">` during training stage only. No COOP/COEP headers on FastAPI server.

## File Structure
```
splatforge/
├── backend/
│   ├── main.py
│   ├── pipeline.py
│   ├── log_parser.py
│   ├── config.py
│   └── ksplat_converter.py
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── jobs/              (runtime)
├── requirements.txt
└── setup.sh
```

## Pipeline Stages
1. **Video Analysis** — `ffprobe` — <1s
2. **Frame Extraction + COLMAP** — `ns-process-data video` — 5-30 min
3. **Gaussian Splat Training** — `ns-train splatfacto` — 10-40 min
4. **Export to PLY** — `ns-export gaussian-splat` — 10-60s
5. **Convert to ksplat** — Node.js converter (optional) — 5-30s
