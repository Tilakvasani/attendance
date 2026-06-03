"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Card from "./components/Card";

const API = (path) => `/api${path}`;

// ── Small UI primitives ───────────────────────────────────────────────────────

function Btn({ onClick, color = "blue", children, disabled = false }) {
  const colors = {
    blue:   "bg-blue-600 hover:bg-blue-500 text-white",
    green:  "bg-green-600 hover:bg-green-500 text-white",
    red:    "bg-red-600   hover:bg-red-500   text-white",
    ghost:  "bg-transparent border border-[#2a2f45] text-slate-300 hover:bg-[#2a2f45]",
    amber:  "bg-amber-500 hover:bg-amber-400 text-black",
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-40 ${colors[color]}`}
    >
      {children}
    </button>
  );
}

function StatusDot({ on }) {
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full mr-2 ${
        on ? "bg-green-500 shadow-[0_0_6px_#22c55e]" : "bg-red-500"
      }`}
    />
  );
}

// ── Confirmation popup ────────────────────────────────────────────────────────

function ConfirmPopup({ item, onConfirm, onIgnore }) {
  const [remaining, setRemaining] = useState(Math.ceil(item.expires_in));

  useEffect(() => {
    if (remaining <= 0) { onIgnore(); return; }
    const t = setTimeout(() => setRemaining((r) => r - 1), 1000);
    return () => clearTimeout(t);
  }, [remaining, onIgnore]);

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-[#1a1f2e] border border-amber-500 rounded-2xl p-7 w-full max-w-sm text-center shadow-2xl">
        {/* Avatar placeholder */}
        <div className="w-16 h-16 rounded-full bg-amber-500/20 border-2 border-amber-500 flex items-center justify-center text-2xl mx-auto mb-4">
          👤
        </div>
        <h3 className="text-lg font-semibold mb-1">
          {item.name.replace(/_/g, " ")}
        </h3>
        <p className="text-slate-400 text-sm mb-1">
          Confidence: <span className="text-white font-medium">{Math.round(item.confidence * 100)}%</span>
        </p>
        <p className="text-slate-500 text-xs mb-6">
          Auto-expires in {remaining}s
        </p>
        <div className="flex gap-3 justify-center">
          <Btn color="amber" onClick={onConfirm}>Confirm Attendance</Btn>
          <Btn color="ghost" onClick={onIgnore}>Ignore</Btn>
        </div>
      </div>
    </div>
  );
}

// ── Detection chips ───────────────────────────────────────────────────────────

function DetectionChips({ detections }) {
  if (!detections.length)
    return <p className="text-slate-500 text-sm">No faces detected</p>;

  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {detections.map((d, i) => {
        if (d.pending_id)
          return (
            <span key={i} className="px-3 py-1 rounded-full text-xs font-medium bg-amber-900/40 text-amber-300 border border-amber-700">
              ⏳ {d.name.replace(/_/g," ")} – Confirm?
            </span>
          );
        if (d.name !== "Unknown")
          return (
            <span key={i} className="px-3 py-1 rounded-full text-xs font-medium bg-green-900/40 text-green-300 border border-green-700">
              ✓ {d.name.replace(/_/g," ")} {Math.round(d.confidence * 100)}%
            </span>
          );
        return (
          <span key={i} className="px-3 py-1 rounded-full text-xs font-medium bg-red-900/40 text-red-300 border border-red-700">
            ? Not Registered
          </span>
        );
      })}
    </div>
  );
}

// ── Attendance table ──────────────────────────────────────────────────────────

function AttendanceTable({ records }) {
  if (!records.length)
    return <p className="text-slate-500 text-sm py-2">No attendance yet today.</p>;

  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr>
          {["Name", "Time", "Conf."].map((h) => (
            <th key={h} className="text-left py-1.5 px-2 text-slate-500 text-xs uppercase tracking-wide border-b border-[#2a2f45]">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {records.map((r, i) => (
          <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#0f1117]/50">
            <td className="py-2 px-2 font-medium">{r.name.replace(/_/g, " ")}</td>
            <td className="py-2 px-2 text-slate-400">{r.time}</td>
            <td className="py-2 px-2 text-slate-400">{Math.round(r.confidence * 100)}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [cameraOn,    setCameraOn]    = useState(false);
  const [detections,  setDetections]  = useState([]);
  const [attendance,  setAttendance]  = useState([]);
  const [pending,     setPending]     = useState([]);
  const [regCount,    setRegCount]    = useState("–");
  const [activeConfirm, setActiveConfirm] = useState(null);

  const pollRef          = useRef(null);
  const feedRef          = useRef(null);
  // Ref keeps the latest activeConfirm readable inside the setInterval closure
  // without recreating the callback (and therefore the interval) every render.
  const activeConfirmRef = useRef(null);
  useEffect(() => { activeConfirmRef.current = activeConfirm; }, [activeConfirm]);

  // ── API helpers ─────────────────────────────────────────────────────────────

  const loadAttendance = useCallback(async () => {
    try {
      const res  = await fetch(API("/attendance/today"));
      const data = await res.json();
      setAttendance(data.records ?? []);
    } catch {/* ignore */}
  }, []);

  const loadRegCount = useCallback(async () => {
    try {
      const res  = await fetch(API("/registered"));
      const data = await res.json();
      setRegCount(data.count);
    } catch {/* ignore */}
  }, []);

  const pollStatus = useCallback(async () => {
    try {
      const [statusRes, pendingRes] = await Promise.all([
        fetch(API("/camera/status")),
        fetch(API("/pending")),
      ]);
      const statusData  = await statusRes.json();
      const pendingData = await pendingRes.json();

      setDetections(statusData.detections ?? []);
      setPending(pendingData);

      // Use ref so we always read the latest value, not a stale closure copy.
      if (pendingData.length && !activeConfirmRef.current) {
        setActiveConfirm(pendingData[0]);
      }
    } catch {/* ignore network errors during polling */}
    loadAttendance();
  }, [loadAttendance]); // activeConfirm intentionally NOT in deps — use ref instead

  // ── Camera controls ─────────────────────────────────────────────────────────

  const startCamera = async () => {
    await fetch(API("/camera/start"));
    setCameraOn(true);
    if (feedRef.current) feedRef.current.src = API("/video_feed") + "?" + Date.now();
    pollRef.current = setInterval(pollStatus, 1500);
  };

  const stopCamera = async () => {
    await fetch(API("/camera/stop"));
    setCameraOn(false);
    if (feedRef.current) feedRef.current.src = "";
    setDetections([]);
    if (pollRef.current) clearInterval(pollRef.current);
  };

  // ── Confirmation actions ────────────────────────────────────────────────────

  const handleConfirm = async () => {
    if (!activeConfirm) return;
    await fetch(API(`/confirm/${activeConfirm.face_id}`), { method: "POST" });
    setActiveConfirm(null);
    loadAttendance();
  };

  const handleIgnore = () => setActiveConfirm(null);

  // ── CSV export ──────────────────────────────────────────────────────────────

  const exportCSV = () => {
    const today = new Date().toISOString().split("T")[0];
    window.open(API(`/attendance?date=${today}`), "_blank");
  };

  // ── Init ─────────────────────────────────────────────────────────────────────

  useEffect(() => {
    loadAttendance();
    loadRegCount();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [loadAttendance, loadRegCount]);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <>
      {/* Confirmation popup */}
      {activeConfirm && (
        <ConfirmPopup
          item={activeConfirm}
          onConfirm={handleConfirm}
          onIgnore={handleIgnore}
        />
      )}

      {/* Page header */}
      <div className="flex items-start justify-between mb-5 flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold">Dashboard</h1>
          <p className="text-slate-500 text-sm mt-0.5">
            {regCount} people registered · real-time recognition
          </p>
        </div>
        <Link
          href="/enroll"
          className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white transition-colors"
        >
          + Enroll Person
        </Link>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4">

        {/* Left: live camera */}
        <div className="flex flex-col gap-4">
          <Card title={`${cameraOn ? "●" : "○"}  Live Camera`}>
            {/* Feed */}
            <img
              ref={feedRef}
              className="w-full rounded-lg bg-black min-h-[280px] object-cover"
              alt="Start camera to see live feed"
            />

            {/* Detection chips */}
            <DetectionChips detections={detections} />

            {/* Controls */}
            <div className="flex gap-2 mt-3 flex-wrap">
              <Btn color="green" onClick={startCamera} disabled={cameraOn}>
                Start Camera
              </Btn>
              <Btn color="red" onClick={stopCamera} disabled={!cameraOn}>
                Stop Camera
              </Btn>
            </div>
          </Card>
        </div>

        {/* Right: attendance */}
        <div className="flex flex-col gap-4">
          <Card title="Today's Attendance">
            <AttendanceTable records={attendance} />
            <div className="flex gap-2 mt-3">
              <Btn color="ghost" onClick={loadAttendance}>Refresh</Btn>
              <Btn color="ghost" onClick={exportCSV}>Export CSV</Btn>
            </div>
          </Card>

          {/* Quick actions */}
          <Card title="Quick Actions">
            <div className="flex flex-col gap-2">
              <Link
                href="/enroll"
                className="w-full text-center px-3 py-2 rounded-lg text-sm border border-[#2a2f45] text-slate-300 hover:bg-[#2a2f45] transition-colors"
              >
                Register New Person
              </Link>
              <Link
                href="/reports"
                className="w-full text-center px-3 py-2 rounded-lg text-sm border border-[#2a2f45] text-slate-300 hover:bg-[#2a2f45] transition-colors"
              >
                View Reports
              </Link>
            </div>
          </Card>
        </div>

      </div>
    </>
  );
}