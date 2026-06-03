# Face Attendance System

Real-time face recognition attendance system built with **FastAPI + DeepFace (ArcFace)** on the backend and **Next.js 14 + Tailwind CSS** on the frontend.

---

## Project Structure

```
face-attendance/
├── .env.example              # Copy to .env and configure
├── .gitignore
├── docker-compose.yml        # Run both services with one command
│
├── backend/                  # FastAPI Python backend
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py       # TF env setup (runs first on import)
│   │   ├── main.py           # FastAPI routes + lifespan
│   │   ├── config.py         # All settings in one place
│   │   ├── attendance.py     # CSV read/write (single module owns the CSV)
│   │   ├── cleanup.py        # Background maintenance thread
│   │   ├── face_utils.py     # DeepFace wrapper — only module that calls DeepFace
│   │   ├── state.py          # Shared in-memory state + locks
│   │   └── video_stream.py   # Webcam loop, MJPEG stream, pending confirmations
│   └── data/
│       ├── registered_faces/ # One sub-folder per person (name/photo.jpg)
│       ├── temp_frames/      # Short-lived frame crops (auto-cleaned)
│       ├── attendance_log.csv# Written by attendance.py
│       └── embeddings_cache.pkl  # Pickled embeddings (rebuilt on startup)
│
└── frontend/                 # Next.js 14 frontend
    ├── Dockerfile
    ├── package.json
    ├── next.config.js        # Proxies /api/* → http://localhost:8000
    ├── tailwind.config.js
    ├── postcss.config.js
    └── app/
        ├── layout.jsx        # Root layout + navigation bar
        ├── globals.css       # Tailwind base + dark-mode scrollbar
        ├── page.jsx          # Dashboard — live camera feed + today's attendance
        ├── components/
        │   └── Card.jsx      # Shared card component
        ├── enroll/
        │   └── page.jsx      # 6-pose webcam enrollment wizard
        └── reports/
            └── page.jsx      # Historical records + registered people management
```

---

## Quick Start (Docker — recommended)

```bash
# 1. Clone / unzip the project
cd face-attendance

# 2. Copy and edit environment variables
cp .env.example .env

# 3. Start both services
docker-compose up --build

# Backend → http://localhost:8000
# Frontend → http://localhost:3000
```

> **Webcam in Docker (Linux):** Uncomment the `devices` block in `docker-compose.yml` to pass `/dev/video0` into the backend container.

---

## Local Development (without Docker)

### Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create required data directories
mkdir -p data/registered_faces data/temp_frames

# Start the server (run from inside backend/)
uvicorn app.main:app --reload --port 8000
```

The built-in dashboard is available at **http://localhost:8000**.

### Frontend

```bash
cd frontend

npm install
npm run dev       # → http://localhost:3000
```

The Next.js dev server proxies all `/api/*` requests to `http://localhost:8000` automatically via `next.config.js`.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Built-in HTML dashboard |
| `GET` | `/camera/start` | Start the webcam recognition thread |
| `GET` | `/camera/stop` | Stop the webcam thread |
| `GET` | `/camera/status` | Current detections + running state |
| `GET` | `/video_feed` | MJPEG live stream (`<img src="/video_feed">`) |
| `GET` | `/pending` | List pending face confirmations |
| `POST` | `/confirm/{face_id}` | Confirm a pending detection → log attendance |
| `POST` | `/register` | Register a new person (multipart: `name` + `files[]`) |
| `GET` | `/registered` | List all registered people |
| `DELETE` | `/delete/{name}` | Delete a person and all their data |
| `POST` | `/check` | Match a single uploaded photo against all registered faces |
| `GET` | `/attendance` | Query attendance records (`?date=YYYY-MM-DD`) |
| `GET` | `/attendance/today` | Today's attendance shortcut |

Interactive docs: **http://localhost:8000/docs**

---

## Frontend Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Live camera feed, detection chips, today's attendance |
| Enroll | `/enroll` | Guided 6-pose webcam enrollment wizard |
| Reports | `/reports` | Historical attendance records + people management |

---

## Configuration

All backend settings can be tuned via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `THRESHOLD` | `0.60` | Cosine distance cutoff (lower = stricter matching) |
| `FRAME_SKIP` | `3` | Run DeepFace every N frames |
| `MIN_FACE_SIZE` | `60` | Minimum face bounding-box size (pixels) |
| `BLUR_THRESHOLD` | `100` | Laplacian variance threshold for blur rejection |
| `VOTING_WINDOW` | `5` | Consecutive matching frames before triggering confirm |
| `CONFIRMATION_TTL` | `15` | Seconds before a pending confirmation expires |
| `DEDUP_TTL_SEC` | `30` | Minimum gap before re-processing the same face |
| `CLEANUP_INTERVAL` | `5400` | Background cleanup interval (default 90 min) |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins (comma-separated) |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Face recognition | [DeepFace](https://github.com/serengil/deepface) with **ArcFace** model |
| Face detection | **YuNet** (fast, no TF warnings) |
| Backend | **FastAPI** + **Uvicorn** |
| Video stream | OpenCV + MJPEG multipart response |
| Frontend | **Next.js 14** (App Router) + **React 18** |
| Styling | **Tailwind CSS v3** |
| Data storage | CSV (attendance log) + Pickle (embeddings cache) |
| Containerisation | Docker + Docker Compose |

---

## How It Works

1. **Registration** — Upload 1–6 face photos per person. DeepFace extracts an ArcFace embedding for each image and stores them in a pickle cache.

2. **Recognition loop** — A background thread reads webcam frames. Every `FRAME_SKIP` frames, it detects faces, extracts embeddings, and computes cosine distance against all stored embeddings.

3. **Voting** — A face must match consistently across `VOTING_WINDOW` frames before triggering a confirmation. This prevents false positives from a single bad frame.

4. **Confirmation** — Matched faces appear as a popup on the dashboard. The operator clicks **Confirm** to write the attendance record. Confirmations auto-expire after `CONFIRMATION_TTL` seconds.

5. **Dedup** — The same embedding fingerprint cannot trigger a new confirmation within `DEDUP_TTL_SEC` seconds, preventing duplicate entries.
