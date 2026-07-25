# LMS MCP Server - Agent Context

This project is an MCP (Model Context Protocol) server that allows LLMs to control a Lyrion Media Server (LMS) instance.

## Project Structure
- `client.py`: High-level `LMSClient` class wrapping `pysqueezebox` and direct JSON-RPC calls.
- `main.py`: FastMCP server implementation mapping `LMSClient` methods to MCP tools.
- `requirements.txt`: Project dependencies.
- `Dockerfile`: Docker configuration.

## Key Functionalities (9 MCP tools, optimized for context efficiency)
- `get_status`: System topology (all players) or now-playing info (with player_id).
- `play_media`: Play by URL, track_id, search_query, or collection ID (album/artist/genre/playlist).
- `search_media`: Search local library + Spotify (Spotty). Returns minimal title/url/source.
- `control_playback`: Pause, stop, play (resume), seek, power_on, power_off.
- `manage_playlist`: Add, insert, delete, clear, move, jump, or save the current playlist.
- `set_player`: Volume (0-100), shuffle (0/1/2), repeat (0/1/2), mute (toggle or explicit).
- `sync_players`: Sync or unsync players.
- `browse_library`: Browse genres/artists/albums/titles/years/playlists (trimmed to id+title).
- `query_lms`: Raw LMS CLI passthrough for advanced commands.

## Tech Stack
- Python 3.12+
- `pysqueezebox`
- `aiohttp`
- `mcp` (FastMCP)

## Connection Lifecycle
- The `LMSClient` connects lazily via `ensure_connected()` and reuses the session
  across tool calls, reconnecting only when the underlying `aiohttp` session is
  closed/dropped. Player lookups refresh the player cache once on miss.
- `play_media` accepts exactly one of `url`, `track_id`, `search_query`;
  multiple or none raise `ValueError`, surfaced to the LLM as an error envelope.

## Transport
- Default transport is `stdio`. Set `MCP_TRANSPORT=sse` or `streamable-http` to
  expose the server over HTTP (used by the Docker image, port 8000). `MCP_HOST`
  / `MCP_PORT` override bind settings for HTTP transports.

## Current Status
- Core client, MCP tools, Docker packaging, and a mocked unit-test suite are
- implemented and validated (100 tests). Read-only paths verified against a live
- LMS 9.1.1 instance. Playback paths verified live (track_id, search via local
- library + Spotify/Spotty, pause, stop, volume, direct RPC, play_collection,
- shuffle/repeat, mute, power, playlist management). Spotify share
- links (`open.spotify.com/...`) and native URIs (`spotify://track:...`) both
- supported.

## Context Efficiency
- Tool count minimized to 9 (from 19) to stay within the 5-10 tool accuracy zone.
- Return payloads trimmed to minimal actionable fields (e.g. browse returns
  only id+title; system_status drops model/firmware/sync_group details;
  now_playing drops artwork_url/coverid/bitrate/rate/sleep/sync_master).
- Total tool definition overhead: ~1,400 tokens (down from ~2,335).

## Instructions for Agents
- Use `client.py` for all LMS interactions.
- Ensure error messages are descriptive to help the LLM correct its behavior.
- Follow the existing patterns in `LMSClient` for adding new features.
- Run `python -m pytest -q` for tests and `python -m mypy client.py main.py`
  for type checking. `pysqueezebox` has no type stubs (import-untyped note is expected).
