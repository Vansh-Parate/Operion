import { Layers3 } from "lucide-react";

export default function Loading() {
  return (
    <div
      className="app-shell"
      aria-busy="true"
      aria-label="Loading Operion incident console"
    >
      {/* Sidebar Skeleton */}
      <aside className="app-sidebar hidden md:flex">
        <div className="sidebar-header">
          <div className="brand-icon-box">
            <Layers3 size={20} />
          </div>
          <div>
            <div className="brand-title">OPERION</div>
            <div className="brand-subtitle">Loading workspace...</div>
          </div>
        </div>
        <div className="p-4 space-y-3">
          <div className="h-8 bg-[#162030] rounded animate-pulse" />
          <div className="h-8 bg-[#162030] rounded animate-pulse" />
          <div className="h-8 bg-[#162030] rounded animate-pulse" />
          <div className="h-8 bg-[#162030] rounded animate-pulse" />
        </div>
      </aside>

      {/* Main Workspace Skeleton */}
      <div className="app-main">
        <div className="console-header">
          <div className="h-6 w-48 bg-[#162030] rounded animate-pulse" />
          <div className="h-6 w-24 bg-[#162030] rounded animate-pulse" />
        </div>

        <main className="workspace-container">
          {/* Banner Skeleton */}
          <div className="h-12 bg-[#121824] rounded-lg animate-pulse" />

          {/* Queue Skeleton */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="h-28 bg-[#121824] rounded-lg animate-pulse" />
            <div className="h-28 bg-[#121824] rounded-lg animate-pulse" />
            <div className="h-28 bg-[#121824] rounded-lg animate-pulse" />
            <div className="h-28 bg-[#121824] rounded-lg animate-pulse" />
          </div>

          {/* Incident Bar Skeleton */}
          <div className="h-20 bg-[#121824] rounded-lg animate-pulse" />

          {/* Columns Skeleton */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div className="h-96 bg-[#121824] rounded-lg animate-pulse" />
            <div className="h-96 bg-[#121824] rounded-lg animate-pulse" />
          </div>
        </main>
      </div>
    </div>
  );
}
