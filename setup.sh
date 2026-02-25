#!/usr/bin/env bash
set -e

# ╔══════════════════════════════════════════╗
# ║       SPLATFORGE — Setup Script          ║
# ╚══════════════════════════════════════════╝

ENV_NAME="splatforge"
PYTHON_VERSION="3.10"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       SPLATFORGE — Setup Script          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Step 1: Create conda environment ─────────────────────────────────────────
echo "[1/4] Creating conda environment '${ENV_NAME}' with Python ${PYTHON_VERSION}..."
if conda info --envs | grep -q "^${ENV_NAME} "; then
    echo "       Environment '${ENV_NAME}' already exists. Activating..."
else
    conda create -n "${ENV_NAME}" python="${PYTHON_VERSION}" -y
fi

# Activate the environment
eval "$(conda shell.bash hook)"
conda activate "${ENV_NAME}"
echo "       Active Python: $(python --version)"

# ── Step 2: Install nerfstudio ────────────────────────────────────────────────
echo ""
echo "[2/4] Installing nerfstudio via pip..."
pip install nerfstudio
echo "       nerfstudio installed."

# ── Step 3: Install FastAPI dependencies ──────────────────────────────────────
echo ""
echo "[3/4] Installing FastAPI dependencies from requirements.txt..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pip install -r "${SCRIPT_DIR}/requirements.txt"
echo "       FastAPI dependencies installed."

# ── Step 4: Verify installations ──────────────────────────────────────────────
echo ""
echo "[4/4] Verifying installations..."

echo -n "       nerfstudio: "
if ns-train --help > /dev/null 2>&1; then
    echo "OK"
else
    echo "WARNING — 'ns-train' command not found. You may need to restart your shell."
fi

echo -n "       COLMAP:     "
if colmap -h > /dev/null 2>&1; then
    echo "OK"
else
    echo "WARNING — 'colmap' not found. Install COLMAP separately:"
    echo "         https://colmap.github.io/install.html"
fi

echo -n "       FastAPI:    "
if python -c "import fastapi; print(f'OK (v{fastapi.__version__})')" 2>/dev/null; then
    :
else
    echo "WARNING — FastAPI import failed."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          Setup Complete!                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "To get started:"
echo "  conda activate ${ENV_NAME}"
echo "  python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload"
echo ""
