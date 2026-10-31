"""Component localisation: which pixels belong to the machine, which are static.

Why this matters: camera-motion compensation needs pixels that are known to
be static (floor, wall). Segmentation does not stabilise video by itself; it
only tells the motion estimator which pixels to treat as "machine" and which
as "reference". If the machine fills the frame, there is no static reference
and hand shake cannot be separated from machine motion; the quality gate
reports this.

Backends:
  * `Sam3Segmenter` - Meta SAM 3 / 3.1 (facebookresearch/sam3) with a text
    prompt such as "pump" or "motor". Requires the `sam3` package, gated
    Hugging Face checkpoints, and a CUDA GPU. It is imported lazily and is
    optional; it is not exercised by the test suite in this repository.
  * `BoxSegmenter` - a user-drawn box (web UI) or a box from another tool.
  * `CentrePriorSegmenter` - the default fallback. The recording guide asks
    users to put the machine in the centre with floor/wall visible around it;
    this backend assumes exactly that, and says so in the report.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Region:
    box: tuple[int, int, int, int]  # x, y, w, h in analysis-frame pixels
    source: str  # sam3 | user_box | centre_prior
    note: str = ""

    def mask(self, shape: tuple[int, int]) -> np.ndarray:
        m = np.zeros(shape, dtype=bool)
        x, y, w, h = self.box
        m[max(0, y):y + h, max(0, x):x + w] = True
        return m


class CentrePriorSegmenter:
    name = "centre_prior"

    def __init__(self, frac: float = 0.42):
        self.frac = frac

    def locate(self, frames: np.ndarray, prompt: str = "machine") -> Region:
        _, h, w = frames.shape
        bw, bh = int(w * self.frac), int(h * self.frac)
        return Region(((w - bw) // 2, (h - bh) // 2, bw, bh), self.name,
                      "Assumed the machine is centred (no segmentation model available).")


class BoxSegmenter:
    name = "user_box"

    def __init__(self, box_frac: tuple[float, float, float, float]):
        self.box_frac = box_frac

    def locate(self, frames: np.ndarray, prompt: str = "machine") -> Region:
        _, h, w = frames.shape
        fx, fy, fw, fh = (min(max(v, 0.0), 1.0) for v in self.box_frac)
        box = (int(fx * w), int(fy * h), max(8, int(fw * w)), max(8, int(fh * h)))
        return Region(box, self.name, "Machine region drawn by the user.")


class Sam3Segmenter:  # pragma: no cover - needs GPU + gated weights
    """Thin adapter around the SAM 3 video predictor.

    Only the first-frame mask's bounding box is used: Stetho tracks motion
    with optical flow, and needs SAM only to label machine vs. background.
    API names follow the facebookresearch/sam3 README at the time of
    writing; verify against the installed version.
    """

    name = "sam3"

    def __init__(self):
        import torch  # noqa: F401
        from sam3.model_builder import build_sam3_image_model  # type: ignore
        from sam3.model.sam3_image_processor import Sam3Processor  # type: ignore

        if not torch.cuda.is_available():
            raise RuntimeError("SAM 3 needs a CUDA GPU")
        self.processor = Sam3Processor(build_sam3_image_model())

    def locate(self, frames: np.ndarray, prompt: str = "machine") -> Region:
        from PIL import Image

        img = Image.fromarray(frames[0]).convert("RGB")
        state = self.processor.set_image(img)
        out = self.processor.set_text_prompt(state=state, prompt=prompt)
        masks = out.get("masks")
        if masks is None or len(masks) == 0:
            raise RuntimeError("SAM 3 found no instance for prompt")
        m = np.asarray(masks[0]).squeeze().astype(bool)
        ys, xs = np.nonzero(m)
        box = (int(xs.min()), int(ys.min()), int(np.ptp(xs)) + 1, int(np.ptp(ys)) + 1)
        return Region(box, self.name, f"Segmented with SAM 3 (prompt '{prompt}').")


def get_segmenter(kind: str = "auto", box_frac: tuple | None = None):
    if box_frac:
        return BoxSegmenter(tuple(box_frac))
    if kind in ("auto", "sam3"):
        try:
            return Sam3Segmenter()
        except Exception:  # missing package, weights or GPU
            if kind == "sam3":
                raise
    return CentrePriorSegmenter()
