"""
state.py — Single source of shared in-memory state.

All modules import from here. Nothing writes to this module's dicts
without acquiring the corresponding lock.
"""

import threading
from typing import Any, Dict, List

# ── Camera ────────────────────────────────────────────────────────────────────
camera: Dict[str, Any] = {
    "running": False,
    "thread":  None,
    "frame":   None,        # latest annotated JPEG bytes for MJPEG stream
    "results": [],          # list[dict] of detections in the current frame
    "lock":    threading.Lock(),
}

# ── Pending confirmations ─────────────────────────────────────────────────────
# face_id (str) → {name, confidence, distance, timestamp (float), frame_path (str)}
pending: Dict[str, dict] = {}
pending_lock = threading.Lock()

# ── Dedup cache ───────────────────────────────────────────────────────────────
# embedding_hash (str) → last_seen epoch timestamp (float)
dedup: Dict[str, float] = {}
dedup_lock = threading.Lock()

# ── Voting buffer ─────────────────────────────────────────────────────────────
# name (str) → list of consecutive matching distances
votes: Dict[str, List[float]] = {}

# ── Embeddings cache (loaded once at startup, updated on register) ─────────────
# name (str) → {"embeddings": list[list[float]]}
embeddings: Dict[str, dict] = {}
embeddings_lock = threading.Lock()
