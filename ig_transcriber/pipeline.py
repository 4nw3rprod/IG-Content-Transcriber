from __future__ import annotations

import json
import math
import os
import re
import shutil
import warnings
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+.*")
warnings.filterwarnings("ignore", message="Support for Python version 3.9 has been deprecated.*")

import whisper
from yt_dlp import YoutubeDL


INSTAGRAM_APP_ID = "936619743392459"
DEFAULT_TIMEOUT_SECONDS = 30
INSTAGRAM_VIDEO_LIMIT = 10
GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
ProgressCallback = Callable[[str, int, str], None]

STOPWORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "more",
    "my",
    "not",
    "of",
    "on",
    "or",
    "our",
    "out",
    "so",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "to",
    "up",
    "use",
    "was",
    "we",
    "what",
    "when",
    "with",
    "you",
    "your",
}

POSITIVE_WORDS = {
    "amazing",
    "best",
    "better",
    "boost",
    "easy",
    "fast",
    "great",
    "improve",
    "love",
    "powerful",
    "simple",
    "smart",
    "strong",
    "win",
}

NEGATIVE_WORDS = {
    "bad",
    "broken",
    "difficult",
    "fail",
    "hard",
    "issue",
    "mistake",
    "problem",
    "risk",
    "slow",
    "stuck",
    "worse",
}

CTA_PATTERNS = (
    "follow",
    "subscribe",
    "comment",
    "share",
    "like",
    "buy",
    "dm",
    "message me",
    "link in bio",
    "sign up",
    "download",
    "book a call",
    "join",
)


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoCandidate:
    source_kind: str
    input_url: str
    canonical_url: str
    source_label: str
    source_group: str
    video_id: str
    timestamp: int
    title: str
    caption: str
    video_url: str
    uploader: str
    platform: str
    position: int = 1
    total_videos: int = 1


def _emit(progress_callback: Optional[ProgressCallback], stage: str, percent: int, message: str) -> None:
    if progress_callback is not None:
        progress_callback(stage, percent, message)


def _safe_slug(value: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower()
    return slug or fallback


def _timestamp_to_iso(timestamp: int) -> Optional[str]:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _file_sha1(path: Path) -> str:
    digest = sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_instagram_host(netloc: str) -> bool:
    return "instagram.com" in netloc.lower()


def normalize_input_url(input_url: str) -> tuple[str, str]:
    parsed = urlparse(input_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise PipelineError("The input URL must start with http:// or https://")
    return input_url.strip(), parsed.geturl()


def detect_input_kind(input_url: str) -> tuple[str, str]:
    parsed = urlparse(input_url)
    if _is_instagram_host(parsed.netloc):
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise PipelineError("Could not determine the Instagram target from the URL")
        if parts[0] in {"reel", "p", "tv"}:
            return "video", input_url
        username = parts[0].lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9._]+", username):
            raise PipelineError(f"Invalid Instagram username parsed from URL: {username}")
        return "instagram_profile", f"https://www.instagram.com/{username}/"
    return "video", input_url


def fetch_profile(username: str, canonical_url: str) -> Dict[str, Any]:
    api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    request = Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "x-ig-app-id": INSTAGRAM_APP_ID,
            "Referer": canonical_url,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise PipelineError(f"Instagram profile not found: {canonical_url}") from exc
        if exc.code in {401, 403}:
            raise PipelineError(
                "Instagram blocked the profile lookup. This pipeline currently supports public profiles only."
            ) from exc
        if exc.code == 429:
            raise PipelineError("Instagram rate-limited the request. Wait and try again.") from exc
        raise PipelineError(f"Instagram profile lookup failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise PipelineError(f"Network error while fetching Instagram profile: {exc.reason}") from exc

    user = payload.get("data", {}).get("user")
    if not user:
        raise PipelineError("Instagram returned an unexpected profile response")
    if user.get("is_private"):
        raise PipelineError("This Instagram profile is private. Only public profiles are supported.")
    return user


def iter_candidate_nodes(user: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for key in ("edge_owner_to_timeline_media", "edge_felix_video_timeline"):
        section = user.get(key) or {}
        for edge in section.get("edges") or []:
            node = edge.get("node") or {}
            if node:
                yield node


def extract_caption(node: Dict[str, Any]) -> str:
    edges = (((node.get("edge_media_to_caption") or {}).get("edges")) or [])
    if not edges:
        return ""
    first = edges[0].get("node") or {}
    return (first.get("text") or "").strip()


def _instagram_video_url(shortcode: str, product_type: Optional[str]) -> str:
    if product_type == "clips":
        return f"https://www.instagram.com/reel/{shortcode}/"
    if product_type == "igtv":
        return f"https://www.instagram.com/tv/{shortcode}/"
    return f"https://www.instagram.com/p/{shortcode}/"


def collect_instagram_profile_videos(canonical_url: str) -> list[VideoCandidate]:
    username = urlparse(canonical_url).path.strip("/").split("/")[0]
    user = fetch_profile(username, canonical_url)

    seen: set[str] = set()
    candidates: list[VideoCandidate] = []

    for node in iter_candidate_nodes(user):
        shortcode = node.get("shortcode")
        if not shortcode or shortcode in seen:
            continue
        seen.add(shortcode)

        is_video = bool(node.get("is_video")) or node.get("product_type") in {"clips", "igtv"}
        if not is_video:
            continue

        timestamp = int(node.get("taken_at_timestamp") or 0)
        if not timestamp:
            continue

        caption = extract_caption(node)
        title = caption.splitlines()[0].strip() if caption.strip() else shortcode
        candidates.append(
            VideoCandidate(
                source_kind="instagram_profile",
                input_url=canonical_url,
                canonical_url=canonical_url,
                source_label=username,
                source_group="instagram_profiles",
                video_id=shortcode,
                timestamp=timestamp,
                title=title,
                caption=caption,
                video_url=_instagram_video_url(shortcode, node.get("product_type")),
                uploader=user.get("username") or username,
                platform="instagram",
            )
        )

    if not candidates:
        raise PipelineError("No videos were found in the public Instagram profile data.")

    candidates.sort(key=lambda item: item.timestamp, reverse=True)
    selected = candidates[:INSTAGRAM_VIDEO_LIMIT]
    total = len(selected)

    return [
        VideoCandidate(
            **{**candidate.__dict__, "position": index, "total_videos": total}
        )
        for index, candidate in enumerate(selected, start=1)
    ]


def _yt_dlp_extract_info(target_url: str) -> Dict[str, Any]:
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True}) as ydl:
            return ydl.extract_info(target_url, download=False)
    except Exception as exc:
        raise PipelineError(f"Failed to inspect video URL: {exc}") from exc


def collect_direct_video(target_url: str) -> list[VideoCandidate]:
    info = _yt_dlp_extract_info(target_url)
    page_url = info.get("webpage_url") or target_url
    uploader = info.get("uploader") or info.get("channel") or info.get("extractor_key") or "video"
    source_label = info.get("uploader_id") or _safe_slug(uploader, "video")
    title = (info.get("title") or info.get("fulltitle") or "Video").strip()
    description = (info.get("description") or "").strip()
    timestamp = int(info.get("timestamp") or 0)
    video_id = str(info.get("id") or _safe_slug(page_url))

    return [
        VideoCandidate(
            source_kind="video_url",
            input_url=target_url,
            canonical_url=page_url,
            source_label=source_label,
            source_group="video_urls",
            video_id=video_id,
            timestamp=timestamp,
            title=title,
            caption=description,
            video_url=target_url,
            uploader=uploader,
            platform=(info.get("extractor_key") or urlparse(target_url).netloc or "video").lower(),
            position=1,
            total_videos=1,
        )
    ]


def resolve_candidates(input_url: str) -> tuple[str, str, list[VideoCandidate]]:
    normalized_input, canonical = normalize_input_url(input_url)
    input_kind, canonical_target = detect_input_kind(canonical)
    if input_kind == "instagram_profile":
        return input_kind, canonical_target, collect_instagram_profile_videos(canonical_target)
    return input_kind, canonical_target, collect_direct_video(normalized_input)


def ensure_run_dir(base_output_dir: Path, candidate: VideoCandidate) -> Path:
    run_dir = base_output_dir / candidate.source_group / _safe_slug(candidate.source_label) / _safe_slug(candidate.video_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _paths_for_run(run_dir: Path) -> tuple[Path, Path, Path]:
    return run_dir / "audio.mp3", run_dir / "transcript.txt", run_dir / "metadata.json"


def _paths_for_batch(base_output_dir: Path, source_group: str, source_label: str) -> tuple[Path, Path]:
    group_dir = base_output_dir / source_group / _safe_slug(source_label)
    group_dir.mkdir(parents=True, exist_ok=True)
    return group_dir, group_dir / "manifest.json"


def _copy_uploaded_audio(source_audio_path: Path, run_dir: Path) -> Path:
    suffix = source_audio_path.suffix.lower() or ".bin"
    destination = run_dir / f"audio{suffix}"
    if destination.exists():
        return destination
    shutil.copy2(source_audio_path, destination)
    return destination


def _sentence_split(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _top_keywords(*texts: str, limit: int = 8) -> list[str]:
    words: list[str] = []
    for text in texts:
        words.extend(re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text.lower()))
    counts = Counter(word for word in words if word not in STOPWORDS)
    return [word for word, _ in counts.most_common(limit)]


def _sentiment(text: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text.lower())
    positive = sum(word in POSITIVE_WORDS for word in words)
    negative = sum(word in NEGATIVE_WORDS for word in words)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def _cta_detected(text: str) -> Optional[str]:
    lowered = text.lower()
    for pattern in CTA_PATTERNS:
        if pattern in lowered:
            return pattern
    return None


def _strip_json_fence(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _groq_chat(messages: list[Dict[str, str]]) -> Optional[str]:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None

    request = Request(
        GROQ_BASE_URL,
        data=json.dumps(
            {
                "model": os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
                "messages": messages,
                "temperature": 0.2,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except Exception:
        return None

    choices = payload.get("choices") or []
    if not choices:
        return None
    return choices[0].get("message", {}).get("content")


def _heuristic_video_ai_insights(transcript_text: str, caption: str, title: str) -> Dict[str, Any]:
    sentences = _sentence_split(transcript_text)
    summary_sentences = sentences[:2] or _sentence_split(caption)[:2] or ([title] if title else [])
    keywords = _top_keywords(title, caption, transcript_text)
    hook = sentences[0] if sentences else (caption.splitlines()[0].strip() if caption.strip() else title)
    cta = _cta_detected(f"{caption}\n{transcript_text}")
    insight_focus = keywords[:3] if keywords else ["content", "message", "audience"]
    summary = " ".join(summary_sentences).strip()

    return {
        "summary": summary,
        "hook": hook,
        "keywords": keywords,
        "sentiment": _sentiment(f"{caption}\n{transcript_text}"),
        "cta": cta,
        "title_suggestions": [
            f"{title or hook}: what matters most",
            f"{' / '.join(insight_focus)} breakdown",
            f"The core idea behind {insight_focus[0] if insight_focus else 'this video'}",
        ],
        "content_angles": [
            f"Turn {insight_focus[0] if insight_focus else 'the topic'} into a carousel or thread.",
            f"Clip the opening hook: {hook[:120] if hook else title}.",
            f"Use {insight_focus[1] if len(insight_focus) > 1 else insight_focus[0] if insight_focus else 'the message'} as the CTA angle for a follow-up post.",
        ],
    }


def generate_video_ai_insights(transcript_text: str, caption: str, title: str) -> Dict[str, Any]:
    fallback = _heuristic_video_ai_insights(transcript_text, caption, title)
    content = _groq_chat(
        [
            {
                "role": "system",
                "content": (
                    "You generate structured creator insights for short-form video transcripts. "
                    "Return only valid JSON with keys: summary, hook, keywords, sentiment, cta, "
                    "title_suggestions, content_angles. keywords/title_suggestions/content_angles must be arrays."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "title": title,
                        "caption": caption,
                        "transcript": transcript_text[:12000],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
    )
    if not content:
        return {**fallback, "provider": "heuristic"}

    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError:
        return {**fallback, "provider": "heuristic"}

    return {
        "summary": str(parsed.get("summary") or fallback["summary"]).strip(),
        "hook": str(parsed.get("hook") or fallback["hook"]).strip(),
        "keywords": [str(item).strip() for item in (parsed.get("keywords") or fallback["keywords"]) if str(item).strip()][:8],
        "sentiment": str(parsed.get("sentiment") or fallback["sentiment"]).strip().lower(),
        "cta": str(parsed.get("cta")).strip() if parsed.get("cta") not in {None, "", "null"} else fallback["cta"],
        "title_suggestions": [str(item).strip() for item in (parsed.get("title_suggestions") or fallback["title_suggestions"]) if str(item).strip()][:3],
        "content_angles": [str(item).strip() for item in (parsed.get("content_angles") or fallback["content_angles"]) if str(item).strip()][:3],
        "provider": "groq",
    }


def _heuristic_batch_ai_overview(videos: list[Dict[str, Any]]) -> Dict[str, Any]:
    transcripts = [video.get("transcript_text", "") for video in videos]
    captions = [video.get("caption", "") for video in videos]
    keywords = _top_keywords(*transcripts, *captions, limit=12)
    hooks = [video.get("ai_insights", {}).get("hook") for video in videos if video.get("ai_insights", {}).get("hook")]
    ctas = [video.get("ai_insights", {}).get("cta") for video in videos if video.get("ai_insights", {}).get("cta")]

    return {
        "summary": f"Processed {len(videos)} videos. The strongest recurring topics were {', '.join(keywords[:5]) or 'the uploaded themes'}.",
        "recurring_keywords": keywords,
        "top_hooks": hooks[:5],
        "cta_patterns": Counter(ctas).most_common(5),
        "video_titles": [video.get("title") for video in videos],
    }


def generate_batch_ai_overview(videos: list[Dict[str, Any]]) -> Dict[str, Any]:
    fallback = _heuristic_batch_ai_overview(videos)
    content = _groq_chat(
        [
            {
                "role": "system",
                "content": (
                    "You generate concise batch insights across multiple short-form video transcripts. "
                    "Return only valid JSON with keys: summary, recurring_keywords, top_hooks, cta_patterns, video_titles. "
                    "recurring_keywords and top_hooks must be arrays of strings. cta_patterns must be an array of [string, number] pairs."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "videos": [
                            {
                                "title": video.get("title"),
                                "caption": video.get("caption"),
                                "transcript": video.get("transcript_text", "")[:4000],
                                "insights": video.get("ai_insights", {}),
                            }
                            for video in videos
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        ]
    )
    if not content:
        return {**fallback, "provider": "heuristic"}

    try:
        parsed = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError:
        return {**fallback, "provider": "heuristic"}

    cta_patterns_raw = parsed.get("cta_patterns") or fallback["cta_patterns"]
    cta_patterns: list[tuple[str, int]] = []
    for item in cta_patterns_raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            cta_patterns.append((str(item[0]).strip(), int(item[1])))

    return {
        "summary": str(parsed.get("summary") or fallback["summary"]).strip(),
        "recurring_keywords": [str(item).strip() for item in (parsed.get("recurring_keywords") or fallback["recurring_keywords"]) if str(item).strip()][:12],
        "top_hooks": [str(item).strip() for item in (parsed.get("top_hooks") or fallback["top_hooks"]) if str(item).strip()][:5],
        "cta_patterns": cta_patterns or fallback["cta_patterns"],
        "video_titles": [str(item).strip() for item in (parsed.get("video_titles") or fallback["video_titles"]) if str(item).strip()],
        "provider": "groq",
    }


def build_video_result(
    candidate: VideoCandidate,
    audio_path: Path,
    transcript_path: Path,
    metadata_path: Path,
    whisper_result: Dict[str, Any],
    *,
    model_name: str,
    cached: bool,
) -> Dict[str, Any]:
    transcript_text = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
    ai_insights = generate_video_ai_insights(transcript_text, candidate.caption, candidate.title)

    return {
        "status": "ok",
        "source_kind": candidate.source_kind,
        "platform": candidate.platform,
        "source_label": candidate.source_label,
        "position": candidate.position,
        "total_videos": candidate.total_videos,
        "video_id": candidate.video_id,
        "title": candidate.title,
        "uploader": candidate.uploader,
        "input_url": candidate.input_url,
        "canonical_url": candidate.canonical_url,
        "video_url": candidate.video_url,
        "caption": candidate.caption,
        "taken_at_timestamp": candidate.timestamp,
        "taken_at_iso": _timestamp_to_iso(candidate.timestamp),
        "audio_file": str(audio_path),
        "transcript_file": str(transcript_path),
        "metadata_file": str(metadata_path),
        "detected_language": whisper_result.get("language"),
        "model": model_name,
        "cached": cached,
        "transcript_text": transcript_text,
        "ai_insights": ai_insights,
    }


def _load_cached_video_result(candidate: VideoCandidate, run_dir: Path, model_name: str) -> Optional[Dict[str, Any]]:
    audio_path, transcript_path, metadata_path = _paths_for_run(run_dir)
    if not (audio_path.exists() and transcript_path.exists() and metadata_path.exists()):
        return None

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    return build_video_result(
        candidate,
        audio_path,
        transcript_path,
        metadata_path,
        {"language": metadata.get("detected_language")},
        model_name=model_name,
        cached=True,
    )


def download_audio(candidate: VideoCandidate, run_dir: Path) -> Path:
    audio_path, _, _ = _paths_for_run(run_dir)
    if audio_path.exists() and audio_path.stat().st_size > 0:
        return audio_path

    output_template = str(run_dir / "%(id)s.%(ext)s")
    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(candidate.video_url, download=True)
    except Exception as exc:
        raise PipelineError(f"Failed to download audio for {candidate.video_url}: {exc}") from exc

    downloaded_audio_path = run_dir / f"{info['id']}.mp3"
    if not downloaded_audio_path.exists():
        candidates = sorted(run_dir.glob(f"{info['id']}.*"))
        if not candidates:
            raise PipelineError("yt-dlp finished without producing an audio file")
        downloaded_audio_path = candidates[0]

    if downloaded_audio_path != audio_path:
        audio_path.unlink(missing_ok=True)
        downloaded_audio_path.replace(audio_path)

    return audio_path


@lru_cache(maxsize=4)
def load_whisper_model(model_name: str) -> Any:
    return whisper.load_model(model_name)


def transcribe_audio(
    audio_path: Path,
    model_name: str,
    language: Optional[str],
    progress_callback: Optional[ProgressCallback] = None,
    percent_range: tuple[int, int] = (55, 85),
) -> Dict[str, Any]:
    start, end = percent_range
    midpoint = start + math.floor((end - start) * 0.25)
    _emit(progress_callback, "loading_model", midpoint, f"Loading Whisper model '{model_name}'")
    try:
        model = load_whisper_model(model_name)
    except Exception as exc:
        raise PipelineError(f"Failed to load Whisper model '{model_name}': {exc}") from exc

    _emit(progress_callback, "transcribing", end, "Transcribing audio with Whisper")
    try:
        return model.transcribe(str(audio_path), fp16=False, language=language, verbose=None)
    except Exception as exc:
        raise PipelineError(f"Whisper transcription failed: {exc}") from exc


def write_video_outputs(
    candidate: VideoCandidate,
    run_dir: Path,
    audio_path: Path,
    whisper_result: Dict[str, Any],
    model_name: str,
) -> Dict[str, Any]:
    _, transcript_path, metadata_path = _paths_for_run(run_dir)
    transcript_text = (whisper_result.get("text") or "").strip()
    transcript_path.write_text(transcript_text + ("\n" if transcript_text else ""), encoding="utf-8")

    video_result = build_video_result(
        candidate,
        audio_path,
        transcript_path,
        metadata_path,
        whisper_result,
        model_name=model_name,
        cached=False,
    )

    metadata = {
        **video_result,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return video_result


def process_video(
    candidate: VideoCandidate,
    *,
    output_dir: Path,
    model_name: str,
    language: Optional[str],
    progress_callback: Optional[ProgressCallback],
    reuse_existing: bool,
) -> Dict[str, Any]:
    run_dir = ensure_run_dir(output_dir, candidate)

    if reuse_existing:
        cached = _load_cached_video_result(candidate, run_dir, model_name)
        if cached is not None:
            return cached
    else:
        # Force-fresh: wipe the candidate's run_dir so download_audio re-pulls the
        # source instead of returning the stale MP3 sitting in the same dir from a
        # prior run. Without this, two IG URLs that yt-dlp normalises to the same
        # `info["id"]` would both serve the first call's transcript.
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
        run_dir = ensure_run_dir(output_dir, candidate)

    audio_path = download_audio(candidate, run_dir)
    whisper_result = transcribe_audio(audio_path, model_name, language, progress_callback=progress_callback)
    _emit(
        progress_callback,
        "generating_insights",
        min(95, 15 + math.floor((candidate.position / max(candidate.total_videos, 1)) * 75)),
        f"Generating AI insights for video {candidate.position}/{candidate.total_videos}",
    )
    return write_video_outputs(candidate, run_dir, audio_path, whisper_result, model_name)


def write_batch_manifest(
    batch_result: Dict[str, Any],
    base_output_dir: Path,
    source_group: str,
    source_label: str,
) -> Path:
    _, manifest_path = _paths_for_batch(base_output_dir, source_group, source_label)
    manifest_path.write_text(json.dumps(batch_result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest_path


def run_audio_file_transcription(
    audio_path: str | Path,
    *,
    original_filename: Optional[str] = None,
    output_dir: str | Path = "outputs",
    model_name: str = "base",
    language: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    source_audio_path = Path(audio_path).expanduser().resolve()
    if not source_audio_path.exists() or not source_audio_path.is_file():
        raise PipelineError(f"Audio file not found: {source_audio_path}")
    if source_audio_path.stat().st_size <= 0:
        raise PipelineError("The uploaded audio file is empty.")

    filename = (original_filename or source_audio_path.name).strip() or source_audio_path.name
    title = Path(filename).stem or "Uploaded audio"
    file_hash = _file_sha1(source_audio_path)[:10]
    source_label = _safe_slug(f"{title}-{file_hash}", "audio-upload")
    timestamp = int(source_audio_path.stat().st_mtime)

    candidate = VideoCandidate(
        source_kind="audio_upload",
        input_url=filename,
        canonical_url=f"upload://{source_label}",
        source_label=source_label,
        source_group="audio_uploads",
        video_id=source_label,
        timestamp=timestamp,
        title=title,
        caption="",
        video_url="",
        uploader="uploaded audio",
        platform="local_audio",
        position=1,
        total_videos=1,
    )

    output_root = Path(output_dir).expanduser().resolve()
    run_dir = ensure_run_dir(output_root, candidate)

    _emit(progress_callback, "validating", 4, "Validating uploaded audio file")
    _emit(progress_callback, "preparing_audio", 18, "Staging uploaded audio for transcription")
    staged_audio_path = _copy_uploaded_audio(source_audio_path, run_dir)

    whisper_result = transcribe_audio(
        staged_audio_path,
        model_name,
        language,
        progress_callback=progress_callback,
        percent_range=(22, 82),
    )
    _emit(progress_callback, "generating_insights", 92, "Generating AI insights from uploaded audio")
    video_result = write_video_outputs(
        candidate,
        run_dir,
        staged_audio_path,
        whisper_result,
        model_name,
    )

    batch_result = {
        "status": "ok",
        "input_kind": "audio_upload",
        "input_url": filename,
        "canonical_url": candidate.canonical_url,
        "model": model_name,
        "language_hint": language,
        "total_videos": 1,
        "completed_videos": 1,
        "videos": [video_result],
        "ai_overview": generate_batch_ai_overview([video_result]),
    }

    manifest_path = write_batch_manifest(
        batch_result,
        output_root,
        candidate.source_group,
        candidate.source_label,
    )
    batch_result["manifest_file"] = str(manifest_path)
    _emit(progress_callback, "completed", 100, "Completed transcription for uploaded audio")
    return batch_result


def run_transcription(
    input_url: str,
    *,
    output_dir: str | Path = "outputs",
    model_name: str = "base",
    language: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    reuse_existing: bool = True,
) -> Dict[str, Any]:
    _emit(progress_callback, "validating", 4, "Validating input URL")
    input_kind, canonical_input, candidates = resolve_candidates(input_url)
    output_root = Path(output_dir).expanduser().resolve()

    if input_kind == "instagram_profile":
        _emit(progress_callback, "collecting_videos", 12, f"Collected the latest {len(candidates)} videos from the Instagram profile")
    else:
        _emit(progress_callback, "collecting_videos", 12, "Resolved the video URL")

    video_results: list[Dict[str, Any]] = []
    total = len(candidates)

    for index, candidate in enumerate(candidates, start=1):
        base_percent = 15 + math.floor(((index - 1) / total) * 75)
        _emit(
            progress_callback,
            "downloading_audio",
            base_percent,
            f"Processing video {index}/{total}: {candidate.title[:80]}",
        )
        video_result = process_video(
            candidate,
            output_dir=output_root,
            model_name=model_name,
            language=language,
            progress_callback=progress_callback,
            reuse_existing=reuse_existing,
        )
        video_results.append(video_result)
        completed_percent = 15 + math.floor((index / total) * 75)
        status_message = (
            f"Finished video {index}/{total}"
            if total > 1
            else "Transcript ready"
        )
        _emit(progress_callback, "writing_files", completed_percent, status_message)

    batch_result = {
        "status": "ok",
        "input_kind": input_kind,
        "input_url": input_url,
        "canonical_url": canonical_input,
        "model": model_name,
        "language_hint": language,
        "total_videos": total,
        "completed_videos": len(video_results),
        "videos": video_results,
        "ai_overview": generate_batch_ai_overview(video_results),
    }

    manifest_path = write_batch_manifest(
        batch_result,
        output_root,
        candidates[0].source_group,
        candidates[0].source_label,
    )
    batch_result["manifest_file"] = str(manifest_path)
    _emit(progress_callback, "completed", 100, f"Completed transcription for {len(video_results)} video(s)")
    return batch_result
