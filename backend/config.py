"""
SplatForge — Central configuration.

Quality presets, directory paths, and application constants.
"""

from pathlib import Path

# ── Directory paths ───────────────────────────────────────────────────────────

BASE_DIR: Path = Path(__file__).resolve().parent.parent
JOBS_DIR: Path = BASE_DIR / "jobs"
FRONTEND_DIR: Path = BASE_DIR / "frontend"

# ── Quality presets ───────────────────────────────────────────────────────────

PRESETS: dict = {
    "draft": {
        "num_frames": 150,
        "max_iterations": 7_000,
        "cull_alpha_thresh": 0.01,
        "description": "Quick preview — ~5-10 min total",
    },
    "standard": {
        "num_frames": 300,
        "max_iterations": 30_000,
        "cull_alpha_thresh": 0.005,
        "description": "Good quality — ~20-40 min total",
    },
    "high": {
        "num_frames": 500,
        "max_iterations": 50_000,
        "cull_alpha_thresh": 0.005,
        "description": "High quality — ~40-60 min total",
    },
    "ultra": {
        "num_frames": 800,
        "max_iterations": 100_000,
        "cull_alpha_thresh": 0.003,
        "description": "Maximum quality — 60+ min",
    },
}

DEFAULT_PRESET: str = "standard"

# ── Upload / validation constants ─────────────────────────────────────────────

MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024 * 1024  # 10 GB
MIN_VIDEO_DURATION: float = 5.0
ALLOWED_EXTENSIONS: set[str] = {".mp4", ".mov", ".avi", ".mkv"}

# ── Network ports ─────────────────────────────────────────────────────────────

VIEWER_PORT: int = 7007
SERVER_PORT: int = 8080
