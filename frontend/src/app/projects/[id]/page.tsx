"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  getProject,
  deleteProject,
  uploadProjectImages,
  deleteProjectImage,
  reorderProjectImages,
  analyzeProjectImages,
  getProjectClips,
  getLatestProjectJob,
  generateProjectClips,
  getProjectVoiceover,
  generateProjectVoiceover,
  getProjectMusic,
  generateProjectMusic,
  getMusicCategories,
  getMusicTracks,
  selectProjectMusic,
  composeProjectVideo,
  getProjectFinalVideo,
  getProjectAnalysisCheckpoints,
  ProjectDetail,
  ScenePlan,
  ClipAsset,
  VoiceoverAsset,
  MusicAsset,
  MusicTrack,
  FinalVideoAsset,
  GenerationJob,
  AnalysisProgressResponse,
  ImageAnalysisCheckpoint,
} from "@/lib/api";

export default function ProjectDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const resolvedParams = use(params);
  const projectId = resolvedParams.id;
  const router = useRouter();

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  // Phase 4: AI Scene Planning state
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisStep, setAnalysisStep] = useState<string>("");
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analysisProgress, setAnalysisProgress] = useState<AnalysisProgressResponse | null>(null);
  const [countdownSeconds, setCountdownSeconds] = useState<number | null>(null);

  // Phase 5: Wan 2.2 Video Generation state
  const [clips, setClips] = useState<ClipAsset[]>([]);
  const [latestJob, setLatestJob] = useState<GenerationJob | null>(null);
  const [generatingClips, setGeneratingClips] = useState(false);
  const [activeSceneNumber, setActiveSceneNumber] = useState<number | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);

  // Phase 6: Chatterbox Voiceover state
  const [voiceover, setVoiceover] = useState<VoiceoverAsset | null>(null);
  const [generatingVoiceover, setGeneratingVoiceover] = useState(false);
  const [voiceoverError, setVoiceoverError] = useState<string | null>(null);

  // Phase 7: Background Music state (ACE-Step & Static Library)
  const [music, setMusic] = useState<MusicAsset | null>(null);
  const [generatingMusic, setGeneratingMusic] = useState(false);
  const [musicError, setMusicError] = useState<string | null>(null);

  // Phase 8: Final Video Composition state (FFmpeg)
  const [finalVideo, setFinalVideo] = useState<FinalVideoAsset | null>(null);
  const [composing, setComposing] = useState(false);
  const [compositionError, setCompositionError] = useState<string | null>(null);

  // Static Music Library state
  const [musicTab, setMusicTab] = useState<"ai" | "library">("library");
  const [categories, setCategories] = useState<string[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string>("all");
  const [libraryTracks, setLibraryTracks] = useState<MusicTrack[]>([]);
  const [loadingTracks, setLoadingTracks] = useState(false);
  const [previewTrackId, setPreviewTrackId] = useState<string | null>(null);
  const [selectingTrackId, setSelectingTrackId] = useState<string | null>(null);

  const apiBase = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1").replace(/\/api\/v1\/?$/, "");

  const fetchLibraryData = async (cat?: string) => {
    try {
      setLoadingTracks(true);
      const [cats, tracks] = await Promise.all([
        getMusicCategories().catch(() => []),
        getMusicTracks(cat && cat !== "all" ? cat : undefined).catch(() => []),
      ]);
      if (cats && cats.length > 0) setCategories(cats);
      setLibraryTracks(tracks);
    } catch (e) {
      console.error("Failed to load music library:", e);
    } finally {
      setLoadingTracks(false);
    }
  };

  useEffect(() => {
    fetchLibraryData(selectedCategory);
  }, [selectedCategory]);

  const fetchClipsAndJob = async () => {
    try {
      const [clipsData, jobData, voiceoverData, musicData, finalVideoData] = await Promise.all([
        getProjectClips(projectId).catch(() => []),
        getLatestProjectJob(projectId).catch(() => null),
        getProjectVoiceover(projectId).catch(() => null),
        getProjectMusic(projectId).catch(() => null),
        getProjectFinalVideo(projectId).catch(() => null),
      ]);
      setClips(clipsData);
      setLatestJob(jobData);
      setVoiceover(voiceoverData);
      setMusic(musicData);
      setFinalVideo(finalVideoData);
      if (musicData?.provider === "static_library") {
        setMusicTab("library");
      }
      if (jobData && (jobData.status === "RUNNING" || jobData.status === "QUEUED" || jobData.status === "WAITING_FOR_RATE_LIMIT")) {
        if (jobData.stage === "ANALYZING_IMAGES") {
          setAnalyzing(true);
        } else if (jobData.stage === "GENERATING_VOICEOVER") {
          setGeneratingVoiceover(true);
        } else if (jobData.stage === "GENERATING_MUSIC") {
          setGeneratingMusic(true);
          setMusicTab("ai");
        } else if (jobData.stage === "COMPOSING") {
          setComposing(true);
        } else {
          setGeneratingClips(true);
        }
      }
    } catch (e) {
      console.error("Clips/Job load error:", e);
    }
  };

  const fetchProject = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getProject(projectId);
      setProject(data);
    } catch (err: any) {
      setError(err.message || "Failed to load project details");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProject();
    fetchClipsAndJob();
  }, [projectId]);

  // Polling for generation job progress (Wan clips, Chatterbox voiceover, ACE-Step music, FFmpeg composition)
  useEffect(() => {
    const isJobActive =
      analyzing ||
      generatingClips ||
      generatingVoiceover ||
      generatingMusic ||
      composing ||
      (latestJob && (latestJob.status === "RUNNING" || latestJob.status === "QUEUED" || latestJob.status === "WAITING_FOR_RATE_LIMIT"));

    if (isJobActive) {
      const interval = setInterval(async () => {
        try {
          const [clipsData, jobData, voiceoverData, musicData, finalVideoData] = await Promise.all([
            getProjectClips(projectId).catch(() => []),
            getLatestProjectJob(projectId).catch(() => null),
            getProjectVoiceover(projectId).catch(() => null),
            getProjectMusic(projectId).catch(() => null),
            getProjectFinalVideo(projectId).catch(() => null),
          ]);
          setClips(clipsData);
          setLatestJob(jobData);
          if (voiceoverData) {
            setVoiceover(voiceoverData);
          }
          if (musicData) {
            setMusic(musicData);
          }
          if (finalVideoData) {
            setFinalVideo(finalVideoData);
          }

          if (jobData && (jobData.status === "COMPLETED" || jobData.status === "FAILED")) {
            setGeneratingClips(false);
            setGeneratingVoiceover(false);
            setGeneratingMusic(false);
            setComposing(false);
            setActiveSceneNumber(null);
            if (jobData.stage === "ANALYZING_IMAGES") {
              setAnalyzing(false);
              await fetchProject();
            }
            if (jobData.status === "FAILED") {
              if (jobData.stage === "GENERATING_VOICEOVER") {
                setVoiceoverError(jobData.error_message || "Voiceover generation failed");
              } else if (jobData.stage === "GENERATING_MUSIC") {
                setMusicError(jobData.error_message || "Music generation failed");
              } else if (jobData.stage === "COMPOSING") {
                setCompositionError(jobData.error_message || "Video composition failed");
              } else if (jobData.stage === "ANALYZING_IMAGES") {
                setAnalysisError(jobData.error_message || "Scene planning failed");
              } else {
                setGenerationError(jobData.error_message || "Video generation failed");
              }
            } else if (jobData.status === "COMPLETED") {
              if (jobData.stage === "GENERATING_VOICEOVER") {
                setVoiceover(voiceoverData);
              } else if (jobData.stage === "GENERATING_MUSIC") {
                setMusic(musicData);
              } else if (jobData.stage === "COMPOSING") {
                setFinalVideo(finalVideoData);
              } else if (jobData.stage === "ANALYZING_IMAGES") {
                await fetchProject();
              }
            }
          }
        } catch (err) {
          console.error("Polling error:", err);
        }
      }, 3000);

      return () => clearInterval(interval);
    }
  }, [analyzing, generatingClips, generatingVoiceover, generatingMusic, composing, latestJob?.status, latestJob?.stage, projectId]);

  // Polling for Checkpoint-based Scene Planning progress & Groq Rate Limits
  useEffect(() => {
    let interval: NodeJS.Timeout | null = null;

    const checkProgress = async () => {
      try {
        const prog = await getProjectAnalysisCheckpoints(projectId);
        setAnalysisProgress(prog);

        if (prog.job_status === "WAITING_FOR_RATE_LIMIT" || prog.job_status === "RUNNING") {
          setAnalyzing(true);
        }

        if (prog.cooldown_seconds_remaining && prog.cooldown_seconds_remaining > 0) {
          setCountdownSeconds(Math.ceil(prog.cooldown_seconds_remaining));
        } else if (prog.job_status !== "WAITING_FOR_RATE_LIMIT") {
          setCountdownSeconds(null);
        }

        if (prog.job_status === "COMPLETED" && prog.pending_count === 0) {
          setAnalyzing(false);
          const updated = await getProject(projectId);
          setProject(updated);
        }
      } catch (e) {
        // Ignored if checkpoints not yet initialized
      }
    };

    checkProgress();

    if (
      analyzing ||
      latestJob?.stage === "ANALYZING_IMAGES" ||
      analysisProgress?.job_status === "WAITING_FOR_RATE_LIMIT" ||
      analysisProgress?.job_status === "RUNNING"
    ) {
      interval = setInterval(checkProgress, 2000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [projectId, analyzing, latestJob?.stage, latestJob?.status, analysisProgress?.job_status]);

  // Live countdown timer decrement
  useEffect(() => {
    if (countdownSeconds === null || countdownSeconds <= 0) return;
    const t = setInterval(() => {
      setCountdownSeconds((prev) => (prev && prev > 1 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(t);
  }, [countdownSeconds]);

  const handleStartClipGeneration = async (sceneNumber?: number, forceSlideshow?: boolean) => {
    try {
      setGeneratingClips(true);
      setGenerationError(null);
      if (sceneNumber) {
        setActiveSceneNumber(sceneNumber);
      }
      await generateProjectClips(projectId, sceneNumber, forceSlideshow);
      // Fetch status right away
      await fetchClipsAndJob();
    } catch (err: any) {
      setGenerationError(err.message || "Failed to start video clip generation");
      setGeneratingClips(false);
      setActiveSceneNumber(null);
    }
  };

  const handleStartVoiceoverGeneration = async (force: boolean = false) => {
    try {
      setGeneratingVoiceover(true);
      setVoiceoverError(null);
      await generateProjectVoiceover(projectId, force);
      await fetchClipsAndJob();
    } catch (err: any) {
      setVoiceoverError(err.message || "Failed to initiate voiceover generation");
      setGeneratingVoiceover(false);
    }
  };

  const handleStartMusicGeneration = async (force: boolean = false) => {
    try {
      setGeneratingMusic(true);
      setMusicError(null);
      if (music?.provider === "static_library") {
        await selectProjectMusic(projectId, null);
        setMusic(null);
      }
      await generateProjectMusic(projectId, force || music?.provider === "static_library");
      await fetchClipsAndJob();
    } catch (err: any) {
      setMusicError(err.message || "Failed to initiate music generation");
      setGeneratingMusic(false);
    }
  };

  const handleSelectLibraryTrack = async (trackId: string) => {
    try {
      setSelectingTrackId(trackId);
      setMusicError(null);
      const asset = await selectProjectMusic(projectId, trackId);
      setMusic(asset);
    } catch (err: any) {
      setMusicError(err.message || "Failed to select library music track");
    } finally {
      setSelectingTrackId(null);
    }
  };

  const handleClearMusicSelection = async () => {
    try {
      setSelectingTrackId("clearing");
      setMusicError(null);
      await selectProjectMusic(projectId, null);
      setMusic(null);
    } catch (err: any) {
      setMusicError(err.message || "Failed to clear music selection");
    } finally {
      setSelectingTrackId(null);
    }
  };

  const handleStartComposition = async (force: boolean = false) => {
    try {
      setComposing(true);
      setCompositionError(null);
      await composeProjectVideo(projectId, force);
      await fetchClipsAndJob();
    } catch (err: any) {
      setCompositionError(err.message || "Failed to initiate video composition");
      setComposing(false);
    }
  };

  const handleDeleteImage = async (imageId: string) => {
    if (!confirm("Are you sure you want to remove this reference photo?")) return;
    try {
      await deleteProjectImage(projectId, imageId);
      await fetchProject();
    } catch (err: any) {
      alert(err.message || "Failed to delete image");
    }
  };

  const handleMoveImage = async (currentIndex: number, newIndex: number) => {
    if (!project || newIndex < 0 || newIndex >= project.images.length) return;
    const reordered = [...project.images];
    const moved = reordered.splice(currentIndex, 1)[0];
    reordered.splice(newIndex, 0, moved);

    // Optimistic UI update
    setProject({ ...project, images: reordered });

    try {
      const updatedImages = await reorderProjectImages(
        projectId,
        reordered.map((img) => img.id)
      );
      setProject((prev) => (prev ? { ...prev, images: updatedImages } : prev));
    } catch (err: any) {
      alert(err.message || "Failed to reorder images");
      await fetchProject();
    }
  };

  const handleAddImages = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    const files = Array.from(e.target.files);
    const remainingSlots = 5 - (project?.images.length || 0);

    if (files.length > remainingSlots) {
      alert(`You can only upload ${remainingSlots} more photo(s). Maximum is 5.`);
      return;
    }

    try {
      setUploading(true);
      await uploadProjectImages(projectId, files);
      await fetchProject();
    } catch (err: any) {
      alert(err.message || "Upload failed");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleDeleteProject = async () => {
    if (!project) return;
    if (!confirm(`Permanently delete '${project.title}' and all its stored photos?`)) return;
    try {
      await deleteProject(projectId);
      router.push("/dashboard");
    } catch (err: any) {
      alert(err.message || "Failed to delete project");
    }
  };

  // Phase 4: Analyze reference images and plan scenes
  const handleAnalyze = async (force: boolean = false) => {
    if (!project || project.images.length === 0) {
      alert("Please upload at least 1 reference image before planning.");
      return;
    }

    try {
      setAnalyzing(true);
      setAnalysisError(null);
      setAnalysisStep("Initiating checkpoint-resilient image analysis...");

      await analyzeProjectImages(projectId, true, force);
      const prog = await getProjectAnalysisCheckpoints(projectId).catch(() => null);
      if (prog) {
        setAnalysisProgress(prog);
      }
      await fetchClipsAndJob();
    } catch (err: any) {
      setAnalysisError(err.message || "Failed to initiate scene plan analysis. Please retry.");
      setAnalyzing(false);
      setAnalysisStep("");
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3 text-slate-400 text-sm">
          <span className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          Loading project...
        </div>
      </div>
    );
  }

  if (error || !project) {
    return (
      <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center p-6 text-center">
        <div className="text-3xl mb-3">⚠️</div>
        <h1 className="text-xl font-bold text-white mb-2">Could Not Load Project</h1>
        <p className="text-slate-400 text-sm max-w-md mb-6">{error || "Project not found or access denied."}</p>
        <Link
          href="/dashboard"
          className="px-6 py-2.5 rounded-xl bg-slate-800 text-slate-200 text-sm hover:bg-slate-700 transition-colors"
        >
          &larr; Back to Dashboard
        </Link>
      </div>
    );
  }

  const expectedSceneCount = project.duration_seconds / 5;
  const planSceneCount = project.scene_plan?.scenes?.length || expectedSceneCount;
  const hasAllClips = clips.length >= planSceneCount && planSceneCount > 0;
  const hasVoiceover = !!voiceover;
  const hasMusic = !!music;
  const isReadyToCompose = hasAllClips && hasVoiceover && hasMusic;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/dashboard"
              className="text-slate-400 hover:text-white text-xs px-2.5 py-1.5 rounded-lg border border-slate-800 hover:border-slate-700"
            >
              &larr; Dashboard
            </Link>
            <span className="text-slate-600">/</span>
            <span className="font-semibold text-sm text-white truncate max-w-xs">{project.title}</span>
          </div>
          <button
            onClick={handleDeleteProject}
            className="text-xs text-red-400 hover:text-red-300 px-3 py-1.5 rounded-lg border border-red-500/20 hover:bg-red-500/10 transition-colors"
          >
            Delete Project
          </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-6xl mx-auto px-6 py-10 w-full space-y-8">
        {/* Project Header Info */}
        <div className="bg-slate-900/60 border border-slate-800/80 rounded-3xl p-6 sm:p-8">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <div className="flex flex-wrap items-center gap-2 mb-2">
                <span className="px-3 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 font-semibold text-xs">
                  {project.occasion}
                </span>
                <span className="px-2.5 py-1 rounded-full bg-slate-800 text-slate-400 text-xs font-mono">
                  {project.status}
                </span>
                {voiceover && (
                  <span className="px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-medium flex items-center gap-1">
                    🎙️ Voiceover Ready ({voiceover.duration_seconds.toFixed(1)}s)
                  </span>
                )}
                {music && (
                  <span className="px-2.5 py-1 rounded-full bg-purple-500/10 border border-purple-500/30 text-purple-300 text-xs font-medium flex items-center gap-1">
                    🎵 {music.title ? music.title : "Music Ready"} ({music.duration_seconds.toFixed(1)}s)
                  </span>
                )}
              </div>
              <h1 className="text-2xl sm:text-3xl font-bold text-white">{project.title}</h1>
              {project.description && (
                <p className="text-slate-400 text-sm mt-2 max-w-2xl leading-relaxed">{project.description}</p>
              )}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 w-full sm:w-auto text-center text-xs">
              <div className="bg-slate-950 p-3 rounded-2xl border border-slate-800">
                <div className="text-slate-500 text-[10px]">DURATION</div>
                <div className="font-semibold text-white mt-0.5">{project.duration_seconds}s</div>
              </div>
              <div className="bg-slate-950 p-3 rounded-2xl border border-slate-800">
                <div className="text-slate-500 text-[10px]">ASPECT RATIO</div>
                <div className="font-semibold text-white mt-0.5">{project.aspect_ratio}</div>
              </div>
              <div className="bg-slate-950 p-3 rounded-2xl border border-slate-800">
                <div className="text-slate-500 text-[10px]">MUSIC MOOD</div>
                <div className="font-semibold text-white mt-0.5">{project.music_mood}</div>
              </div>
              <div className="bg-slate-950 p-3 rounded-2xl border border-slate-800">
                <div className="text-slate-500 text-[10px]">PHOTOS</div>
                <div className="font-semibold text-indigo-400 mt-0.5 font-mono">{project.images.length} / 5</div>
              </div>
            </div>
          </div>
        </div>

        {/* Reference Images Gallery */}
        <div className="bg-slate-900/60 border border-slate-800/80 rounded-3xl p-6 sm:p-8">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between pb-6 border-b border-slate-800 gap-4">
            <div>
              <h2 className="text-lg font-bold text-white">Reference Photos & Sequence</h2>
              <p className="text-xs text-slate-400 mt-1">
                Upload up to 5 photos. Reorder them using arrows to define the narrative flow.
              </p>
            </div>

            <div className="flex items-center gap-3">
              {project.images.length < 5 && (
                <div>
                  <input
                    type="file"
                    id="add-more-files"
                    multiple
                    accept="image/jpeg,image/png,image/webp"
                    onChange={handleAddImages}
                    className="hidden"
                  />
                  <label
                    htmlFor="add-more-files"
                    className={`cursor-pointer px-4 py-2 rounded-xl text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 transition-all flex items-center gap-2 border border-slate-700 ${uploading ? "opacity-50 cursor-not-allowed" : ""
                      }`}
                  >
                    {uploading ? "Uploading..." : "+ Add Photos"}
                  </label>
                </div>
              )}

              {/* Analyze Button */}
              {project.images.length > 0 && (
                <button
                  onClick={() => handleAnalyze(false)}
                  disabled={analyzing}
                  className={`px-5 py-2.5 rounded-xl text-xs font-semibold shadow-lg transition-all flex items-center gap-2 ${analyzing
                    ? "bg-indigo-600/50 text-indigo-200 cursor-wait"
                    : "bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white shadow-indigo-500/20"
                    }`}
                >
                  {analyzing ? (
                    <>
                      <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                      Analyzing Images...
                    </>
                  ) : project.scene_plan ? (
                    "🔄 Re-Analyze & Update Plan"
                  ) : (
                    "✨ Analyze Images & Generate Scene Plan"
                  )}
                </button>
              )}
            </div>
          </div>

          {/* Phase 4: Checkpoint & Rate-Limit Cooldown Progress Monitor */}
          {(analyzing || analysisProgress?.job_status === "WAITING_FOR_RATE_LIMIT" || (analysisProgress && analysisProgress.checkpoints.length > 0 && !project.scene_plan)) && (
            <div className={`mt-6 p-5 rounded-2xl border transition-all ${analysisProgress?.job_status === "WAITING_FOR_RATE_LIMIT"
              ? "bg-amber-950/20 border-amber-500/40 shadow-lg shadow-amber-950/30"
              : "bg-gradient-to-r from-indigo-950/30 via-slate-900/60 to-purple-950/30 border-indigo-500/30 shadow-lg shadow-indigo-950/20"
              }`}>
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
                <div className="flex items-center gap-3">
                  {analysisProgress?.job_status === "WAITING_FOR_RATE_LIMIT" ? (
                    <div className="w-10 h-10 rounded-xl bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-xl animate-pulse">
                      ⏳
                    </div>
                  ) : (
                    <div className="w-10 h-10 rounded-xl bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center">
                      <span className="w-5 h-5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                    </div>
                  )}
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-white">
                        {analysisProgress?.job_status === "WAITING_FOR_RATE_LIMIT"
                          ? "Rate-Limit Cooldown Active — Automatic Resume Armed"
                          : "AI Multimodal Scene Planning & Image Checkpointing"}
                      </h3>
                      {analysisProgress?.auto_resume && (
                        <span className="px-2 py-0.5 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-[10px] font-mono text-emerald-300">
                          Auto-Resume
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-slate-400 mt-0.5">
                      {analysisProgress?.job_status === "WAITING_FOR_RATE_LIMIT"
                        ? `Groq Free Tier ${analysisProgress.rate_limit_type || "token rate limit"} reached. Progress is safely committed in database.`
                        : analysisStep || "Analyzing visual subjects, environment, lighting, and synthesizing cinematic prompts..."}
                    </p>
                  </div>
                </div>

                {/* Cooldown Timer or Status Badge */}
                {analysisProgress?.job_status === "WAITING_FOR_RATE_LIMIT" ? (
                  <div className="flex items-center gap-3 bg-amber-500/10 border border-amber-500/30 px-3.5 py-2 rounded-xl">
                    <span className="text-xs text-amber-300 font-medium">Resuming In:</span>
                    <span className="font-mono text-base font-bold text-amber-200">
                      {countdownSeconds !== null ? `${countdownSeconds}s` : "Waiting..."}
                    </span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 bg-indigo-500/10 border border-indigo-500/30 px-3.5 py-1.5 rounded-xl">
                    <span className="text-xs text-indigo-300 font-medium">
                      {analysisProgress?.completed_count || 0} of {analysisProgress?.total_images || project.images.length} Analyzed
                    </span>
                  </div>
                )}
              </div>

              {/* Checkpoint Stats Metrics */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 text-xs">
                <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800">
                  <div className="text-[11px] text-slate-400">Total Photos</div>
                  <div className="text-sm font-bold text-white mt-1">
                    {analysisProgress?.total_images || project.images.length}
                  </div>
                </div>
                <div className="p-3 rounded-xl bg-emerald-950/20 border border-emerald-800/40">
                  <div className="text-[11px] text-emerald-400">Checkpoints Saved</div>
                  <div className="text-sm font-bold text-emerald-300 mt-1 flex items-center gap-1.5">
                    <span>✓</span> {analysisProgress?.completed_count || 0}
                  </div>
                </div>
                <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800">
                  <div className="text-[11px] text-slate-400">Pending Analysis</div>
                  <div className="text-sm font-bold text-slate-300 mt-1">
                    {analysisProgress?.pending_count || 0}
                  </div>
                </div>
                <div className="p-3 rounded-xl bg-indigo-950/20 border border-indigo-800/40">
                  <div className="text-[11px] text-indigo-400">Active Photo</div>
                  <div className="text-sm font-bold text-indigo-300 mt-1 truncate">
                    {analysisProgress?.active_order_index !== null && analysisProgress?.active_order_index !== undefined
                      ? `Photo #${analysisProgress.active_order_index + 1}`
                      : "Synthesizing..."}
                  </div>
                </div>
              </div>

              {/* Progress Bar */}
              <div className="mt-4">
                <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                  <div
                    className="bg-gradient-to-r from-emerald-500 to-indigo-500 h-2 rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.round(
                        ((analysisProgress?.completed_count || 0) /
                          Math.max(1, analysisProgress?.total_images || project.images.length)) *
                        100
                      )}%`,
                    }}
                  />
                </div>
              </div>

              {/* Rate-Limit Reassurance Banner */}
              {analysisProgress?.job_status === "WAITING_FOR_RATE_LIMIT" && (
                <div className="mt-4 p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-xs text-amber-200/90 flex items-start gap-2.5">
                  <span className="text-amber-400 mt-0.5">ℹ️</span>
                  <div>
                    <span className="font-semibold text-amber-100">Zero Progress Lost: </span>
                    Photos 1 to {analysisProgress.completed_count} are permanently persisted in the database. When the
                    reset cooldown finishes, the system will automatically resume starting from Photo #{
                      (analysisProgress.active_order_index ?? 0) + 1
                    }. You do not need to click retry or refresh.
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Analysis Error Banner */}
          {analysisError && (
            <div className="mt-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/30 flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <span className="text-xl">⚠️</span>
                <div>
                  <div className="text-sm font-semibold text-red-300">Scene Planning Failed</div>
                  <div className="text-xs text-red-400 mt-0.5">{analysisError}</div>
                </div>
              </div>
              <button
                onClick={() => handleAnalyze(true)}
                className="px-3 py-1.5 rounded-lg bg-red-600/30 hover:bg-red-600/50 text-red-200 text-xs font-medium transition-colors"
              >
                Retry
              </button>
            </div>
          )}

          {project.images.length === 0 ? (
            <div className="py-14 text-center">
              <div className="text-3xl mb-2">📷</div>
              <p className="text-slate-400 text-sm">No photos uploaded yet. Upload 1 to 5 photos to begin.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-5 mt-6">
              {project.images.map((img, idx) => {
                const imageUrl = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"}/projects/${project.id}/images/${img.id}/file`;
                const cp = analysisProgress?.checkpoints?.find((c) => c.image_id === img.id);
                return (
                  <div
                    key={img.id}
                    className="group relative rounded-2xl overflow-hidden border border-slate-800 bg-slate-950 flex flex-col"
                  >
                    {/* Image Preview Container */}
                    <div className="aspect-square relative bg-slate-900 overflow-hidden">
                      <img
                        src={imageUrl}
                        alt={img.original_filename}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                        onError={(e) => {
                          (e.target as HTMLElement).style.display = "none";
                        }}
                      />
                      {/* Photo Badge */}
                      <div className="absolute top-2 left-2 px-2.5 py-0.5 rounded-lg bg-slate-950/85 backdrop-blur-md text-[11px] font-mono text-indigo-300 border border-indigo-500/30">
                        #{idx + 1}
                      </div>

                      {/* Checkpoint Status Pill */}
                      {cp?.status === "COMPLETED" && (
                        <div className="absolute bottom-2 left-2 px-2 py-0.5 rounded-lg bg-emerald-600/90 backdrop-blur-md text-[10px] font-semibold text-white flex items-center gap-1 shadow">
                          <span>✓</span> Saved
                        </div>
                      )}
                      {cp?.status === "IN_PROGRESS" && (
                        <div className="absolute bottom-2 left-2 px-2 py-0.5 rounded-lg bg-indigo-600/90 backdrop-blur-md text-[10px] font-semibold text-white flex items-center gap-1 shadow animate-pulse">
                          <span className="w-2 h-2 border border-white border-t-transparent rounded-full animate-spin" />
                          Analyzing
                        </div>
                      )}
                      {cp?.status === "WAITING_FOR_RATE_LIMIT" && (
                        <div className="absolute bottom-2 left-2 px-2 py-0.5 rounded-lg bg-amber-600/90 backdrop-blur-md text-[10px] font-semibold text-white flex items-center gap-1 shadow">
                          <span>⏳</span> Cooldown
                        </div>
                      )}
                      {(!cp || cp?.status === "PENDING") && analysisProgress && (
                        <div className="absolute bottom-2 left-2 px-2 py-0.5 rounded-lg bg-slate-900/85 backdrop-blur-md text-[10px] text-slate-400 flex items-center gap-1 border border-slate-700/50">
                          <span>•</span> Pending
                        </div>
                      )}
                      {/* Reorder & Delete buttons */}
                      <div className="absolute top-2 right-2 flex items-center gap-1">
                        {idx > 0 && (
                          <button
                            onClick={() => handleMoveImage(idx, idx - 1)}
                            className="w-7 h-7 rounded-full bg-slate-900/90 hover:bg-slate-800 text-white flex items-center justify-center text-xs border border-slate-700 shadow"
                            title="Move earlier"
                          >
                            ←
                          </button>
                        )}
                        {idx < project.images.length - 1 && (
                          <button
                            onClick={() => handleMoveImage(idx, idx + 1)}
                            className="w-7 h-7 rounded-full bg-slate-900/90 hover:bg-slate-800 text-white flex items-center justify-center text-xs border border-slate-700 shadow"
                            title="Move later"
                          >
                            →
                          </button>
                        )}
                        <button
                          onClick={() => handleDeleteImage(img.id)}
                          className="w-7 h-7 rounded-full bg-red-600 hover:bg-red-500 text-white flex items-center justify-center text-sm shadow-md transition-opacity"
                          title="Remove photo"
                        >
                          ×
                        </button>
                      </div>
                    </div>

                    {/* Metadata footer */}
                    <div className="p-3 text-xs bg-slate-950/90 border-t border-slate-800/60">
                      <div className="font-medium text-white truncate" title={img.original_filename}>
                        {img.original_filename}
                      </div>
                      <div className="flex items-center justify-between text-[10px] text-slate-500 mt-1">
                        <span>{(img.file_size / 1024).toFixed(0)} KB</span>
                        {img.width && img.height && (
                          <span>
                            {img.width}×{img.height}
                          </span>
                        )}
                        <span className="uppercase">{img.mime_type.split("/")[1]}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Phase 4: Generated Scene Plan Viewer */}
        {project.scene_plan && (
          <div className="bg-slate-900/60 border border-slate-800/80 rounded-3xl p-6 sm:p-8 space-y-8">
            {/* Scene Plan Overview Header */}
            <div>
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-6 border-b border-slate-800">
                <div>
                  <div className="flex flex-wrap items-center gap-2 mb-2">
                    <span className="px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 font-semibold text-xs">
                      AI Scene Plan Verified
                    </span>
                    <span className="text-xs text-slate-500 font-mono">
                      {project.scene_plan.scenes.length} Scenes • {project.scene_plan.total_duration}s Total
                    </span>
                    <span className="px-2.5 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 text-xs font-mono">
                      {clips.length} / {project.scene_plan.scenes.length} Clips Generated
                    </span>
                    {voiceover && (
                      <span className="px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs font-mono flex items-center gap-1">
                        <span>🎙️</span> Voiceover Ready ({voiceover.duration_seconds.toFixed(1)}s)
                      </span>
                    )}
                  </div>
                  <h2 className="text-xl sm:text-2xl font-bold text-white">
                    {project.scene_plan.video_concept}
                  </h2>
                  <p className="text-slate-300 text-sm mt-2 max-w-3xl leading-relaxed">
                    {project.scene_plan.story_summary}
                  </p>
                </div>

                <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full sm:w-auto">
                  <button
                    onClick={() => handleStartClipGeneration(undefined, false)}
                    disabled={generatingClips || analyzing}
                    className={`px-5 py-2.5 rounded-xl font-semibold text-sm shadow-lg flex items-center justify-center gap-2 transition-all ${generatingClips
                      ? "bg-slate-800 text-slate-400 cursor-not-allowed border border-slate-700"
                      : clips.length === project.scene_plan.scenes.length
                        ? "bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-emerald-500/20"
                        : "bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white shadow-indigo-500/20 cursor-pointer"
                      }`}
                  >
                    {generatingClips ? (
                      <>
                        <span className="w-4 h-4 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                        Generating Clips...
                      </>
                    ) : clips.length === project.scene_plan.scenes.length ? (
                      <>
                        <span>✓</span> All Clips Ready (Regenerate All)
                      </>
                    ) : (
                      <>
                        <span>🎬</span> Generate Video Clips (Wan 2.2 + Auto Fallback)
                      </>
                    )}
                  </button>

                  <button
                    onClick={() => handleStartClipGeneration(undefined, true)}
                    disabled={generatingClips || analyzing}
                    title="Instantly generate all scene clips using smooth Ken Burns photo motion (zero cloud queue)"
                    className="px-4 py-2.5 rounded-xl font-semibold text-xs border border-amber-500/40 bg-amber-950/40 hover:bg-amber-900/40 text-amber-300 transition-all flex items-center justify-center gap-1.5 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <span>⚡</span> Slideshow Mode (Instant)
                  </button>
                </div>
              </div>

              {/* Active Generation Progress Banner */}
              {(generatingClips || latestJob?.status === "RUNNING" || latestJob?.status === "QUEUED") && (
                <div className="p-4 rounded-2xl bg-indigo-950/40 border border-indigo-500/30 space-y-2 mt-4">
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full bg-indigo-400 animate-ping" />
                      <span className="font-semibold text-indigo-300">
                        {activeSceneNumber ? `Generating Scene ${activeSceneNumber}...` : "Generating Video Clips with Wan 2.2"}
                      </span>
                      <span className="text-slate-400 hidden sm:inline">
                        (ZeroGPU free inference • ~25s per clip)
                      </span>
                    </div>
                    <span className="font-mono text-indigo-300 font-bold">
                      {latestJob?.progress_percent || 0}%
                    </span>
                  </div>
                  <div className="w-full bg-slate-950 rounded-full h-2 overflow-hidden border border-slate-800">
                    <div
                      className="bg-gradient-to-r from-indigo-500 to-purple-500 h-2 transition-all duration-500 rounded-full"
                      style={{ width: `${Math.max(latestJob?.progress_percent || 0, 10)}%` }}
                    />
                  </div>
                  <div className="text-[11px] text-slate-400 flex items-center justify-between">
                    <span>Stage: {latestJob?.stage || "GENERATING_CLIPS"} • In-process execution</span>
                    <span>Completed clips appear automatically below</span>
                  </div>
                </div>
              )}

              {/* Generation Error Banner */}
              {generationError && (
                <div className="p-4 rounded-2xl bg-red-950/40 border border-red-500/30 flex items-center justify-between text-xs text-red-300 mt-4">
                  <div className="flex items-center gap-2">
                    <span>⚠️</span>
                    <span>{generationError}</span>
                  </div>
                  <button
                    onClick={() => setGenerationError(null)}
                    className="text-red-400 hover:text-white ml-3 text-xs underline cursor-pointer"
                  >
                    Dismiss
                  </button>
                </div>
              )}

              {/* Aesthetic and Direction Parameters */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-6">
                <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800/80">
                  <div className="text-[10px] text-slate-500 font-mono uppercase tracking-wider">Visual Aesthetic</div>
                  <div className="text-xs text-slate-200 mt-1 font-medium">{project.scene_plan.visual_style}</div>
                </div>
                <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800/80">
                  <div className="text-[10px] text-slate-500 font-mono uppercase tracking-wider">Emotional Tone</div>
                  <div className="text-xs text-slate-200 mt-1 font-medium">{project.scene_plan.emotional_tone}</div>
                </div>
                <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800/80">
                  <div className="text-[10px] text-slate-500 font-mono uppercase tracking-wider">Soundtrack Mood</div>
                  <div className="text-xs text-slate-200 mt-1 font-medium">
                    {project.scene_plan.music_mood}
                    {project.scene_plan.background_music_prompt && (
                      <span className="block text-[11px] text-slate-400 mt-1 truncate">
                        {project.scene_plan.background_music_prompt}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Unselected Images Rationale (if applicable) */}
              {project.scene_plan.unselected_images_explanation && (
                <div className="mt-4 p-3.5 rounded-2xl bg-slate-950/80 border border-slate-800 text-xs flex items-start gap-2.5">
                  <span className="text-amber-400 text-sm">💡</span>
                  <div>
                    <span className="font-semibold text-slate-300 block mb-0.5">Image Selection Rationale:</span>
                    <span className="text-slate-400 leading-relaxed">{project.scene_plan.unselected_images_explanation}</span>
                  </div>
                </div>
              )}
            </div>

            {/* Phase 6: Voiceover Narration & Chatterbox Audio Card */}
            <div className="p-6 rounded-2xl bg-gradient-to-br from-indigo-950/30 via-slate-900/60 to-purple-950/30 border border-indigo-500/20 space-y-4">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <span className="text-lg">🎙️</span>
                  <div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                      Voiceover Narration (Chatterbox TTS)
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 normal-case">
                        ZeroGPU Free
                      </span>
                    </h3>
                    <p className="text-xs text-slate-400">
                      High-fidelity 24kHz speech synthesis via ResembleAI/Chatterbox
                    </p>
                  </div>
                </div>

                <button
                  onClick={() => handleStartVoiceoverGeneration(!!voiceover)}
                  disabled={generatingVoiceover || analyzing}
                  className={`px-4 py-2 rounded-xl font-semibold text-xs shadow-md flex items-center justify-center gap-2 transition-all ${generatingVoiceover
                    ? "bg-slate-800 text-slate-400 cursor-not-allowed border border-slate-700"
                    : voiceover
                      ? "bg-gradient-to-r from-teal-600 to-emerald-600 hover:from-teal-500 hover:to-emerald-500 text-white shadow-emerald-500/20 cursor-pointer"
                      : "bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white shadow-indigo-500/20 cursor-pointer"
                    }`}
                >
                  {generatingVoiceover ? (
                    <>
                      <span className="w-3.5 h-3.5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                      Generating Voiceover...
                    </>
                  ) : voiceover ? (
                    <>
                      <span>✓</span> Voiceover Ready (Regenerate)
                    </>
                  ) : (
                    <>
                      <span>🎙️</span> Generate Voiceover
                    </>
                  )}
                </button>
              </div>

              {/* Generating Voiceover Progress Banner */}
              {generatingVoiceover && (
                <div className="p-3.5 rounded-xl bg-indigo-950/60 border border-indigo-500/40 space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-indigo-400 animate-ping" />
                      <span className="font-semibold text-indigo-300">
                        Synthesizing Voiceover Audio with Chatterbox...
                      </span>
                    </div>
                    <span className="font-mono text-indigo-300 font-bold">
                      {latestJob?.stage === "GENERATING_VOICEOVER" ? `${latestJob?.progress_percent || 0}%` : "In Progress"}
                    </span>
                  </div>
                  <div className="w-full bg-slate-950 rounded-full h-1.5 overflow-hidden border border-slate-800">
                    <div
                      className="bg-gradient-to-r from-indigo-500 to-purple-500 h-1.5 transition-all duration-500 rounded-full"
                      style={{ width: `${Math.max(latestJob?.stage === "GENERATING_VOICEOVER" ? (latestJob?.progress_percent || 0) : 30, 20)}%` }}
                    />
                  </div>
                  <div className="text-[11px] text-slate-400 flex items-center justify-between">
                    <span>Chunking narration (&le; 280 chars) • Gradio 6 SSE streaming</span>
                    <span>Direct ZeroGPU execution</span>
                  </div>
                </div>
              )}

              {/* Voiceover Error Banner */}
              {voiceoverError && (
                <div className="p-3.5 rounded-xl bg-red-950/40 border border-red-500/30 flex items-center justify-between text-xs text-red-300">
                  <div className="flex items-center gap-2">
                    <span>⚠️</span>
                    <span>{voiceoverError}</span>
                  </div>
                  <button
                    onClick={() => setVoiceoverError(null)}
                    className="text-red-400 hover:text-white ml-3 text-xs underline cursor-pointer"
                  >
                    Dismiss
                  </button>
                </div>
              )}

              {/* Audio Player and Asset Info */}
              {voiceover && (
                <div className="p-4 rounded-xl bg-slate-950/90 border border-emerald-500/30 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
                      <span className="text-xs font-bold text-emerald-300">Generated Voiceover Audio</span>
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                        {voiceover.sample_rate || 24000} Hz WAV
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-[11px] font-mono text-slate-400">
                      <span>⏱️ {voiceover.duration_seconds.toFixed(2)}s</span>
                      <span>•</span>
                      <span>📦 {((voiceover.file_size || 0) / 1024).toFixed(1)} KB</span>
                      <span>•</span>
                      <a
                        href={`${apiBase}${voiceover.stream_url}`}
                        download={`${project.title.replace(/\s+/g, "_")}_voiceover.wav`}
                        className="text-indigo-400 hover:text-indigo-300 underline font-medium"
                      >
                        Download Audio
                      </a>
                    </div>
                  </div>
                  <audio
                    controls
                    src={`${apiBase}${voiceover.stream_url}`}
                    className="w-full h-11 rounded-lg"
                    preload="metadata"
                  />
                </div>
              )}

              {/* Script Text */}
              <p className="text-sm text-slate-200 leading-relaxed italic bg-slate-950/60 p-4 rounded-xl border border-slate-800">
                "{project.scene_plan.full_narration_script}"
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
                <div className="p-3 rounded-xl bg-slate-950/40 border border-slate-800/60">
                  <span className="text-indigo-400 font-semibold block mb-1">Opening Hook:</span>
                  <span className="text-slate-300">"{project.scene_plan.opening_narration}"</span>
                </div>
                <div className="p-3 rounded-xl bg-slate-950/40 border border-slate-800/60">
                  <span className="text-purple-400 font-semibold block mb-1">Closing Sign-off:</span>
                  <span className="text-slate-300">"{project.scene_plan.closing_narration}"</span>
                </div>
              </div>
            </div>

            {/* Phase 7: Background Music Card (Dual-Mode: AI ACE-Step & Static Library) */}
            <div className="p-6 rounded-2xl bg-gradient-to-br from-purple-950/30 via-slate-900/60 to-pink-950/30 border border-purple-500/20 space-y-5">
              {/* Card Header & Mode Switcher */}
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
                <div className="flex items-center gap-2">
                  <span className="text-xl">🎵</span>
                  <div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                      Background Soundtrack
                      {music && (
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 normal-case">
                          {music.provider === "static_library" ? "Library Selected" : "AI Generated"}
                        </span>
                      )}
                    </h3>
                    <p className="text-xs text-slate-400">
                      Choose between royalty-free static library tracks or AI synthesis
                    </p>
                  </div>
                </div>

                {/* Mode Switcher Tabs */}
                <div className="flex items-center bg-slate-950 p-1 rounded-xl border border-slate-800 text-xs font-medium">
                  <button
                    onClick={() => setMusicTab("library")}
                    className={`px-3 py-1.5 rounded-lg transition-all ${musicTab === "library"
                      ? "bg-purple-600 text-white shadow-sm"
                      : "text-slate-400 hover:text-white"
                      }`}
                  >
                    📚 Music Library
                  </button>
                  <button
                    onClick={() => setMusicTab("ai")}
                    className={`px-3 py-1.5 rounded-lg transition-all ${musicTab === "ai"
                      ? "bg-purple-600 text-white shadow-sm"
                      : "text-slate-400 hover:text-white"
                      }`}
                  >
                    ✨ AI Generated (ACE-Step)
                  </button>
                </div>
              </div>

              {/* Informational Callout when No Predefined Track is Selected */}
              {!music && (
                <div className="p-4 rounded-xl bg-purple-950/40 border border-purple-500/30 flex items-start gap-3 text-xs text-purple-200">
                  <span className="text-lg">💡</span>
                  <div className="space-y-1">
                    <p className="font-semibold text-white">
                      Conditional Music Selection Workflow
                    </p>
                    <p className="text-purple-300/90 leading-relaxed">
                      You can select a royalty-free predefined track from the <strong>Music Library</strong> below, or leave it unselected.
                      If you do <strong>not</strong> select a library track, the system will automatically synthesize custom AI background music matching your project mood (<span className="font-mono text-purple-200">{project.music_mood || "Cinematic"}</span>) using <strong>ACE-Step</strong>.
                    </p>
                  </div>
                </div>
              )}

              {/* Active Selected Track Player (Common to both modes when audio exists) */}
              {music && (
                <div className="p-4 rounded-xl bg-slate-950/90 border border-purple-500/30 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
                      <span className="text-xs font-bold text-purple-200">
                        Active Project Soundtrack: {music.title || music.music_mood || "Soundtrack"}
                      </span>
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30">
                        {music.provider === "static_library" ? "Static Library (Predefined)" : "ACE-Step AI Generated"}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-[11px] font-mono text-slate-400">
                      <span>⏱️ {music.duration_seconds.toFixed(2)}s</span>
                      <span>•</span>
                      <span>📦 {((music.file_size || 0) / 1024).toFixed(1)} KB</span>
                      <span>•</span>
                      <a
                        href={`${apiBase}${music.stream_url}`}
                        download={`${project.title.replace(/\s+/g, "_")}_music.wav`}
                        className="text-purple-400 hover:text-purple-300 underline font-medium"
                      >
                        Download Audio
                      </a>
                      {music.provider === "static_library" && (
                        <>
                          <span>•</span>
                          <button
                            onClick={handleClearMusicSelection}
                            disabled={selectingTrackId === "clearing"}
                            className="text-amber-400 hover:text-amber-300 underline font-medium cursor-pointer"
                          >
                            {selectingTrackId === "clearing" ? "Clearing..." : "Switch to AI Music"}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  <audio
                    controls
                    src={`${apiBase}${music.stream_url}`}
                    className="w-full h-11 rounded-lg"
                    preload="metadata"
                  />
                  {music.attribution && (
                    <div className="text-[11px] text-slate-400 italic">
                      Attribution: {music.attribution}
                    </div>
                  )}
                </div>
              )}

              {/* Error Banner */}
              {musicError && (
                <div className="p-3.5 rounded-xl bg-red-950/40 border border-red-500/30 flex items-center justify-between text-xs text-red-300">
                  <div className="flex items-center gap-2">
                    <span>⚠️</span>
                    <span>{musicError}</span>
                  </div>
                  <button
                    onClick={() => setMusicError(null)}
                    className="text-red-400 hover:text-white ml-3 text-xs underline cursor-pointer"
                  >
                    Dismiss
                  </button>
                </div>
              )}

              {/* Mode A: AI Generation with ACE-Step */}
              {musicTab === "ai" && (
                <div className="space-y-4 pt-1">
                  {music?.provider === "static_library" && (
                    <div className="p-3 rounded-xl bg-amber-950/30 border border-amber-500/30 text-xs text-amber-200 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span>ℹ️</span>
                        <span>
                          A predefined library track (<strong>{music.title}</strong>) is currently selected. Generating AI music will automatically unselect the library track.
                        </span>
                      </div>
                      <button
                        onClick={handleClearMusicSelection}
                        className="px-2.5 py-1 rounded bg-amber-600/30 text-amber-200 border border-amber-500/40 hover:bg-amber-600/50 text-[11px] font-medium cursor-pointer"
                      >
                        Clear Selection
                      </button>
                    </div>
                  )}

                  <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                    <div>
                      <h4 className="text-xs font-semibold text-white uppercase tracking-wider">
                        ACE-Step Multi-Instrumental Synthesis
                      </h4>
                      <p className="text-xs text-slate-400 mt-0.5">
                        Synthesize an ambient score customized to the scene plan mood ({project.music_mood || "Cinematic"}).
                      </p>
                    </div>

                    <button
                      onClick={() => handleStartMusicGeneration(!!music)}
                      disabled={generatingMusic || analyzing}
                      className={`px-4 py-2 rounded-xl font-semibold text-xs shadow-md flex items-center justify-center gap-2 transition-all ${generatingMusic
                        ? "bg-slate-800 text-slate-400 cursor-not-allowed border border-slate-700"
                        : music?.provider === "ace_step"
                          ? "bg-gradient-to-r from-teal-600 to-emerald-600 hover:from-teal-500 hover:to-emerald-500 text-white shadow-emerald-500/20 cursor-pointer"
                          : "bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white shadow-purple-500/20 cursor-pointer"
                        }`}
                    >
                      {generatingMusic ? (
                        <>
                          <span className="w-3.5 h-3.5 border-2 border-purple-400 border-t-transparent rounded-full animate-spin" />
                          Generating with ACE-Step...
                        </>
                      ) : music?.provider === "ace_step" ? (
                        <>
                          <span>✓</span> Regenerate AI Music
                        </>
                      ) : (
                        <>
                          <span>🎵</span> Generate AI Music
                        </>
                      )}
                    </button>
                  </div>

                  {/* Generating Music Progress Banner */}
                  {generatingMusic && (
                    <div className="p-3.5 rounded-xl bg-purple-950/60 border border-purple-500/40 space-y-2">
                      <div className="flex items-center justify-between text-xs">
                        <div className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-purple-400 animate-ping" />
                          <span className="font-semibold text-purple-300">
                            Synthesizing ACE-Step Music Track...
                          </span>
                        </div>
                        <span className="font-mono text-purple-300 font-bold">
                          {latestJob?.stage === "GENERATING_MUSIC" ? `${latestJob?.progress_percent || 0}%` : "In Progress"}
                        </span>
                      </div>
                      <div className="w-full bg-slate-950 rounded-full h-1.5 overflow-hidden border border-slate-800">
                        <div
                          className="bg-gradient-to-r from-purple-500 to-pink-500 h-1.5 transition-all duration-500 rounded-full"
                          style={{ width: `${Math.max(latestJob?.stage === "GENERATING_MUSIC" ? (latestJob?.progress_percent || 0) : 30, 20)}%` }}
                        />
                      </div>
                      <div className="text-[11px] text-slate-400 flex items-center justify-between">
                        <span>Mood: {project.music_mood || "Cinematic"} • Gradio 5 SSE queue</span>
                        <span>ZeroGPU hosted pipeline</span>
                      </div>
                    </div>
                  )}

                  {/* Soundtrack Prompt / Mood details */}
                  <div className="text-xs text-slate-300 bg-slate-950/60 p-3.5 rounded-xl border border-slate-800 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-purple-400 font-semibold">Mood Directive:</span>
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-200 font-mono text-[11px]">
                        {project.music_mood || "Cinematic"}
                      </span>
                    </div>
                    {project.scene_plan.background_music_prompt && (
                      <p className="text-slate-400 italic text-[11px] pt-1">
                        "{project.scene_plan.background_music_prompt}"
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Mode B: Predefined Static Music Library */}
              {musicTab === "library" && (
                <div className="space-y-4 pt-1">
                  <div className="p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-300">
                    💡 <strong>Tip:</strong> Selecting a predefined track will use it directly for your final video and bypass ACE-Step AI generation. If you do not select a track, ACE-Step AI music generation will be used automatically.
                  </div>

                  {/* Category Filter Pills */}
                  <div className="flex flex-wrap items-center gap-1.5">
                    <button
                      onClick={() => setSelectedCategory("all")}
                      className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${selectedCategory === "all"
                        ? "bg-purple-600 text-white shadow-sm"
                        : "bg-slate-950 text-slate-400 hover:text-white border border-slate-800"
                        }`}
                    >
                      All Tracks
                    </button>
                    {categories.map((cat) => (
                      <button
                        key={cat}
                        onClick={() => setSelectedCategory(cat)}
                        className={`px-3 py-1 rounded-full text-xs font-medium capitalize transition-all ${selectedCategory === cat
                          ? "bg-purple-600 text-white shadow-sm"
                          : "bg-slate-950 text-slate-400 hover:text-white border border-slate-800"
                          }`}
                      >
                        {cat}
                      </button>
                    ))}
                  </div>

                  {/* Loading State */}
                  {loadingTracks && (
                    <div className="p-6 text-center text-xs text-slate-400 flex items-center justify-center gap-2">
                      <span className="w-3.5 h-3.5 border-2 border-purple-400 border-t-transparent rounded-full animate-spin" />
                      Loading tracks...
                    </div>
                  )}

                  {/* Tracks List */}
                  {!loadingTracks && libraryTracks.length === 0 && (
                    <div className="p-6 text-center text-xs text-slate-500 bg-slate-950/40 rounded-xl border border-slate-800/60">
                      No tracks found in category "{selectedCategory}".
                    </div>
                  )}

                  {!loadingTracks && libraryTracks.length > 0 && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
                      {libraryTracks.map((track) => {
                        const isSelected = music?.track_id === track.id;
                        const isSelecting = selectingTrackId === track.id;

                        return (
                          <div
                            key={track.id}
                            className={`p-4 rounded-xl border transition-all flex flex-col justify-between gap-3 ${isSelected
                              ? "bg-purple-950/40 border-purple-500/50 shadow-md shadow-purple-950/20"
                              : "bg-slate-950/70 border-slate-800/80 hover:border-slate-700"
                              }`}
                          >
                            <div>
                              <div className="flex items-start justify-between gap-2">
                                <div>
                                  <h5 className="text-xs font-bold text-white flex items-center gap-1.5">
                                    {track.title}
                                    {isSelected && (
                                      <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                                        Active
                                      </span>
                                    )}
                                  </h5>
                                  <span className="text-[10px] font-mono uppercase text-purple-400 font-semibold tracking-wider">
                                    {track.category}
                                  </span>
                                </div>
                                <span className="text-[11px] font-mono text-slate-400">
                                  ⏱️ {track.duration_seconds.toFixed(0)}s
                                </span>
                              </div>
                              <p className="text-[11px] text-slate-400 mt-1.5 line-clamp-2">
                                {track.description}
                              </p>
                              <div className="text-[10px] text-slate-500 mt-1 truncate">
                                Licence: {track.license}
                              </div>
                            </div>

                            {/* Preview Audio Controls & Select Button */}
                            <div className="space-y-2 pt-2 border-t border-slate-800/60">
                              <audio
                                controls
                                src={`${apiBase}${track.preview_url}`}
                                className="w-full h-8 rounded"
                                preload="none"
                              />

                              <div className="flex items-center justify-end">
                                <button
                                  onClick={() => isSelected ? handleClearMusicSelection() : handleSelectLibraryTrack(track.id)}
                                  disabled={isSelecting || (!isSelected && !track.available)}
                                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-all ${isSelected
                                    ? "bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 hover:bg-amber-950/40 hover:border-amber-500/40 hover:text-amber-200 cursor-pointer"
                                    : !track.available
                                      ? "bg-slate-800 text-slate-500 cursor-not-allowed"
                                      : "bg-purple-600 hover:bg-purple-500 text-white shadow-sm cursor-pointer"
                                    }`}
                                >
                                  {isSelecting ? (
                                    <>
                                      <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                                      Updating...
                                    </>
                                  ) : isSelected ? (
                                    <>
                                      <span>✓</span> Selected (Click to Remove)
                                    </>
                                  ) : (
                                    <>
                                      <span>✓</span> Select This Track
                                    </>
                                  )}
                                </button>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Scene-by-Scene Breakdown Cards */}
            <div>
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <span>🎬</span> Scene-by-Scene Wan 2.2 Generation Plan
                </h3>
                <span className="text-xs text-slate-400">
                  {project.scene_plan.scenes.length} Scenes of 5s each
                </span>
              </div>

              <div className="space-y-4">
                {project.scene_plan.scenes.map((scene, idx) => {
                  // Find referenced image
                  const refImg = project.images.find((img) =>
                    scene.reference_image_ids.includes(img.id)
                  );
                  const refImgUrl = refImg
                    ? `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"}/projects/${project.id}/images/${refImg.id}/file`
                    : null;

                  const sceneClip = clips.find((c) => c.scene_number === scene.scene_number);

                  return (
                    <div
                      key={scene.scene_number}
                      className="p-5 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col md:flex-row gap-5 hover:border-slate-700 transition-colors"
                    >
                      {/* Left: Thumbnail and Scene Number */}
                      <div className="flex md:flex-col items-center gap-3 md:w-36 flex-shrink-0">
                        {refImgUrl ? (
                          <div className="w-24 h-24 md:w-36 md:h-28 rounded-xl overflow-hidden bg-slate-900 border border-slate-800 flex-shrink-0 relative">
                            <img
                              src={refImgUrl}
                              alt={refImg?.original_filename || "Reference"}
                              className="w-full h-full object-cover"
                            />
                            <div className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-slate-950/80 text-[10px] text-slate-300 font-mono">
                              Photo #{refImg ? project.images.indexOf(refImg) + 1 : idx + 1}
                            </div>
                          </div>
                        ) : (
                          <div className="w-24 h-24 md:w-36 md:h-28 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-center text-xs text-slate-500">
                            Photo #{idx + 1}
                          </div>
                        )}

                        <div className="flex flex-col md:items-center">
                          <span className="text-xs font-mono font-bold text-indigo-400">
                            Scene {scene.scene_number} / {expectedSceneCount}
                          </span>
                          <span className="text-[10px] text-slate-500 font-mono">
                            {scene.duration_seconds}s • {scene.transition}
                          </span>
                        </div>
                      </div>

                      {/* Right: Scene Details & Prompts */}
                      <div className="flex-1 space-y-3">
                        <div className="flex items-start justify-between gap-2">
                          <h4 className="text-sm font-bold text-white">{scene.title}</h4>
                          {sceneClip ? (
                            sceneClip.fallback_used || sceneClip.provider === "ffmpeg_slideshow" ? (
                              <span className="px-2.5 py-0.5 rounded bg-amber-500/20 border border-amber-500/30 text-[10px] font-mono font-semibold text-amber-300 flex items-center gap-1">
                                <span>🖼️</span> Animated Slideshow (Fallback)
                              </span>
                            ) : (
                              <span className="px-2.5 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/30 text-[10px] font-mono font-semibold text-emerald-300 flex items-center gap-1">
                                <span>✓</span> Wan 2.2 AI Generated
                              </span>
                            )
                          ) : (
                            <span className="px-2 py-0.5 rounded bg-slate-800 text-[10px] font-mono text-slate-300">
                              Wan 2.2 / Slideshow Ready
                            </span>
                          )}
                        </div>

                        <p className="text-xs text-slate-300 leading-relaxed">
                          {scene.visual_description}
                        </p>

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                          <div className="p-2.5 rounded-xl bg-slate-900/60 border border-slate-800">
                            <span className="text-[10px] text-slate-500 font-mono uppercase block">Camera Movement</span>
                            <span className="text-slate-200 font-medium">{scene.camera_movement}</span>
                          </div>
                          <div className="p-2.5 rounded-xl bg-slate-900/60 border border-slate-800">
                            <span className="text-[10px] text-slate-500 font-mono uppercase block">Objective</span>
                            <span className="text-slate-200 font-medium">{scene.scene_objective}</span>
                          </div>
                        </div>

                        {/* Motion Prompt */}
                        <div className="p-3 rounded-xl bg-indigo-950/20 border border-indigo-500/20 text-xs">
                          <span className="text-[10px] text-indigo-400 font-mono uppercase font-bold block mb-1">
                            Wan 2.2 Motion Synthesis Prompt:
                          </span>
                          <span className="text-indigo-200/90 leading-relaxed font-mono text-[11px]">
                            {scene.motion_prompt}
                          </span>
                        </div>

                        {/* Spoken Voiceover line */}
                        <div className="p-3 rounded-xl bg-purple-950/20 border border-purple-500/20 text-xs flex items-start gap-2">
                          <span className="text-sm">🗣️</span>
                          <div>
                            <span className="text-[10px] text-purple-400 font-mono uppercase font-bold block mb-0.5">
                              Voiceover Narration (5s):
                            </span>
                            <span className="text-purple-200 italic">
                              "{scene.narration_text}"
                            </span>
                          </div>
                        </div>

                        {/* Video Clip Preview & Controls */}
                        {sceneClip ? (
                          <div className={`mt-4 p-4 rounded-2xl bg-slate-900 border space-y-3 ${
                            sceneClip.fallback_used || sceneClip.provider === "ffmpeg_slideshow"
                              ? "border-amber-500/40"
                              : "border-emerald-500/30"
                          }`}>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <span className={`w-2 h-2 rounded-full animate-pulse ${
                                  sceneClip.fallback_used || sceneClip.provider === "ffmpeg_slideshow"
                                    ? "bg-amber-400"
                                    : "bg-emerald-400"
                                }`} />
                                <span className={`text-xs font-bold ${
                                  sceneClip.fallback_used || sceneClip.provider === "ffmpeg_slideshow"
                                    ? "text-amber-300"
                                    : "text-emerald-300"
                                }`}>
                                  Scene {scene.scene_number} Video Clip {
                                    sceneClip.fallback_used || sceneClip.provider === "ffmpeg_slideshow"
                                      ? "(Animated Slideshow Fallback)"
                                      : "(Wan 2.2 AI)"
                                  }
                                </span>
                              </div>
                              <div className="flex items-center gap-2 text-[11px] text-slate-400 font-mono">
                                <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">{sceneClip.resolution || "720x1280"}</span>
                                <span>•</span>
                                <span>{sceneClip.duration_seconds.toFixed(1)}s</span>
                                <span>•</span>
                                <span>{(sceneClip.file_size / 1024).toFixed(0)} KB</span>
                              </div>
                            </div>

                            {/* Informational Banner for Fallback Scenes */}
                            {(sceneClip.fallback_used || sceneClip.provider === "ffmpeg_slideshow") && (
                              <div className="p-3 rounded-xl bg-amber-950/30 border border-amber-500/30 text-xs text-amber-200/90 flex items-start gap-2.5">
                                <span className="text-base leading-none">ℹ️</span>
                                <div>
                                  <div className="font-semibold text-amber-300 mb-0.5">
                                    Animated Photo Slideshow Fallback Active
                                  </div>
                                  <div className="text-[11px] text-amber-200/80 leading-relaxed">
                                    AI video generation was unavailable or timed out on Hugging Face ZeroGPU; this scene was smoothly animated using FFmpeg Ken Burns ({sceneClip.camera_movement || "subtle motion"}).
                                  </div>
                                </div>
                              </div>
                            )}

                            <div className="rounded-xl overflow-hidden bg-black flex justify-center border border-slate-800">
                              <video
                                controls
                                playsInline
                                preload="metadata"
                                className="max-h-80 w-auto object-contain rounded-xl"
                                src={`${apiBase}${sceneClip.stream_url}`}
                              />
                            </div>

                            <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs">
                              <span className="text-[11px] text-slate-500">
                                HTTP Range 206 stream • AVC1/H.264
                              </span>
                              <div className="flex items-center gap-2">
                                <button
                                  onClick={() => handleStartClipGeneration(scene.scene_number, false)}
                                  disabled={generatingClips}
                                  className="text-xs text-slate-300 hover:text-white px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  <span>🔄</span> Wan 2.2
                                </button>
                                <button
                                  onClick={() => handleStartClipGeneration(scene.scene_number, true)}
                                  disabled={generatingClips}
                                  className="text-xs text-amber-300 hover:text-amber-100 px-2.5 py-1.5 rounded-lg bg-amber-950/50 hover:bg-amber-900/50 border border-amber-500/40 transition-colors flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  <span>⚡</span> Slideshow
                                </button>
                              </div>
                            </div>
                          </div>
                        ) : generatingClips && (activeSceneNumber === scene.scene_number || (!activeSceneNumber && latestJob?.stage === "GENERATING_CLIPS")) ? (
                          <div className="mt-4 p-4 rounded-xl bg-indigo-950/30 border border-indigo-500/30 flex items-center justify-center gap-3 text-xs text-indigo-300">
                            <span className="w-4 h-4 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                            <span>Synthesizing video motion (Wan 2.2 / Slideshow Fallback)...</span>
                          </div>
                        ) : (
                          <div className="mt-4 p-3.5 rounded-xl bg-slate-900/40 border border-slate-800/80 flex flex-wrap items-center justify-between gap-2">
                            <span className="text-xs text-slate-400">
                              Clip not yet generated
                            </span>
                            <div className="flex items-center gap-2">
                              <button
                                onClick={() => handleStartClipGeneration(scene.scene_number, false)}
                                disabled={generatingClips}
                                className="text-xs text-indigo-300 hover:text-white px-3 py-1.5 rounded-lg bg-indigo-950/60 hover:bg-indigo-900/60 border border-indigo-500/40 transition-colors flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <span>🎬</span> Wan 2.2
                              </button>
                              <button
                                onClick={() => handleStartClipGeneration(scene.scene_number, true)}
                                disabled={generatingClips}
                                className="text-xs text-amber-300 hover:text-amber-100 px-3 py-1.5 rounded-lg bg-amber-950/60 hover:bg-amber-900/60 border border-amber-500/40 transition-colors flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <span>⚡</span> Slideshow
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Phase 8: Final Video Assembly & Composition */}
            <div className="p-6 md:p-8 rounded-3xl bg-slate-900 border border-slate-800 shadow-xl space-y-6">
              {/* Header & Main Assembly Trigger */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2.5">
                    <span className="text-xl">🎬</span>
                    <h3 className="text-lg font-bold text-white">Phase 8: Final Video Composition</h3>
                    {finalVideo?.status === "COMPLETED" && (
                      <span className="px-2.5 py-0.5 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-[11px] font-mono text-emerald-300 font-semibold flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                        Final Video Ready
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-1 max-w-2xl leading-relaxed">
                    Normalizes scene clips to target resolution ({project.aspect_ratio}), concatenates scenes in storyboard order, mixes voiceover and background music with audio fades, and renders an optimized H.264 MP4 with FFmpeg.
                  </p>
                </div>

                <div className="flex items-center gap-2.5 flex-shrink-0">
                  <button
                    onClick={() => handleStartComposition(finalVideo?.status === "COMPLETED")}
                    disabled={composing || !isReadyToCompose}
                    className={`px-5 py-2.5 rounded-xl font-semibold text-xs shadow-lg flex items-center justify-center gap-2 transition-all ${composing
                      ? "bg-slate-800 text-slate-400 cursor-not-allowed border border-slate-700"
                      : !isReadyToCompose
                        ? "bg-slate-800/80 text-slate-500 cursor-not-allowed border border-slate-800"
                        : finalVideo?.status === "COMPLETED"
                          ? "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 hover:border-slate-600 cursor-pointer"
                          : "bg-gradient-to-r from-purple-600 via-indigo-600 to-emerald-600 hover:from-purple-500 hover:via-indigo-500 hover:to-emerald-500 text-white shadow-emerald-500/20 cursor-pointer"
                      }`}
                  >
                    {composing ? (
                      <>
                        <span className="w-3.5 h-3.5 border-2 border-purple-400 border-t-transparent rounded-full animate-spin" />
                        <span>Composing Video (FFmpeg)...</span>
                      </>
                    ) : finalVideo?.status === "COMPLETED" ? (
                      <>
                        <span>🔄</span>
                        <span>Re-compose Final Video</span>
                      </>
                    ) : (
                      <>
                        <span>✨</span>
                        <span>Assemble Final Video</span>
                      </>
                    )}
                  </button>
                </div>
              </div>

              {/* Composition Readiness Checklist */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
                {/* 1. Scene Clips */}
                <div className="p-4 rounded-2xl bg-slate-950/70 border border-slate-800/80 space-y-1.5">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-400 flex items-center gap-1.5 font-medium">
                      <span>🎞️</span> Scene Clips
                    </span>
                    {hasAllClips ? (
                      <span className="text-emerald-400 font-mono text-[10px] font-bold">✓ Ready</span>
                    ) : (
                      <span className="text-amber-400 font-mono text-[10px] font-bold">
                        {clips.length} / {planSceneCount}
                      </span>
                    )}
                  </div>
                  <div className="text-xs font-bold text-white">
                    {hasAllClips ? `All ${planSceneCount} Scenes Ready` : `${clips.length} of ${planSceneCount} Scenes Ready`}
                  </div>
                  <p className="text-[10px] text-slate-500">
                    {hasAllClips
                      ? clips.some((c) => c.fallback_used || c.provider === "ffmpeg_slideshow")
                        ? "Clips ready (Wan 2.2 + Slideshow)"
                        : "Wan 2.2 AI clips ready"
                      : "Generate missing clips above"}
                  </p>
                </div>

                {/* 2. Voiceover Narration */}
                <div className="p-4 rounded-2xl bg-slate-950/70 border border-slate-800/80 space-y-1.5">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-400 flex items-center gap-1.5 font-medium">
                      <span>🎙️</span> Voiceover
                    </span>
                    {hasVoiceover ? (
                      <span className="text-emerald-400 font-mono text-[10px] font-bold">✓ Ready</span>
                    ) : (
                      <span className="text-amber-400 font-mono text-[10px] font-bold">Missing</span>
                    )}
                  </div>
                  <div className="text-xs font-bold text-white truncate">
                    {voiceover ? `${voiceover.duration_seconds.toFixed(1)}s Narration` : "Not Generated"}
                  </div>
                  <p className="text-[10px] text-slate-500">
                    {voiceover ? "Chatterbox AI voiceover ready" : "Generate voiceover above"}
                  </p>
                </div>

                {/* 3. Background Music */}
                <div className="p-4 rounded-2xl bg-slate-950/70 border border-slate-800/80 space-y-1.5">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-400 flex items-center gap-1.5 font-medium">
                      <span>🎵</span> Soundtrack
                    </span>
                    {hasMusic ? (
                      <span className="text-emerald-400 font-mono text-[10px] font-bold">✓ Ready</span>
                    ) : (
                      <span className="text-amber-400 font-mono text-[10px] font-bold">Missing</span>
                    )}
                  </div>
                  <div className="text-xs font-bold text-white truncate">
                    {music ? (music.title || (music.provider === "static_library" ? "Library Track" : "AI Soundtrack")) : "Not Selected"}
                  </div>
                  <p className="text-[10px] text-slate-500">
                    {music ? (music.provider === "static_library" ? "Royalty-free library" : "ACE-Step generated") : "Select music track above"}
                  </p>
                </div>

                {/* 4. Output Target Specs */}
                <div className="p-4 rounded-2xl bg-slate-950/70 border border-slate-800/80 space-y-1.5">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-slate-400 flex items-center gap-1.5 font-medium">
                      <span>📐</span> Format & Target
                    </span>
                    <span className="text-indigo-400 font-mono text-[10px] font-bold">
                      {project.aspect_ratio}
                    </span>
                  </div>
                  <div className="text-xs font-bold text-white">
                    {project.aspect_ratio === "16:9" ? "1280x720 Landscape" : project.aspect_ratio === "1:1" ? "720x720 Square" : "720x1280 Portrait"}
                  </div>
                  <p className="text-[10px] text-slate-500">
                    {project.duration_seconds}s • 30 FPS • H.264/AAC
                  </p>
                </div>
              </div>

              {/* Not Ready Guidance Banner */}
              {!isReadyToCompose && !finalVideo && (
                <div className="p-4 rounded-2xl bg-slate-950/60 border border-amber-500/30 text-xs text-amber-300 flex items-start gap-3">
                  <span className="text-base flex-shrink-0">⚠️</span>
                  <div className="leading-relaxed">
                    <span className="font-semibold text-white">Prerequisites incomplete:</span>{" "}
                    {!hasAllClips && (
                      <span className="inline-block mr-2">• Generate all {planSceneCount} scene clips ({clips.length} done).</span>
                    )}
                    {!hasVoiceover && (
                      <span className="inline-block mr-2">• Generate the Chatterbox voiceover narration above.</span>
                    )}
                    {!hasMusic && (
                      <span className="inline-block">• Select or generate background music above.</span>
                    )}
                  </div>
                </div>
              )}

              {/* Error Banner */}
              {compositionError && (
                <div className="p-4 rounded-2xl bg-red-950/40 border border-red-500/30 flex items-center justify-between text-xs text-red-300">
                  <div className="flex items-center gap-2.5">
                    <span className="text-base">⚠️</span>
                    <div>
                      <div className="font-bold text-white">Composition Error</div>
                      <div className="text-red-200/90 mt-0.5">{compositionError}</div>
                    </div>
                  </div>
                  <button
                    onClick={() => setCompositionError(null)}
                    className="text-red-400 hover:text-white ml-4 text-xs underline cursor-pointer"
                  >
                    Dismiss
                  </button>
                </div>
              )}

              {/* Live Composition Progress Banner */}
              {composing && (
                <div className="p-5 rounded-2xl bg-purple-950/50 border border-purple-500/40 space-y-3 shadow-lg shadow-purple-950/30">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="flex items-center gap-2.5">
                      <span className="w-2.5 h-2.5 rounded-full bg-purple-400 animate-ping" />
                      <span className="font-bold text-purple-200">
                        {latestJob?.status === "QUEUED"
                          ? "Queued in composition processing queue..."
                          : (latestJob?.progress_percent || 0) <= 30
                            ? "Preparing video clips, voiceover, and soundtrack..."
                            : (latestJob?.progress_percent || 0) <= 60
                              ? `Normalizing clips to ${project.aspect_ratio} (30 FPS) & concatenating...`
                              : (latestJob?.progress_percent || 0) <= 85
                                ? "Mixing dual audio streams (Voiceover 1.0 + Music 0.22 with fades)..."
                                : "Validating container integrity and optimizing for web streaming..."}
                      </span>
                    </div>
                    <span className="font-mono text-purple-300 font-bold text-sm">
                      {latestJob?.stage === "COMPOSING" ? `${latestJob?.progress_percent || 10}%` : "Composing..."}
                    </span>
                  </div>

                  <div className="w-full bg-slate-950 rounded-full h-2.5 overflow-hidden border border-slate-800">
                    <div
                      className="bg-gradient-to-r from-purple-500 via-indigo-500 to-emerald-500 h-2.5 rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.max(10, latestJob?.stage === "COMPOSING" ? (latestJob?.progress_percent || 10) : 15)}%`,
                      }}
                    />
                  </div>

                  <div className="flex items-center justify-between text-[11px] text-purple-300/70 font-mono">
                    <span>Active Subprocess: FFmpeg libx264 fast preset</span>
                    <span>Stage: COMPOSING ({latestJob?.status || "RUNNING"})</span>
                  </div>
                </div>
              )}

              {/* Final Video Player & Download Card */}
              {finalVideo && finalVideo.status === "COMPLETED" && finalVideo.stream_url && (
                <div className="p-6 rounded-2xl bg-slate-950 border border-emerald-500/40 space-y-5 shadow-2xl shadow-emerald-950/20">
                  <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-800">
                    <div className="flex items-center gap-2.5">
                      <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
                      <h4 className="text-sm font-bold text-white flex items-center gap-2">
                        Final Composed Video
                      </h4>
                      <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] font-mono font-semibold">
                        Ready for Preview & Download
                      </span>
                    </div>

                    <div className="flex flex-wrap items-center gap-2.5">
                      <a
                        href={`${apiBase}${finalVideo.download_url || finalVideo.stream_url}`}
                        download={`${project.title.replace(/\s+/g, "_")}_final.mp4`}
                        className="px-4 py-2 rounded-xl text-xs font-semibold bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-md shadow-emerald-600/30 flex items-center gap-1.5 transition-all cursor-pointer"
                      >
                        <span>⬇️</span> Download Final MP4
                      </a>
                    </div>
                  </div>

                  {/* HTML5 Video Player */}
                  <div className="rounded-2xl overflow-hidden bg-black flex justify-center border border-slate-800/90 shadow-2xl">
                    <video
                      controls
                      playsInline
                      preload="auto"
                      className="max-h-[480px] w-auto object-contain rounded-2xl"
                      src={`${apiBase}${finalVideo.stream_url}`}
                    />
                  </div>

                  {/* Technical Metadata Bar */}
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2.5 text-center">
                    <div className="p-3 rounded-xl bg-slate-900/80 border border-slate-800">
                      <span className="text-[10px] text-slate-500 font-mono uppercase block">Duration</span>
                      <span className="text-xs font-bold text-white font-mono">
                        ⏱️ {(finalVideo.duration_seconds || project.duration_seconds).toFixed(1)}s
                      </span>
                    </div>
                    <div className="p-3 rounded-xl bg-slate-900/80 border border-slate-800">
                      <span className="text-[10px] text-slate-500 font-mono uppercase block">Resolution</span>
                      <span className="text-xs font-bold text-white font-mono">
                        📐 {finalVideo.resolution || (project.aspect_ratio === "16:9" ? "1280x720" : project.aspect_ratio === "1:1" ? "720x720" : "720x1280")}
                      </span>
                    </div>
                    <div className="p-3 rounded-xl bg-slate-900/80 border border-slate-800">
                      <span className="text-[10px] text-slate-500 font-mono uppercase block">File Size</span>
                      <span className="text-xs font-bold text-white font-mono">
                        📦 {finalVideo.file_size ? `${(finalVideo.file_size / (1024 * 1024)).toFixed(2)} MB` : "Ready"}
                      </span>
                    </div>
                    <div className="p-3 rounded-xl bg-slate-900/80 border border-slate-800">
                      <span className="text-[10px] text-slate-500 font-mono uppercase block">Video Codec</span>
                      <span className="text-xs font-bold text-white font-mono">
                        🎞️ {finalVideo.video_codec ? finalVideo.video_codec.toUpperCase() : "H.264"} (30 FPS)
                      </span>
                    </div>
                    <div className="p-3 rounded-xl bg-slate-900/80 border border-slate-800">
                      <span className="text-[10px] text-slate-500 font-mono uppercase block">Audio Codec</span>
                      <span className="text-xs font-bold text-white font-mono">
                        🔊 {finalVideo.audio_codec ? finalVideo.audio_codec.toUpperCase() : "AAC"} Stereo
                      </span>
                    </div>
                    <div className="p-3 rounded-xl bg-slate-900/80 border border-slate-800">
                      <span className="text-[10px] text-slate-500 font-mono uppercase block">Streaming</span>
                      <span className="text-xs font-bold text-emerald-400 font-mono">
                        🚀 FastStart (206)
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
