"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getMe, logout } from "@/lib/api";

interface UserProfile {
  id: string;
  email: string;
  full_name: string;
}

interface HeaderProps {
  projectTitle?: string;
  showBack?: boolean;
  backHref?: string;
  backLabel?: string;
  rightAction?: React.ReactNode;
}

export default function Header({
  projectTitle,
  showBack = false,
  backHref = "/dashboard",
  backLabel = "Dashboard",
  rightAction,
}: HeaderProps) {
  const router = useRouter();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    let mounted = true;
    getMe()
      .then((data) => {
        if (mounted && data?.id) {
          setUser(data);
        }
      })
      .catch(() => {
        if (mounted) setUser(null);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const handleLogout = async () => {
    try {
      setLoggingOut(true);
      await logout();
      setUser(null);
      router.push("/login");
    } catch (err) {
      console.error("Logout failed:", err);
      router.push("/login");
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-md sticky top-0 z-50">
      <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between gap-4">
        {/* Left: Brand or Back Navigation */}
        <div className="flex items-center gap-3 min-w-0">
          {showBack ? (
            <>
              <Link
                href={backHref}
                className="text-slate-400 hover:text-white text-xs px-2.5 py-1.5 rounded-lg border border-slate-800 hover:border-slate-700 transition-colors flex items-center gap-1.5 flex-shrink-0"
              >
                <span>&larr;</span> {backLabel}
              </Link>
              {projectTitle && (
                <>
                  <span className="text-slate-700 flex-shrink-0">/</span>
                  <span className="font-semibold text-sm text-white truncate max-w-[200px] sm:max-w-xs">
                    {projectTitle}
                  </span>
                </>
              )}
            </>
          ) : (
            <Link href={user ? "/dashboard" : "/"} className="flex items-center gap-2.5 flex-shrink-0">
              <div className="h-8 w-8 rounded-lg bg-gradient-to-tr from-indigo-600 to-violet-600 flex items-center justify-center font-bold text-sm text-white shadow-md shadow-indigo-600/30">
                V
              </div>
              <span className="font-semibold text-sm sm:text-base text-white tracking-tight">
                AI Video Studio
              </span>
            </Link>
          )}
        </div>

        {/* Right: Actions, User Info & Logout */}
        <div className="flex items-center gap-3 flex-shrink-0">
          {rightAction}

          {loading ? (
            <div className="w-16 h-8 bg-slate-900 rounded-lg animate-pulse" />
          ) : user ? (
            <div className="flex items-center gap-2.5">
              <Link
                href="/dashboard"
                className="hidden sm:inline-block text-xs font-medium text-slate-300 hover:text-white px-2.5 py-1.5 rounded-lg hover:bg-slate-900 transition-colors"
              >
                Projects
              </Link>
              <Link
                href="/create"
                className="hidden sm:inline-flex text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg transition-all shadow-md shadow-indigo-600/20"
              >
                + New
              </Link>

              {/* User email badge */}
              <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-900 border border-slate-800 text-[11px] text-slate-300">
                <span className="w-2 h-2 rounded-full bg-emerald-400" />
                <span className="max-w-[140px] truncate">{user.email}</span>
              </div>

              {/* Logout Button */}
              <button
                onClick={handleLogout}
                disabled={loggingOut}
                className="text-xs text-slate-400 hover:text-red-300 px-2.5 py-1.5 rounded-lg border border-slate-800 hover:border-red-500/40 hover:bg-red-500/10 transition-colors flex items-center gap-1 cursor-pointer disabled:opacity-50"
                title="Sign out of account"
              >
                {loggingOut ? (
                  <span className="w-3 h-3 border border-red-400 border-t-transparent rounded-full animate-spin" />
                ) : (
                  <span>Sign Out</span>
                )}
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Link
                href="/login"
                className="text-xs font-medium text-slate-300 hover:text-white px-3 py-1.5 rounded-lg hover:bg-slate-900 transition-colors"
              >
                Sign In
              </Link>
              <Link
                href="/register"
                className="text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg transition-all shadow-md shadow-indigo-600/20"
              >
                Get Started
              </Link>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
