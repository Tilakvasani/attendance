"""
face_utils.py — Face processing helpers.

Responsibilities:
  - Extract face embeddings via DeepFace
  - Match an embedding against the in-memory cache
  - Persist / load the embeddings cache (pickle)
  - Blur detection + minimum size guard
  - Dedup helper (embedding fingerprint)

Cross-file rule: ONLY this module calls DeepFace. No other module does.
"""

import hashlib
import pickle
import time
from typing import Optional

import cv2
import numpy as np
from deepface import DeepFace

import state
from config import (
    BLUR_THRESHOLD,
    CACHE_PATH,
    DETECTOR_ENROLL,
    DETECTOR_VIDEO,
    MIN_FACE_SIZE,
    MODEL_NAME,
    REGISTERED_DIR,
    THRESHOLD,
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cosine(a: list, b: list) -> float:
    """Pure cosine distance between two embedding vectors."""
    va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(1.0 - np.dot(va, vb) / (denom + 1e-10))


def _embedding_fingerprint(embedding: list) -> str:
    """Short MD5 of the first 20 embedding values (rounded).
    Used as a cheap dedup key — two captures of the same face in quick succession
    will hash to the same key."""
    key = str([round(v, 2) for v in embedding[:20]])
    return hashlib.md5(key.encode()).hexdigest()


# ── Public: quality checks ────────────────────────────────────────────────────

def is_blurry(image: np.ndarray) -> bool:
    """Return True if the image Laplacian variance is below the blur threshold."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var()) < BLUR_THRESHOLD


def is_too_small(w: int, h: int) -> bool:
    """Return True if bounding box is smaller than MIN_FACE_SIZE."""
    return w < MIN_FACE_SIZE or h < MIN_FACE_SIZE


# ── Public: cache management ──────────────────────────────────────────────────

def load_cache() -> None:
    """
    Load embeddings from disk into state.embeddings.
    Falls back to rebuild_cache() if the pickle file is missing.
    Called once at application startup.
    """
    with state.embeddings_lock:
        if CACHE_PATH.exists():
            try:
                with open(CACHE_PATH, "rb") as fh:
                    state.embeddings.update(pickle.load(fh))
                print(f"[face_utils] Loaded cache: {len(state.embeddings)} people.")
                return
            except Exception as e:
                print(f"[face_utils] Cache corrupt ({e}), rebuilding…")
        _rebuild_cache_locked()


def _rebuild_cache_locked() -> None:
    """Rebuild state.embeddings from registered_faces/ (caller holds the lock)."""
    state.embeddings.clear()
    REGISTERED_DIR.mkdir(parents=True, exist_ok=True)
    for person_dir in sorted(REGISTERED_DIR.iterdir()):
        if not person_dir.is_dir():
            continue
        embeddings_for_person = []
        for img_path in sorted(person_dir.glob("*.jpg")):
            emb = _extract_embedding(str(img_path), detector=DETECTOR_ENROLL)
            if emb is not None:
                embeddings_for_person.append(emb)
        if embeddings_for_person:
            state.embeddings[person_dir.name] = {"embeddings": embeddings_for_person}
    _save_cache_locked()
    print(f"[face_utils] Cache rebuilt: {len(state.embeddings)} people.")


def _save_cache_locked() -> None:
    """Persist state.embeddings to disk (caller holds the lock)."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "wb") as fh:
        pickle.dump(dict(state.embeddings), fh)


def add_person_to_cache(name: str, image_paths: list[str]) -> bool:
    """
    Compute embeddings for new image paths and add to in-memory cache + disk.
    Returns True if at least one embedding was successfully extracted.
    """
    new_embeddings = []
    for p in image_paths:
        emb = _extract_embedding(p, detector=DETECTOR_ENROLL)
        if emb is not None:
            new_embeddings.append(emb)

    if not new_embeddings:
        return False

    with state.embeddings_lock:
        existing = state.embeddings.get(name, {}).get("embeddings", [])
        state.embeddings[name] = {"embeddings": existing + new_embeddings}
        _save_cache_locked()

    return True


def remove_person_from_cache(name: str) -> None:
    """Remove a person from the in-memory cache and persist."""
    with state.embeddings_lock:
        state.embeddings.pop(name, None)
        _save_cache_locked()


# ── Public: extraction & matching ─────────────────────────────────────────────

def _extract_embedding(image_path: str, detector: str = DETECTOR_VIDEO) -> Optional[list]:
    """
    Extract a single face embedding from an image path.
    Returns None if no face is detected or on any error.
    """
    try:
        result = DeepFace.represent(
            img_path=image_path,
            model_name=MODEL_NAME,
            detector_backend=detector,
            enforce_detection=True,
        )
        return result[0]["embedding"]
    except Exception:
        return None


def extract_embedding_from_path(image_path: str) -> Optional[list]:
    """Public wrapper — uses the video detector (yunet, fast)."""
    return _extract_embedding(image_path, detector=DETECTOR_VIDEO)


def match_embedding(embedding: list) -> dict:
    """
    Compare embedding against every stored embedding in state.embeddings.

    Returns:
        {
          "matched":    bool,
          "name":       str,          # person name or "Unknown"
          "confidence": float,        # 0.0–1.0
          "distance":   float,        # raw cosine distance
        }
    """
    best_name = None
    best_dist = float("inf")

    with state.embeddings_lock:
        snapshot = {k: v["embeddings"] for k, v in state.embeddings.items()}

    for name, stored_list in snapshot.items():
        for stored_emb in stored_list:
            dist = _cosine(embedding, stored_emb)
            if dist < best_dist:
                best_dist = dist
                best_name = name

    if best_name and best_dist < THRESHOLD:
        confidence = round(max(0.0, min(1.0, 1.0 - best_dist / THRESHOLD)), 4)
        return {
            "matched":    True,
            "name":       best_name,
            "confidence": confidence,
            "distance":   round(best_dist, 4),
        }

    return {
        "matched":    False,
        "name":       "Unknown",
        "confidence": 0.0,
        "distance":   round(best_dist, 4),
    }


def is_dedup_hit(embedding: list, ttl: float) -> bool:
    """
    Return True if this embedding was seen within `ttl` seconds.
    Also updates the last-seen timestamp for this fingerprint.
    """
    fp  = _embedding_fingerprint(embedding)
    now = time.monotonic()
    with state.dedup_lock:
        last = state.dedup.get(fp, 0.0)
        if now - last < ttl:
            return True
        state.dedup[fp] = now
    return False


def detect_faces_in_frame(frame: np.ndarray) -> list[dict]:
    """
    Detect all faces in a BGR frame using DeepFace.extract_faces.
    Returns list of {"x", "y", "w", "h", "image": np.ndarray (face crop)}.
    Never raises.
    """
    import tempfile, os
    tmp = tempfile.mktemp(suffix=".jpg")
    try:
        cv2.imwrite(tmp, frame)
        faces = DeepFace.extract_faces(
            img_path=tmp,
            detector_backend=DETECTOR_VIDEO,
            enforce_detection=False,
        )
        result = []
        for f in faces:
            r = f.get("facial_area", {})
            x, y, w, h = r.get("x", 0), r.get("y", 0), r.get("w", 0), r.get("h", 0)
            if is_too_small(w, h):
                continue
            crop = frame[
                max(0, y): min(frame.shape[0], y + h),
                max(0, x): min(frame.shape[1], x + w),
            ]
            if is_blurry(crop):
                continue
            result.append({"x": x, "y": y, "w": w, "h": h, "image": crop})
        return result
    except Exception:
        return []
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
