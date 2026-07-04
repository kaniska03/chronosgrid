import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useLiveEvents } from "../hooks/useWebSocket";

const NAV = [
  { to: "/", label: "Overview", icon: "◫" },
  { to: "/queues", label: "Queues", icon: "☰" },
  { to: "/jobs", label: "Jobs", icon: "⚙" },
  { to: "/workflows", label: "Workflows", icon: "⇶" },
  { to: "/workers", label: "Workers", icon: "▣" },
  { to: "/recurring", label: "Recurring", icon: "↻" },
  { to: "/dlq", label: "Dead Letters", icon: "☠" },
  { to: "/audit", label: "Audit Log", icon: "≡" },
  { to: "/settings", label: "Settings", icon: "✦" },
];

export default function Layout() {
  const { org, project, projects, selectProject, logout } = useAuth();
  const { connected } = useLiveEvents();
  const [open, setOpen] = useState(false);

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className={`fixed inset-y-0 left-0 z-30 w-60 transform border-r border-slate-200 bg-white transition-transform lg:static lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex h-14 items-center gap-2 border-b border-slate-200 px-4">
          <span className="text-lg font-bold text-brand-700">⏱ ChronosGrid</span>
        </div>
        <div className="border-b border-slate-100 p-3">
          <div className="text-xs text-slate-400">{org?.name ?? ""}</div>
          <select
            aria-label="project"
            className="input mt-1"
            value={project?.id ?? ""}
            onChange={(e) => {
              const p = projects.find((x) => x.id === e.target.value);
              if (p) selectProject(p);
            }}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <nav className="p-2">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${isActive ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-50"}`}>
              <span className="w-4 text-center">{item.icon}</span> {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="absolute bottom-0 w-full border-t border-slate-100 p-3">
          <button className="btn-secondary w-full justify-center" onClick={logout}>Sign out</button>
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-20 bg-black/30 lg:hidden" onClick={() => setOpen(false)} />}

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-slate-200 bg-white px-4">
          <button className="btn-secondary lg:hidden" onClick={() => setOpen(true)}>☰</button>
          <div className="hidden text-sm text-slate-500 lg:block">
            {project ? `${project.name} · ${project.slug}` : ""}
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-amber-500"}`} />
            <span className="text-slate-500">{connected ? "Live" : "Polling"}</span>
          </div>
        </header>
        <main className="flex-1 p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
