"use client";

import { useState, useEffect, useCallback } from "react";

/* ---------- Types ---------- */

interface ReportItem {
  title: string;
  summary: string;
  url?: string;
  source: string;
  category?: string;
  relevance?: number;
  tags?: string[];
  score?: number;
}

interface Report {
  date: string;
  executive_summary: string;
  items: ReportItem[];
  generated_at?: string;
}

interface ReportListEntry {
  date: string;
  title?: string;
}

/* ---------- Source colours ---------- */

const SOURCE_COLORS: Record<string, string> = {
  "hacker news": "#22c55e",
  "github trending": "#a78bfa",
  "product hunt": "#f59e0b",
  "arxiv": "#38bdf8",
  "reddit": "#f97316",
  "rss精选": "#6366f1",
  default: "#e94560",
};

function sourceColor(type: string): string {
  return SOURCE_COLORS[type.toLowerCase()] ?? SOURCE_COLORS.default;
}

/* ---------- Component ---------- */

export default function Dashboard() {
  const [reports, setReports] = useState<ReportListEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggerLoading, setTriggerLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* Fetch report list */
  const fetchReports = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/reports");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: ReportListEntry[] = await res.json();
      setReports(data);
      if (data.length > 0 && !selected) {
        setSelected(data[0].date);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [selected]);

  /* Fetch single report */
  useEffect(() => {
    fetchReports();
  }, [fetchReports]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/reports/${selected}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: Report = await res.json();
        if (!cancelled) setReport(data);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  /* Trigger new run */
  const handleTrigger = async () => {
    setTriggerLoading(true);
    try {
      const res = await fetch("/api/trigger", { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Refresh list after a short delay to allow processing to start
      setTimeout(() => fetchReports(), 2000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTriggerLoading(false);
    }
  };

  /* Group items by source */
  const groupedItems: Record<string, ReportItem[]> = {};
  if (report?.items) {
    for (const item of report.items) {
      const key = item.source || "Unknown";
      if (!groupedItems[key]) groupedItems[key] = [];
      groupedItems[key].push(item);
    }
  }

  /* ---------- Render ---------- */

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ---- Sidebar ---- */}
      <aside
        className="w-72 flex-shrink-0 flex flex-col border-r overflow-y-auto"
        style={{
          backgroundColor: "#1a1a2e",
          borderColor: "#334155",
        }}
      >
        {/* Logo / Title */}
        <div
          className="px-5 py-6 border-b"
          style={{ borderColor: "#334155" }}
        >
          <h1 className="text-xl font-bold tracking-tight" style={{ color: "#e94560" }}>
            AI Daily Digest
          </h1>
          <p className="text-xs mt-1" style={{ color: "#94a3b8" }}>
            RSS-Notion Intelligence Feed
          </p>
        </div>

        {/* Trigger button */}
        <div className="px-4 py-3">
          <button
            onClick={handleTrigger}
            disabled={triggerLoading}
            className="w-full py-2 px-4 rounded-lg text-sm font-medium transition-colors cursor-pointer disabled:opacity-50"
            style={{
              backgroundColor: "#e94560",
              color: "#ffffff",
            }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.backgroundColor = "#d63d56")
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.backgroundColor = "#e94560")
            }
          >
            {triggerLoading ? "Running..." : "Trigger New Run"}
          </button>
        </div>

        {/* Date list */}
        <nav className="flex-1 px-3 py-2 space-y-1">
          {loading && (
            <p className="text-sm px-3 py-2" style={{ color: "#94a3b8" }}>
              Loading...
            </p>
          )}
          {reports.map((r) => {
            const isActive = r.date === selected;
            return (
              <button
                key={r.date}
                onClick={() => setSelected(r.date)}
                className="w-full text-left px-3 py-2.5 rounded-lg text-sm transition-colors cursor-pointer"
                style={{
                  backgroundColor: isActive ? "#0f3460" : "transparent",
                  color: isActive ? "#ffffff" : "#94a3b8",
                  borderLeft: isActive ? "3px solid #e94560" : "3px solid transparent",
                }}
                onMouseEnter={(e) => {
                  if (!isActive)
                    e.currentTarget.style.backgroundColor = "#16213e";
                }}
                onMouseLeave={(e) => {
                  if (!isActive)
                    e.currentTarget.style.backgroundColor = "transparent";
                }}
              >
                <span className="font-medium">{r.date}</span>
                {r.title && (
                  <span className="block text-xs mt-0.5 opacity-70 truncate">
                    {r.title}
                  </span>
                )}
              </button>
            );
          })}
          {!loading && reports.length === 0 && (
            <p className="text-sm px-3 py-2" style={{ color: "#94a3b8" }}>
              No reports yet.
            </p>
          )}
        </nav>
      </aside>

      {/* ---- Main content ---- */}
      <main className="flex-1 overflow-y-auto p-8" style={{ backgroundColor: "#0f172a" }}>
        {error && (
          <div
            className="mb-6 px-4 py-3 rounded-lg text-sm"
            style={{ backgroundColor: "#7f1d1d", color: "#fca5a5" }}
          >
            Error: {error}
            <button
              className="ml-3 underline cursor-pointer"
              onClick={() => setError(null)}
            >
              dismiss
            </button>
          </div>
        )}

        {!selected && !loading && (
          <div className="flex items-center justify-center h-full">
            <p className="text-lg" style={{ color: "#94a3b8" }}>
              Select a report from the sidebar, or trigger a new run.
            </p>
          </div>
        )}

        {selected && report && (
          <>
            {/* Header */}
            <div className="flex items-start justify-between mb-8">
              <div>
                <h2 className="text-2xl font-bold tracking-tight">
                  Digest for{" "}
                  <span style={{ color: "#e94560" }}>{report.date}</span>
                </h2>
                {report.generated_at && (
                  <p className="text-xs mt-1" style={{ color: "#94a3b8" }}>
                    Generated {report.generated_at}
                  </p>
                )}
              </div>
              <a
                href={`/api/reports/${report.date}/pdf`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                style={{
                  backgroundColor: "#0f3460",
                  color: "#e2e8f0",
                  border: "1px solid #334155",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.backgroundColor = "#1a4a7a")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.backgroundColor = "#0f3460")
                }
              >
                <svg
                  width="16"
                  height="16"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M12 10v6m0 0l-3-3m3 3l3-3M3 17v3a2 2 0 002 2h14a2 2 0 002-2v-3"
                  />
                </svg>
                Download PDF
              </a>
            </div>

            {/* Executive Summary */}
            <section
              className="rounded-xl p-6 mb-8"
              style={{
                backgroundColor: "#1e293b",
                border: "1px solid #334155",
              }}
            >
              <h3
                className="text-xs font-semibold uppercase tracking-wider mb-3"
                style={{ color: "#e94560" }}
              >
                Executive Summary
              </h3>
              <p className="leading-relaxed" style={{ color: "#cbd5e1" }}>
                {report.executive_summary}
              </p>
            </section>

            {/* Items grouped by source */}
            {Object.entries(groupedItems).map(([source, items]) => {
              const color = sourceColor(source);
              return (
                <section key={source} className="mb-8">
                  <div className="flex items-center gap-3 mb-4">
                    <span
                      className="inline-block w-3 h-3 rounded-full"
                      style={{ backgroundColor: color }}
                    />
                    <h3 className="text-lg font-semibold">{source}</h3>
                    <span
                      className="text-xs px-2 py-0.5 rounded-full"
                      style={{
                        backgroundColor: color + "20",
                        color: color,
                      }}
                    >
                      {items.length} item{items.length > 1 ? "s" : ""}
                    </span>
                  </div>
                  <div className="grid gap-4">
                    {items.map((item, idx) => (
                      <article
                        key={idx}
                        className="rounded-lg p-5 transition-colors"
                        style={{
                          backgroundColor: "#1e293b",
                          border: "1px solid #334155",
                          borderLeft: `3px solid ${color}`,
                        }}
                        onMouseEnter={(e) =>
                          (e.currentTarget.style.backgroundColor = "#253449")
                        }
                        onMouseLeave={(e) =>
                          (e.currentTarget.style.backgroundColor = "#1e293b")
                        }
                      >
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex-1 min-w-0">
                            <h4 className="font-medium text-sm leading-snug mb-2">
                              {item.url ? (
                                <a
                                  href={item.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="hover:underline"
                                  style={{ color: "#e2e8f0" }}
                                >
                                  {item.title}
                                </a>
                              ) : (
                                item.title
                              )}
                            </h4>
                            <p
                              className="text-sm leading-relaxed"
                              style={{ color: "#94a3b8" }}
                            >
                              {item.summary}
                            </p>
                          </div>
                          {item.url && (
                            <a
                              href={item.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex-shrink-0 mt-1"
                              style={{ color: "#64748b" }}
                            >
                              <svg
                                width="14"
                                height="14"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth={2}
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                                />
                              </svg>
                            </a>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
              );
            })}

            {report.items?.length === 0 && (
              <p style={{ color: "#94a3b8" }}>
                No items in this report.
              </p>
            )}
          </>
        )}
      </main>
    </div>
  );
}
