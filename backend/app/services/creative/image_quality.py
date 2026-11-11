"""Image quality assessment for generated images.

Provides lightweight, dependency-free (numpy + PIL only) metrics that help
determine whether a generated image is genuinely useful or just abstract
noise / a flat color.  These are used:

* In :func:`app.services.creative.providers.validate_image_output` to gate
  AI-generation outputs before they are returned to the user.
* By tests to assert that generated images contain real visual content.

Metrics
-------
* **color_variance** — mean per-channel standard deviation. A solid-color
  image scores ~0; a real photograph or painting scores >8.
* **entropy** — Shannon entropy of the luminance histogram (0–8). Flat
  gradients score <1; images with texture score >3.
* **distinct_colors** — number of unique RGB tuples (sampled for speed).
  Solid fills have 1; real images have hundreds to thousands.
* **edge_density** — fraction of pixels where the luminance gradient
  magnitude exceeds a threshold. Detects "blank" outputs.
* **quality_score** — weighted composite (0–100). ``passes_qa`` is True
  when all individual metrics exceed their respective thresholds.
"""
from __future__ import annotations

import io
import math

import numpy as np


# ---------------------------------------------------------------------------
#  Thresholds
# ---------------------------------------------------------------------------

_MIN_COLOR_VARIANCE = 5.0      # std-dev per channel mean
_MIN_ENTROPY = 3.0             # Shannon entropy of luminance histogram
_MIN_EDGE_DENSITY = 0.02       # 2 % of pixels must be edges
_MIN_DISTINCT_COLORS = 10      # at least 10 unique colours


def assess_image_quality(data: bytes) -> dict:
    """Assess the visual quality of an image's raw bytes.

    Args:
        data: Raw image bytes (PNG, JPEG, etc.).

    Returns:
        dict with keys: ``quality_score``, ``color_variance``,
        ``entropy``, ``distinct_colors``, ``edge_density``,
        ``width``, ``height``, ``passes_qa``.

        Returns a dict with ``passes_qa=False`` and all metrics set to 0
        if the bytes cannot be decoded as an image.
    """
    from PIL import Image

    fallback = {
        "quality_score": 0.0,
        "color_variance": 0.0,
        "entropy": 0.0,
        "distinct_colors": 0,
        "edge_density": 0.0,
        "width": 0,
        "height": 0,
        "passes_qa": False,
    }

    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
    except Exception:
        return fallback

    arr = np.asarray(img, dtype=np.float64)
    h, w = arr.shape[:2]

    # --- color variance (mean per-channel std-dev) ---------------------------
    channel_std = arr.std(axis=(0, 1))  # shape (3,)
    color_variance = float(channel_std.mean())

    # --- entropy (luminance histogram) ---------------------------------------
    luminance = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    hist, _ = np.histogram(luminance, bins=64, range=(0, 256))
    total = hist.sum()
    if total > 0:
        probs = hist / total
        probs = probs[probs > 0]
        entropy = float(-np.sum(probs * np.log2(probs)))
    else:
        entropy = 0.0

    # --- distinct colors (sample for speed on large images) ------------------
    max_sample = 65536  # 256×256
    if arr.shape[0] * arr.shape[1] > max_sample:
        stride = int(math.sqrt(arr.shape[0] * arr.shape[1] / max_sample))
        sampled = arr[::stride, ::stride]
    else:
        sampled = arr
    rgb_ints = (sampled[:, :, 0].astype(np.int32) << 16
                | sampled[:, :, 1].astype(np.int32) << 8
                | sampled[:, :, 2].astype(np.int32))
    distinct_colors = int(len(np.unique(rgb_ints)))

    # --- edge density --------------------------------------------------------
    gray = luminance
    # Sobel-like gradient via numpy.gradient
    gy, gx = np.gradient(gray)
    grad_mag = np.sqrt(gx ** 2 + gy ** 2)
    edge_pixels = np.sum(grad_mag > 8.0)
    total_pixels = gray.size
    edge_density = float(edge_pixels / total_pixels) if total_pixels > 0 else 0.0

    # --- composite quality score (0–100) -------------------------------------
    cv_score = min(1.0, color_variance / 20.0)            # 20 → full marks
    ent_score = min(1.0, entropy / 7.0)                     # 7 → full marks
    edge_score = min(1.0, edge_density / 0.15)              # 15 % → full marks
    color_score = min(1.0, distinct_colors / 500.0)       # 500 → full marks
    quality_score = round(
        (cv_score * 0.30 + ent_score * 0.25 + edge_score * 0.25 + color_score * 0.20) * 100, 2,
    )

    passes_qa = (
        color_variance >= _MIN_COLOR_VARIANCE
        and entropy >= _MIN_ENTROPY
        and edge_density >= _MIN_EDGE_DENSITY
        and distinct_colors >= _MIN_DISTINCT_COLORS
    )

    return {
        "quality_score": quality_score,
        "color_variance": round(color_variance, 4),
        "entropy": round(entropy, 4),
        "distinct_colors": distinct_colors,
        "edge_density": round(edge_density, 4),
        "width": w,
        "height": h,
        "passes_qa": passes_qa,
    }


__all__ = ["assess_image_quality"]
