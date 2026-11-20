import Link from "next/link";
import Header from "@/components/Header";

export default function Home() {
  const occasions = [
    { title: "Birthday", emoji: "🎂", desc: "Celebrate milestones with joy and animated highlights" },
    { title: "Anniversary", emoji: "💍", desc: "Romantic cinematic transitions and warm nostalgia" },
    { title: "Wedding", emoji: "🕊️", desc: "Grand, elegant visual storytelling and orchestral music" },
    { title: "Travel", emoji: "✈️", desc: "Dynamic motion, vibrant colors, and adventure beats" },
    { title: "Celebration", emoji: "🎉", desc: "High-energy festival vibes, fireworks and upbeat rhythms" },
    { title: "Custom Occasion", emoji: "✨", desc: "Tailored visual theme with custom prompts and pacing" },
  ];

  const pipelineSteps = [
    {
      step: "01",
      name: "Image Understanding",
      engine: "Qwen3.6-27B via Groq",
      desc: "Deep visual semantics analyze your photos to craft a structured 5-second scene plan.",
    },
    {
      step: "02",
      name: "Scene Motion Synthesis",
      engine: "Wan 2.2 via Hugging Face Space",
      desc: "Image-to-video generation transforms static photos into vivid cinematic motion clips.",
    },
    {
      step: "03",
      name: "Soundtrack & Voiceover",
      engine: "ACE-Step & Chatterbox via HF Spaces",
      desc: "Creates customized mood-matched background music and synchronized voiceover narration.",
    },
    {
      step: "04",
      name: "Composition & Export",
      engine: "FFmpeg Pipeline",
      desc: "Concatenates normalized clips, text overlays, crossfade transitions and mixed audio into 1080p MP4.",
    },
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col selection:bg-indigo-500 selection:text-white">
      <Header />

      {/* Hero Section */}
      <main className="flex-1 flex flex-col">
        <section className="relative px-6 pt-24 pb-20 max-w-5xl mx-auto text-center flex flex-col items-center">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-indigo-500/30 bg-indigo-500/10 text-indigo-300 text-xs font-medium mb-8">
            <span className="flex h-2 w-2 rounded-full bg-indigo-400 animate-pulse" />
            Wan 2.2 5B & ACE-Step Integration
          </div>

          <h1 className="text-4xl sm:text-6xl font-extrabold tracking-tight text-white max-w-4xl leading-tight">
            Turn Up to 5 Reference Photos into{" "}
            <span className="bg-gradient-to-r from-indigo-400 via-purple-300 to-pink-400 bg-clip-text text-transparent">
              Cinematic AI Videos
            </span>
          </h1>

          <p className="mt-6 text-lg sm:text-xl text-slate-400 max-w-2xl leading-relaxed">
            Upload your memories, pick an occasion, choose your duration (15s, 20s, or 30s), and let our hosted AI pipeline compose a personalized MP4 complete with synchronized music and text overlays.
          </p>

          <div className="mt-10 flex flex-col sm:flex-row gap-4 items-center justify-center w-full max-w-md">
            <Link
              href="/create"
              className="w-full sm:w-auto px-8 py-3.5 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-400 hover:to-purple-500 text-white font-semibold text-base transition-all shadow-lg shadow-indigo-500/25 flex items-center justify-center gap-2"
            >
              Create Video Wizard &rarr;
            </Link>
            <Link
              href="/dashboard"
              className="w-full sm:w-auto px-6 py-3.5 rounded-xl border border-slate-800 bg-slate-900/60 hover:bg-slate-800/80 text-slate-300 font-medium text-base transition-colors flex items-center justify-center"
            >
              View Projects
            </Link>
          </div>

          {/* Quick Metrics */}
          <div className="mt-16 grid grid-cols-3 gap-6 sm:gap-12 border-y border-slate-800/80 py-8 w-full max-w-3xl">
            <div>
              <div className="text-2xl sm:text-3xl font-bold text-white">5 Photos</div>
              <div className="text-xs sm:text-sm text-slate-400 mt-1">Maximum Input</div>
            </div>
            <div>
              <div className="text-2xl sm:text-3xl font-bold text-indigo-400">15-30s</div>
              <div className="text-xs sm:text-sm text-slate-400 mt-1">Custom Duration</div>
            </div>
            <div>
              <div className="text-2xl sm:text-3xl font-bold text-white">100% Hosted</div>
              <div className="text-xs sm:text-sm text-slate-400 mt-1">Zero Local Models</div>
            </div>
          </div>
        </section>

        {/* Occasions Showcase */}
        <section className="px-6 py-16 max-w-6xl mx-auto w-full">
          <div className="text-center mb-12">
            <h2 className="text-2xl sm:text-3xl font-bold text-white">Tailored for Every Occasion</h2>
            <p className="text-slate-400 mt-2 text-sm sm:text-base">
              The AI dynamically adapts prompts, visual pacing, and background music moods to fit the moment.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {occasions.map((occ) => (
              <div
                key={occ.title}
                className="p-6 rounded-2xl border border-slate-800/80 bg-slate-900/40 hover:border-slate-700 transition-colors"
              >
                <div className="text-3xl mb-3">{occ.emoji}</div>
                <h3 className="font-semibold text-lg text-white">{occ.title}</h3>
                <p className="text-sm text-slate-400 mt-2 leading-relaxed">{occ.desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* AI Pipeline Architecture */}
        <section className="px-6 py-16 bg-slate-900/30 border-t border-slate-800/80">
          <div className="max-w-6xl mx-auto">
            <div className="text-center mb-14">
              <span className="text-xs font-semibold uppercase tracking-wider text-indigo-400">Architecture</span>
              <h2 className="text-2xl sm:text-3xl font-bold text-white mt-1">The Multi-Stage AI Pipeline</h2>
              <p className="text-slate-400 mt-2 text-sm sm:text-base max-w-xl mx-auto">
                Powered by asynchronous Celery & Redis background workers, ensuring non-blocking execution with real-time stage tracking.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              {pipelineSteps.map((step) => (
                <div
                  key={step.step}
                  className="p-6 rounded-2xl border border-slate-800 bg-slate-950/60 flex flex-col justify-between"
                >
                  <div>
                    <span className="text-xs font-mono font-bold text-indigo-400">{step.step}</span>
                    <h3 className="font-semibold text-base text-white mt-2">{step.name}</h3>
                    <div className="text-xs text-indigo-300/80 font-mono mt-1">{step.engine}</div>
                    <p className="text-xs text-slate-400 mt-3 leading-relaxed">{step.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800/80 py-8 px-6 text-center text-xs text-slate-500">
        <p>AI-Powered Personalized Video Generation Studio &copy; 2026. Built with Next.js, FastAPI, PostgreSQL & Free Open-Source Hosted Models.</p>
      </footer>
    </div>
  );
}
