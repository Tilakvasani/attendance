"""
Face Recognition Attendance System
====================================
Webcam streams to browser via MJPEG.
Faces are detected, matched, and attendance is marked automatically.

Run:  uvicorn op:app --reload --port 8000
Open: http://localhost:8000
"""

# ─── ENV FIRST — before any import ───────────────────────────────────────────
import os
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["TF_CPP_MIN_LOG_LEVEL"]  = "3"

# ─── IMPORTS ──────────────────────────────────────────────────────────────────
import csv
import json
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from deepface import DeepFace
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

# ─── CONFIG ───────────────────────────────────────────────────────────────────

REGISTERED_DIR  = "registered_faces"
ATTENDANCE_LOG  = "attendance_log.csv"
MODEL_NAME      = "ArcFace"
DETECTOR        = "yunet"
DISTANCE_METRIC = "cosine"
THRESHOLD       = 0.40

# How many seconds to wait before re-checking the same face
# Prevents marking attendance 30 times per second
RECOGNITION_COOLDOWN = 60

# Only run DeepFace every N frames (webcam is 30fps, we don't need to check every frame)
PROCESS_EVERY_N_FRAMES = 60

# ─── SETUP ────────────────────────────────────────────────────────────────────

os.makedirs(REGISTERED_DIR, exist_ok=True)

if not os.path.exists(ATTENDANCE_LOG):
    with open(ATTENDANCE_LOG, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "date", "time", "confidence", "status"])

# ─── CAMERA STATE ─────────────────────────────────────────────────────────────
# Shared state between the camera thread and the API

camera_state = {
    "running":       False,
    "cap":           None,
    "frame":         None,         # latest annotated frame (JPEG bytes)
    "last_results":  [],           # list of {name, confidence, box}
    "lock":          threading.Lock(),
    "last_seen":     {},           # name -> timestamp, for cooldown
    "frame_count":   0,
}

# ─── CORE HELPERS ─────────────────────────────────────────────────────────────

def _already_marked_today(name: str, date: str) -> bool:
    if not os.path.exists(ATTENDANCE_LOG):
        return False
    df = pd.read_csv(ATTENDANCE_LOG)
    return not df[(df["name"] == name) & (df["date"] == date)].empty


def _write_attendance(name: str, confidence: float):
    now = datetime.now()
    with open(ATTENDANCE_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            name,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            round(confidence, 4),
            "present",
        ])


def _get_registered_images():
    return (
        list(Path(REGISTERED_DIR).glob("*.jpg"))  +
        list(Path(REGISTERED_DIR).glob("*.jpeg")) +
        list(Path(REGISTERED_DIR).glob("*.png"))
    )


def _match_face(image_path: str) -> dict:
    """Try to match a face image against registered faces."""
    registered = _get_registered_images()
    if not registered:
        return {"matched": False, "name": "Unknown", "confidence": 0.0}

    best_match    = None
    best_distance = float("inf")

    for reg_img in registered:
        try:
            result = DeepFace.verify(
                img1_path=image_path,
                img2_path=str(reg_img),
                model_name=MODEL_NAME,
                detector_backend=DETECTOR,
                distance_metric=DISTANCE_METRIC,
                enforce_detection=True,
                silent=True,
            )
            if result["verified"] and result["distance"] < best_distance:
                best_distance = result["distance"]
                best_match    = reg_img.stem
        except Exception:
            continue

    if best_match is None:
        return {"matched": False, "name": "Unknown", "confidence": 0.0}

    confidence = round(max(0.0, min(1.0, 1 - (best_distance / THRESHOLD))), 4)
    return {"matched": True, "name": best_match, "confidence": confidence}


def _draw_box(frame, x, y, w, h, name, confidence, color):
    """Draw bounding box + label on frame."""
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    label = f"{name} ({int(confidence * 100)}%)" if name != "Unknown" else "Unknown"
    label_y = y - 10 if y - 10 > 10 else y + h + 20
    cv2.rectangle(frame, (x, label_y - 18), (x + len(label) * 10, label_y + 4), color, -1)
    cv2.putText(frame, label, (x + 4, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)


# ─── CAMERA THREAD ────────────────────────────────────────────────────────────

def camera_worker():
    """
    Runs in a background thread.
    - Reads frames from webcam
    - Every N frames: runs DeepFace to detect + match faces
    - Draws boxes on frame
    - Marks attendance (with cooldown)
    - Stores annotated JPEG in camera_state["frame"]
    """
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: Could not open webcam.")
        camera_state["running"] = False
        return

    camera_state["cap"] = cap
    frame_count = 0
    current_results = []   # holds last known face detections

    print("Camera started.")

    while camera_state["running"]:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        frame_count += 1

        # ── Every N frames: run face detection + recognition ──────────────
        if frame_count % PROCESS_EVERY_N_FRAMES == 0:
            try:
                # Step 1: Detect all faces in frame using yunet directly
                tmp_path = tempfile.mktemp(suffix=".jpg")
                cv2.imwrite(tmp_path, frame)

                faces = DeepFace.extract_faces(
                    img_path=tmp_path,
                    detector_backend=DETECTOR,
                    enforce_detection=False,
                )

                new_results = []

                for face_data in faces:
                    region = face_data.get("facial_area", {})
                    x = region.get("x", 0)
                    y = region.get("y", 0)
                    w = region.get("w", 0)
                    h = region.get("h", 0)

                    if w < 30 or h < 30:   # skip tiny false positives
                        continue

                    # Step 2: Crop the face and save to temp file for matching
                    face_crop = frame[
                        max(0, y):min(frame.shape[0], y + h),
                        max(0, x):min(frame.shape[1], x + w)
                    ]

                    face_tmp = tempfile.mktemp(suffix=".jpg")
                    cv2.imwrite(face_tmp, face_crop)

                    match = _match_face(face_tmp)
                    os.unlink(face_tmp)

                    new_results.append({
                        "name":       match["name"],
                        "confidence": match["confidence"],
                        "matched":    match["matched"],
                        "box":        (x, y, w, h),
                    })

                    # Step 3: Mark attendance (with cooldown + daily dedup)
                    if match["matched"]:
                        name = match["name"]
                        now  = time.time()
                        last = camera_state["last_seen"].get(name, 0)

                        if now - last > RECOGNITION_COOLDOWN:
                            today   = datetime.now().strftime("%Y-%m-%d")
                            already = _already_marked_today(name, today)
                            if not already:
                                _write_attendance(name, match["confidence"])
                                print(f"✅ Attendance marked: {name} ({match['confidence']*100:.0f}%)")
                            else:
                                print(f"ℹ️  Already marked today: {name}")
                            camera_state["last_seen"][name] = now

                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

                current_results = new_results

            except Exception as e:
                print(f"Recognition error: {e}")

        # ── Draw boxes on every frame using last known results ─────────────
        annotated = frame.copy()
        for r in current_results:
            x, y, w, h = r["box"]
            color = (0, 200, 0) if r["matched"] else (0, 0, 220)   # green=known, red=unknown
            _draw_box(annotated, x, y, w, h, r["name"], r["confidence"], color)

        # ── Encode to JPEG and store ───────────────────────────────────────
        _, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with camera_state["lock"]:
            camera_state["frame"]        = jpeg.tobytes()
            camera_state["last_results"] = current_results

    cap.release()
    camera_state["cap"]   = None
    camera_state["frame"] = None
    print("Camera stopped.")


# ─── MJPEG GENERATOR ──────────────────────────────────────────────────────────

def mjpeg_generator():
    """Yield MJPEG frames for the /video_feed endpoint."""
    while camera_state["running"]:
        with camera_state["lock"]:
            frame = camera_state["frame"]

        if frame is None:
            time.sleep(0.05)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" +
            frame +
            b"\r\n"
        )
        time.sleep(1 / 25)   # ~25fps to browser


# ─── FASTAPI APP ──────────────────────────────────────────────────────────────

app = FastAPI(title="Face Attendance API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── VIDEO ROUTES ─────────────────────────────────────────────────────────────

@app.get("/camera/start")
async def start_camera():
    """Start the webcam and face recognition."""
    if camera_state["running"]:
        return {"status": "already_running"}
    camera_state["running"] = True
    t = threading.Thread(target=camera_worker, daemon=True)
    t.start()
    return {"status": "started"}


@app.get("/camera/stop")
async def stop_camera():
    """Stop the webcam."""
    camera_state["running"] = False
    return {"status": "stopped"}


@app.get("/video_feed")
async def video_feed():
    """MJPEG stream — put this in an <img src> tag in your browser."""
    if not camera_state["running"]:
        return {"error": "Camera not started. Call /camera/start first."}
    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/camera/status")
async def camera_status():
    """Get current detections from the camera."""
    with camera_state["lock"]:
        results = camera_state["last_results"]
    return {
        "running":    camera_state["running"],
        "detections": [
            {"name": r["name"], "confidence": r["confidence"], "matched": r["matched"]}
            for r in results
        ],
    }


# ─── EXISTING ROUTES (register, check-attendance, etc.) ──────────────────────

@app.post("/register")
async def api_register(file: UploadFile = File(...), name: str = Form(...)):
    """Register a new person with their photo."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    ext  = Path(tmp_path).suffix or ".jpg"
    dest = os.path.join(REGISTERED_DIR, f"{name}{ext}")
    try:
        DeepFace.extract_faces(img_path=tmp_path, detector_backend=DETECTOR)
        shutil.copy(tmp_path, dest)
        result = {"success": True, "message": f"Registered {name} successfully."}
    except Exception as e:
        result = {"success": False, "message": f"No face detected or error: {e}"}
    os.unlink(tmp_path)
    return result


@app.post("/check-attendance")
async def api_check_attendance(file: UploadFile = File(...)):
    """Single photo check — match and mark attendance."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    match = _match_face(tmp_path)
    os.unlink(tmp_path)
    if match["matched"]:
        today   = datetime.now().strftime("%Y-%m-%d")
        already = _already_marked_today(match["name"], today)
        if not already:
            _write_attendance(match["name"], match["confidence"])
        match["already_marked"] = already
        match["message"] = "Already marked today." if already else f"Attendance marked for {match['name']}!"
    else:
        match["message"] = "Face not recognized."
    return match


@app.get("/attendance")
async def api_get_attendance(date: str = None):
    """Get attendance records. Optional ?date=YYYY-MM-DD filter."""
    if not os.path.exists(ATTENDANCE_LOG):
        return []
    df = pd.read_csv(ATTENDANCE_LOG)
    if date:
        df = df[df["date"] == date]
    return df.to_dict(orient="records")


@app.get("/registered")
async def api_list_registered():
    """List all registered people."""
    people = [
        Path(f).stem
        for f in os.listdir(REGISTERED_DIR)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]
    return {"registered": people, "count": len(people)}


# ─── BROWSER UI ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Built-in browser UI — open http://localhost:8000"""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Face Attendance System</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 24px; }
    h1 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
    .sub { color: #64748b; font-size: 13px; margin-bottom: 24px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; max-width: 1000px; }
    .card { background: #1e2130; border: 1px solid #2d3148; border-radius: 12px; padding: 20px; }
    .card h2 { font-size: 14px; font-weight: 600; margin-bottom: 14px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
    #feed { width: 100%; border-radius: 8px; background: #0f1117; min-height: 240px; display: block; }
    .btn { padding: 8px 18px; border-radius: 8px; border: none; cursor: pointer; font-size: 13px; font-weight: 500; transition: opacity 0.15s; }
    .btn:hover { opacity: 0.85; }
    .btn-green  { background: #22c55e; color: #fff; }
    .btn-red    { background: #ef4444; color: #fff; }
    .btn-blue   { background: #3b82f6; color: #fff; }
    .btn-row    { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
    .dot-green { background: #22c55e; }
    .dot-red   { background: #ef4444; }
    #detections { margin-top: 12px; min-height: 48px; }
    .det-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 99px; font-size: 12px; margin: 3px; }
    .det-known   { background: #14532d; color: #86efac; border: 1px solid #16a34a; }
    .det-unknown { background: #450a0a; color: #fca5a5; border: 1px solid #b91c1c; }
    table { width: 100%; font-size: 13px; border-collapse: collapse; }
    th { text-align: left; padding: 6px 8px; color: #64748b; font-weight: 500; border-bottom: 1px solid #2d3148; }
    td { padding: 7px 8px; border-bottom: 1px solid #1a1f35; }
    input[type=file], input[type=text] { background: #0f1117; border: 1px solid #2d3148; border-radius: 8px; padding: 7px 10px; color: #e2e8f0; font-size: 13px; width: 100%; margin-bottom: 8px; }
    .msg { font-size: 13px; margin-top: 8px; padding: 6px 10px; border-radius: 6px; }
    .msg-ok  { background: #14532d; color: #86efac; }
    .msg-err { background: #450a0a; color: #fca5a5; }
    @media (max-width: 640px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <h1>Face Attendance System</h1>
  <p class="sub">Real-time webcam recognition — powered by DeepFace + ArcFace</p>

  <div class="grid">

    <!-- Live Camera -->
    <div class="card" style="grid-column: span 2;">
      <h2>
        <span class="status-dot dot-red" id="dot"></span>
        Live Camera
      </h2>
      <img id="feed" src="" alt="Camera feed will appear here after starting" />
      <div id="detections"></div>
      <div class="btn-row">
        <button class="btn btn-green" onclick="startCamera()">Start Camera</button>
        <button class="btn btn-red"   onclick="stopCamera()">Stop Camera</button>
      </div>
    </div>

    <!-- Register Person -->
    <div class="card">
      <h2>Register New Person</h2>
      <input type="text"  id="reg-name" placeholder="Person name (e.g. john_doe)" />
      <input type="file"  id="reg-file" accept="image/*" />
      <button class="btn btn-blue" onclick="registerPerson()">Register</button>
      <div id="reg-msg"></div>
    </div>

    <!-- Today's Attendance -->
    <div class="card">
      <h2>Today's Attendance</h2>
      <table>
        <thead><tr><th>Name</th><th>Time</th><th>Confidence</th></tr></thead>
        <tbody id="att-body"><tr><td colspan="3" style="color:#64748b">Loading...</td></tr></tbody>
      </table>
      <div class="btn-row">
        <button class="btn btn-blue" onclick="loadAttendance()">Refresh</button>
      </div>
    </div>

  </div>

<script>
  let statusInterval = null;

  function startCamera() {
    fetch('/camera/start')
      .then(r => r.json())
      .then(() => {
        document.getElementById('feed').src = '/video_feed';
        document.getElementById('dot').className = 'status-dot dot-green';
        statusInterval = setInterval(updateDetections, 1000);
      });
  }

  function stopCamera() {
    fetch('/camera/stop').then(() => {
      document.getElementById('feed').src = '';
      document.getElementById('dot').className = 'status-dot dot-red';
      document.getElementById('detections').innerHTML = '';
      if (statusInterval) clearInterval(statusInterval);
    });
  }

  function updateDetections() {
    fetch('/camera/status')
      .then(r => r.json())
      .then(data => {
        const box = document.getElementById('detections');
        if (!data.detections || data.detections.length === 0) {
          box.innerHTML = '<span style="color:#64748b;font-size:13px;">No faces detected</span>';
          return;
        }
        box.innerHTML = data.detections.map(d =>
          d.matched
            ? `<span class="det-chip det-known">✓ ${d.name} (${Math.round(d.confidence*100)}%)</span>`
            : `<span class="det-chip det-unknown">? Unknown</span>`
        ).join('');
        loadAttendance();
      });
  }

  function registerPerson() {
    const name = document.getElementById('reg-name').value.trim();
    const file = document.getElementById('reg-file').files[0];
    const msg  = document.getElementById('reg-msg');
    if (!name || !file) { msg.innerHTML = '<div class="msg msg-err">Fill in name and select a photo.</div>'; return; }
    const fd = new FormData();
    fd.append('name', name);
    fd.append('file', file);
    fetch('/register', { method: 'POST', body: fd })
      .then(r => r.json())
      .then(d => {
        msg.innerHTML = d.success
          ? `<div class="msg msg-ok">${d.message}</div>`
          : `<div class="msg msg-err">${d.message}</div>`;
      });
  }

  function loadAttendance() {
    const today = new Date().toISOString().split('T')[0];
    fetch('/attendance?date=' + today)
      .then(r => r.json())
      .then(rows => {
        const tbody = document.getElementById('att-body');
        if (!rows.length) {
          tbody.innerHTML = '<tr><td colspan="3" style="color:#64748b">No attendance yet today</td></tr>';
          return;
        }
        tbody.innerHTML = rows.map(r =>
          `<tr><td>${r.name}</td><td>${r.time}</td><td>${Math.round(r.confidence*100)}%</td></tr>`
        ).join('');
      });
  }

  loadAttendance();
</script>
</body>
</html>
"""