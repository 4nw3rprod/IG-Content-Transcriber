#!/usr/bin/env python3
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ig_transcriber import PipelineError, run_audio_file_transcription, run_transcription


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
UPLOAD_STAGING_DIR = BASE_DIR / ".upload_staging"
UPLOAD_STAGING_DIR.mkdir(exist_ok=True)


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class JobRecord:
    id: str
    input_url: str
    model: str
    language: Optional[str]
    input_mode: str = "url"
    upload_file_path: Optional[str] = None
    status: str = "queued"
    stage: str = "queued"
    progress: int = 0
    message: str = "Waiting to start"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    events: list[Dict[str, Any]] = field(default_factory=list)


class JobRequest(BaseModel):
    input_url: str = Field(..., min_length=1)
    model: str = Field(default="base")
    language: Optional[str] = None


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ig-transcriber")

    def list_jobs(self) -> list[JobRecord]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def get_job(self, job_id: str) -> JobRecord:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job

    def create_job(self, request: JobRequest) -> JobRecord:
        job_id = uuid.uuid4().hex[:10]
        job = JobRecord(
            id=job_id,
            input_url=request.input_url.strip(),
            model=request.model.strip() or "base",
            language=(request.language or "").strip() or None,
        )
        self._append_event(job, "queued", 0, "Job created")
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run_job, job_id)
        return job

    def create_upload_job(self, *, filename: str, staged_path: Path, model: str, language: Optional[str]) -> JobRecord:
        job_id = uuid.uuid4().hex[:10]
        job = JobRecord(
            id=job_id,
            input_url=filename,
            model=model.strip() or "base",
            language=(language or "").strip() or None,
            input_mode="audio_upload",
            upload_file_path=str(staged_path),
        )
        self._append_event(job, "queued", 0, "Audio upload job created")
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run_job, job_id)
        return job

    def _append_event(self, job: JobRecord, stage: str, progress: int, message: str) -> None:
        event = {
            "time": utc_now(),
            "stage": stage,
            "progress": progress,
            "message": message,
        }
        job.events = (job.events + [event])[-25:]
        job.updated_at = event["time"]

    def _set_progress(self, job_id: str, stage: str, progress: int, message: str, status: Optional[str] = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.stage = stage
            job.progress = progress
            job.message = message
            if status is not None:
                job.status = status
            self._append_event(job, stage, progress, message)

    def _run_job(self, job_id: str) -> None:
        self._set_progress(job_id, "starting", 2, "Starting pipeline", status="running")
        job = self.get_job(job_id)

        def progress_callback(stage: str, progress: int, message: str) -> None:
            self._set_progress(job_id, stage, progress, message, status="running")

        try:
            try:
                if job.input_mode == "audio_upload":
                    if not job.upload_file_path:
                        raise PipelineError("Uploaded audio file is missing.")
                    result = run_audio_file_transcription(
                        job.upload_file_path,
                        original_filename=job.input_url,
                        output_dir=OUTPUT_DIR,
                        model_name=job.model,
                        language=job.language,
                        progress_callback=progress_callback,
                    )
                else:
                    result = run_transcription(
                        job.input_url,
                        output_dir=OUTPUT_DIR,
                        model_name=job.model,
                        language=job.language,
                        progress_callback=progress_callback,
                        reuse_existing=True,
                    )
            except PipelineError as exc:
                with self._lock:
                    current = self._jobs[job_id]
                    current.status = "failed"
                    current.error = str(exc)
                    current.message = str(exc)
                    self._append_event(current, "failed", current.progress, str(exc))
                return
            except Exception as exc:
                with self._lock:
                    current = self._jobs[job_id]
                    current.status = "failed"
                    current.error = f"Unexpected error: {exc}"
                    current.message = current.error
                    self._append_event(current, "failed", current.progress, current.error)
                return

            with self._lock:
                current = self._jobs[job_id]
                current.status = "completed"
                current.stage = "completed"
                current.progress = 100
                current.message = "Transcript ready"
                current.result = self._with_asset_urls(result)
                self._append_event(current, "completed", 100, "Transcript ready")
        finally:
            if job.upload_file_path:
                staged_path = Path(job.upload_file_path)
                staged_path.unlink(missing_ok=True)
                try:
                    staged_path.parent.rmdir()
                except OSError:
                    pass
                with self._lock:
                    current = self._jobs.get(job_id)
                    if current is not None:
                        current.upload_file_path = None

    def _with_asset_urls(self, result: Dict[str, Any]) -> Dict[str, Any]:
        enriched = dict(result)

        manifest_file = enriched.get("manifest_file")
        if manifest_file:
            enriched["manifest_url"] = self._asset_url(manifest_file)

        videos = []
        for video in enriched.get("videos", []):
            enriched_video = dict(video)
            for key, result_key in (
                ("audio_url", "audio_file"),
                ("transcript_url", "transcript_file"),
                ("metadata_url", "metadata_file"),
            ):
                enriched_video[key] = self._asset_url(video[result_key])
            videos.append(enriched_video)
        enriched["videos"] = videos
        return enriched

    def _asset_url(self, absolute_path: str) -> str:
        path = Path(absolute_path).resolve()
        relative = path.relative_to(OUTPUT_DIR.resolve())
        return f"/outputs/{relative.as_posix()}"


app = FastAPI(title="IG Content Transcriber")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

jobs = JobManager()


def serialize_job(job: JobRecord) -> Dict[str, Any]:
    payload = asdict(job)
    payload.pop("upload_file_path", None)
    return payload


@app.post("/api/jobs")
def create_job(request: JobRequest) -> Dict[str, Any]:
    job = jobs.create_job(request)
    return serialize_job(job)


@app.post("/api/jobs/upload")
async def create_upload_job(
    audio_file: UploadFile = File(...),
    model: str = Form("base"),
    language: Optional[str] = Form(None),
) -> Dict[str, Any]:
    filename = Path((audio_file.filename or "uploaded-audio").strip() or "uploaded-audio").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".webm", ".mp4", ".mpeg", ".mpga"}:
        raise HTTPException(status_code=400, detail="Unsupported audio format.")

    staging_dir = UPLOAD_STAGING_DIR / uuid.uuid4().hex[:10]
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / filename
    contents = await audio_file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")
    staged_path.write_bytes(contents)

    job = jobs.create_upload_job(
        filename=filename,
        staged_path=staged_path,
        model=model,
        language=language,
    )
    return serialize_job(job)


@app.get("/api/jobs")
def list_jobs() -> list[Dict[str, Any]]:
    return [serialize_job(job) for job in jobs.list_jobs()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    try:
        job = jobs.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return serialize_job(job)


if FRONTEND_DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST_DIR, html=True), name="frontend")
else:
    @app.get("/")
    def missing_frontend() -> Dict[str, str]:
        raise HTTPException(status_code=503, detail="Frontend build missing. Run `./run_ui.sh` to build and start the UI.")
