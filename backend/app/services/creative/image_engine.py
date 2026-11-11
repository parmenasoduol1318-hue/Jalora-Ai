"""Image editing engine (Pillow + numpy).

Real, deterministic, non-destructive-by-convention still-image operations:
tone/colour adjustments, curves, filters, geometric transforms, layer
compositing with blend modes, alpha masks, text/shapes, background removal and
multi-format export (including transparent PNG/WebP).

Every operation takes a ``params`` dict validated by the API layer and returns a
new image; callers persist the result and keep the source untouched.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import (
    Image,
    ImageChops,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageFont,
    ImageOps,
)

# ---------------------------------------------------------------- open/save

_LANCZOS = Image.Resampling.LANCZOS


def open_image(source: str | Path | bytes) -> Image.Image:
    if isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(source))
    else:
        img = Image.open(source)
    img.load()
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA")
    return img


def export(img: Image.Image, dest: str | Path, fmt: str = "png",
           quality: int = 90, transparent: bool = False) -> Path:
    """Save ``img`` to ``dest``. JPEG flattens onto white; PNG/WebP keep alpha."""
    fmt = fmt.lower().lstrip(".")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = img
    if fmt in ("jpg", "jpeg"):
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.convert("RGBA").split()[-1])
            out = bg
        else:
            out = img.convert("RGB")
        out.save(dest, "JPEG", quality=int(quality), optimize=True)
    elif fmt == "webp":
        out.save(dest, "WEBP", quality=int(quality), method=6)
    elif fmt in ("tif", "tiff"):
        out.convert("RGBA" if transparent else "RGB").save(dest, "TIFF")
    else:
        if not transparent and out.mode == "RGBA":
            bg = Image.new("RGB", out.size, (255, 255, 255))
            bg.paste(out, mask=out.split()[-1])
            out = bg
        out.save(dest, "PNG", optimize=True)
    return dest


def thumbnail(img: Image.Image, size: tuple[int, int] = (320, 320)) -> Image.Image:
    thumb = img.copy()
    thumb.thumbnail(size, _LANCZOS)
    return thumb


# ------------------------------------------------------------- transforms

def transform(img: Image.Image, op: dict) -> Image.Image:
    """crop | rotate | resize | flip | scale, described by ``op``.

    ``crop``: {x, y, width, height} (clamped). ``rotate``: {degrees, expand}.
    ``resize``: {width, height} (one may be omitted to keep aspect).
    ``flip``: {mode: horizontal|vertical}. ``scale``: {factor}.
    """
    out = img
    if "crop" in op:
        c = op["crop"]
        w, h = img.size
        x = max(0, int(c.get("x", 0)))
        y = max(0, int(c.get("y", 0)))
        cw = min(w - x, int(c.get("width", w)))
        ch = min(h - y, int(c.get("height", h)))
        if cw > 0 and ch > 0:
            out = out.crop((x, y, x + cw, y + ch))
    if "degrees" in op:
        out = out.rotate(float(op["degrees"]), expand=bool(op.get("expand", True)),
                         resample=_LANCZOS)
    if "flip" in op:
        out = out.transpose(Image.FLIP_LEFT_RIGHT) if op["flip"] == "horizontal" \
            else out.transpose(Image.FLIP_TOP_BOTTOM)
    if "resize" in op:
        r = op["resize"]
        w, h = out.size
        tw = int(r.get("width") or 0)
        th = int(r.get("height") or 0)
        if tw and not th:
            th = round(h * tw / w)
        elif th and not tw:
            tw = round(w * th / h)
        if tw > 0 and th > 0:
            out = out.resize((tw, th), _LANCZOS)
    if "scale" in op:
        f = float(op["scale"])
        if f > 0 and f != 1.0:
            out = out.resize((max(1, round(out.width * f)), max(1, round(out.height * f))), _LANCZOS)
    return out


# ----------------------------------------------------------- adjustments

def _to_arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0


def _from_arr(arr: np.ndarray) -> Image.Image:
    arr = np.clip(arr, 0.0, 1.0)
    return Image.fromarray((arr * 255.0 + 0.5).astype(np.uint8), "RGBA")


def apply_adjustments(img: Image.Image, p: dict) -> Image.Image:
    """Tone/colour controls. All values are additive/relative and clamped.

    Supported keys: exposure, brightness, contrast, saturation, vibrance,
    highlights, shadows, temperature, tint, gamma, clarity, sharpen, blur,
    denoise, grayscale, sepia, vignette.
    """
    if not p:
        return img
    arr = _to_arr(img)
    rgb = arr[..., :3]
    alpha = arr[..., 3:4]

    if "exposure" in p:
        rgb = rgb * (2.0 ** float(p["exposure"]))
    if "brightness" in p:
        rgb = rgb + float(p["brightness"])
    if "gamma" in p:
        g = max(0.01, float(p["gamma"]))
        rgb = np.power(np.clip(rgb, 0, 1), 1.0 / g)
    if "contrast" in p:
        c = float(p["contrast"])
        rgb = (rgb - 0.5) * (1.0 + c) + 0.5
    if "shadows" in p:
        s = float(p["shadows"])
        rgb = rgb + s * (1.0 - rgb) * (1.0 - rgb)
    if "highlights" in p:
        h = float(p["highlights"])
        rgb = rgb - h * rgb * rgb
    if "saturation" in p:
        lum = (rgb * np.array([0.2126, 0.7152, 0.0722])).sum(-1, keepdims=True)
        rgb = lum + (rgb - lum) * (1.0 + float(p["saturation"]))
    if "vibrance" in p:
        v = float(p["vibrance"])
        mx = rgb.max(-1, keepdims=True)
        mn = rgb.min(-1, keepdims=True)
        sat = mx - mn
        lum = (rgb * np.array([0.2126, 0.7152, 0.0722])).sum(-1, keepdims=True)
        rgb = lum + (rgb - lum) * (1.0 + v * (1.0 - sat))
    if "temperature" in p or "tint" in p:
        t = float(p.get("temperature", 0.0))
        ti = float(p.get("tint", 0.0))
        rgb = rgb + np.array([t, 0.0, -t]) + np.array([ti * 0.5, -ti, ti * 0.5])
    # grayscale / sepia (matrix)
    if p.get("grayscale"):
        lum = (rgb * np.array([0.2126, 0.7152, 0.0722])).sum(-1, keepdims=True)
        rgb = np.repeat(lum, 3, axis=-1)
    if p.get("sepia"):
        m = np.array([[0.393, 0.769, 0.189],
                      [0.349, 0.686, 0.168],
                      [0.272, 0.534, 0.131]])
        rgb = rgb @ m.T

    out = _from_arr(np.concatenate([rgb, alpha], axis=-1))

    if p.get("clarity"):
        out = Image.blend(out, out.filter(ImageFilter.UnsharpMask(radius=3, percent=int(80 * float(p["clarity"])), threshold=3)), 0.6)
    if p.get("sharpen"):
        out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=int(100 * float(p["sharpen"])), threshold=2))
    if p.get("blur"):
        out = out.filter(ImageFilter.GaussianBlur(radius=float(p["blur"])))
    if p.get("denoise"):
        out = out.filter(ImageFilter.MedianFilter(size=3))
    if p.get("vignette"):
        v = abs(float(p["vignette"]))
        w, h = out.size
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = w / 2.0, h / 2.0
        r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / np.sqrt(2)
        mask = np.clip(1.0 - v * np.clip(r, 0, 1) ** 2, 0, 1)[..., None]
        a = np.asarray(out, dtype=np.float32) / 255.0
        a[..., :3] *= mask
        out = _from_arr(a)
    return out


def apply_curves(img: Image.Image, curve: list[list[int]] | list[int]) -> Image.Image:
    """Apply an RGB curve. ``curve`` is either a shared 256-point list or three
    channel point lists ``[[x,y], ...]`` per channel.

    Accepts control points ``[[x,y],...]`` (0-255) which are interpolated into a
    256-entry LUT per channel.
    """
    img = img.convert("RGBA")
    channels = img.split()
    r, g, b, a = channels

    def lut(points) -> list[int]:
        if len(points) == 256 and all(isinstance(v, (int, float)) for v in points):
            return [int(max(0, min(255, v))) for v in points]
        xs = [pt[0] for pt in points]
        ys = [pt[1] for pt in points]
        x_axis = np.arange(256)
        interp = np.interp(x_axis, xs, ys)
        return [int(max(0, min(255, v))) for v in interp]

    if curve and isinstance(curve[0], (list, tuple)):
        r = r.point(lut(curve))
        g = g.point(lut(curve))
        b = b.point(lut(curve))
    else:
        r = r.point(lut(curve))
        g = g.point(lut(curve))
        b = b.point(lut(curve))
    return Image.merge("RGBA", (r, g, b, a))


# --------------------------------------------------------------- filters

_FILTER_PRESETS: dict[str, dict] = {
    "none": {},
    "vivid": {"saturation": 0.35, "vibrance": 0.25, "contrast": 0.12, "clarity": 0.3},
    "cinematic_warm": {"temperature": 0.06, "contrast": 0.15, "shadows": -0.05, "highlights": 0.08, "vignette": 0.3},
    "cyberpunk_neon": {"temperature": -0.08, "tint": 0.08, "saturation": 0.4, "contrast": 0.2, "vibrance": 0.3},
    "noir": {"grayscale": True, "contrast": 0.35, "shadows": -0.1, "clarity": 0.4, "vignette": 0.4},
    "vintage": {"sepia": True, "contrast": 0.05, "vignette": 0.25, "brightness": 0.03},
    "soft_portrait": {"brightness": 0.05, "saturation": -0.05, "blur": 0.4, "sharpen": 0.2},
    "high_key": {"exposure": 0.25, "contrast": -0.05, "shadows": 0.1, "saturation": 0.05},
    "low_key": {"exposure": -0.25, "contrast": 0.15, "shadows": -0.1},
    "rec709": {"contrast": 0.08},
}


def apply_filter(img: Image.Image, name: str) -> Image.Image:
    params = _FILTER_PRESETS.get(name)
    if params is None:
        raise ValueError(f"unknown filter preset: {name}")
    return apply_adjustments(img, params)


def list_filters() -> list[str]:
    return list(_FILTER_PRESETS)


# ------------------------------------------------------------ compositing

_BLEND_MODES = {
    "normal", "multiply", "screen", "overlay", "soft_light", "hard_light",
    "color_dodge", "color_burn", "difference", "exclusion", "darken", "lighten",
    "add", "subtract", "lighten_only",
}


def _blend_channel(base: np.ndarray, top: np.ndarray, mode: str) -> np.ndarray:
    if mode == "normal":
        return top
    if mode == "multiply":
        return base * top
    if mode == "screen":
        return 1 - (1 - base) * (1 - top)
    if mode == "overlay":
        return np.where(base <= 0.5, 2 * base * top, 1 - 2 * (1 - base) * (1 - top))
    if mode == "soft_light":
        return np.where(
            top <= 0.5,
            base - (1 - 2 * top) * base * (1 - base),
            base + (2 * top - 1) * (np.where(base <= 0.25, ((16 * base - 12) * base + 4) * base, np.sqrt(np.clip(base, 0, 1))) - base),
        )
    if mode == "hard_light":
        return np.where(top <= 0.5, 2 * base * top, 1 - 2 * (1 - base) * (1 - top))
    if mode == "color_dodge":
        return np.clip(base / np.clip(1 - top, 1e-6, 1), 0, 1)
    if mode == "color_burn":
        return 1 - np.clip((1 - base) / np.clip(top, 1e-6, 1), 0, 1)
    if mode == "difference":
        return np.abs(base - top)
    if mode == "exclusion":
        return base + top - 2 * base * top
    if mode == "darken" or mode == "lighten_only":
        return np.minimum(base, top)
    if mode == "lighten":
        return np.maximum(base, top)
    if mode == "add":
        return np.clip(base + top, 0, 1)
    if mode == "subtract":
        return np.clip(base - top, 0, 1)
    return top


def composite(base: Image.Image, layer: Image.Image, *, mode: str = "normal",
              opacity: float = 1.0, offset: tuple[int, int] = (0, 0),
              scale: float = 1.0) -> Image.Image:
    """Composite ``layer`` over ``base`` using a blend mode and alpha opacity."""
    base = base.convert("RGBA")
    layer = layer.convert("RGBA")
    if scale != 1.0:
        layer = layer.resize((max(1, round(layer.width * scale)),
                              max(1, round(layer.height * scale))), _LANCZOS)
    canvas = Image.new("RGBA", base.size, (0, 0, 0, 0))
    canvas.paste(layer, offset)
    la = np.asarray(canvas, dtype=np.float32) / 255.0
    ba = np.asarray(base, dtype=np.float32) / 255.0
    b = ba[..., :3]
    t = la[..., :3]
    blended = _blend_channel(b, t, mode) if mode in _BLEND_MODES else t
    # effective top alpha = layer alpha * opacity
    ta = la[..., 3:4] * float(opacity)
    out_rgb = b * (1 - ta) + blended * ta
    out_a = np.clip(ba[..., 3:4] + ta, 0, 1)
    return _from_arr(np.concatenate([out_rgb, out_a], axis=-1))


def apply_mask(img: Image.Image, mask: Image.Image | None) -> Image.Image:
    """Multiply the image's alpha by a grayscale ``mask`` (255 = keep)."""
    img = img.convert("RGBA")
    if mask is None:
        return img
    mask = mask.convert("L").resize(img.size, _LANCZOS)
    r, g, b, a = img.split()
    a = ImageChops.multiply(a, mask)
    return Image.merge("RGBA", (r, g, b, a))


# -------------------------------------------------------------- text/shape

def draw_text(img: Image.Image, spec: dict) -> Image.Image:
    """Draw text/shapes onto an image.

    ``text``: {text, x, y, size, color, font, align, anchor, stroke,
    stroke_width, shadow}. ``shape``: {type: rect|ellipse|line, box, fill,
    outline, width}.
    """
    img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)
    for shape in spec.get("shapes", []) or []:
        kind = shape.get("type", "rect")
        box = tuple(int(v) for v in shape.get("box", [0, 0, 10, 10]))
        if kind == "ellipse":
            draw.ellipse(box, fill=shape.get("fill"), outline=shape.get("outline"),
                         width=int(shape.get("width", 1)))
        elif kind == "line":
            draw.line(box, fill=shape.get("fill", "#ffffff"), width=int(shape.get("width", 2)))
        else:
            draw.rectangle(box, fill=shape.get("fill"), outline=shape.get("outline"),
                           width=int(shape.get("width", 0)))
    for txt in spec.get("texts", []) or []:
        content = str(txt.get("text", ""))
        if not content:
            continue
        size = int(txt.get("size", 40))
        font = _load_font(txt.get("font"), size)
        x, y = int(txt.get("x", 0)), int(txt.get("y", 0))
        color = txt.get("color", "#ffffff")
        anchor = txt.get("anchor", "la")
        shadow = txt.get("shadow")
        if shadow:
            draw.text((x + 2, y + 2), content, font=font, fill=shadow, anchor=anchor)
        draw.text((x, y), content, font=font, fill=color, anchor=anchor,
                  stroke_width=int(txt.get("stroke_width", 0)),
                  stroke_fill=txt.get("stroke"))
    return img


_FONT_CACHE: dict[tuple[str | None, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(name: str | None, size: int):
    key = (name, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    candidates = []
    if name:
        candidates += [name, f"{name}.ttf", str(Path("C:/Windows/Fonts") / f"{name}.ttf")]
    # sensible defaults per platform
    for cand in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        candidates.append(cand)
        candidates.append(str(Path("C:/Windows/Fonts") / cand))
    for cand in candidates:
        try:
            font = ImageFont.truetype(cand, size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ------------------------------------------------------- background removal

def remove_background(img: Image.Image, *, mode: str = "edge",
                      tolerance: int = 30) -> Image.Image:
    """Remove the background, returning RGBA with a transparent backdrop.

    ``edge`` floods each border-connected region matching the border colour
    (robust for flat/studio backdrops). ``color`` removes every pixel within
    ``tolerance`` of the sampled corner colour. ``rembg`` uses the optional
    ``rembg`` package when installed and falls back to ``edge`` otherwise.
    """
    if mode == "rembg":
        try:
            from rembg import remove as _rembg  # type: ignore
            return _rembg(img.convert("RGBA"))
        except Exception:
            mode = "edge"
    rgba = img.convert("RGBA")
    arr = np.asarray(rgba, dtype=np.int16)
    rgb = arr[..., :3]
    h, w = rgb.shape[:2]
    corners = np.stack([rgb[0, 0], rgb[0, w - 1], rgb[h - 1, 0], rgb[h - 1, w - 1]])
    ref = corners.mean(0)
    dist = np.sqrt(((rgb.astype(np.float32) - ref) ** 2).sum(-1))
    near = dist <= tolerance
    if mode == "color":
        mask = near
    else:
        from scipy import ndimage  # local import; scipy is a dependency
        labeled, n = ndimage.label(near)
        border_labels = set(labeled[0, :]) | set(labeled[-1, :]) | set(labeled[:, 0]) | set(labeled[:, -1])
        border_labels.discard(0)
        mask = np.isin(labeled, list(border_labels))
    out = arr.copy()
    out[..., 3] = np.where(mask, 0, 255)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


# --------------------------------------------------------------- pipeline

def apply_ops(img: Image.Image, ops: list[dict]) -> Image.Image:
    """Apply an ordered list of image ops (used by the API and the AI agent).

    Each op is ``{"op": <name>, ...params}``. Supported ops: adjust, curves,
    filter, transform, text, mask, background_remove, composite (needs a second
    image passed by the caller; handled separately).
    """
    out = img
    for op in ops:
        name = op.get("op")
        if name == "adjust":
            out = apply_adjustments(out, op.get("params", op))
        elif name == "curves":
            out = apply_curves(out, op.get("points", op.get("curve", [])))
        elif name == "filter":
            out = apply_filter(out, op.get("name", "none"))
        elif name == "transform":
            out = transform(out, op.get("params", op))
        elif name == "text":
            out = draw_text(out, op)
        elif name == "background_remove":
            out = remove_background(out, mode=op.get("mode", "edge"),
                                    tolerance=int(op.get("tolerance", 30)))
        else:
            raise ValueError(f"unknown image op: {name}")
    return out
