from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ig_transcriber import PipelineError, run_audio_file_transcription, run_transcription


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = Path(os.environ.get("IG_TRANSCRIBER_OUTPUT_DIR", REPO_ROOT / "outputs")).expanduser().resolve()
MAX_LIST_LIMIT = 50


def _safe_slug(value: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower()
    return slug or fallback


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _within_output_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved != DEFAULT_OUTPUT_DIR and DEFAULT_OUTPUT_DIR not in resolved.parents:
        raise FileNotFoundError(f"Path is outside the output directory: {resolved}")
    return resolved


def _manifest_path(source_group: str, source_label: str) -> Path:
    return _within_output_root(
        DEFAULT_OUTPUT_DIR / _safe_slug(source_group, "group") / _safe_slug(source_label, "source") / "manifest.json"
    )


def _video_dir(source_group: str, source_label: str, video_id: str) -> Path:
    return _within_output_root(
        DEFAULT_OUTPUT_DIR
        / _safe_slug(source_group, "group")
        / _safe_slug(source_label, "source")
        / _safe_slug(video_id, "video")
    )


def _manifest_resource_uri(source_group: str, source_label: str) -> str:
    return f"ig-transcriber://manifest/{_safe_slug(source_group, 'group')}/{_safe_slug(source_label, 'source')}"


def _transcript_resource_uri(source_group: str, source_label: str, video_id: str) -> str:
    return (
        "ig-transcriber://transcript/"
        f"{_safe_slug(source_group, 'group')}/{_safe_slug(source_label, 'source')}/{_safe_slug(video_id, 'video')}"
    )


def _recent_manifest_paths(limit: int) -> list[Path]:
    capped_limit = min(max(limit, 1), MAX_LIST_LIMIT)
    manifests = sorted(
        DEFAULT_OUTPUT_DIR.glob("*/*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return manifests[:capped_limit]


def _manifest_summary(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    relative = path.relative_to(DEFAULT_OUTPUT_DIR)
    source_group, source_label = relative.parts[0], relative.parts[1]
    return {
        "source_group": source_group,
        "source_label": source_label,
        "manifest_file": str(path),
        "manifest_resource": _manifest_resource_uri(source_group, source_label),
        "input_kind": payload.get("input_kind"),
        "input_url": payload.get("input_url"),
        "canonical_url": payload.get("canonical_url"),
        "model": payload.get("model"),
        "total_videos": payload.get("total_videos"),
        "completed_videos": payload.get("completed_videos"),
        "updated_at": path.stat().st_mtime,
    }


def _attach_resource_links(batch_result: dict[str, Any], manifest_path: Path | None = None) -> dict[str, Any]:
    manifest_file = batch_result.get("manifest_file")
    if manifest_file:
        manifest_path = _within_output_root(Path(manifest_file))
    elif manifest_path is not None:
        manifest_path = _within_output_root(manifest_path)
        batch_result["manifest_file"] = str(manifest_path)

    if manifest_path is not None:
        relative = manifest_path.relative_to(DEFAULT_OUTPUT_DIR)
        source_group, source_label = relative.parts[0], relative.parts[1]
        batch_result["manifest_resource"] = _manifest_resource_uri(source_group, source_label)
        for video in batch_result.get("videos", []):
            video["transcript_resource"] = _transcript_resource_uri(
                source_group,
                source_label,
                str(video.get("video_id") or "video"),
            )
    return batch_result


def _strip_transcript_text(batch_result: dict[str, Any]) -> dict[str, Any]:
    stripped = json.loads(json.dumps(batch_result))
    for video in stripped.get("videos", []):
        video.pop("transcript_text", None)
    return stripped


def build_server(*, host: str, port: int, debug: bool) -> FastMCP:
    mcp = FastMCP(
        name="IG Content Transcriber",
        instructions=(
            "Use this server to transcribe a direct video URL or the latest 10 videos from a public Instagram profile. "
            "The main tool is transcribe_input. Use list_recent_batches, read_batch_manifest, and read_video_output "
            "to inspect prior results. Resources expose server info, saved manifests, and transcript text."
        ),
        host=host,
        port=port,
        debug=debug,
        log_level="DEBUG" if debug else "ERROR",
        json_response=True,
    )

    @mcp.resource("ig-transcriber://server")
    def server_resource() -> str:
        return _json(
            {
                "name": "IG Content Transcriber",
                "output_root": str(DEFAULT_OUTPUT_DIR),
                "tools": [
                    "transcribe_input",
                    "transcribe_local_audio",
                    "list_recent_batches",
                    "read_batch_manifest",
                    "read_video_output",
                ],
                "input_support": {
                    "instagram_profile": "Fetches and transcribes the latest 10 videos from a public Instagram profile.",
                    "video_url": "Transcribes a single direct video URL.",
                },
                "resources": [
                    "ig-transcriber://server",
                    "ig-transcriber://recent-batches",
                    "ig-transcriber://manifest/{source_group}/{source_label}",
                    "ig-transcriber://transcript/{source_group}/{source_label}/{video_id}",
                ],
            }
        )

    @mcp.resource("ig-transcriber://recent-batches")
    def recent_batches_resource() -> str:
        return _json(
            {
                "output_root": str(DEFAULT_OUTPUT_DIR),
                "batches": [_manifest_summary(path) for path in _recent_manifest_paths(limit=10)],
            }
        )

    @mcp.resource("ig-transcriber://manifest/{source_group}/{source_label}")
    def manifest_resource(source_group: str, source_label: str) -> str:
        path = _manifest_path(source_group, source_label)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found for {source_group}/{source_label}")
        payload = _attach_resource_links(_load_json(path), manifest_path=path)
        return _json(payload)

    @mcp.resource("ig-transcriber://transcript/{source_group}/{source_label}/{video_id}")
    def transcript_resource(source_group: str, source_label: str, video_id: str) -> str:
        transcript_path = _video_dir(source_group, source_label, video_id) / "transcript.txt"
        if not transcript_path.exists():
            raise FileNotFoundError(f"Transcript not found for {source_group}/{source_label}/{video_id}")
        return transcript_path.read_text(encoding="utf-8")

    @mcp.tool(description="Transcribe a direct video URL or the latest 10 videos from a public Instagram profile URL.")
    async def transcribe_input(
        input_url: str,
        ctx: Context,
        model_name: str = "base",
        language: str | None = None,
        reuse_existing: bool = True,
        include_transcript_text: bool = True,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        last_stage: str | None = None

        def progress_callback(stage: str, percent: int, message: str) -> None:
            nonlocal last_stage
            progress_message = f"[{stage}] {message}"
            asyncio.run_coroutine_threadsafe(
                ctx.report_progress(progress=float(percent), total=100.0, message=progress_message),
                loop,
            )
            if stage != last_stage:
                last_stage = stage
                asyncio.run_coroutine_threadsafe(ctx.info(progress_message), loop)

        await ctx.info(f"Starting transcription for {input_url}")

        try:
            batch_result = await asyncio.to_thread(
                run_transcription,
                input_url,
                output_dir=DEFAULT_OUTPUT_DIR,
                model_name=model_name,
                language=language,
                progress_callback=progress_callback,
                reuse_existing=reuse_existing,
            )
        except PipelineError as exc:
            await ctx.error(str(exc))
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover - unexpected failures should still be surfaced cleanly.
            await ctx.error(f"Unexpected failure: {exc}")
            return {"status": "error", "error": f"Unexpected failure: {exc}"}

        enriched_result = _attach_resource_links(batch_result)
        if not include_transcript_text:
            enriched_result = _strip_transcript_text(enriched_result)

        await ctx.report_progress(progress=100.0, total=100.0, message="Transcription completed")
        await ctx.info(f"Completed transcription for {enriched_result.get('completed_videos', 0)} video(s)")

        return enriched_result

    @mcp.tool(description="Transcribe a local audio file path and generate AI insights from the transcript.")
    async def transcribe_local_audio(
        audio_path: str,
        ctx: Context,
        original_filename: str | None = None,
        model_name: str = "base",
        language: str | None = None,
        include_transcript_text: bool = True,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        last_stage: str | None = None

        def progress_callback(stage: str, percent: int, message: str) -> None:
            nonlocal last_stage
            progress_message = f"[{stage}] {message}"
            asyncio.run_coroutine_threadsafe(
                ctx.report_progress(progress=float(percent), total=100.0, message=progress_message),
                loop,
            )
            if stage != last_stage:
                last_stage = stage
                asyncio.run_coroutine_threadsafe(ctx.info(progress_message), loop)

        await ctx.info(f"Starting local audio transcription for {audio_path}")

        try:
            batch_result = await asyncio.to_thread(
                run_audio_file_transcription,
                audio_path,
                original_filename=original_filename,
                output_dir=DEFAULT_OUTPUT_DIR,
                model_name=model_name,
                language=language,
                progress_callback=progress_callback,
            )
        except PipelineError as exc:
            await ctx.error(str(exc))
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # pragma: no cover
            await ctx.error(f"Unexpected failure: {exc}")
            return {"status": "error", "error": f"Unexpected failure: {exc}"}

        enriched_result = _attach_resource_links(batch_result)
        if not include_transcript_text:
            enriched_result = _strip_transcript_text(enriched_result)

        await ctx.report_progress(progress=100.0, total=100.0, message="Local audio transcription completed")
        await ctx.info("Completed local audio transcription")

        return enriched_result

    @mcp.tool(description="List the most recent saved transcription batches from the local outputs directory.")
    def list_recent_batches(limit: int = 10) -> dict[str, Any]:
        manifests = [_manifest_summary(path) for path in _recent_manifest_paths(limit)]
        return {
            "status": "ok",
            "output_root": str(DEFAULT_OUTPUT_DIR),
            "count": len(manifests),
            "batches": manifests,
        }

    @mcp.tool(description="Load a saved batch manifest by source group and source label.")
    def read_batch_manifest(source_group: str, source_label: str) -> dict[str, Any]:
        path = _manifest_path(source_group, source_label)
        if not path.exists():
            return {
                "status": "error",
                "error": f"Manifest not found for {source_group}/{source_label}",
            }

        payload = _attach_resource_links(_load_json(path), manifest_path=path)
        return {
            "status": "ok",
            "manifest_file": str(path),
            "manifest_resource": _manifest_resource_uri(source_group, source_label),
            "batch": payload,
        }

    @mcp.tool(description="Load the saved transcript and metadata for a single processed video.")
    def read_video_output(source_group: str, source_label: str, video_id: str) -> dict[str, Any]:
        run_dir = _video_dir(source_group, source_label, video_id)
        transcript_path = run_dir / "transcript.txt"
        metadata_path = run_dir / "metadata.json"
        audio_path = run_dir / "audio.mp3"

        if not metadata_path.exists():
            return {
                "status": "error",
                "error": f"Video output not found for {source_group}/{source_label}/{video_id}",
            }

        metadata = _load_json(metadata_path)
        transcript_text = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
        return {
            "status": "ok",
            "audio_file": str(audio_path) if audio_path.exists() else None,
            "transcript_file": str(transcript_path) if transcript_path.exists() else None,
            "metadata_file": str(metadata_path),
            "transcript_resource": _transcript_resource_uri(source_group, source_label, video_id),
            "transcript_text": transcript_text,
            "metadata": metadata,
        }

    return mcp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Expose the IG Content Transcriber pipeline as an MCP server for other AI clients."
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
        help="MCP transport to run. stdio is the default and is the right choice for Claude/Cursor-style integrations.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for HTTP transports.")
    parser.add_argument("--port", type=int, default=8000, help="Port for HTTP transports.")
    parser.add_argument("--debug", action="store_true", help="Enable MCP server debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = build_server(host=args.host, port=args.port, debug=args.debug)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
