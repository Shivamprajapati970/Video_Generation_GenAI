"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createProject, uploadProjectImages, Project, getMe } from "@/lib/api";
import Header from "@/components/Header";

const OCCASIONS = [
  { id: "Birthday", label: "Birthday", emoji: "🎂", desc: "Joyful highlights & birthday party vibes" },
  { id: "Anniversary", label: "Anniversary", emoji: "💍", desc: "Romantic cinematic narrative" },
  { id: "Wedding", label: "Wedding", emoji: "🕊️", desc: "Grand, elegant visual storytelling" },
  { id: "Travel", label: "Travel", emoji: "✈️", desc: "Dynamic motion & adventure montage" },
  { id: "Celebration", label: "Celebration", emoji: "🎉", desc: "Festival beats & fireworks energy" },
  { id: "Other", label: "Other", emoji: "✨", desc: "Custom theme & flexible style" },
];

const DURATIONS = [
  { seconds: 15, label: "15 Seconds", scenes: "3 scenes (5s each)" },
  { seconds: 20, label: "20 Seconds", scenes: "4 scenes (5s each)" },
  { seconds: 30, label: "30 Seconds", scenes: "6 scenes (5s each)" },
];

const ASPECT_RATIOS = [
  { id: "9:16", label: "9:16 Portrait", sub: "Reels, TikTok & Shorts", icon: "📱" },
  { id: "16:9", label: "16:9 Landscape", sub: "YouTube & Desktop", icon: "🖥️" },
  { id: "1:1", label: "1:1 Square", sub: "Instagram Posts", icon: "⏹️" },
];

const MOODS = [
  "Happy",
  "Romantic",
  "Emotional",
  "Cinematic",
  "Calm",
  "Celebration",
  "Custom prompt",
];

export default function CreateProjectPage() {
  const router = useRouter();

  // Form State
  const [title, setTitle] = useState("My Special Video");
  const [occasion, setOccasion] = useState("Birthday");
  const [duration, setDuration] = useState(15);
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [musicMood, setMusicMood] = useState("Happy");
  const [customMusicPrompt, setCustomMusicPrompt] = useState("");
  const [description, setDescription] = useState("");

  // Uploaded Files State
  const [selectedFiles, setSelectedFiles] = useState<{ file: File; preview: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [uploadStep, setUploadStep] = useState<string>("");
  const [createdProject, setCreatedProject] = useState<Project | null>(null);
  const [isAuthError, setIsAuthError] = useState(false);

  useEffect(() => {
    getMe().catch(() => {
      setIsAuthError(true);
    });
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    setError(null);
    if (!e.target.files) return;

    const newFiles = Array.from(e.target.files);
    const validExtensions = [".jpg", ".jpeg", ".png", ".webp"];

    if (selectedFiles.length + newFiles.length > 5) {
      setError(`Maximum 5 images allowed per project. You already have ${selectedFiles.length}.`);
      return;
    }

    const processed: { file: File; preview: string }[] = [];
    for (const f of newFiles) {
      const ext = f.name.substring(f.name.lastIndexOf(".")).toLowerCase();
      if (!validExtensions.includes(ext)) {
        setError(`File '${f.name}' has an unsupported format. Allowed: JPG, JPEG, PNG, WEBP.`);
        return;
      }
      if (f.size > 25 * 1024 * 1024) {
        setError(`File '${f.name}' exceeds the 25MB limit.`);
        return;
      }
      processed.push({
        file: f,
        preview: URL.createObjectURL(f),
      });
    }

    setSelectedFiles((prev) => [...prev, ...processed]);
    e.target.value = "";
  };

  const removeFile = (index: number) => {
    setSelectedFiles((prev) => {
      const updated = [...prev];
      URL.revokeObjectURL(updated[index].preview);
      updated.splice(index, 1);
      return updated;
    });
  };

  const moveFileLeft = (index: number) => {
    if (index === 0) return;
    setSelectedFiles((prev) => {
      const copy = [...prev];
      const temp = copy[index - 1];
      copy[index - 1] = copy[index];
      copy[index] = temp;
      return copy;
    });
  };

  const moveFileRight = (index: number) => {
    if (index === selectedFiles.length - 1) return;
    setSelectedFiles((prev) => {
      const copy = [...prev];
      const temp = copy[index + 1];
      copy[index + 1] = copy[index];
      copy[index] = temp;
      return copy;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (selectedFiles.length === 0) {
      setError("Please select at least 1 reference image (up to 5).");
      return;
    }

    setIsSubmitting(true);
    try {
      // 1. Create Project
      setUploadStep("Creating video project record...");
      const project = await createProject({
        title,
        occasion,
        duration_seconds: duration,
        aspect_ratio: aspectRatio,
        music_mood: musicMood,
        custom_music_prompt: musicMood === "Custom prompt" ? customMusicPrompt : undefined,
        description: description.trim() || undefined,
      });

      // 2. Upload Reference Images
      setUploadStep(`Uploading ${selectedFiles.length} reference images to secure storage...`);
      const rawFiles = selectedFiles.map((sf) => sf.file);
      await uploadProjectImages(project.id, rawFiles);

      setCreatedProject(project);
      setUploadStep("Completed!");
    } catch (err: any) {
      setError(err.message || "Failed to create project and upload images");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (createdProject) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center px-4 py-12">
        <div className="max-w-xl w-full bg-slate-900 border border-emerald-500/30 rounded-3xl p-8 text-center shadow-2xl shadow-emerald-500/10">
          <div className="w-16 h-16 bg-emerald-500/20 text-emerald-400 rounded-full flex items-center justify-center mx-auto text-3xl mb-4">
            ✓
          </div>
          <h1 className="text-2xl font-bold text-white">Project & Images Uploaded!</h1>
          <p className="text-slate-400 mt-2 text-sm">
            Your project <span className="text-emerald-400 font-semibold">{createdProject.title}</span> has been created with{" "}
            {selectedFiles.length} reference photo{selectedFiles.length > 1 ? "s" : ""} saved in storage.
          </p>

          <div className="mt-6 bg-slate-950/70 rounded-2xl p-5 text-left text-xs space-y-2.5 border border-slate-800">
            <div className="flex justify-between text-slate-300">
              <span className="text-slate-500">Occasion:</span>
              <span className="font-semibold text-white">{createdProject.occasion}</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span className="text-slate-500">Duration:</span>
              <span className="font-semibold text-white">{createdProject.duration_seconds} Seconds</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span className="text-slate-500">Aspect Ratio:</span>
              <span className="font-semibold text-white">{createdProject.aspect_ratio}</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span className="text-slate-500">Soundtrack Mood:</span>
              <span className="font-semibold text-white">{createdProject.music_mood}</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span className="text-slate-500">Status:</span>
              <span className="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 font-medium text-[11px]">
                {createdProject.status}
              </span>
            </div>
          </div>

          <div className="mt-8 flex flex-col sm:flex-row gap-3 justify-center">
            <Link
              href={`/projects/${createdProject.id}`}
              className="px-6 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all"
            >
              View Project Details & Images
            </Link>
            <Link
              href="/dashboard"
              className="px-6 py-3 rounded-xl border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium transition-all"
            >
              Go to Dashboard
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Header showBack backHref="/dashboard" backLabel="Dashboard" />

      {/* Main Wizard Form */}
      <main className="flex-1 max-w-4xl mx-auto px-6 py-10 w-full">
        {isAuthError && (
          <div className="mb-6 p-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="text-base">⚠️</span>
              <span>You are not signed in. Please sign in to create and save video projects.</span>
            </div>
            <Link
              href="/login"
              className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-all shadow-md flex-shrink-0"
            >
              Sign In &rarr;
            </Link>
          </div>
        )}
        <div className="mb-8">
          <span className="text-xs font-semibold text-indigo-400 uppercase tracking-wider">Step 1 & 2</span>
          <h1 className="text-3xl font-extrabold text-white mt-1">Create Video Project & Upload Photos</h1>
          <p className="text-slate-400 text-sm mt-1">
            Configure your video parameters and upload up to 5 reference photos.
          </p>
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-sm flex items-start gap-3">
            <span className="text-lg">⚠️</span>
            <div className="flex-1">{error}</div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-10">
          {/* Section 1: Project Title */}
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6">
            <label className="block text-sm font-semibold text-white mb-2">Video Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              className="w-full px-4 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 text-sm"
              placeholder="e.g., Sarah's 30th Birthday Celebration"
            />
          </div>

          {/* Section 2: Occasion */}
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6">
            <label className="block text-sm font-semibold text-white mb-1">Select Occasion</label>
            <p className="text-xs text-slate-400 mb-4">Informs the AI scene pacing and motion transitions.</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {OCCASIONS.map((occ) => (
                <button
                  type="button"
                  key={occ.id}
                  onClick={() => setOccasion(occ.id)}
                  className={`p-4 rounded-xl border text-left transition-all ${
                    occasion === occ.id
                      ? "border-indigo-500 bg-indigo-600/15 ring-2 ring-indigo-500/30 text-white"
                      : "border-slate-800 bg-slate-950/60 text-slate-300 hover:border-slate-700"
                  }`}
                >
                  <div className="text-2xl mb-1">{occ.emoji}</div>
                  <div className="font-semibold text-sm">{occ.label}</div>
                  <div className="text-[11px] text-slate-400 mt-1 leading-snug">{occ.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Section 3: Duration & Aspect Ratio */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Duration */}
            <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6">
              <label className="block text-sm font-semibold text-white mb-1">Video Duration</label>
              <p className="text-xs text-slate-400 mb-4">Each scene clip is precisely 5 seconds.</p>
              <div className="grid grid-cols-3 gap-2">
                {DURATIONS.map((dur) => (
                  <button
                    type="button"
                    key={dur.seconds}
                    onClick={() => setDuration(dur.seconds)}
                    className={`py-3 px-2 rounded-xl border text-center transition-all ${
                      duration === dur.seconds
                        ? "border-indigo-500 bg-indigo-600/20 text-white font-bold"
                        : "border-slate-800 bg-slate-950/60 text-slate-400 hover:border-slate-700"
                    }`}
                  >
                    <div className="text-sm">{dur.label}</div>
                    <div className="text-[10px] text-slate-400 mt-1">{dur.scenes}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Aspect Ratio */}
            <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6">
              <label className="block text-sm font-semibold text-white mb-1">Aspect Ratio</label>
              <p className="text-xs text-slate-400 mb-4">Formatted for target social media playback.</p>
              <div className="grid grid-cols-3 gap-2">
                {ASPECT_RATIOS.map((ratio) => (
                  <button
                    type="button"
                    key={ratio.id}
                    onClick={() => setAspectRatio(ratio.id)}
                    className={`py-3 px-2 rounded-xl border text-center transition-all ${
                      aspectRatio === ratio.id
                        ? "border-indigo-500 bg-indigo-600/20 text-white font-bold"
                        : "border-slate-800 bg-slate-950/60 text-slate-400 hover:border-slate-700"
                    }`}
                  >
                    <div className="text-lg">{ratio.icon}</div>
                    <div className="text-xs mt-1">{ratio.label}</div>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Section 4: Soundtrack Mood */}
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6">
            <label className="block text-sm font-semibold text-white mb-1">Soundtrack Mood</label>
            <p className="text-xs text-slate-400 mb-4">Synthesized by the ACE-Step music engine.</p>
            <div className="flex flex-wrap gap-2">
              {MOODS.map((mood) => (
                <button
                  type="button"
                  key={mood}
                  onClick={() => setMusicMood(mood)}
                  className={`px-4 py-2 rounded-xl text-xs font-medium border transition-all ${
                    musicMood === mood
                      ? "border-indigo-500 bg-indigo-600 text-white shadow-md shadow-indigo-600/20"
                      : "border-slate-800 bg-slate-950/60 text-slate-400 hover:border-slate-700 hover:text-white"
                  }`}
                >
                  {mood}
                </button>
              ))}
            </div>

            {musicMood === "Custom prompt" && (
              <div className="mt-4">
                <label className="block text-xs font-semibold text-slate-300 mb-1">Custom Music Prompt</label>
                <input
                  type="text"
                  value={customMusicPrompt}
                  onChange={(e) => setCustomMusicPrompt(e.target.value)}
                  placeholder="e.g., Warm lo-fi chillhop with acoustic guitar and soft rain"
                  className="w-full px-4 py-2 rounded-xl bg-slate-950 border border-slate-800 text-white text-xs placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </div>
            )}
          </div>

          {/* Section 5: Description */}
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6">
            <label className="block text-sm font-semibold text-white mb-1">Video Story & Description (Optional)</label>
            <p className="text-xs text-slate-400 mb-3">Provide context or specific messages for the AI director and narration.</p>
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full px-4 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 text-sm"
              placeholder="e.g., Celebrate 10 years of wonderful memories with warm golden-hour lighting and heartfelt voiceover."
            />
          </div>

          {/* Section 6: Reference Image Uploads (Up to 5) */}
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-2">
              <div>
                <label className="block text-sm font-semibold text-white">Reference Photos</label>
                <p className="text-xs text-slate-400">
                  Upload up to 5 photos (JPG, PNG, WEBP, max 25MB). Photos represent scenes in planned sequence.
                </p>
              </div>
              <span className="text-xs font-mono px-2.5 py-1 rounded-lg bg-slate-800 text-indigo-300">
                {selectedFiles.length} / 5 Photos
              </span>
            </div>

            {/* Drop Zone */}
            {selectedFiles.length < 5 && (
              <div className="mt-4 border-2 border-dashed border-slate-800 hover:border-indigo-500/50 rounded-2xl p-6 text-center transition-all bg-slate-950/30">
                <input
                  type="file"
                  id="image-upload"
                  multiple
                  accept="image/jpeg,image/png,image/webp"
                  onChange={handleFileSelect}
                  className="hidden"
                />
                <label htmlFor="image-upload" className="cursor-pointer flex flex-col items-center">
                  <div className="w-12 h-12 rounded-2xl bg-indigo-600/10 text-indigo-400 flex items-center justify-center text-2xl mb-2">
                    📁
                  </div>
                  <span className="text-sm font-semibold text-white">Click to select photos</span>
                  <span className="text-xs text-slate-500 mt-1">Supports JPG, PNG, WEBP up to 25MB each</span>
                </label>
              </div>
            )}

            {/* Previews List with Order */}
            {selectedFiles.length > 0 && (
              <div className="mt-6 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-4">
                {selectedFiles.map((sf, idx) => (
                  <div
                    key={idx}
                    className="relative group rounded-xl overflow-hidden border border-slate-800 bg-slate-950 aspect-square flex flex-col items-center justify-center"
                  >
                    <img
                      src={sf.preview}
                      alt={sf.file.name}
                      className="w-full h-full object-cover"
                    />
                    {/* Scene Badge */}
                    <div className="absolute top-2 left-2 px-2 py-0.5 rounded-md bg-slate-950/80 backdrop-blur-sm text-[10px] font-mono text-indigo-300 border border-indigo-500/30">
                      Scene {idx + 1}
                    </div>
                    {/* Reorder & Remove Controls */}
                    <div className="absolute top-2 right-2 flex items-center gap-1">
                      {idx > 0 && (
                        <button
                          type="button"
                          onClick={() => moveFileLeft(idx)}
                          className="w-6 h-6 rounded-full bg-slate-900/90 hover:bg-slate-800 text-white flex items-center justify-center text-xs border border-slate-700 shadow"
                          title="Move earlier (previous scene)"
                        >
                          ←
                        </button>
                      )}
                      {idx < selectedFiles.length - 1 && (
                        <button
                          type="button"
                          onClick={() => moveFileRight(idx)}
                          className="w-6 h-6 rounded-full bg-slate-900/90 hover:bg-slate-800 text-white flex items-center justify-center text-xs border border-slate-700 shadow"
                          title="Move later (next scene)"
                        >
                          →
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => removeFile(idx)}
                        className="w-6 h-6 rounded-full bg-red-600 hover:bg-red-500 text-white flex items-center justify-center text-xs opacity-90 transition-opacity shadow"
                        title="Remove image"
                      >
                        ×
                      </button>
                    </div>
                    {/* Filename footer */}
                    <div className="absolute bottom-0 inset-x-0 bg-slate-950/90 py-1 px-2 text-[10px] text-slate-300 truncate">
                      {sf.file.name}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Submit Action */}
          <div className="pt-4 flex flex-col sm:flex-row items-center justify-between gap-4">
            <Link
              href="/dashboard"
              className="text-sm text-slate-400 hover:text-white px-4 py-2"
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={isSubmitting || selectedFiles.length === 0}
              className={`w-full sm:w-auto px-8 py-3.5 rounded-xl font-semibold text-sm transition-all flex items-center justify-center gap-2 ${
                isSubmitting || selectedFiles.length === 0
                  ? "bg-slate-800 text-slate-500 cursor-not-allowed"
                  : "bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/30"
              }`}
            >
              {isSubmitting ? (
                <>
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  <span>{uploadStep || "Processing..."}</span>
                </>
              ) : (
                <span>Create Project & Upload ({selectedFiles.length} Photos) &rarr;</span>
              )}
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}
