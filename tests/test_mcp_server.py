from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

import mcp_server


@pytest.fixture
def server():
    return mcp_server.build_server(host="127.0.0.1", port=8000, debug=False)


@pytest.fixture
def clean_outputs(output_root: Path):
    for child in output_root.iterdir():
        shutil.rmtree(child, ignore_errors=True)
    yield output_root


def _write_batch(output_root: Path, group: str, label: str, video_id: str, transcript: str = "hello world") -> None:
    video_dir = output_root / group / label / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
    (video_dir / "metadata.json").write_text(json.dumps({"video_id": video_id, "title": "t"}), encoding="utf-8")
    manifest = {
        "status": "ok",
        "input_kind": "video_url",
        "input_url": "https://example.com/v",
        "canonical_url": "https://example.com/v",
        "model": "base",
        "total_videos": 1,
        "completed_videos": 1,
        "videos": [{"video_id": video_id, "transcript_text": transcript}],
        "manifest_file": str(output_root / group / label / "manifest.json"),
    }
    (output_root / group / label / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit tests for validation helpers
# ---------------------------------------------------------------------------


def test_safe_slug():
    assert mcp_server._safe_slug("Hello World!") == "hello-world"
    assert mcp_server._safe_slug("../../etc/passwd") == "etc-passwd"
    assert mcp_server._safe_slug("") == "item"
    assert mcp_server._safe_slug("   ", "fallback") == "fallback"
    # Dot-only inputs must never become traversal path components.
    assert mcp_server._safe_slug("..") == "item"
    assert mcp_server._safe_slug(".") == "item"
    assert mcp_server._safe_slug(".-.-") == "item"
    assert mcp_server._safe_slug("vid1.mp3") == "vid1.mp3"


def test_within_output_root_rejects_traversal(output_root: Path):
    with pytest.raises(FileNotFoundError):
        mcp_server._within_output_root(output_root / ".." / "somewhere-else")
    with pytest.raises(FileNotFoundError):
        mcp_server._within_output_root(Path("/etc/passwd"))
    assert mcp_server._within_output_root(output_root / "a" / "b") == (output_root / "a" / "b").resolve()


def test_validate_url():
    url, err = mcp_server._validate_url("  'https://www.instagram.com/foo/'  ")
    assert err is None and url == "https://www.instagram.com/foo/"

    url, err = mcp_server._validate_url("<https://example.com/video>")
    assert err is None and url == "https://example.com/video"

    _, err = mcp_server._validate_url("")
    assert err["status"] == "error" and err["error_type"] == "invalid_input"

    _, err = mcp_server._validate_url("ftp://example.com/x")
    assert err["error_type"] == "invalid_input"

    _, err = mcp_server._validate_url("not a url")
    assert err["error_type"] == "invalid_input"


def test_validate_model():
    model, err = mcp_server._validate_model("base")
    assert err is None and model == "base"

    model, err = mcp_server._validate_model("")
    assert err is None and model == "base"

    _, err = mcp_server._validate_model("gpt-4")
    assert err["error_type"] == "invalid_input"
    assert "base" in err["hint"]


def test_validate_model_extra_env(monkeypatch):
    monkeypatch.setenv("REELRECON_EXTRA_MODELS", "custom-model, another")
    model, err = mcp_server._validate_model("custom-model")
    assert err is None and model == "custom-model"


def test_normalize_language():
    assert mcp_server._normalize_language(None) == (None, None)
    assert mcp_server._normalize_language("auto") == (None, None)
    assert mcp_server._normalize_language("  ") == (None, None)
    assert mcp_server._normalize_language(" EN ") == ("en", None)
    assert mcp_server._normalize_language("english") == ("english", None)

    _, err = mcp_server._normalize_language("!!nonsense!!")
    assert err["error_type"] == "invalid_input"


def test_validate_limit():
    assert mcp_server._validate_limit(0) == 1
    assert mcp_server._validate_limit(-5) == 1
    assert mcp_server._validate_limit(10_000) == mcp_server.MAX_LIST_LIMIT
    assert mcp_server._validate_limit(7) == 7


def test_shape_batch_result_strip_and_truncate():
    batch = {"status": "ok", "videos": [{"video_id": "v1", "transcript_text": "abcdefghij"}]}

    stripped = mcp_server._shape_batch_result(dict(batch), include_transcript_text=False, max_transcript_chars=0)
    assert "transcript_text" not in stripped["videos"][0]
    assert stripped["videos"][0]["transcript_chars"] == 10

    truncated = mcp_server._shape_batch_result(
        json.loads(json.dumps(batch)), include_transcript_text=True, max_transcript_chars=4
    )
    assert truncated["videos"][0]["transcript_text"] == "abcd"
    assert truncated["videos"][0]["transcript_text_truncated"] is True
    assert truncated["videos"][0]["transcript_chars"] == 10

    untouched = mcp_server._shape_batch_result(
        json.loads(json.dumps(batch)), include_transcript_text=True, max_transcript_chars=0
    )
    assert untouched["videos"][0]["transcript_text"] == "abcdefghij"


def test_manifest_summary_corrupt(clean_outputs: Path):
    bad_dir = clean_outputs / "group" / "label"
    bad_dir.mkdir(parents=True)
    (bad_dir / "manifest.json").write_text("{ this is not json", encoding="utf-8")

    summary = mcp_server._manifest_summary(bad_dir / "manifest.json")
    assert summary["status"] == "unreadable"
    assert "error" in summary


def test_attach_resource_links_survives_bad_manifest_path():
    batch = {"status": "ok", "manifest_file": "/etc/passwd", "videos": [{"video_id": "v"}]}
    result = mcp_server._attach_resource_links(dict(batch))
    assert result["status"] == "ok"
    assert "manifest_resource" not in result


# ---------------------------------------------------------------------------
# End-to-end tool calls over a real in-memory MCP session
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tools_and_server_resource(server):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools.tools}
        assert tool_names == {
            "transcribe_input",
            "transcribe_local_audio",
            "list_recent_batches",
            "read_batch_manifest",
            "read_video_output",
            "check_health",
        }

        from pydantic import AnyUrl

        resource = await client.read_resource(AnyUrl("reelrecon://server"))
        payload = json.loads(resource.contents[0].text)
        assert payload["name"] == mcp_server.SERVER_NAME
        assert "error_contract" in payload


@pytest.mark.anyio
async def test_transcribe_input_rejects_bad_inputs(server):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool("transcribe_input", {"input_url": ""})
        assert result.structuredContent["status"] == "error"
        assert result.structuredContent["error_type"] == "invalid_input"

        result = await client.call_tool("transcribe_input", {"input_url": "file:///etc/passwd"})
        assert result.structuredContent["error_type"] == "invalid_input"

        result = await client.call_tool(
            "transcribe_input",
            {"input_url": "https://www.instagram.com/x/", "model_name": "bogus-model"},
        )
        assert result.structuredContent["error_type"] == "invalid_input"

        result = await client.call_tool(
            "transcribe_input",
            {"input_url": "https://www.instagram.com/x/", "language": "!!bad!!"},
        )
        assert result.structuredContent["error_type"] == "invalid_input"


@pytest.mark.anyio
async def test_transcribe_local_audio_rejects_bad_paths(server, tmp_path):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool("transcribe_local_audio", {"audio_path": ""})
        assert result.structuredContent["error_type"] == "invalid_input"

        result = await client.call_tool("transcribe_local_audio", {"audio_path": "/does/not/exist.mp3"})
        assert result.structuredContent["error_type"] == "not_found"

        result = await client.call_tool("transcribe_local_audio", {"audio_path": str(tmp_path)})
        assert result.structuredContent["error_type"] == "invalid_input"

        empty = tmp_path / "empty.mp3"
        empty.touch()
        result = await client.call_tool("transcribe_local_audio", {"audio_path": str(empty)})
        assert result.structuredContent["error_type"] == "invalid_input"


@pytest.mark.anyio
async def test_list_and_read_batches(server, clean_outputs: Path):
    _write_batch(clean_outputs, "video_urls", "creator", "vid1", transcript="a" * 100)
    corrupt_dir = clean_outputs / "video_urls" / "broken"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "manifest.json").write_text("not json", encoding="utf-8")

    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool("list_recent_batches", {"limit": 10})
        payload = result.structuredContent
        assert payload["status"] == "ok"
        assert payload["count"] == 2
        statuses = {batch["source_label"]: batch["status"] for batch in payload["batches"]}
        assert statuses["creator"] == "ok"
        assert statuses["broken"] == "unreadable"

        result = await client.call_tool(
            "read_batch_manifest", {"source_group": "video_urls", "source_label": "creator"}
        )
        payload = result.structuredContent
        assert payload["status"] == "ok"
        assert payload["batch"]["videos"][0]["video_id"] == "vid1"

        # Truncation applies to embedded transcripts too.
        result = await client.call_tool(
            "read_batch_manifest",
            {"source_group": "video_urls", "source_label": "creator", "max_transcript_chars": 10},
        )
        video = result.structuredContent["batch"]["videos"][0]
        assert len(video["transcript_text"]) == 10
        assert video["transcript_text_truncated"] is True

        result = await client.call_tool(
            "read_batch_manifest", {"source_group": "video_urls", "source_label": "missing"}
        )
        payload = result.structuredContent
        assert payload["error_type"] == "not_found"
        assert "available_batches" in payload

        result = await client.call_tool(
            "read_batch_manifest", {"source_group": "video_urls", "source_label": "broken"}
        )
        assert result.structuredContent["error_type"] == "internal_error"

        result = await client.call_tool("read_batch_manifest", {"source_group": "", "source_label": "x"})
        assert result.structuredContent["error_type"] == "invalid_input"

        # Path traversal attempts are neutralized by slugging, not executed.
        result = await client.call_tool(
            "read_batch_manifest", {"source_group": "../../etc", "source_label": "passwd"}
        )
        assert result.structuredContent["status"] == "error"
        assert result.structuredContent["error_type"] in {"not_found", "invalid_input"}


@pytest.mark.anyio
async def test_read_video_output(server, clean_outputs: Path):
    _write_batch(clean_outputs, "video_urls", "creator", "vid1", transcript="hello transcript")

    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "read_video_output",
            {"source_group": "video_urls", "source_label": "creator", "video_id": "vid1"},
        )
        payload = result.structuredContent
        assert payload["status"] == "ok"
        assert payload["transcript_text"] == "hello transcript"
        assert payload["transcript_chars"] == len("hello transcript")

        result = await client.call_tool(
            "read_video_output",
            {"source_group": "video_urls", "source_label": "creator", "video_id": "vid1", "max_transcript_chars": 5},
        )
        payload = result.structuredContent
        assert payload["transcript_text"] == "hello"
        assert payload["transcript_text_truncated"] is True

        result = await client.call_tool(
            "read_video_output",
            {"source_group": "video_urls", "source_label": "creator", "video_id": "nope"},
        )
        assert result.structuredContent["error_type"] == "not_found"


@pytest.mark.anyio
async def test_recent_batches_resource_tolerates_corruption(server, clean_outputs: Path):
    corrupt_dir = clean_outputs / "g" / "l"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "manifest.json").write_text("{bad", encoding="utf-8")

    from pydantic import AnyUrl

    async with create_connected_server_and_client_session(server._mcp_server) as client:
        resource = await client.read_resource(AnyUrl("reelrecon://recent-batches"))
        payload = json.loads(resource.contents[0].text)
        assert payload["batches"][0]["status"] == "unreadable"


@pytest.mark.anyio
async def test_check_health(server):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool("check_health", {})
        payload = result.structuredContent
        assert payload["status"] in {"ok", "degraded"}
        assert payload["output_root_writable"] is True
        assert payload["jobs"]["max_concurrent"] == mcp_server.MAX_CONCURRENT_JOBS
        assert "base" in payload["allowed_models"]
        assert isinstance(payload["problems"], list)
