# Lyrion MCP Server

An [MCP](https://modelcontextprotocol.io/) server that lets LLMs control a
[Lyrion Music Server](https://lyrion.org/) (LMS / Squeezebox) instance.

## Features

- **9 MCP tools** (optimized for context efficiency — ~1,400 tokens of definition overhead)
- Play by URL, track ID, search, collection ID, or Spotify Artist Radio
- Search across local library + Spotify (via the Spotty plugin)
- Full playback control: pause, stop, seek, power on/off
- Playlist management: add, insert, delete, clear, move, jump, save
- Player settings: volume, shuffle, repeat, mute
- Player sync/unsync
- Library browsing: genres, artists, albums, titles, years, playlists
- Raw LMS CLI passthrough for advanced commands

## Quick Start

```bash
pip install -r requirements.txt
LMS_HOST=your-lms-host python main.py
```

## Configuration

Set environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `LMS_HOST` | `localhost` | Lyrion server host |
| `LMS_PORT` | `9000` | Lyrion server port |
| `LMS_USERNAME` | (none) | LMS username (if auth enabled) |
| `LMS_PASSWORD` | (none) | LMS password (if auth enabled) |
| `LMS_HTTPS` | off | Set `1` if LMS uses HTTPS |
| `MCP_TRANSPORT` | `stdio` | `stdio`, `sse`, or `streamable-http` |
| `MCP_HOST` | `0.0.0.0` | Bind address (HTTP transports) |
| `MCP_PORT` | `8000` | Port (HTTP transports) |

## Docker

```bash
docker build -t lyrion-mcp .
docker run -p 8000:8000 -e LMS_HOST=your-lms-host lyrion-mcp
```

For stdio mode (e.g. Claude Desktop subprocess):

```json
{
  "mcpServers": {
    "lyrion": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
        "-e", "LMS_HOST=your-lms-host",
        "-e", "MCP_TRANSPORT=stdio",
        "lyrion-mcp"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `get_status` | System topology (all players) or now-playing (with player_id) |
| `play_media` | Play by URL, track_id, search (defaults to Artist Radio), or collection ID |
| `search_media` | Search local library + Spotify, return playable URLs |
| `control_playback` | Pause, stop, play, seek, power on/off |
| `manage_playlist` | Add, insert, delete, clear, move, jump, save |
| `set_player` | Volume, shuffle, repeat, mute |
| `sync_players` | Sync or unsync players |
| `browse_library` | Browse genres/artists/albums/titles/years/playlists |
| `query_lms` | Raw LMS CLI passthrough |

## Development

```bash
pip install -r requirements.txt
python -m pytest -q          # 110 tests
python -m mypy client.py main.py
python -m pyflakes client.py main.py tests/
```

## Requirements

- Python 3.12+
- A running Lyrion Music Server (9.x)
- [Spotty plugin](https://github.com/michaelherger/Spotty-Plugin) (optional, for Spotify)
