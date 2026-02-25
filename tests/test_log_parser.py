"""
Tests for backend.log_parser — TDD: written before implementation.

Covers strip_ansi(), parse_training_line(), and parse_colmap_line().
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.log_parser import strip_ansi, parse_training_line, parse_colmap_line


# ── strip_ansi ───────────────────────────────────────────────────────────────


class TestStripAnsi:
    def test_removes_ansi_codes(self):
        assert strip_ansi("\x1b[32mHello\x1b[0m World") == "Hello World"

    def test_no_ansi_passthrough(self):
        assert strip_ansi("plain text") == "plain text"

    def test_empty_string(self):
        assert strip_ansi("") == ""

    def test_multiple_ansi_codes(self):
        assert strip_ansi("\x1b[1;31mERROR\x1b[0m: \x1b[33mwarning\x1b[0m") == "ERROR: warning"


# ── parse_training_line: iteration ───────────────────────────────────────────


class TestParseTrainingIteration:
    def test_step_format(self):
        result = parse_training_line("Step 1000/30000 (3.3%): loss=0.0234 psnr=21.2")
        assert result is not None
        assert result["iteration"] == 1000

    def test_iter_format(self):
        result = parse_training_line("Iter 5000")
        assert result is not None
        assert result["iteration"] == 5000

    def test_iteration_format(self):
        result = parse_training_line("Iteration 250")
        assert result is not None
        assert result["iteration"] == 250


# ── parse_training_line: loss ────────────────────────────────────────────────


class TestParseTrainingLoss:
    def test_loss_equals(self):
        result = parse_training_line("loss=0.0234")
        assert result is not None
        assert abs(result["loss"] - 0.0234) < 1e-6

    def test_loss_colon(self):
        result = parse_training_line("loss: 0.0042")
        assert result is not None
        assert abs(result["loss"] - 0.0042) < 1e-6

    def test_loss_scientific_notation(self):
        result = parse_training_line("loss=3.5e-03")
        assert result is not None
        assert abs(result["loss"] - 0.0035) < 1e-6

    def test_loss_scientific_notation_positive_exp(self):
        result = parse_training_line("loss=1.2e+01")
        assert result is not None
        assert abs(result["loss"] - 12.0) < 1e-6


# ── parse_training_line: psnr ────────────────────────────────────────────────


class TestParseTrainingPsnr:
    def test_psnr_equals(self):
        result = parse_training_line("psnr=21.2")
        assert result is not None
        assert abs(result["psnr"] - 21.2) < 1e-6

    def test_psnr_colon_uppercase(self):
        result = parse_training_line("PSNR: 27.3")
        assert result is not None
        assert abs(result["psnr"] - 27.3) < 1e-6


# ── parse_training_line: combined ────────────────────────────────────────────


class TestParseTrainingCombined:
    def test_full_line(self):
        line = "Step 12450/30000 loss=0.0042 psnr=27.3 num_gaussians=1200000"
        result = parse_training_line(line)
        assert result is not None
        assert result["iteration"] == 12450
        assert abs(result["loss"] - 0.0042) < 1e-6
        assert abs(result["psnr"] - 27.3) < 1e-6
        assert result["num_gaussians"] == 1200000


# ── parse_training_line: gaussians ───────────────────────────────────────────


class TestParseTrainingGaussians:
    def test_num_gaussians_with_commas(self):
        result = parse_training_line("num_gaussians: 1,200,000")
        assert result is not None
        assert result["num_gaussians"] == 1200000

    def test_splats_keyword(self):
        result = parse_training_line("splats: 500000")
        assert result is not None
        assert result["num_gaussians"] == 500000

    def test_small_count_ignored(self):
        """Gaussian counts <= 1000 are likely not gaussian counts."""
        result = parse_training_line("num_gaussians: 500")
        # Should be None because 500 is below the threshold
        assert result is None


# ── parse_training_line: junk / unrecognized lines ───────────────────────────


class TestParseTrainingJunk:
    def test_loading_checkpoint(self):
        assert parse_training_line("Loading checkpoint...") is None

    def test_empty_string(self):
        assert parse_training_line("") is None

    def test_random_log_line(self):
        assert parse_training_line("Some random log line") is None

    def test_whitespace_only(self):
        assert parse_training_line("   \t\n  ") is None


# ── parse_training_line: ANSI codes ──────────────────────────────────────────


class TestParseTrainingWithAnsi:
    def test_ansi_codes_stripped_before_parsing(self):
        line = "\x1b[32mStep 1000/30000\x1b[0m loss=\x1b[33m0.0234\x1b[0m"
        result = parse_training_line(line)
        assert result is not None
        assert result["iteration"] == 1000
        assert abs(result["loss"] - 0.0234) < 1e-6


# ── parse_colmap_line: registered images ─────────────────────────────────────


class TestParseColmapRegistered:
    def test_registered_with_total(self):
        result = parse_colmap_line("Registered 287 / 300 images")
        assert result is not None
        assert result["registered_images"] == 287

    def test_registered_without_total(self):
        result = parse_colmap_line("Registered 287 images")
        assert result is not None
        assert result["registered_images"] == 287


# ── parse_colmap_line: points ────────────────────────────────────────────────


class TestParseColmapPoints:
    def test_num_points3d(self):
        result = parse_colmap_line("num_points3D = 45231")
        assert result is not None
        assert result["num_points3d"] == 45231


# ── parse_colmap_line: reprojection error ────────────────────────────────────


class TestParseColmapReprojectionError:
    def test_mean_reprojection_error(self):
        result = parse_colmap_line("mean_reprojection_error = 0.8432")
        assert result is not None
        assert abs(result["reprojection_error"] - 0.8432) < 1e-6


# ── parse_colmap_line: junk ──────────────────────────────────────────────────


class TestParseColmapJunk:
    def test_bundle_adjustment(self):
        result = parse_colmap_line("Running bundle adjustment...")
        assert result is None or result == {}

    def test_empty_string(self):
        result = parse_colmap_line("")
        assert result is None

    def test_random_line(self):
        result = parse_colmap_line("Some unrelated COLMAP output")
        assert result is None


# ── parse_colmap_line: with ANSI codes ───────────────────────────────────────


class TestParseColmapWithAnsi:
    def test_ansi_codes_stripped_before_parsing(self):
        line = "\x1b[36mRegistered 150 / 200 images\x1b[0m"
        result = parse_colmap_line(line)
        assert result is not None
        assert result["registered_images"] == 150
