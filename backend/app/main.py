"""
main.py — FastAPI application entry point.

Run from face_attendance/ directory:
    uvicorn app.main:app --reload --port 8000

Routes:
    GET  /                     Built-in dashboard UI
    GET  /video_feed           MJPEG stream
    GET  /camera/start         Start webcam thread
    GET  /camera/stop          Stop webcam thread
    GET  /camera/status        Current detections + camera running flag
    GET  /pending              List of confirmations waiting for user action
    POST /confirm/{face_id}    Confirm a pending detection → mark attendance
    POST /register             Register new person (multipart: file + name)
    GET  /registered           List registered people
    DELETE /delete/{name}      Delete a registered person
    GET  /attendance           Query attendance records (?date=YYYY-MM-DD)
    GET  /attendance/today     Today's attendance shortcut
"""

import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from .attendance import ensure_log_exists, get_attendance
from .cleanup import start_cleanup
from .config import ALLOWED_ORIGINS, REGISTERED_DIR, TEMP_DIR
from .face_utils import (
    add_person_to_cache,
    load_cache,
    remove_person_from_cache,
    extract_embedding_from_path,
    match_embedding,
)
from .video_stream import (
    confirm_pending,
    get_pending_list,
    mjpeg_generator,
    start_camera,
    stop_camera,
)
from . import state


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load cache, ensure directories, start cleanup thread."""
    REGISTERED_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    ensure_log_exists()
    load_cache()
    start_cleanup()
    print("[main] Application startup complete.")
    yield
    stop_camera()
    print("[main] Application shutdown.")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Face Attendance API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Camera / stream ───────────────────────────────────────────────────────────

@app.get("/camera/start", summary="Start webcam recognition thread")
async def api_start_camera():
    return start_camera()


@app.get("/camera/stop", summary="Stop webcam recognition thread")
async def api_stop_camera():
    return stop_camera()


@app.get("/camera/status", summary="Current detections and camera state")
async def api_camera_status():
    with state.camera["lock"]:
        results = list(state.camera["results"])
    return {
        "running": state.camera["running"],
        "detections": [
            {
                "name":       r["name"],
                "confidence": r["confidence"],
                "pending_id": r.get("pending_id"),
            }
            for r in results
        ],
    }


@app.get("/video_feed", summary="MJPEG stream — use as <img src='/video_feed'>")
async def api_video_feed():
    if not state.camera["running"]:
        raise HTTPException(status_code=400, detail="Camera not running. Call /camera/start first.")
    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ── Confirmations ─────────────────────────────────────────────────────────────

@app.get("/pending", summary="List pending face confirmations")
async def api_pending():
    return get_pending_list()


@app.post("/confirm/{face_id}", summary="Confirm attendance for a pending detection")
async def api_confirm(face_id: str):
    result = confirm_pending(face_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result["message"])
    return result


# ── Registration ──────────────────────────────────────────────────────────────

@app.post("/register", summary="Register a new person with one or more photos")
async def api_register(
    files: list[UploadFile] = File(...),
    name:  str              = Form(...),
):
    """
    Upload 1–6 photos of a person with their name.
    Each photo is validated for face presence before saving.
    """
    name = name.strip().lower().replace(" ", "_")
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty.")

    person_dir = REGISTERED_DIR / name
    person_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    failed      = 0

    for upload in files:
        suffix   = Path(upload.filename).suffix or ".jpg"
        tmp_path = tempfile.mktemp(suffix=suffix, dir=str(TEMP_DIR))
        try:
            with open(tmp_path, "wb") as fh:
                shutil.copyfileobj(upload.file, fh)

            # Validate a face exists before accepting
            emb = extract_embedding_from_path(tmp_path)
            if emb is None:
                failed += 1
                os.unlink(tmp_path)
                continue

            import time as _time
            dest = person_dir / f"{int(_time.time() * 1000)}{suffix}"
            shutil.move(tmp_path, str(dest))
            saved_paths.append(str(dest))

        except Exception as e:
            failed += 1
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    if not saved_paths:
        raise HTTPException(status_code=422, detail="No valid face found in any uploaded image.")

    success = add_person_to_cache(name, saved_paths)
    return {
        "success":       success,
        "name":          name,
        "images_saved":  len(saved_paths),
        "images_failed": failed,
        "message":       f"Registered {name} with {len(saved_paths)} image(s).",
    }


@app.get("/registered", summary="List all registered people")
async def api_registered():
    people = [
        d.name
        for d in sorted(REGISTERED_DIR.iterdir())
        if d.is_dir()
    ]
    return {"registered": people, "count": len(people)}


@app.delete("/delete/{name}", summary="Delete a registered person and all their data")
async def api_delete(name: str):
    person_dir = REGISTERED_DIR / name
    if not person_dir.exists():
        raise HTTPException(status_code=404, detail=f"{name} not found.")
    shutil.rmtree(person_dir)
    remove_person_from_cache(name)
    return {"deleted": True, "name": name}


# ── Single-image check (REST, no video required) ──────────────────────────────

@app.post("/check", summary="Match a single uploaded photo against registered faces")
async def api_check(file: UploadFile = File(...)):
    suffix   = Path(file.filename).suffix or ".jpg"
    tmp_path = tempfile.mktemp(suffix=suffix, dir=str(TEMP_DIR))
    try:
        with open(tmp_path, "wb") as fh:
            shutil.copyfileobj(file.file, fh)
        emb = extract_embedding_from_path(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if emb is None:
        raise HTTPException(status_code=422, detail="No face detected in the uploaded image.")

    return match_embedding(emb)


# ── Attendance queries ────────────────────────────────────────────────────────

@app.get("/attendance/today", summary="Today's attendance list")
async def api_attendance_today():
    today   = date.today().isoformat()
    records = get_attendance(filter_date=today)
    return {"date": today, "count": len(records), "records": records}


@app.get("/attendance", summary="Query attendance records (?date=YYYY-MM-DD)")
async def api_attendance(filter_date: Optional[str] = None):
    records = get_attendance(filter_date=filter_date)
    return {"filter_date": filter_date, "count": len(records), "records": records}


# ── Built-in dashboard UI ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, summary="Browser dashboard")
async def dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Face Attendance System</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:      #0f1117;
    --surface: #1a1f2e;
    --border:  #2a2f45;
    --text:    #e2e8f0;
    --muted:   #64748b;
    --green:   #22c55e;
    --red:     #ef4444;
    --amber:   #f59e0b;
    --blue:    #3b82f6;
  }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; min-height: 100vh; padding: 20px; }
  h1   { font-size: 20px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 13px; margin-top: 2px; }
  .header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
  .grid   { display: grid; grid-template-columns: 1fr 340px; gap: 14px; }
  .card   { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px; }
  .card h2 { font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 12px; }
  #feed    { width: 100%; border-radius: 8px; background: #000; min-height: 300px; display: block; object-fit: cover; }
  .btn     { padding: 7px 16px; border-radius: 8px; border: none; cursor: pointer; font-size: 13px; font-weight: 500; transition: opacity .15s; }
  .btn:hover { opacity: .82; }
  .btn-green  { background: var(--green);  color: #fff; }
  .btn-red    { background: var(--red);    color: #fff; }
  .btn-blue   { background: var(--blue);   color: #fff; }
  .btn-amber  { background: var(--amber);  color: #000; }
  .btn-ghost  { background: transparent; color: var(--text); border: 1px solid var(--border); }
  .btn-row    { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  .dot        { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 5px; }
  .dot-on  { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot-off { background: var(--red); }

  /* Detections */
  #detections { min-height: 28px; margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 99px; font-size: 12px; font-weight: 500; }
  .chip-known   { background: #14532d55; color: #86efac; border: 1px solid #166534; }
  .chip-unknown { background: #450a0a55; color: #fca5a5; border: 1px solid #7f1d1d; }
  .chip-pending { background: #78350f55; color: #fcd34d; border: 1px solid #92400e; }

  /* Confirmation popup */
  #confirm-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 50; align-items: center; justify-content: center; }
  #confirm-overlay.show { display: flex; }
  .confirm-box { background: var(--surface); border: 1px solid var(--amber); border-radius: 14px; padding: 28px 24px; max-width: 340px; width: 90%; text-align: center; }
  .confirm-box h3 { font-size: 17px; margin-bottom: 6px; }
  .confirm-box p  { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
  .confirm-btns   { display: flex; gap: 10px; justify-content: center; }

  /* Attendance table */
  .att-table { width: 100%; font-size: 13px; border-collapse: collapse; }
  .att-table th { text-align: left; padding: 5px 8px; color: var(--muted); border-bottom: 1px solid var(--border); }
  .att-table td { padding: 7px 8px; border-bottom: 1px solid #1a1f35; }
  .att-empty    { color: var(--muted); font-size: 13px; padding: 10px 0; }

  /* Register form */
  .field { margin-bottom: 10px; }
  .field label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  .field input  { width: 100%; padding: 7px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 13px; }
  .msg { font-size: 13px; margin-top: 8px; padding: 6px 10px; border-radius: 6px; }
  .msg-ok  { background: #14532d55; color: #86efac; }
  .msg-err { background: #450a0a55; color: #fca5a5; }

  @media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>Face Attendance System</h1>
    <p class="sub">Real-time recognition · DeepFace + ArcFace · <span id="reg-count">–</span> people registered</p>
  </div>
  <button class="btn btn-blue" onclick="goEnroll()">+ Register Person</button>
</div>

<div class="grid">

  <!-- Left: live feed -->
  <div>
    <div class="card">
      <h2><span class="dot dot-off" id="dot"></span>Live Camera</h2>
      <img id="feed" src="" alt="Start camera to see feed" />
      <div id="detections"><span class="att-empty">No detections yet</span></div>
      <div class="btn-row">
        <button class="btn btn-green" onclick="startCamera()">Start Camera</button>
        <button class="btn btn-red"   onclick="stopCamera()">Stop Camera</button>
      </div>
    </div>
  </div>

  <!-- Right: attendance + register -->
  <div style="display:flex;flex-direction:column;gap:14px;">

    <div class="card">
      <h2>Today's Attendance</h2>
      <table class="att-table">
        <thead><tr><th>Name</th><th>Time</th><th>%</th></tr></thead>
        <tbody id="att-body"><tr><td colspan="3" class="att-empty">Loading…</td></tr></tbody>
      </table>
      <div class="btn-row">
        <button class="btn btn-ghost" onclick="loadAttendance()">Refresh</button>
        <button class="btn btn-ghost" onclick="exportCSV()">Export CSV</button>
      </div>
    </div>

    <div class="card" id="register-card">
      <h2>Quick Register</h2>
      <div class="field"><label>Name</label>
        <input type="text" id="reg-name" placeholder="e.g. john_doe" />
      </div>
      <div class="field"><label>Photos (up to 6)</label>
        <input type="file" id="reg-files" accept="image/*" multiple />
      </div>
      <button class="btn btn-blue" onclick="registerPerson()">Register</button>
      <div id="reg-msg"></div>
    </div>

  </div>
</div>

<!-- Confirmation overlay -->
<div id="confirm-overlay">
  <div class="confirm-box">
    <h3 id="confirm-name">John Doe</h3>
    <p id="confirm-sub">Face recognised. Mark attendance?</p>
    <div class="confirm-btns">
      <button class="btn btn-amber"  id="confirm-yes" onclick="confirmAttendance()">Confirm Attendance</button>
      <button class="btn btn-ghost"  onclick="ignoreConfirm()">Ignore</button>
    </div>
  </div>
</div>

<script>
  let statusTimer = null;
  let currentFaceId = null;
  let confirmExpiry = null;

  // ── Camera ─────────────────────────────────────────────────────────────────
  function startCamera() {
    fetch('/camera/start').then(r => r.json()).then(() => {
      document.getElementById('feed').src = '/video_feed?' + Date.now();
      document.getElementById('dot').className = 'dot dot-on';
      statusTimer = setInterval(pollStatus, 1500);
    });
  }

  function stopCamera() {
    fetch('/camera/stop').then(() => {
      document.getElementById('feed').src = '';
      document.getElementById('dot').className = 'dot dot-off';
      document.getElementById('detections').innerHTML = '<span class="att-empty">Camera stopped</span>';
      if (statusTimer) clearInterval(statusTimer);
    });
  }

  // ── Status polling ─────────────────────────────────────────────────────────
  function pollStatus() {
    fetch('/camera/status').then(r => r.json()).then(data => {
      const box = document.getElementById('detections');
      if (!data.detections.length) {
        box.innerHTML = '<span class="att-empty">No faces detected</span>';
        return;
      }
      box.innerHTML = data.detections.map(d => {
        if (d.pending_id)   return `<span class="chip chip-pending">⏳ ${d.name} – Confirm?</span>`;
        if (d.name !== 'Unknown') return `<span class="chip chip-known">✓ ${d.name} ${Math.round(d.confidence*100)}%</span>`;
        return `<span class="chip chip-unknown">? Not Registered</span>`;
      }).join('');
    });
    pollPending();
    loadAttendance();
  }

  // ── Pending confirmations ──────────────────────────────────────────────────
  function pollPending() {
    fetch('/pending').then(r => r.json()).then(list => {
      if (!list.length || currentFaceId) return;
      const item = list[0];
      currentFaceId = item.face_id;
      document.getElementById('confirm-name').textContent = item.name.replace(/_/g,' ');
      document.getElementById('confirm-sub').textContent =
        `Confidence: ${Math.round(item.confidence * 100)}%  ·  expires in ${item.expires_in}s`;
      document.getElementById('confirm-overlay').classList.add('show');
      confirmExpiry = setTimeout(ignoreConfirm, item.expires_in * 1000);
    });
  }

  function confirmAttendance() {
    if (!currentFaceId) return;
    fetch('/confirm/' + currentFaceId, { method: 'POST' })
      .then(r => r.json())
      .then(() => { hideConfirm(); loadAttendance(); });
  }

  function ignoreConfirm() { hideConfirm(); }

  function hideConfirm() {
    document.getElementById('confirm-overlay').classList.remove('show');
    currentFaceId = null;
    if (confirmExpiry) { clearTimeout(confirmExpiry); confirmExpiry = null; }
  }

  // ── Attendance ─────────────────────────────────────────────────────────────
  function loadAttendance() {
    const today = new Date().toISOString().split('T')[0];
    fetch('/attendance/today').then(r => r.json()).then(data => {
      const tbody = document.getElementById('att-body');
      if (!data.records.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="att-empty">No attendance yet today</td></tr>';
        return;
      }
      tbody.innerHTML = data.records.map(r =>
        `<tr><td>${r.name.replace(/_/g,' ')}</td><td>${r.time}</td><td>${Math.round(r.confidence*100)}%</td></tr>`
      ).join('');
    });
  }

  function exportCSV() {
    const today = new Date().toISOString().split('T')[0];
    window.open('/attendance?date=' + today, '_blank');
  }

  // ── Register ───────────────────────────────────────────────────────────────
  function goEnroll() {
    document.getElementById('register-card').scrollIntoView({ behavior: 'smooth' });
    document.getElementById('reg-name').focus();
  }

  function registerPerson() {
    const name  = document.getElementById('reg-name').value.trim();
    const files = document.getElementById('reg-files').files;
    const msg   = document.getElementById('reg-msg');
    if (!name || !files.length) {
      msg.innerHTML = '<div class="msg msg-err">Enter a name and select at least one photo.</div>';
      return;
    }
    const fd = new FormData();
    fd.append('name', name);
    for (const f of files) fd.append('files', f);
    msg.innerHTML = '<div class="msg" style="background:#1e2130;color:var(--muted)">Uploading…</div>';
    fetch('/register', { method: 'POST', body: fd })
      .then(r => r.json())
      .then(d => {
        msg.innerHTML = d.success
          ? `<div class="msg msg-ok">${d.message}</div>`
          : `<div class="msg msg-err">${d.message || 'Registration failed.'}</div>`;
        loadRegisteredCount();
      })
      .catch(() => { msg.innerHTML = '<div class="msg msg-err">Network error.</div>'; });
  }

  // ── Registered count ───────────────────────────────────────────────────────
  function loadRegisteredCount() {
    fetch('/registered').then(r => r.json()).then(d => {
      document.getElementById('reg-count').textContent = d.count;
    });
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  loadAttendance();
  loadRegisteredCount();
</script>
</body>
</html>"""