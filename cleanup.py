"""
cleanup.py — Background maintenance thread.

Runs every CLEANUP_INTERVAL seconds (default 90 min) and:
  1. Expires pending confirmations older than CONFIRMATION_TTL seconds.
  2. Deletes temp frame files older than CONFIRMATION_TTL seconds.
  3. Trims stale entries from the dedup cache.

Cross-file rule: Only touches state dicts (via locks) and the TEMP_DIR filesystem.
                 Never calls DeepFace or writes to the attendance CSV.
"""

import threading
import time

import state
from config import CLEANUP_INTERVAL, CONFIRMATION_TTL, DEDUP_TTL_SEC, TEMP_DIR


def _expire_pending() -> int:
    """Remove pending confirmations older than CONFIRMATION_TTL. Returns count removed."""
    now     = time.monotonic()
    expired = []
    with state.pending_lock:
        for fid, entry in state.pending.items():
            if now - entry["timestamp"] > CONFIRMATION_TTL:
                expired.append(fid)
        for fid in expired:
            del state.pending[fid]
    return len(expired)


def _purge_temp_files() -> int:
    """Delete temp frame JPEGs older than CONFIRMATION_TTL seconds. Returns count deleted."""
    if not TEMP_DIR.exists():
        return 0
    now     = time.time()
    removed = 0
    for f in TEMP_DIR.glob("*.jpg"):
        try:
            if now - f.stat().st_mtime > CONFIRMATION_TTL:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _trim_dedup_cache() -> int:
    """Remove stale entries from the dedup cache. Returns count removed."""
    now    = time.monotonic()
    stale  = []
    with state.dedup_lock:
        for fp, ts in state.dedup.items():
            if now - ts > DEDUP_TTL_SEC * 2:   # keep twice the TTL for safety
                stale.append(fp)
        for fp in stale:
            del state.dedup[fp]
    return len(stale)


def _run_cleanup() -> None:
    expired_pending  = _expire_pending()
    deleted_files    = _purge_temp_files()
    trimmed_dedup    = _trim_dedup_cache()
    print(
        f"[cleanup] pending expired={expired_pending}  "
        f"temp files deleted={deleted_files}  "
        f"dedup trimmed={trimmed_dedup}"
    )


def cleanup_worker() -> None:
    """Daemon thread: sleeps CLEANUP_INTERVAL seconds between runs."""
    print(f"[cleanup] Worker started (interval={CLEANUP_INTERVAL}s).")
    while True:
        time.sleep(CLEANUP_INTERVAL)
        try:
            _run_cleanup()
        except Exception as e:
            print(f"[cleanup] ERROR: {e}")


def start_cleanup() -> None:
    """Start the background cleanup daemon thread. Call once at app startup."""
    t = threading.Thread(target=cleanup_worker, name="cleanup-worker", daemon=True)
    t.start()
