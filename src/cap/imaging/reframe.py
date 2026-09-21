"""Aspect-ratio reframing: resize, content-aware crop, or GenAI outpaint (expand).

Strategy "auto" is cost-aware: crop when the crop keeps the subject (free, instant), and only
pay for outpainting when a crop would cut into the product.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

from ..cache import Cache, cache_key, sha256
from ..providers import ImageProvider, ProviderError
from .align import align, harmonize
from .saliency import best_crop

KEEP_THRESHOLD = 0.85  # min saliency retained for "auto" to accept a crop


@dataclass
class Reframed:
    image: Image.Image
    method: str  # resize | crop | expand
    detail: str
    upscale: float  # output px per source px (>2 means visibly soft)
    cached: bool = False


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def expand_canvas(size: tuple[int, int], ratio: float) -> tuple[int, int]:
    w, h = size
    if w / h < ratio:
        return math.ceil(h * ratio), h
    return w, math.ceil(w / ratio)


def recomposite(expanded: Image.Image, src: Image.Image) -> Image.Image:
    """Paste the original pixels back over the outpainted result with a feathered seam.

    Generative models may subtly redraw everything, including the product. Brand-approved pixels
    must survive untouched, so only the new margins come from the model.
    """
    cw, ch = expanded.size
    x, y = (cw - src.width) // 2, (ch - src.height) // 2
    feather = max(2, int(min(src.size) * 0.02))
    # Feather only the sides that face generated margins. A side lying on the canvas border has nothing
    # to blend into, so the mask runs off the edge there (no opaque "strip", which showed as corner notches).
    pad = feather * 4
    left, top = pad + feather if x > 0 else 0, pad + feather if y > 0 else 0
    right = pad + src.width - feather - 1 if x + src.width < cw else src.width + 2 * pad
    bottom = pad + src.height - feather - 1 if y + src.height < ch else src.height + 2 * pad
    mask = Image.new("L", (src.width + 2 * pad, src.height + 2 * pad), 0)
    ImageDraw.Draw(mask).rectangle([left, top, right, bottom], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(feather / 2)).crop((pad, pad, pad + src.width, pad + src.height))
    out = expanded.copy()
    out.paste(src, (x, y), mask)
    return out


def reframe(
    src: Image.Image,
    out_size: tuple[int, int],
    *,
    strategy: str,
    provider: ImageProvider,
    cache: Cache,
    prompt: str,
    focus: tuple[float, float] | None = None,
) -> Reframed:
    ow, oh = out_size
    ratio = ow / oh
    src_ratio = src.width / src.height

    if abs(src_ratio - ratio) / ratio < 0.01:
        return Reframed(src.resize(out_size, Image.LANCZOS), "resize", "source already matches ratio", ow / src.width)

    box, kept = best_crop(src, ratio, focus)
    crop_detail = f"content-aware crop kept {kept:.0%} of subject saliency"
    want_expand = strategy == "expand" or (strategy == "auto" and kept < KEEP_THRESHOLD)

    if want_expand and provider.supports_expand:
        canvas = expand_canvas(src.size, ratio)
        src_png = _png(src)
        key = cache_key(
            op="expand",
            provider=provider.name,
            model=provider.model,
            src=sha256(src_png),
            canvas=canvas,
            prompt=prompt,
            tag=provider.cache_tag,
        )
        try:
            data = cache.get(key)
            cached = data is not None
            if data is None:
                data = provider.expand(src_png, canvas, prompt)
                cache.put(key, data)
            expanded = Image.open(io.BytesIO(data)).convert("RGB").resize(canvas, Image.LANCZOS)
            # Models without a real mask redraw the centre: undo its drift and tone shift before pasting back.
            aligned, note = align(expanded, src)
            merged = recomposite(harmonize(aligned, src), src)
            why = "forced" if strategy == "expand" else f"crop would keep only {kept:.0%} of subject"
            return Reframed(
                merged.resize(out_size, Image.LANCZOS),
                "expand",
                f"{provider.name} outpaint to {canvas[0]}x{canvas[1]} ({why}); {note}; original pixels re-composited",
                ow / canvas[0],
                cached,
            )
        except ProviderError as e:
            crop_detail += f"; expand failed, fell back to crop ({str(e)[:120]})"
    elif want_expand:
        crop_detail += f"; {provider.name} cannot outpaint"

    cropped = src.crop(box)
    return Reframed(cropped.resize(out_size, Image.LANCZOS), "crop", crop_detail, ow / cropped.width)
