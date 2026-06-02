"""
config.py — All settings in one place.
Every other module imports from here. Never import config from a sibling module.

Run server from face_attendance/ directory:
    uvicorn app.main:app --reload --port 8000
"""

import os
from pathlib import Path

# ── Silence TF noise before any heavy import ──────────────────────────────────
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL",  "3")

# ── Load .env if present ───────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # python-dotenv optional; values fall back to os.getenv defaults

# ── Paths (relative to face_attendance/) ──────────────────────────────────────
_ROOT          = Path(__file__).resolve().parent.parent   # face_attendance/
DATA_DIR       = _ROOT / "data"
REGISTERED_DIR = DATA_DIR / "registered_faces"   # one sub-folder per person
LOG_PATH       = DATA_DIR / "attendance_log.csv"
TEMP_DIR       = DATA_DIR / "temp_frames"
CACHE_PATH     = DATA_DIR / "embeddings_cache.pkl"

# ── Face recognition ──────────────────────────────────────────────────────────
MODEL_NAME       = "ArcFace"    # 99.4 % accuracy on LFW
DETECTOR_ENROLL  = "yunet"      # fast + no TF1 warnings; used during registration
DETECTOR_VIDEO   = "yunet"      # used in live video loop
DISTANCE_METRIC  = "cosine"
THRESHOLD        = float(os.getenv("THRESHOLD", "0.60"))   # tune per environment

# ── Performance ───────────────────────────────────────────────────────────────
FRAME_SKIP      = int(os.getenv("FRAME_SKIP",     "3"))    # run DeepFace every N frames
MIN_FACE_SIZE   = int(os.getenv("MIN_FACE_SIZE",  "60"))   # pixels; smaller = skip
BLUR_THRESHOLD  = float(os.getenv("BLUR_THRESHOLD","100")) # Laplacian variance
VOTING_WINDOW   = int(os.getenv("VOTING_WINDOW",  "5"))    # frames before triggering confirm

# ── Timing ────────────────────────────────────────────────────────────────────
CONFIRMATION_TTL = int(os.getenv("CONFIRMATION_TTL", "15"))   # seconds
DEDUP_TTL_SEC    = int(os.getenv("DEDUP_TTL_SEC",    "30"))   # seconds
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", "5400")) # 90 min in seconds

# ── Server ────────────────────────────────────────────────────────────────────
API_PORT        = int(os.getenv("API_PORT", "8000"))
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
