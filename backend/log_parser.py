"""
SplatForge — Log parser for nerfstudio training and COLMAP output.

Parses real-time stdout lines to extract progress metrics such as
iteration count, loss, PSNR, gaussian count, registered images, etc.

Design contract:
  - If a line doesn't match any known pattern, return None silently.
  - Never raise exceptions from parsing — malformed input is expected.
"""

from __future__ import annotations

import re

# ── ANSI escape code removal ────────────────────────────────────────────────

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return ANSI_PATTERN.sub("", text)


# ── Training line parser (ns-train output) ───────────────────────────────────

def parse_training_line(line: str) -> dict | None:
    """
    Parse a single line of nerfstudio training output.

    Returns a dict with any recognized fields, or None if nothing matched.
    Possible keys: iteration, loss, psnr, num_gaussians.
    """
    line = strip_ansi(line)
    result: dict = {}

    # Try to extract iteration (Step 1000/30000, Iter 5000, Iteration 250)
    iter_match = re.search(
        r"(?:Step|Iter(?:ation)?)\s*[:=]?\s*(\d[\d,]*)", line, re.IGNORECASE
    )
    if iter_match:
        result["iteration"] = int(iter_match.group(1).replace(",", ""))

    # Try to extract loss (supports scientific notation: 3.5e-03, 1.2e+01)
    loss_match = re.search(
        r"loss\s*[:=]\s*([\d.]+(?:e[+-]?\d+)?)", line, re.IGNORECASE
    )
    if loss_match:
        result["loss"] = float(loss_match.group(1))

    # Try to extract PSNR
    psnr_match = re.search(r"psnr\s*[:=]\s*([\d.]+)", line, re.IGNORECASE)
    if psnr_match:
        result["psnr"] = float(psnr_match.group(1))

    # Try to extract gaussian count
    gauss_match = re.search(
        r"(?:num[_\s]?gaussians?|splats?|num_points)\s*[:=]\s*([\d,]+)",
        line,
        re.IGNORECASE,
    )
    if gauss_match:
        val = gauss_match.group(1).replace(",", "")
        if val.isdigit() and int(val) > 1000:
            result["num_gaussians"] = int(val)

    return result if result else None


# ── COLMAP line parser (ns-process-data / colmap output) ─────────────────────

def parse_colmap_line(line: str) -> dict | None:
    """
    Parse a single line of COLMAP or ns-process-data output.

    Returns a dict with any recognized fields, or None if nothing matched.
    Possible keys: registered_images, num_points3d, reprojection_error.
    """
    line = strip_ansi(line)
    result: dict = {}

    # Registered images: "Registered 287 / 300 images" or "Registered 287 images"
    reg_match = re.search(r"[Rr]egistered\s+(\d+)", line)
    if reg_match:
        result["registered_images"] = int(reg_match.group(1))

    # 3D point count: "num_points3D = 45231"
    pts_match = re.search(r"num_points3D\s*=\s*(\d+)", line)
    if pts_match:
        result["num_points3d"] = int(pts_match.group(1))

    # Reprojection error: "mean_reprojection_error = 0.8432"
    err_match = re.search(r"mean_reprojection_error\s*=\s*([\d.]+)", line)
    if err_match:
        result["reprojection_error"] = float(err_match.group(1))

    return result if result else None
