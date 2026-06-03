"""
video_stream.py — Webcam capture, face recognition loop, and MJPEG stream.

Responsibilities:
  - Run a background thread that reads webcam frames
  - Every FRAME_SKIP frames: detect faces, extract embeddings, match
  - Voting buffer: require VOTING_WINDOW consistent matches before triggering
  - Dedup: skip re-processing an embedding seen < DEDUP_TTL_SEC seconds ago
  - Annotate frames with bounding boxes and labels
  - Store JPEG bytes in state.camera["frame"] for MJPEG streaming
  - Manage pending confirmations (add / confirm / expire)

Cross-file rule:
  - All face logic goes through face_utils.
  - All CSV writes go through attendance.
  - Shared dicts live in state.
"""

import os
import tempfile
import threading
import time
import uuid
from typing import Generator

import cv2

from . import state
from .attendance import is_marked_today, mark_attendance
from .config import (
    CONFIRMATION_TTL,
    DEDUP_TTL_SEC,
    FRAME_SKIP,
    TEMP_DIR,
    VOTING_WINDOW,
)
from .face_utils import (
    detect_faces_in_frame,
    extract_embedding_from_path,
    is_dedup_hit,
    match_embedding,
)

# ── Drawing helpers ───────────────────────────────────────────────────────────

_COLOR_KNOWN   = (34,  197,  94)   # green  (BGR)
_COLOR_UNKNOWN = (239,  68,  68)   # red
_COLOR_PENDING = (234, 179,   8)   # amber  (confirmation waiting)


def _draw_box(frame, x: int, y: int, w: int, h: int,
              label: str, color: tuple) -> None:
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    text_y    = y - 10 if y - 10 > 15 else y + h + 20
    text_w    = max(len(label) * 9, w)
    cv2.rectangle(frame, (x, text_y - 18), (x + text_w, text_y + 4), color, cv2.FILLED)
    cv2.putText(frame, label, (x + 4, text_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)


# ── Camera thread ─────────────────────────────────────────────────────────────

def camera_worker() -> None:
    """
    Main camera loop (runs in a daemon thread).
    Reads frames, runs recognition every FRAME_SKIP frames, annotates, stores JPEG.
    """
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)   # CAP_DSHOW = faster init on Windows

    if not cap.isOpened():
        # Fall back without backend flag (Linux / macOS)
        cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[video] ERROR: cannot open webcam.")
        state.camera["running"] = False
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS,          30)

    frame_idx   = 0
    last_result = []   # annotations from last recognition pass

    print("[video] Camera started.")

    while state.camera["running"]:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        frame_idx += 1

        # ── Recognition pass every FRAME_SKIP frames ──────────────────────
        if frame_idx % FRAME_SKIP == 0:
            last_result = _process_frame(frame)

        # ── Annotate every frame with last known detections ────────────────
        annotated = frame.copy()
        for det in last_result:
            x, y, w, h  = det["x"], det["y"], det["w"], det["h"]
            name        = det["name"]
            confidence  = det["confidence"]
            pending_id  = det.get("pending_id")

            if name == "Unknown":
                label = "Not Registered"
                color = _COLOR_UNKNOWN
            elif pending_id:
                label = f"{name} – Confirm?"
                color = _COLOR_PENDING
            else:
                label = f"{name}  {int(confidence * 100)}%"
                color = _COLOR_KNOWN

            _draw_box(annotated, x, y, w, h, label, color)

        # ── Encode and store ───────────────────────────────────────────────
        ok, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            with state.camera["lock"]:
                state.camera["frame"]   = jpeg.tobytes()
                state.camera["results"] = last_result

    cap.release()
    with state.camera["lock"]:
        state.camera["frame"]   = None
        state.camera["results"] = []
    print("[video] Camera stopped.")


def _process_frame(frame) -> list[dict]:
    """
    Detect faces in `frame`, match each against the cache, manage voting and
    pending confirmations.

    Returns a list of annotation dicts for _draw_box.
    """
    faces   = detect_faces_in_frame(frame)
    results = []

    for face in faces:
        x, y, w, h  = face["x"], face["y"], face["w"], face["h"]
        crop        = face["image"]

        # Save crop to temp file so extract_embedding_from_path can read it
        tmp = tempfile.mktemp(suffix=".jpg", dir=str(TEMP_DIR))
        cv2.imwrite(tmp, crop)

        try:
            embedding = extract_embedding_from_path(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        if embedding is None:
            results.append({"x": x, "y": y, "w": w, "h": h,
                             "name": "Unknown", "confidence": 0.0,
                             "pending_id": None})
            continue

        # Dedup: skip if same face was processed very recently
        if is_dedup_hit(embedding, DEDUP_TTL_SEC):
            # Return previous result for this face if available
            results.append({"x": x, "y": y, "w": w, "h": h,
                             "name": "Unknown", "confidence": 0.0,
                             "pending_id": None})
            continue

        match = match_embedding(embedding)

        if not match["matched"]:
            # Reset vote buffer for any name that was accumulating
            state.votes.pop("_last", None)
            results.append({"x": x, "y": y, "w": w, "h": h,
                             "name": "Unknown", "confidence": 0.0,
                             "pending_id": None})
            continue

        name = match["name"]

        # Voting: accumulate VOTING_WINDOW consistent detections before acting
        buf = state.votes.get(name, [])
        buf.append(match["distance"])
        if len(buf) > VOTING_WINDOW:
            buf = buf[-VOTING_WINDOW:]
        state.votes[name] = buf

        pending_id = None

        if len(buf) >= VOTING_WINDOW:
            # Enough consistent frames — check if a confirmation is already pending
            already_pending = _has_pending_for_name(name)
            already_marked  = is_marked_today(name)

            if not already_pending and not already_marked:
                pending_id = _add_pending(name, match["confidence"], match["distance"])
            elif already_pending:
                pending_id = _get_pending_id_for_name(name)

        results.append({
            "x": x, "y": y, "w": w, "h": h,
            "name":       name,
            "confidence": match["confidence"],
            "distance":   match["distance"],
            "pending_id": pending_id,
        })

    return results


# ── Pending confirmations ─────────────────────────────────────────────────────

def _add_pending(name: str, confidence: float, distance: float) -> str:
    face_id = str(uuid.uuid4())[:8]
    with state.pending_lock:
        state.pending[face_id] = {
            "name":       name,
            "confidence": confidence,
            "distance":   distance,
            "timestamp":  time.monotonic(),
        }
    return face_id


def _has_pending_for_name(name: str) -> bool:
    with state.pending_lock:
        return any(v["name"] == name for v in state.pending.values())


def _get_pending_id_for_name(name: str) -> str | None:
    with state.pending_lock:
        for fid, v in state.pending.items():
            if v["name"] == name:
                return fid
    return None


def confirm_pending(face_id: str) -> dict:
    """
    Called by the /confirm/{face_id} endpoint.
    Marks attendance and removes the pending entry.

    Returns the result from attendance.mark_attendance or an error dict.
    """
    with state.pending_lock:
        entry = state.pending.pop(face_id, None)

    if entry is None:
        return {"success": False, "message": "Confirmation not found or expired."}

    result = mark_attendance(entry["name"], entry["confidence"])
    # Clear the voting buffer so the same person doesn't immediately re-trigger
    state.votes.pop(entry["name"], None)
    return result


def get_pending_list() -> list[dict]:
    """Return all pending confirmations (for frontend polling)."""
    now = time.monotonic()
    with state.pending_lock:
        return [
            {
                "face_id":    fid,
                "name":       v["name"],
                "confidence": v["confidence"],
                "expires_in": max(0.0, round(CONFIRMATION_TTL - (now - v["timestamp"]), 1)),
            }
            for fid, v in state.pending.items()
        ]


# ── Start / stop ──────────────────────────────────────────────────────────────

def start_camera() -> dict:
    if state.camera["running"]:
        return {"status": "already_running"}
    state.camera["running"] = True
    t = threading.Thread(target=camera_worker, name="camera-worker", daemon=True)
    t.start()
    state.camera["thread"] = t
    return {"status": "started"}


def stop_camera() -> dict:
    state.camera["running"] = False
    return {"status": "stopped"}


# ── MJPEG generator ───────────────────────────────────────────────────────────

def mjpeg_generator() -> Generator[bytes, None, None]:
    """Yield multipart MJPEG chunks (~25 fps to browser)."""
    while state.camera["running"]:
        with state.camera["lock"]:
            frame = state.camera["frame"]

        if frame is None:
            time.sleep(0.04)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame
            + b"\r\n"
        )
        time.sleep(0.04)   # ~25 fps