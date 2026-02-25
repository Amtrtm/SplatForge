"""
SplatForge -- PLY to ksplat converter.

Wraps a Node.js script (create-ksplat.js) that converts Gaussian Splat
PLY files into the compressed ksplat format used by the web viewer.

If Node.js is not installed, conversion is silently skipped.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

# Relative path to the bundled Node.js converter script.
_CONVERTER_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "create-ksplat.js"


async def convert_ply_to_ksplat(
    ply_path: str | Path,
    output_path: str | Path,
) -> bool:
    """Convert a Gaussian Splat PLY file to ksplat format.

    Uses a Node.js helper script.  Returns *True* on success, *False* if
    the converter is unavailable or the conversion fails for any reason.
    """
    try:
        if not is_converter_available():
            return False

        ply_path = Path(ply_path)
        output_path = Path(output_path)

        proc = await asyncio.create_subprocess_exec(
            "node",
            str(_CONVERTER_SCRIPT),
            str(ply_path),
            str(output_path),
            "1",   # compression level
            "10",  # block size
            "1",   # SH degree
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False


def is_converter_available() -> bool:
    """Return *True* if Node.js is on the PATH."""
    return shutil.which("node") is not None
