"""
SplatForge -- Log parser for nerfstudio training and COLMAP output.

Parses real-time stdout lines to extract progress metrics such as
iteration count, loss, PSNR, gaussian count, registered images, etc.

Design contract:
  - If a line doesn't match any known pattern, return None silently.
  - Never raise exceptions from parsing -- malformed input is expected.
"""

from __future__ import annotations

import re

# -- ANSI escape code removal --------------------------------------------------

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from *text*."""
    return ANSI_PATTERN.sub("", text)


# -- Training line parser (ns-train output) ------------------------------------

_ITER_RE = re.compile(
    r"(?:Step|Iter(?:ation)?)\s*[:=]?\s*(\d[\d,]*)", re.IGNORECASE
)
_LOSS_RE = re.compile(
    r"loss\s*[:=]\s*([\d.]+(?:e[+-]?\d+)?)", re.IGNORECASE
)
_PSNR_RE = re.compile(
    r"psnr\s*[:=]\s*([\d.]+)", re.IGNORECASE
)
_GAUSS_RE = re.compile(
    r"(?:num[_\s]?gaussians?|splats?|num_points)\s*[:=]\s*([\d,]+)",
    re.IGNORECASE,
)


def parse_training_line(line: str) -> dict | None:
    """Parse a single line of nerfstudio training output.

    Returns a dict with any recognised fields, or ``None`` if nothing matched.
    Possible keys: ``iteration``, ``loss``, ``psnr``, ``num_gaussians``.
    """
    try:
        line = strip_ansi(line)
        result: dict = {}

        m = _ITER_RE.search(line)
        if m:
            result["iteration"] = int(m.group(1).replace(",", ""))

        m = _LOSS_RE.search(line)
        if m:
            result["loss"] = float(m.group(1))

        m = _PSNR_RE.search(line)
        if m:
            result["psnr"] = float(m.group(1))

        m = _GAUSS_RE.search(line)
        if m:
            val = int(m.group(1).replace(",", ""))
            if val > 1000:
                result["num_gaussians"] = val

        return result if result else None
    except Exception:
        return None


# -- COLMAP line parser (ns-process-data / colmap output) ----------------------

_REG_RE = re.compile(r"[Rr]egistered\s+(\d+)")
_PTS_RE = re.compile(r"num_points3D\s*=\s*(\d+)")
_ERR_RE = re.compile(r"mean_reprojection_error\s*=\s*([\d.]+)")


def parse_colmap_line(line: str) -> dict | None:
    """Parse a single line of COLMAP or ns-process-data output.

    Returns a dict with any recognised fields, or ``None`` if nothing matched.
    Possible keys: ``registered_images``, ``num_points3d``, ``reprojection_error``.
    """
    try:
        line = strip_ansi(line)
        result: dict = {}

        m = _REG_RE.search(line)
        if m:
            result["registered_images"] = int(m.group(1))

        m = _PTS_RE.search(line)
        if m:
            result["num_points3d"] = int(m.group(1))

        m = _ERR_RE.search(line)
        if m:
            result["reprojection_error"] = float(m.group(1))

        return result if result else None
    except Exception:
        return None
