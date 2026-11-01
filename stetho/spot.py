"""Same-spot check: was this recording taken from the usual position?

Baseline comparison only makes sense when the phone sits at the same marked
spot, so Stetho stores a *view fingerprint* of each recording: up to 300
ORB keypoints (positions + 32-byte binary descriptors) from the first frame
of the background region. No image is stored, and the image cannot be
reconstructed from these descriptors in any practical sense. (They are still
derived data, and are covered by the DELETE command.)

A new recording's fingerprint is matched to the first baseline's with a
ratio test and a RANSAC similarity transform. The transform's scale,
rotation and shift tell us whether the camera moved:

  same_spot       enough inliers; scale within ±12 %, rotation < 6°, shift < 12 % of width
  moved           enough inliers, but outside those limits (closer/further/sideways)
  different_view  too few matches: different place, machine, or very different light

Effect: video features are only compared for `same_spot`. Audio features
are still compared, because they're less sensitive to small position changes,
but a `moved` or `different_view` result is reported to the user. Limits
are initial guesses for the validation protocol to tune.
"""

from __future__ import annotations

import base64

import cv2
import numpy as np

MAX_KP = 300
MIN_INLIERS = 20
SCALE_TOL = 0.12
ROT_TOL_DEG = 6.0
SHIFT_TOL = 0.12


def fingerprint(frame: np.ndarray, machine_box=None) -> dict | None:
    """ORB keypoints of the view, preferring the static background around the machine."""
    h, w = frame.shape
    mask = np.full((h, w), 255, np.uint8)
    if machine_box is not None:
        x, y, bw, bh = machine_box
        # Keep the machine too, but background points come first (static reference).
        mask[max(0, y):y + bh, max(0, x):x + bw] = 128
    orb = cv2.ORB_create(nfeatures=MAX_KP, fastThreshold=10)
    kps, des = orb.detectAndCompute(frame, (mask > 0).astype(np.uint8) * 255)
    if des is None or len(kps) < MIN_INLIERS:
        return None
    pts = np.float32([k.pt for k in kps])
    return {"w": w, "h": h, "pts": base64.b64encode(pts.astype("<f4").tobytes()).decode(),
            "des": base64.b64encode(des.tobytes()).decode()}


def _decode(fp: dict):
    pts = np.frombuffer(base64.b64decode(fp["pts"]), dtype="<f4").reshape(-1, 2)
    des = np.frombuffer(base64.b64decode(fp["des"]), dtype=np.uint8).reshape(-1, 32)
    return pts, des


def compare(current: dict | None, reference: dict | None) -> dict:
    if not current or not reference:
        return {"status": "not_available"}
    p1, d1 = _decode(reference)
    p2, d2 = _decode(current)
    # Normalise to the reference resolution if analysis sizes differ.
    if (current["w"], current["h"]) != (reference["w"], reference["h"]):
        p2 = p2 * np.float32([reference["w"] / current["w"], reference["h"] / current["h"]])
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(d2, d1, k=2)
    good = [m for m, *rest in pairs if rest and m.distance < 0.75 * rest[0].distance]
    if len(good) < MIN_INLIERS:
        return {"status": "different_view", "matches": len(good)}
    src = np.float32([p2[m.queryIdx] for m in good])
    dst = np.float32([p1[m.trainIdx] for m in good])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    n_in = int(inl.sum()) if inl is not None else 0
    if M is None or n_in < MIN_INLIERS:
        return {"status": "different_view", "matches": len(good), "inliers": n_in}
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    cx, cy = reference["w"] / 2, reference["h"] / 2
    mapped = M @ np.array([cx, cy, 1.0])
    shift = float(np.hypot(mapped[0] - cx, mapped[1] - cy) / reference["w"])
    same = abs(scale - 1) <= SCALE_TOL and abs(rot) <= ROT_TOL_DEG and shift <= SHIFT_TOL
    hint = None
    if not same:
        if abs(scale - 1) > SCALE_TOL:
            hint = "move_back" if scale < 1 else "move_closer"  # current view is magnified when scale < 1
        elif shift > SHIFT_TOL:
            hint = "move_sideways"
        else:
            hint = "level_phone"
    return {"status": "same_spot" if same else "moved", "inliers": n_in, "scale": round(scale, 3),
            "rotation_deg": round(rot, 1), "shift_frac": round(shift, 3), "hint": hint}
