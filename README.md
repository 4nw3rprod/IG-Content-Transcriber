# IG Content Transcriber

This pipeline takes a public Instagram profile URL, a direct video URL, or an uploaded audio file.

- If you pass an Instagram profile URL, it fetches the latest 10 videos and transcribes all of them.
- If you pass a direct video URL, it transcribes that single video.
- If you upload an audio file, it transcribes the audio directly and generates AI insights from the transcript.

It also ships with a local web UI built with React, Vite, Tailwind, and shadcn/ui components for running jobs, watching live progress, opening the generated artifacts, and showing AI insights.
It also ships with an MCP server so other AI clients can operate the pipeline over a standard tool interface.

## Requirements

- Python 3.9+
- `ffmpeg` installed and available on `PATH`

## Install

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Optional for AI insights:

```bash
cp .env.example .env.local
```

Then set `GROQ_API_KEY` in `.env.local`.

## Usage

```bash
./run_latest_reel_transcription.sh "https://www.instagram.com/nike/"
```

Optional flags:

```bash
./run_latest_reel_transcription.sh \
  "https://www.instagram.com/reel/DXR5AB3j78e/" \
  --model small \
  --language en \
  --output-dir outputs
```

Machine-readable output for agent workflows:

```bash
./run_latest_reel_transcription.sh \
  "https://www.instagram.com/nike/" \
  --json
```

## MCP Server

Start the MCP server over stdio:

```bash
./run_mcp_server.sh
```

This is the preferred mode for local MCP clients such as Claude Code, Claude Desktop, and Cursor. Point the client at:

- command: `/Users/anw3r/AI Projects/IG Content Transcriber/run_mcp_server.sh`
- args: none

If your MCP client prefers HTTP instead of stdio:

```bash
./run_mcp_server.sh --transport streamable-http --host 127.0.0.1 --port 8001
```

Then connect the client to:

```text
http://127.0.0.1:8001/mcp
```

MCP tools exposed:

- `transcribe_input`
- `transcribe_local_audio`
- `list_recent_batches`
- `read_batch_manifest`
- `read_video_output`

MCP resources exposed:

- `ig-transcriber://server`
- `ig-transcriber://recent-batches`
- `ig-transcriber://manifest/{source_group}/{source_label}`
- `ig-transcriber://transcript/{source_group}/{source_label}/{video_id}`

## Web UI

Start the app:

```bash
./run_ui.sh
```

The launcher will pick an open localhost port and open the browser automatically.
It also installs the frontend dependencies if needed and rebuilds the shadcn dashboard before starting the server.

If you want to disable auto-open:

```bash
./run_ui.sh --no-open
```

If you want to open it manually, use the URL printed in the terminal. Typical URLs are:

```text
http://127.0.0.1:8000
```

The UI lets you:

- submit Instagram profile URLs or direct video URLs
- upload audio files such as `mp3`, `wav`, `m4a`, `aac`, `flac`, `ogg`, and `webm`
- choose the Whisper model and optional language hint
- monitor progress across each pipeline stage
- inspect recent jobs
- open generated audio, transcript, metadata, and manifest files
- review per-video transcripts and AI insights
- keep input, progress, transcript, activity, and history visible in a single fixed dashboard

## Output

Each run writes files under source-based folders such as:

```text
outputs/instagram_profiles/<username>/<video_id>/
outputs/video_urls/<source_label>/<video_id>/
```

Files created:

- `audio.mp3`
- `transcript.txt`
- `metadata.json`
- `manifest.json` at the batch/source level

## Notes

- This currently supports public Instagram profiles only.
- If Instagram rate-limits or blocks anonymous access, rerun later.
- Uploaded audio does not depend on Instagram and can be transcribed directly from the dashboard upload control.
- Larger Whisper models improve accuracy but take more time and memory.
- For Claude Code or other agent workflows, see `CLAUDE.md`.
- For MCP client setup, use `run_mcp_server.sh`.
- The backend now caches loaded Whisper models and reuses existing transcript outputs for the same latest reel when possible.
- AI insights use GroqCloud when `GROQ_API_KEY` is available. If Groq is unavailable, the app falls back to local heuristic insights so transcription still completes.
