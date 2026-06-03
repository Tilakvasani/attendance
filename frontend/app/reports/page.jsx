"use client";

import { useCallback, useEffect, useState } from "react";
import Card from "../components/Card";

const API = (path) => `/api${path}`;

// ── Attendance table ──────────────────────────────────────────────────────────

function AttendanceTable({ records, loading }) {
  if (loading)
    return <p className="text-slate-500 text-sm py-4 text-center">Loading…</p>;

  if (!records.length)
    return <p className="text-slate-500 text-sm py-4 text-center">No records for this date.</p>;

  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr>
          {["Name", "Date", "Time", "Confidence", "Status"].map((h) => (
            <th key={h} className="text-left py-2 px-3 text-xs text-slate-500 uppercase tracking-wide border-b border-[#2a2f45]">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {records.map((r, i) => (
          <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#0f1117]/60 transition-colors">
            <td className="py-2.5 px-3 font-medium">{r.name.replace(/_/g, " ")}</td>
            <td className="py-2.5 px-3 text-slate-400">{r.date}</td>
            <td className="py-2.5 px-3 text-slate-400">{r.time}</td>
            <td className="py-2.5 px-3 text-slate-400">{Math.round(r.confidence * 100)}%</td>
            <td className="py-2.5 px-3">
              <span className="px-2 py-0.5 rounded-full text-xs bg-green-900/40 text-green-300 border border-green-800">
                {r.status}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Registered people ─────────────────────────────────────────────────────────

function RegisteredList({ people, onDelete }) {
  if (!people.length)
    return <p className="text-slate-500 text-sm">No people registered yet.</p>;

  return (
    <div className="flex flex-col gap-2">
      {people.map((name) => (
        <div key={name} className="flex items-center justify-between px-3 py-2 rounded-lg bg-[#0f1117] border border-[#2a2f45]">
          <span className="text-sm">{name.replace(/_/g, " ")}</span>
          <button
            onClick={() => onDelete(name)}
            className="text-xs text-red-400 hover:text-red-300 transition-colors px-2 py-0.5 rounded border border-red-900/50 hover:bg-red-900/20"
          >
            Delete
          </button>
        </div>
      ))}
    </div>
  );
}

// ── Stats cards ───────────────────────────────────────────────────────────────

function StatCard({ value, label, color = "blue" }) {
  const colors = {
    blue:  "text-blue-400",
    green: "text-green-400",
    amber: "text-amber-400",
  };
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f45] rounded-xl p-4 text-center">
      <div className={`text-3xl font-bold mb-1 ${colors[color]}`}>{value}</div>
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
    </div>
  );
}

// ── Main Reports Page ─────────────────────────────────────────────────────────

export default function ReportsPage() {
  const today   = new Date().toISOString().split("T")[0];

  const [date,      setDate]      = useState(today);
  const [records,   setRecords]   = useState([]);
  const [loading,   setLoading]   = useState(false);
  const [people,    setPeople]    = useState([]);
  const [deleteMsg, setDeleteMsg] = useState(null);

  // ── Fetch attendance ─────────────────────────────────────────────────────────

  const loadRecords = useCallback(async (d) => {
    setLoading(true);
    try {
      const res  = await fetch(API(`/attendance?date=${d}`));
      const data = await res.json();
      setRecords(data.records ?? []);
    } catch {
      setRecords([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Fetch registered people ──────────────────────────────────────────────────

  const loadPeople = useCallback(async () => {
    try {
      const res  = await fetch(API("/registered"));
      const data = await res.json();
      setPeople(data.registered ?? []);
    } catch {
      setPeople([]);
    }
  }, []);

  useEffect(() => {
    loadRecords(date);
    loadPeople();
  }, [date, loadRecords, loadPeople]);

  // ── Delete person ────────────────────────────────────────────────────────────

  const handleDelete = async (name) => {
    if (!window.confirm(`Delete ${name.replace(/_/g, " ")} from the system?`)) return;
    try {
      const res  = await fetch(API(`/delete/${name}`), { method: "DELETE" });
      const data = await res.json();
      if (data.deleted) {
        setDeleteMsg({ type: "ok",  msg: `Deleted ${name}.` });
        loadPeople();
      } else {
        setDeleteMsg({ type: "err", msg: "Could not delete." });
      }
    } catch {
      setDeleteMsg({ type: "err", msg: "Network error." });
    }
    setTimeout(() => setDeleteMsg(null), 3000);
  };

  // ── CSV export ───────────────────────────────────────────────────────────────

  const exportCSV = () => window.open(API(`/attendance?date=${date}`), "_blank");

  // ── Stats ────────────────────────────────────────────────────────────────────

  const uniquePresent  = new Set(records.map((r) => r.name)).size;
  const avgConf        = records.length
    ? Math.round(records.reduce((s, r) => s + r.confidence, 0) / records.length * 100)
    : 0;

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <>
      <div className="mb-5">
        <h1 className="text-xl font-semibold">Reports</h1>
        <p className="text-slate-500 text-sm mt-0.5">Attendance history and people management</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">

        {/* Left: attendance records */}
        <div className="flex flex-col gap-4">

          {/* Stats row */}
          <div className="grid grid-cols-3 gap-3">
            <StatCard value={people.length} label="Registered"  color="blue"  />
            <StatCard value={uniquePresent}  label="Present today" color="green" />
            <StatCard value={`${avgConf}%`}  label="Avg confidence" color="amber" />
          </div>

          {/* Date filter + export */}
          <Card title="Attendance Records">
            <div className="flex items-center gap-3 mb-4 flex-wrap">
              <input
                type="date"
                value={date}
                max={today}
                onChange={(e) => setDate(e.target.value)}
                className="px-3 py-1.5 rounded-lg bg-[#0f1117] border border-[#2a2f45] text-slate-200 text-sm focus:outline-none focus:border-blue-500"
              />
              <button
                onClick={exportCSV}
                className="px-3 py-1.5 rounded-lg text-sm border border-[#2a2f45] text-slate-300 hover:bg-[#2a2f45] transition-colors"
              >
                Export CSV
              </button>
              <span className="text-slate-500 text-xs ml-auto">
                {records.length} record{records.length !== 1 ? "s" : ""}
              </span>
            </div>

            <div className="overflow-x-auto">
              <AttendanceTable records={records} loading={loading} />
            </div>
          </Card>
        </div>

        {/* Right: registered people */}
        <div>
          <Card title="Registered People">
            {deleteMsg && (
              <p className={`text-sm mb-3 px-3 py-2 rounded-lg ${
                deleteMsg.type === "ok"
                  ? "bg-green-900/40 text-green-300"
                  : "bg-red-900/40 text-red-300"
              }`}>
                {deleteMsg.msg}
              </p>
            )}
            <RegisteredList people={people} onDelete={handleDelete} />
            <button
              onClick={loadPeople}
              className="mt-3 w-full py-1.5 rounded-lg text-xs border border-[#2a2f45] text-slate-500 hover:bg-[#2a2f45] transition-colors"
            >
              Refresh list
            </button>
          </Card>
        </div>

      </div>
    </>
  );
}