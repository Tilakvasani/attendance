import "./globals.css";
import Link from "next/link";

export const metadata = {
  title:       "Face Attendance System",
  description: "Real-time face recognition attendance powered by DeepFace + ArcFace",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#0f1117] text-slate-200">

        {/* ── Top navigation ──────────────────────────────────────── */}
        <nav className="border-b border-[#2a2f45] bg-[#0f1117] sticky top-0 z-40">
          <div className="max-w-6xl mx-auto px-4 h-14 flex items-center justify-between">

            {/* Brand */}
            <Link href="/" className="flex items-center gap-2 font-semibold text-sm">
              <span className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center text-white text-xs font-bold">
                FA
              </span>
              Face Attendance
            </Link>

            {/* Links */}
            <div className="flex items-center gap-1">
              <NavLink href="/">Dashboard</NavLink>
              <NavLink href="/enroll">Enroll</NavLink>
              <NavLink href="/reports">Reports</NavLink>
            </div>
          </div>
        </nav>

        {/* ── Page content ────────────────────────────────────────── */}
        <main className="max-w-6xl mx-auto px-4 py-6">
          {children}
        </main>

      </body>
    </html>
  );
}

function NavLink({ href, children }) {
  return (
    <Link
      href={href}
      className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 hover:bg-[#1a1f2e] transition-colors"
    >
      {children}
    </Link>
  );
}
