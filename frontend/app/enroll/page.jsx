"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Card from "../components/Card";

const API = (path) => `/api${path}`;

const POSES = [
  { label: "Look straight",     emoji: "😐" },
  { label: "Turn slightly left", emoji: "↖️" },
  { label: "Turn slightly right","emoji": "↗️" },
  { label: "Look up",           emoji: "⬆️" },
  { label: "Look down",         emoji: "⬇️" },
  { label: "Natural smile",     emoji: "😊" },
];

// ── Webcam helpers ────────────────────────────────────────────────────────────

async function startWebcam(videoEl) {
  const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 480, height: 360 } });
  videoEl.srcObject = stream;
  await videoEl.play();
  return stream;
}

function stopWebcam(stream) {
  stream?.getTracks().forEach((t) => t.stop());
}

function captureFrame(videoEl, canvasEl) {
  canvasEl.width  = videoEl.videoWidth;
  canvasEl.height = videoEl.videoHeight;
  canvasEl.getContext("2d").drawImage(videoEl, 0, 0);
  return new Promise((res) => canvasEl.toBlob(res, "image/jpeg", 0.92));
}

// ── Step indicator ────────────────────────────────────────────────────────────

function StepDots({ total, current, captures }) {
  return (
    <div className="flex gap-2 justify-center my-4">
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          className={`w-3 h-3 rounded-full border-2 transition-all ${
            captures[i]
              ? "bg-green-500 border-green-500"
              : i === current
              ? "bg-blue-500 border-blue-500"
              : "bg-transparent border-[#2a2f45]"
          }`}
        />
      ))}
    </div>
  );
}

// ── Thumbnail strip ───────────────────────────────────────────────────────────

function Thumbnails({ captures }) {
  return (
    <div className="flex gap-2 flex-wrap justify-center mt-3">
      {captures.map((blob, i) => (
        <div key={i} className="relative">
          <img
            src={URL.createObjectURL(blob)}
            className="w-14 h-14 rounded-lg object-cover border-2 border-green-500"
            alt={`capture ${i + 1}`}
          />
          <span className="absolute -top-1 -right-1 bg-green-500 text-white text-[9px] rounded-full w-4 h-4 flex items-center justify-center font-bold">
            ✓
          </span>
        </div>
      ))}
      {/* Empty slots */}
      {Array.from({ length: POSES.length - captures.length }).map((_, i) => (
        <div
          key={`empty-${i}`}
          className="w-14 h-14 rounded-lg border-2 border-dashed border-[#2a2f45] flex items-center justify-center text-slate-600 text-lg"
        >
          {POSES[captures.length + i]?.emoji}
        </div>
      ))}
    </div>
  );
}

// ── Main Enroll Page ──────────────────────────────────────────────────────────

export default function EnrollPage() {
  const [name,       setName]       = useState("");
  const [step,       setStep]       = useState(-1);       // -1 = waiting for name
  const [captures,   setCaptures]   = useState([]);       // list of Blob
  const [stream,     setStream]     = useState(null);
  const [status,     setStatus]     = useState(null);     // {type:"ok"|"err", msg}
  const [uploading,  setUploading]  = useState(false);
  const [countdown,  setCountdown]  = useState(null);     // null | 3 | 2 | 1

  const videoRef      = useRef(null);
  const canvasRef     = useRef(null);
  const countdownRef  = useRef(null);   // stores setInterval id for cleanup

  // ── Camera lifecycle ─────────────────────────────────────────────────────────

  const openCamera = useCallback(async () => {
    try {
      const s = await startWebcam(videoRef.current);
      setStream(s);
    } catch {
      setStatus({ type: "err", msg: "Cannot access webcam. Check browser permissions." });
    }
  }, []);

  const closeCamera = useCallback(() => {
    stopWebcam(stream);
    setStream(null);
  }, [stream]);

  // Open camera AFTER step >= 0 so the <video> element is in the DOM
  useEffect(() => {
    if (step === 0 && !stream) {
      openCamera();
    }
  }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => {
      stopWebcam(stream);
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
  }, [stream]);

  // ── Start enrollment ─────────────────────────────────────────────────────────

  const startEnrollment = () => {
    if (!name.trim()) {
      setStatus({ type: "err", msg: "Please enter a name first." });
      return;
    }
    setCaptures([]);
    setStatus(null);
    setStep(0); // camera opens via useEffect once video element renders
  };

  // ── Capture with 3-second countdown ─────────────────────────────────────────

  const captureWithCountdown = () => {
    if (countdown !== null) return;
    let c = 3;
    setCountdown(c);
    countdownRef.current = setInterval(() => {
      c -= 1;
      if (c <= 0) {
        clearInterval(countdownRef.current);
        countdownRef.current = null;
        setCountdown(null);
        doCapture();
      } else {
        setCountdown(c);
      }
    }, 1000);
  };

  const doCapture = async () => {
    const blob = await captureFrame(videoRef.current, canvasRef.current);
    const next = [...captures, blob];
    setCaptures(next);

    if (next.length >= POSES.length) {
      // All poses done — close camera and upload
      closeCamera();
      setStep(POSES.length);
      await uploadAll(next);
    } else {
      setStep(next.length);
    }
  };

  // ── Upload all blobs ─────────────────────────────────────────────────────────

  const uploadAll = async (blobs) => {
    setUploading(true);
    setStatus(null);
    const fd = new FormData();
    fd.append("name", name.trim().toLowerCase().replace(/\s+/g, "_"));
    blobs.forEach((b, i) => fd.append("files", b, `pose_${i + 1}.jpg`));

    try {
      const res  = await fetch(API("/register"), { method: "POST", body: fd });
      const data = await res.json();
      if (data.success) {
        setStatus({ type: "ok", msg: `✅ ${data.message}` });
      } else {
        setStatus({ type: "err", msg: data.detail || data.message || "Registration failed." });
      }
    } catch {
      setStatus({ type: "err", msg: "Network error. Is the backend running?" });
    } finally {
      setUploading(false);
    }
  };

  // ── Reset ─────────────────────────────────────────────────────────────────────

  const reset = () => {
    closeCamera();
    setName("");
    setStep(-1);
    setCaptures([]);
    setStatus(null);
    setCountdown(null);
  };

  // ── Render ──────────────────────────────────────────────────────────────────

  const currentPose = POSES[step] ?? null;
  const isDone      = step >= POSES.length;

  return (
    <>
      <div className="mb-5">
        <h1 className="text-xl font-semibold">Enroll New Person</h1>
        <p className="text-slate-500 text-sm mt-0.5">
          Capture 6 poses for best recognition accuracy
        </p>
      </div>

      <div className="max-w-xl mx-auto flex flex-col gap-4">

        {/* ── Step -1: Enter name ── */}
        {step === -1 && (
          <Card title="Person Details">
            <label className="block text-xs text-slate-500 mb-1">Full name</label>
            <input
              type="text"
              placeholder="e.g. John Doe"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && startEnrollment()}
              className="w-full px-3 py-2 rounded-lg bg-[#0f1117] border border-[#2a2f45] text-slate-200 text-sm focus:outline-none focus:border-blue-500 mb-3"
            />
            <button
              onClick={startEnrollment}
              className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors"
            >
              Start Enrollment
            </button>
            {status && (
              <p className={`mt-2 text-sm p-2 rounded-lg ${status.type === "ok" ? "bg-green-900/40 text-green-300" : "bg-red-900/40 text-red-300"}`}>
                {status.msg}
              </p>
            )}
          </Card>
        )}

        {/* ── Steps 0–5: Webcam capture ── */}
        {step >= 0 && !isDone && (
          <Card>
            {/* Pose instruction */}
            <div className="text-center mb-3">
              <div className="text-4xl mb-1">{currentPose.emoji}</div>
              <p className="font-semibold text-base">{currentPose.label}</p>
              <p className="text-slate-500 text-xs">Step {step + 1} of {POSES.length}</p>
            </div>

            {/* Progress dots */}
            <StepDots total={POSES.length} current={step} captures={captures} />

            {/* Webcam video */}
            <div className="relative rounded-xl overflow-hidden bg-black">
              <video ref={videoRef} className="w-full rounded-xl" playsInline muted />
              {/* Countdown overlay */}
              {countdown !== null && (
                <div className="absolute inset-0 flex items-center justify-center bg-black/50 rounded-xl">
                  <span className="text-8xl font-bold text-white">{countdown}</span>
                </div>
              )}
            </div>
            <canvas ref={canvasRef} className="hidden" />

            {/* Capture button */}
            <button
              onClick={captureWithCountdown}
              disabled={countdown !== null || !stream}
              className="mt-3 w-full py-3 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white font-medium transition-colors"
            >
              {countdown !== null ? `Capturing in ${countdown}…` : "Capture Photo"}
            </button>

            {/* Thumbnails of already-captured poses */}
            {captures.length > 0 && <Thumbnails captures={captures} />}
          </Card>
        )}

        {/* ── Done state ── */}
        {isDone && (
          <Card>
            <div className="text-center py-4">
              {uploading ? (
                <>
                  <div className="text-4xl mb-3 animate-pulse">⏳</div>
                  <p className="font-semibold">Uploading & processing…</p>
                  <p className="text-slate-500 text-sm mt-1">
                    Computing face embeddings for {POSES.length} images
                  </p>
                </>
              ) : status?.type === "ok" ? (
                <>
                  <div className="text-5xl mb-3">✅</div>
                  <p className="font-semibold text-green-400">{status.msg}</p>
                  <Thumbnails captures={captures} />
                  <button
                    onClick={reset}
                    className="mt-5 px-6 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors"
                  >
                    Enroll Another Person
                  </button>
                </>
              ) : (
                <>
                  <div className="text-5xl mb-3">❌</div>
                  <p className="text-red-400 text-sm">{status?.msg}</p>
                  <button
                    onClick={reset}
                    className="mt-4 px-5 py-2 rounded-lg border border-[#2a2f45] text-slate-300 text-sm hover:bg-[#2a2f45] transition-colors"
                  >
                    Try Again
                  </button>
                </>
              )}
            </div>
          </Card>
        )}

      </div>
    </>
  );
}