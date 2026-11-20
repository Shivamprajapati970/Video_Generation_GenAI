"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { listProjects, deleteProject, Project, getMe } from "@/lib/api";
import Header from "@/components/Header";

export default function DashboardPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isAuthError, setIsAuthError] = useState(false);

  const fetchProjects = async () => {
    try {
      setLoading(true);
      setError(null);
      setIsAuthError(false);

      // Verify user session first
      try {
        await getMe();
      } catch (authErr: any) {
        setIsAuthError(true);
        setError("Your session has expired or you are not signed in.");
        setLoading(false);
        return;
      }

      const data = await listProjects();
      setProjects(data);
    } catch (err: any) {
      const msg = err.message || "Failed to load projects";
      setError(msg);
      if (msg.toLowerCase().includes("credentials") || msg.toLowerCase().includes("unauthorized") || msg.toLowerCase().includes("401")) {
        setIsAuthError(true);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProjects();
  }, []);

  const handleDelete = async (id: string, title: string) => {
    if (!confirm(`Are you sure you want to delete '${title}' and all its uploaded images?`)) {
      return;
    }
    try {
      await deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (err: any) {
      alert(err.message || "Failed to delete project");
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Header />

      {/* Main Content */}
      <main className="flex-1 max-w-6xl mx-auto px-6 py-10 w-full">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-6 border-b border-slate-800/80 gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white">Your Video Projects</h1>
            <p className="text-slate-400 text-xs mt-1">Manage created video projects and uploaded reference photos.</p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={fetchProjects}
              className="text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-lg border border-slate-800 hover:border-slate-700 transition-colors"
            >
              Refresh
            </button>
            <Link
              href="/create"
              className="text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white px-3.5 py-1.5 rounded-lg transition-all shadow-md shadow-indigo-600/20"
            >
              + Create New Video
            </Link>
          </div>
        </div>

        {error && (
          <div className="my-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <div>
              <span className="font-semibold text-white">Authentication / Request Note:</span> {error}
            </div>
            {isAuthError && (
              <Link
                href="/login"
                className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-all shadow-md flex-shrink-0"
              >
                Sign In Now &rarr;
              </Link>
            )}
          </div>
        )}

        {loading ? (
          <div className="py-20 text-center text-slate-500 text-sm flex flex-col items-center gap-3">
            <span className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            Loading projects...
          </div>
        ) : projects.length === 0 ? (
          <div className="py-20 text-center bg-slate-900/30 border border-dashed border-slate-800 rounded-3xl mt-6 p-10">
            <div className="text-4xl mb-3">🎬</div>
            <h2 className="text-lg font-semibold text-white">No video projects yet</h2>
            <p className="text-slate-400 text-xs mt-1 max-w-md mx-auto">
              Start by uploading up to 5 reference photos and configuring your personalized video duration and music mood.
            </p>
            <Link
              href="/create"
              className="inline-block mt-6 px-6 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs shadow-lg shadow-indigo-600/20 transition-all"
            >
              Create Your First Project &rarr;
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 mt-6">
            {projects.map((p) => (
              <div
                key={p.id}
                className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-5 flex flex-col justify-between hover:border-slate-700 transition-all group"
              >
                <div>
                  <div className="flex items-center justify-between text-xs mb-3">
                    <span className="px-2.5 py-0.5 rounded-md bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 font-medium">
                      {p.occasion}
                    </span>
                    <span className="px-2 py-0.5 rounded-md bg-slate-800 text-slate-400 font-mono text-[11px]">
                      {p.status}
                    </span>
                  </div>

                  <h3 className="font-semibold text-base text-white group-hover:text-indigo-400 transition-colors truncate">
                    {p.title}
                  </h3>
                  {p.description && (
                    <p className="text-xs text-slate-400 mt-1 line-clamp-2 leading-relaxed">
                      {p.description}
                    </p>
                  )}

                  {/* Creation Date */}
                  <div className="text-[11px] text-slate-500 mt-2 flex items-center gap-1.5 font-mono">
                    <span>📅</span>
                    <span>
                      {p.created_at
                        ? new Date(p.created_at).toLocaleDateString(undefined, {
                            year: "numeric",
                            month: "short",
                            day: "numeric",
                          })
                        : "Recently created"}
                    </span>
                  </div>

                  {/* Metadata pills */}
                  <div className="flex flex-wrap gap-2 mt-4 text-[11px] text-slate-400">
                    <span className="px-2 py-1 rounded-lg bg-slate-950 border border-slate-800">
                      ⏱️ {p.duration_seconds}s
                    </span>
                    <span className="px-2 py-1 rounded-lg bg-slate-950 border border-slate-800">
                      📐 {p.aspect_ratio}
                    </span>
                    <span className="px-2 py-1 rounded-lg bg-slate-950 border border-slate-800">
                      🎵 {p.music_mood}
                    </span>
                    <span className="px-2 py-1 rounded-lg bg-slate-950 border border-slate-800 font-mono">
                      🖼️ {p.image_count} / 5 photos
                    </span>
                  </div>
                </div>

                <div className="mt-6 pt-4 border-t border-slate-800/60 flex items-center justify-between">
                  <Link
                    href={`/projects/${p.id}`}
                    className="text-xs font-semibold text-indigo-400 hover:text-indigo-300 transition-colors"
                  >
                    View Details & Photos &rarr;
                  </Link>
                  <button
                    onClick={() => handleDelete(p.id, p.title)}
                    className="text-xs text-red-400 hover:text-red-300 px-2 py-1 rounded hover:bg-red-500/10 transition-colors cursor-pointer"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
