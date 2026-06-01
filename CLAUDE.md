# Claude Usage

Use this repository to:

- fetch the latest 10 videos from a public Instagram profile and transcribe them, or
- transcribe a single direct video URL, or
- transcribe a local uploaded audio file.

There is also a local web app for interactive use and progress tracking. The frontend is a Vite React app built with shadcn/ui components and served by the FastAPI backend after build.
There is also an MCP server so Claude or other MCP-compatible clients can operate the tool directly.

AI insights are generated with GroqCloud when `GROQ_API_KEY` is available. The app falls back to local heuristic insights if Groq is unavailable.

## Install

Run:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Optional:

```bash
cp .env.example .env.local
```

Then set `GROQ_API_KEY` in `.env.local`.

Requirements:

- `ffmpeg` available on `PATH`
- network access enabled
- public Instagram profile URL

## Preferred command

Use JSON mode so stdout is machine-readable:

```bash
./run_latest_reel_transcription.sh "https://www.instagram.com/<username>/" --json
```

Optional:

```bash
./run_latest_reel_transcription.sh "https://www.instagram.com/reel/<id>/" --json --model small --language en
```

## MCP

Preferred command for MCP clients:

```bash
./run_mcp_server.sh
```

This starts the server over stdio. The MCP surface exposes:

- `transcribe_input`
- `transcribe_local_audio`
- `list_recent_batches`
- `read_batch_manifest`
- `read_video_output`

Resources:

- `ig-transcriber://server`
- `ig-transcriber://recent-batches`
- `ig-transcriber://manifest/{source_group}/{source_label}`
- `ig-transcriber://transcript/{source_group}/{source_label}/{video_id}`

If an MCP client needs HTTP instead of stdio:

```bash
./run_mcp_server.sh --transport streamable-http --host 127.0.0.1 --port 8001
```

Then connect the client to `http://127.0.0.1:8001/mcp`.

## UI

Start the local app:

```bash
./run_ui.sh
```

The launcher picks an open localhost port and opens the browser automatically. If needed, read the URL from terminal output.
It also builds the frontend before starting the server.

## Success contract

On success, stdout is a single JSON object with:

- `status`
- `input_kind`
- `input_url`
- `canonical_url`
- `total_videos`
- `completed_videos`
- `videos`
- `ai_overview`
- `manifest_file`

Each item in `videos` includes transcript paths, metadata paths, detected language, and `ai_insights`.

## Failure contract

On failure, the command exits non-zero.

If `--json` is used, stdout includes:

```json
{"status":"error","error":"..."}
```

Human-readable error details are also written to stderr.

## Notes

- Public profiles only.
- Local audio uploads bypass Instagram entirely.
- Instagram may rate-limit anonymous requests.
- The wrapper prefers Python 3.11 when available to avoid `yt-dlp` Python 3.9 deprecation noise.
- The wrapper prefers the repo-local `.venv` first when present.
