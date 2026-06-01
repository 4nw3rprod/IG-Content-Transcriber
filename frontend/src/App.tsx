import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Activity,
  Brain,
  CheckCircle2,
  Clapperboard,
  Clock3,
  Gauge,
  ExternalLink,
  FileAudio2,
  FileJson2,
  FileText,
  Layers3,
  Lightbulb,
  Link2,
  ListVideo,
  LoaderCircle,
  Network,
  Radar,
  Rocket,
  Settings,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Upload,
  X,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"

type JobEvent = {
  time: string
  stage: string
  progress: number
  message: string
}

type VideoInsights = {
  summary: string
  hook: string
  keywords: string[]
  sentiment: string
  cta: string | null
  title_suggestions: string[]
  content_angles: string[]
}

type BatchOverview = {
  summary: string
  recurring_keywords: string[]
  top_hooks: string[]
  cta_patterns: [string, number][]
  video_titles: string[]
}

type VideoResult = {
  status: "ok"
  source_kind: string
  platform: string
  source_label: string
  position: number
  total_videos: number
  video_id: string
  title: string
  uploader: string
  input_url: string
  canonical_url: string
  video_url: string
  caption: string
  taken_at_timestamp: number
  taken_at_iso: string | null
  audio_file: string
  transcript_file: string
  metadata_file: string
  detected_language: string | null
  model: string
  cached: boolean
  transcript_text: string
  ai_insights: VideoInsights
  audio_url: string
  transcript_url: string
  metadata_url: string
}

type BatchResult = {
  status: "ok"
  input_kind: "instagram_profile" | "video" | "audio_upload"
  input_url: string
  canonical_url: string
  model: string
  language_hint: string | null
  total_videos: number
  completed_videos: number
  videos: VideoResult[]
  ai_overview: BatchOverview
  manifest_file: string
  manifest_url: string
}

type Job = {
  id: string
  input_url: string
  model: string
  language: string | null
  input_mode: "url" | "audio_upload"
  status: "queued" | "running" | "completed" | "failed"
  stage: string
  progress: number
  message: string
  created_at: string
  updated_at: string
  result: BatchResult | null
  error: string | null
  events: JobEvent[]
}

const stageOrder = [
  "queued",
  "starting",
  "validating",
  "collecting_videos",
  "preparing_audio",
  "downloading_audio",
  "loading_model",
  "transcribing",
  "generating_insights",
  "writing_files",
  "completed",
] as const

const stageLabels: Record<string, string> = {
  queued: "Queued",
  starting: "Starting",
  validating: "Validate URL",
  collecting_videos: "Resolve source",
  preparing_audio: "Stage audio",
  downloading_audio: "Download audio",
  loading_model: "Load model",
  transcribing: "Transcribe",
  generating_insights: "AI insights",
  writing_files: "Write files",
  completed: "Complete",
}

const modelOptions = ["tiny", "base", "small", "medium", "large"]

const navItems: Array<[string, LucideIcon]> = [
  ["Dashboard", Gauge],
  ["Video Pipeline", Network],
  ["AI Insights", Sparkles],
  ["Transcription Logs", TerminalSquare],
  ["MCP Settings", Settings],
]

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        hour: "numeric",
        minute: "2-digit",
        month: "short",
        day: "numeric",
      }).format(date)
}

function statusVariant(status: Job["status"]) {
  if (status === "completed") return "default"
  if (status === "failed") return "destructive"
  return "secondary"
}

function summarizeInputLabel(inputUrl: string, result: BatchResult | null) {
  if (result?.input_kind === "instagram_profile" && result.videos[0]) {
    return `@${result.videos[0].source_label}`
  }
  if (result?.input_kind === "audio_upload" && result.videos[0]) {
    return result.videos[0].title
  }
  return inputUrl
}

function App() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [selectedVideoId, setSelectedVideoId] = useState<string | null>(null)
  const [inputUrl, setInputUrl] = useState("")
  const [audioFile, setAudioFile] = useState<File | null>(null)
  const [model, setModel] = useState("base")
  const [language, setLanguage] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [loading, setLoading] = useState(true)
  const pollRef = useRef<number | null>(null)
  const audioInputRef = useRef<HTMLInputElement | null>(null)

  const resolvedActiveJobId = activeJobId ?? jobs[0]?.id ?? null
  const activeJob = useMemo(() => jobs.find((job) => job.id === resolvedActiveJobId) ?? null, [jobs, resolvedActiveJobId])
  const activeVideos = useMemo(() => activeJob?.result?.videos ?? [], [activeJob?.result?.videos])
  const selectedVideo = useMemo(
    () => activeVideos.find((video) => video.video_id === selectedVideoId) ?? activeVideos[0] ?? null,
    [activeVideos, selectedVideoId]
  )

  const fetchJson = useCallback(async <T,>(url: string, options?: RequestInit): Promise<T> => {
    const response = await fetch(url, options)
    if (!response.ok) {
      throw new Error(await response.text())
    }
    return response.json() as Promise<T>
  }, [])

  const refreshJobs = useCallback(
    async (initial = false) => {
      try {
        const payload = await fetchJson<Job[]>("/api/jobs")
        setJobs(payload)
      } catch (error) {
        if (initial) {
          toast.error("Failed to load jobs", { description: error instanceof Error ? error.message : "Unknown error" })
        }
      } finally {
        if (initial) {
          setLoading(false)
        }
      }
    },
    [fetchJson]
  )

  useEffect(() => {
    void refreshJobs(true)

    return () => {
      if (pollRef.current) {
        window.clearInterval(pollRef.current)
      }
    }
  }, [refreshJobs])

  useEffect(() => {
    const shouldPoll = activeJob && (activeJob.status === "queued" || activeJob.status === "running")
    if (!shouldPoll) {
      if (pollRef.current) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }

    if (pollRef.current) {
      window.clearInterval(pollRef.current)
    }

    pollRef.current = window.setInterval(() => {
      void refreshJobs()
    }, 1200)

    return () => {
      if (pollRef.current) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [activeJob, refreshJobs])

  useEffect(() => {
    if (activeVideos.length === 0) {
      setSelectedVideoId(null)
      return
    }
    if (!selectedVideoId || !activeVideos.some((video) => video.video_id === selectedVideoId)) {
      setSelectedVideoId(activeVideos[0].video_id)
    }
  }, [activeVideos, selectedVideoId])

  async function submitJob(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!inputUrl.trim() && !audioFile) {
      toast.error("Provide a URL or upload an audio file")
      return
    }

    setSubmitting(true)
    try {
      const job = audioFile
        ? await fetchJson<Job>("/api/jobs/upload", {
            method: "POST",
            body: (() => {
              const formData = new FormData()
              formData.append("audio_file", audioFile)
              formData.append("model", model)
              if (language.trim()) {
                formData.append("language", language.trim())
              }
              return formData
            })(),
          })
        : await fetchJson<Job>("/api/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              input_url: inputUrl.trim(),
              model,
              language: language.trim() || null,
            }),
          })
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)])
      setActiveJobId(job.id)
      setSelectedVideoId(null)
      toast.success("Transcription started", {
        description: audioFile
          ? "Uploaded audio is being transcribed with Whisper and AI insights."
          : "Profile URLs will process the latest 10 videos. Direct video URLs will process one video.",
      })
      setInputUrl("")
      setAudioFile(null)
      if (audioInputRef.current) {
        audioInputRef.current.value = ""
      }
    } catch (error) {
      toast.error("Unable to start transcription", { description: error instanceof Error ? error.message : "Unknown error" })
    } finally {
      setSubmitting(false)
    }
  }

  const completedStages = activeJob ? stageOrder.indexOf(activeJob.stage as (typeof stageOrder)[number]) : -1

  return (
    <div className="relative h-screen overflow-hidden bg-background text-foreground">
      <header className="fixed top-0 right-0 left-0 z-40 flex h-14 items-center justify-between border-b border-[#2D2D30] bg-[#0B0B0C] px-4 lg:left-[260px] lg:px-6">
        <div className="flex items-center gap-3">
          <div className="grid size-7 place-items-center rounded border border-primary/40 bg-primary/10 text-primary">
            <Clapperboard className="size-4" />
          </div>
          <div>
            <p className="text-sm font-bold tracking-tight text-white">Instagram AI Pipeline</p>
            <p className="font-mono text-[10px] text-muted-foreground">Frontend synced from Stitch</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="hidden border-[#2D2D30] bg-[#161618] font-mono text-[#4edea3] sm:inline-flex">
            <span className="size-1.5 rounded-full bg-[#4edea3]" />
            MCP server active
          </Badge>
          <Button variant="ghost" size="icon" className="text-muted-foreground hover:text-white">
            <Settings className="size-4" />
          </Button>
        </div>
      </header>

      <aside className="fixed top-0 bottom-0 left-0 z-50 hidden w-[260px] border-r border-[#2D2D30] bg-[#0B0B0C] lg:flex lg:flex-col">
        <div className="px-6 pt-5 pb-7">
          <h1 className="text-xl font-black tracking-tight text-white">AI Pipeline</h1>
          <p className="font-mono text-xs text-muted-foreground">V1.0.4-stable</p>
        </div>
        <nav className="flex-1 space-y-1 px-3">
          {navItems.map(([label, Icon], index) => (
            <button
              key={String(label)}
              type="button"
              className={`flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors ${
                index === 0
                  ? "border-l-4 border-primary bg-[#212124] text-primary"
                  : "text-muted-foreground hover:bg-[#161618] hover:text-gray-200"
              }`}
            >
              <Icon className="size-4" />
              {label}
            </button>
          ))}
        </nav>
        <div className="space-y-2 border-t border-[#2D2D30] p-4">
          <div className="rounded border border-[#2D2D30] bg-[#161618] p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-white">
              <ShieldCheck className="size-4 text-[#4edea3]" />
              Agent bridge
            </div>
            <p className="text-xs leading-5 text-muted-foreground">CLI, UI, and MCP clients operate the same backend pipeline.</p>
          </div>
        </div>
      </aside>

      <main className="flex h-full min-h-0 flex-col gap-4 px-4 pt-[72px] pb-4 lg:ml-[260px] lg:px-6">
        <Card className="border-[#2D2D30] bg-[#161618] py-0 shadow-none">
          <CardContent className="grid gap-4 px-4 py-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.6fr)] xl:items-end">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="border border-primary/25 bg-primary/10 text-primary">
                  <Rocket className="size-3.5" />
                  Command center
                </Badge>
                <Badge variant="outline" className="border-[#2D2D30] bg-[#0B0B0C] text-[#4edea3]">
                  <Sparkles className="size-3.5" />
                  Groq insights
                </Badge>
                <Badge variant="outline" className="border-[#2D2D30] bg-[#0B0B0C]">
                  <ListVideo className="size-3.5" />
                  Latest 10 profile videos
                </Badge>
                <Badge variant="outline" className="border-[#2D2D30] bg-[#0B0B0C]">
                  <Upload className="size-3.5" />
                  Audio upload
                </Badge>
              </div>
              <div>
                <h2 className="text-2xl font-semibold tracking-tight text-white">Command Center</h2>
                <p className="text-sm text-muted-foreground">
                  Initialize Instagram profile ingestion, direct video transcription, or upload an audio file for direct Whisper transcription.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Sheet>
                  <SheetTrigger asChild>
                    <Button variant="outline" size="sm" className="border-[#2D2D30] bg-[#0B0B0C]">
                      <Activity className="size-4" />
                      Activity log
                    </Button>
                  </SheetTrigger>
                  <SheetContent side="right" className="p-0">
                    <SheetHeader className="border-b">
                      <SheetTitle>Activity log</SheetTitle>
                      <SheetDescription>Everything the active batch is doing, in order.</SheetDescription>
                    </SheetHeader>
                    <ScrollArea className="min-h-0 flex-1 px-6">
                      <div className="space-y-3 pb-6">
                        {(activeJob?.events?.length ? [...activeJob.events].reverse() : []).map((event) => (
                          <div key={`${event.time}-${event.stage}`} className="rounded border border-[#2D2D30] bg-[#161618] p-3">
                            <div className="mb-2 flex items-center justify-between gap-3">
                              <Badge variant="outline">{stageLabels[event.stage] ?? event.stage}</Badge>
                              <span className="text-muted-foreground text-xs">{formatTime(event.time)}</span>
                            </div>
                            <p className="text-sm">{event.message}</p>
                            <p className="text-muted-foreground mt-1 text-xs">{event.progress}% complete</p>
                          </div>
                        ))}
                        {!activeJob?.events?.length ? <p className="text-muted-foreground text-sm">No events yet.</p> : null}
                      </div>
                    </ScrollArea>
                  </SheetContent>
                </Sheet>

                <Sheet>
                  <SheetTrigger asChild>
                    <Button variant="outline" size="sm" className="border-[#2D2D30] bg-[#0B0B0C]">
                      <ListVideo className="size-4" />
                      Recent jobs
                    </Button>
                  </SheetTrigger>
                  <SheetContent side="right" className="p-0">
                    <SheetHeader className="border-b">
                      <SheetTitle>Recent jobs</SheetTitle>
                      <SheetDescription>Jump between previous profile batches and direct video runs.</SheetDescription>
                    </SheetHeader>
                    <ScrollArea className="min-h-0 flex-1 px-6">
                      <div className="space-y-3 pb-6">
                        {jobs.map((job) => {
                          const selected = job.id === resolvedActiveJobId
                          return (
                            <button
                              key={job.id}
                              type="button"
                              onClick={() => setActiveJobId(job.id)}
                              className={`w-full rounded-xl border p-3 text-left transition-colors ${
                                selected ? "border-primary/40 bg-primary/10" : "border-[#2D2D30] bg-[#161618] hover:bg-[#212124]"
                              }`}
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <p className="truncate text-sm font-medium">{summarizeInputLabel(job.input_url, job.result)}</p>
                                  <p className="text-muted-foreground mt-1 text-xs">
                                    {job.result ? `${job.result.completed_videos}/${job.result.total_videos} video(s)` : job.message}
                                  </p>
                                </div>
                                <Badge variant={statusVariant(job.status)}>{job.status}</Badge>
                              </div>
                              <div className="text-muted-foreground mt-3 flex items-center justify-between text-xs">
                                <span>{formatTime(job.created_at)}</span>
                                <span>{job.progress}%</span>
                              </div>
                            </button>
                          )
                        })}
                        {jobs.length === 0 ? <p className="text-muted-foreground text-sm">No jobs yet. Start one from the top bar.</p> : null}
                      </div>
                    </ScrollArea>
                  </SheetContent>
                </Sheet>
              </div>
            </div>

            <form onSubmit={submitJob} className="grid w-full gap-3 lg:grid-cols-[minmax(0,1.8fr)_160px_140px_auto]">
              <div className="space-y-2">
                <Label htmlFor="input-url">Instagram profile URL, video URL, or upload audio</Label>
                <Input
                  id="input-url"
                  value={inputUrl}
                  onChange={(event) => setInputUrl(event.target.value)}
                  className="border-[#2D2D30] bg-[#0B0B0C]"
                  placeholder="https://www.instagram.com/nike/ or leave blank and upload audio"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    ref={audioInputRef}
                    type="file"
                    accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.webm,.mp4,.mpeg,.mpga,audio/*"
                    className="hidden"
                    onChange={(event) => setAudioFile(event.target.files?.[0] ?? null)}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="border-[#2D2D30] bg-[#0B0B0C]"
                    onClick={() => audioInputRef.current?.click()}
                  >
                    <Upload className="size-4" />
                    Upload audio
                  </Button>
                  {audioFile ? (
                    <div className="inline-flex items-center gap-2 rounded border border-[#2D2D30] bg-[#0B0B0C] px-3 py-1.5 text-xs text-muted-foreground">
                      <FileAudio2 className="size-3.5 text-primary" />
                      <span className="max-w-[220px] truncate">{audioFile.name}</span>
                      <button
                        type="button"
                        onClick={() => {
                          setAudioFile(null)
                          if (audioInputRef.current) {
                            audioInputRef.current.value = ""
                          }
                        }}
                        className="text-muted-foreground transition-colors hover:text-white"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">Supported: mp3, wav, m4a, aac, flac, ogg, webm</span>
                  )}
                </div>
              </div>
              <div className="space-y-2">
                <Label>Whisper model</Label>
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger>
                    <SelectValue placeholder="Model" />
                  </SelectTrigger>
                  <SelectContent>
                    {modelOptions.map((option) => (
                      <SelectItem key={option} value={option}>
                        {option}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="language-hint">Language</Label>
                <Input
                  id="language-hint"
                  value={language}
                  onChange={(event) => setLanguage(event.target.value)}
                  className="border-[#2D2D30] bg-[#0B0B0C]"
                  placeholder="en"
                />
              </div>
              <div className="flex items-end">
                <Button type="submit" className="w-full bg-primary text-white hover:bg-[#005bc1]" disabled={submitting}>
                  {submitting ? <LoaderCircle className="animate-spin" /> : <Radar />}
                  {audioFile ? "Transcribe audio" : "Fetch profile"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <div className="grid gap-3 md:grid-cols-3">
          <Card className="relative overflow-hidden border-[#2D2D30] bg-[#161618] py-0 shadow-none">
            <div className="absolute top-0 bottom-0 left-0 w-1 bg-primary" />
            <CardContent className="flex items-center justify-between px-4 py-3">
              <div>
                <p className="text-xs font-medium text-muted-foreground">Videos in active batch</p>
                <p className="text-2xl font-black text-white">{activeJob?.result?.total_videos ?? activeVideos.length}</p>
              </div>
              <ListVideo className="size-8 text-primary" />
            </CardContent>
          </Card>
          <Card className="relative overflow-hidden border-[#2D2D30] bg-[#161618] py-0 shadow-none">
            <div className="absolute top-0 bottom-0 left-0 w-1 bg-[#4edea3]" />
            <CardContent className="flex items-center justify-between px-4 py-3">
              <div>
                <p className="text-xs font-medium text-muted-foreground">Pipeline progress</p>
                <p className="text-2xl font-black text-white">{activeJob?.progress ?? 0}%</p>
              </div>
              <Activity className="size-8 text-[#4edea3]" />
            </CardContent>
          </Card>
          <Card className="relative overflow-hidden border-[#2D2D30] bg-[#161618] py-0 shadow-none">
            <div className="absolute top-0 bottom-0 left-0 w-1 bg-[#ef6719]" />
            <CardContent className="flex items-center justify-between px-4 py-3">
              <div>
                <p className="text-xs font-medium text-muted-foreground">Insights generated</p>
                <p className="text-2xl font-black text-white">{activeVideos.filter((video) => video.ai_insights).length}</p>
              </div>
              <Brain className="size-8 text-[#ef6719]" />
            </CardContent>
          </Card>
        </div>

        <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[370px_minmax(0,1fr)_370px]">
          <Card className="min-h-0 border-[#2D2D30] bg-[#161618] shadow-none">
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-lg">Pipeline status</CardTitle>
                  <CardDescription>Current stage plus per-video batch results.</CardDescription>
                </div>
                {activeJob ? <Badge variant={statusVariant(activeJob.status)}>{activeJob.status}</Badge> : null}
              </div>
            </CardHeader>
            <CardContent className="flex min-h-0 flex-1 flex-col gap-4">
              <div className="rounded border border-[#2D2D30] bg-[#0B0B0C] p-4">
                <div className="mb-3 flex items-end justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {activeJob ? summarizeInputLabel(activeJob.input_url, activeJob.result) : "No active job"}
                    </p>
                    <p className="text-muted-foreground text-xs">{activeJob?.message ?? "Start a transcription to populate the dashboard."}</p>
                  </div>
                  <div className="text-2xl font-semibold tracking-tight">{activeJob?.progress ?? 0}%</div>
                </div>
                <Progress value={activeJob?.progress ?? 0} />
                {activeJob?.result ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge variant="outline">
                      {activeJob.result.input_kind === "instagram_profile"
                        ? "Profile batch"
                        : activeJob.result.input_kind === "audio_upload"
                          ? "Uploaded audio"
                          : "Single video"}
                    </Badge>
                    <Badge variant="secondary">{activeJob.result.completed_videos} video(s)</Badge>
                    <Badge variant="outline">{activeJob.result.model}</Badge>
                  </div>
                ) : null}
              </div>

              <ScrollArea className="min-h-0 rounded border border-[#2D2D30] bg-[#0B0B0C]">
                <div className="space-y-2 p-3">
                  {stageOrder.map((stage, index) => {
                    const done = completedStages >= index || activeJob?.status === "completed"
                    const active = activeJob?.stage === stage
                    return (
                      <div
                        key={stage}
                        className={`rounded-lg border p-3 transition-colors ${
                          active
                            ? "border-primary/50 bg-primary/10"
                            : done
                              ? "border-primary/20 bg-primary/5"
                              : "border-[#2D2D30] bg-[#161618]"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            {done ? <CheckCircle2 className="text-primary size-4" /> : <Clock3 className="text-muted-foreground size-4" />}
                            <span className="text-sm font-medium">{stageLabels[stage]}</span>
                          </div>
                          <Badge variant={active ? "default" : "outline"}>{done ? "done" : active ? "live" : "wait"}</Badge>
                        </div>
                        <p className="text-muted-foreground mt-2 text-xs">{active ? activeJob?.message : "Standing by"}</p>
                      </div>
                    )
                  })}
                </div>
              </ScrollArea>

              <Separator />

              <div className="min-h-0 flex-1">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">Videos in result set</p>
                  {activeJob?.result ? <Badge variant="outline">{activeJob.result.total_videos}</Badge> : null}
                </div>
                <ScrollArea className="h-full rounded border border-[#2D2D30] bg-[#0B0B0C]">
                  <div className="space-y-2 p-3">
                    {activeJob?.result?.videos?.map((video) => {
                      const selected = video.video_id === selectedVideo?.video_id
                      return (
                        <button
                          key={video.video_id}
                          type="button"
                          onClick={() => setSelectedVideoId(video.video_id)}
                          className={`w-full rounded-lg border p-3 text-left transition-colors ${
                            selected ? "border-primary/45 bg-primary/10" : "border-[#2D2D30] bg-[#161618] hover:bg-[#212124]"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium">{video.position}. {video.title}</p>
                              <p className="text-muted-foreground mt-1 text-xs">{video.detected_language ?? "auto"} · {video.cached ? "cached" : "fresh"}</p>
                            </div>
                            <Badge variant="outline">{video.position}/{video.total_videos}</Badge>
                          </div>
                        </button>
                      )
                    })}
                    {!activeJob?.result?.videos?.length ? (
                      <p className="text-muted-foreground p-2 text-sm">Completed videos will appear here after the job finishes.</p>
                    ) : null}
                  </div>
                </ScrollArea>
              </div>
            </CardContent>
          </Card>

          <Card className="min-h-0 border-[#2D2D30] bg-[#161618] shadow-none">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-lg">Transcript workspace</CardTitle>
                  <CardDescription>Transcript plus output artifacts for the selected video.</CardDescription>
                </div>
                {activeJob?.result ? (
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="secondary">
                      {activeJob.result.input_kind === "instagram_profile"
                        ? "Latest 10 profile videos"
                        : activeJob.result.input_kind === "audio_upload"
                          ? "Uploaded audio file"
                          : "Single video URL"}
                    </Badge>
                    <Badge variant="outline">{activeJob.result.model}</Badge>
                  </div>
                ) : null}
              </div>
            </CardHeader>
            <CardContent className="grid min-h-0 flex-1 grid-rows-[auto_auto_minmax(0,1fr)] gap-4">
              <Card className="gap-3 rounded border-[#2D2D30] bg-[#0B0B0C] py-4 shadow-none">
                <CardContent className="space-y-3 px-4">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <Link2 className="size-4" />
                    Output set
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {activeJob?.result ? (
                      <>
                        <Button asChild size="sm" variant="outline">
                          <a href={activeJob.result.manifest_url} target="_blank" rel="noreferrer">
                            <FileJson2 />
                            Manifest
                          </a>
                        </Button>
                        {selectedVideo ? (
                          <>
                            <Button asChild size="sm" variant="outline">
                              <a href={selectedVideo.audio_url} target="_blank" rel="noreferrer">
                                <FileAudio2 />
                                Audio
                              </a>
                            </Button>
                            <Button asChild size="sm" variant="outline">
                              <a href={selectedVideo.transcript_url} target="_blank" rel="noreferrer">
                                <FileText />
                                Transcript
                              </a>
                            </Button>
                            <Button asChild size="sm" variant="outline">
                              <a href={selectedVideo.metadata_url} target="_blank" rel="noreferrer">
                                <FileJson2 />
                                Metadata
                              </a>
                            </Button>
                            {selectedVideo.video_url ? (
                              <Button asChild size="sm">
                                <a href={selectedVideo.video_url} target="_blank" rel="noreferrer">
                                  <ExternalLink />
                                  Video
                                </a>
                              </Button>
                            ) : null}
                          </>
                        ) : null}
                      </>
                    ) : (
                      <p className="text-muted-foreground text-xs">Artifacts will unlock here after processing completes.</p>
                    )}
                  </div>
                </CardContent>
              </Card>

              <Separator />

              <Card className="min-h-0 rounded border-[#2D2D30] bg-[#050505] py-0 text-zinc-50 shadow-none">
                <CardContent className="flex h-full min-h-0 flex-col px-0">
                  <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <FileText className="size-4 text-emerald-300" />
                      {selectedVideo ? selectedVideo.title : "Transcript"}
                    </div>
                    {activeJob?.updated_at ? <span className="text-xs text-zinc-400">Updated {formatTime(activeJob.updated_at)}</span> : null}
                  </div>
                  <ScrollArea className="min-h-0 flex-1">
                    <div className="p-4">
                      {loading ? (
                        <div className="space-y-3">
                          <Skeleton className="h-4 w-10/12 bg-white/10" />
                          <Skeleton className="h-4 w-11/12 bg-white/10" />
                          <Skeleton className="h-4 w-8/12 bg-white/10" />
                        </div>
                      ) : (
                        <pre className="font-mono text-sm leading-7 whitespace-pre-wrap text-zinc-100">
                          {selectedVideo?.transcript_text ||
                            (activeJob?.status === "failed"
                              ? activeJob.error || activeJob.message
                              : "Select a processed video to inspect the transcript.")}
                        </pre>
                      )}
                    </div>
                  </ScrollArea>
                </CardContent>
              </Card>
            </CardContent>
          </Card>

          <Card className="min-h-0 border-[#2D2D30] bg-[#161618] shadow-none">
            <CardHeader className="pb-3">
              <CardTitle className="text-lg">AI insights</CardTitle>
              <CardDescription>Batch overview and per-video AI interpretation stay visible here.</CardDescription>
            </CardHeader>
            <CardContent className="min-h-0 flex-1 px-0">
              <ScrollArea className="h-full px-6">
                <div className="space-y-3 pb-6">
                  <Card className="gap-3 rounded border-[#2D2D30] bg-[#0B0B0C] py-4 shadow-none">
                    <CardContent className="space-y-3 px-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Brain className="size-4" />
                        Batch AI overview
                      </div>
                      <p className="text-sm">
                        {activeJob?.result?.ai_overview.summary || "AI overview appears after the batch finishes."}
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {activeJob?.result?.ai_overview.recurring_keywords?.slice(0, 8).map((keyword) => (
                          <Badge key={keyword} variant="outline">{keyword}</Badge>
                        ))}
                      </div>
                    </CardContent>
                  </Card>

                  <Card className="gap-3 rounded border-[#2D2D30] bg-[#0B0B0C] py-4 shadow-none">
                    <CardContent className="space-y-3 px-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Lightbulb className="size-4" />
                        Selected video insight
                      </div>
                      <p className="text-sm">{selectedVideo?.ai_insights.summary || "Per-video AI insights appear here after processing."}</p>
                      <div className="flex flex-wrap gap-2">
                        {selectedVideo?.ai_insights.keywords?.slice(0, 6).map((keyword) => (
                          <Badge key={keyword} variant="outline">{keyword}</Badge>
                        ))}
                      </div>
                      {selectedVideo?.ai_insights.cta ? (
                        <Badge className="w-fit">CTA detected: {selectedVideo.ai_insights.cta}</Badge>
                      ) : null}
                    </CardContent>
                  </Card>

                  <Card className="gap-3 rounded border-[#2D2D30] bg-[#0B0B0C] py-4 shadow-none">
                    <CardContent className="space-y-2 px-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Sparkles className="size-4" />
                        Hook
                      </div>
                      <p className="text-sm">{selectedVideo?.ai_insights.hook || "No hook available yet."}</p>
                    </CardContent>
                  </Card>

                  <Card className="gap-3 rounded border-[#2D2D30] bg-[#0B0B0C] py-4 shadow-none">
                    <CardContent className="space-y-2 px-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Layers3 className="size-4" />
                        Title suggestions
                      </div>
                      <div className="space-y-2">
                        {selectedVideo?.ai_insights.title_suggestions?.map((suggestion) => (
                          <div key={suggestion} className="rounded border border-[#2D2D30] bg-[#161618] p-2 text-sm">{suggestion}</div>
                        )) || <p className="text-sm">Title suggestions will appear here.</p>}
                      </div>
                    </CardContent>
                  </Card>

                  <Card className="gap-3 rounded border-[#2D2D30] bg-[#0B0B0C] py-4 shadow-none">
                    <CardContent className="space-y-2 px-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Activity className="size-4" />
                        Content angles
                      </div>
                      <div className="space-y-2">
                        {selectedVideo?.ai_insights.content_angles?.map((angle) => (
                          <div key={angle} className="rounded border border-[#2D2D30] bg-[#161618] p-2 text-sm">{angle}</div>
                        )) || <p className="text-sm">Content angle ideas will appear here.</p>}
                      </div>
                    </CardContent>
                  </Card>
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  )
}

export default App
