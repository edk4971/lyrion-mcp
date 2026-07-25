import logging
import os
from typing import Any, Dict, List, Literal, Optional, cast

from client import LMSClient
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger("lms-mcp")

LMS_HOST = os.getenv("LMS_HOST", "localhost")
LMS_PORT = int(os.getenv("LMS_PORT", "9000"))
LMS_USERNAME = os.getenv("LMS_USERNAME", "") or None
LMS_PASSWORD = os.getenv("LMS_PASSWORD", "") or None
LMS_HTTPS = os.getenv("LMS_HTTPS", "").lower() in ("1", "true", "yes", "on")

mcp = FastMCP("LMS-Control")

client = LMSClient(
    host=LMS_HOST,
    port=LMS_PORT,
    username=LMS_USERNAME,
    password=LMS_PASSWORD,
    https=LMS_HTTPS,
)


@mcp.tool()
async def get_status(player_id: Optional[str] = None) -> Dict[str, Any]:
    """Get player status. With player_id: now-playing (mode, title, artist,
    album, position, duration, volume, shuffle, repeat, playlist info).
    Without: system topology (all players, server version)."""
    try:
        if player_id:
            return await client.get_now_playing(player_id=player_id)
        return await client.get_system_status()
    except Exception as e:
        _LOGGER.exception("get_status failed")
        return {"error": str(e)}


@mcp.tool()
async def play_media(
    url: Optional[str] = None,
    track_id: Optional[int] = None,
    search_query: Optional[str] = None,
    album_id: Optional[str] = None,
    artist_id: Optional[str] = None,
    genre_id: Optional[str] = None,
    playlist_id: Optional[str] = None,
    radio: Optional[bool] = None,
    player_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Play media. Provide exactly one source:
    - url: stream or spotify:track:... link
    - track_id: local library track by numeric ID
    - search_query: defaults to Spotify Artist Radio (~200 recommended
      tracks). This is the right choice for "play Foo Fighters" or any
      artist request. Pass radio=false ONLY if the user names a specific
      song, e.g. "play Everlong by Foo Fighters" (plays one track then stops).
    - album_id/artist_id/genre_id/playlist_id: play a collection by ID.
    player_id targets a specific player (default: first)."""
    try:
        # Radio is the default for search_query — playing one track and
        # stopping is rarely what the user wants. Override with radio=False
        # only when a specific song is requested.
        if search_query and radio is not False:
            success = await client.play_radio(
                search_query=search_query, player_id=player_id
            )
        elif album_id or artist_id or genre_id or playlist_id:
            success = await client.play_collection(
                player_id=player_id,
                album_id=album_id,
                artist_id=artist_id,
                genre_id=genre_id,
                playlist_id=playlist_id,
            )
        else:
            success = await client.play_media(
                url=url, track_id=track_id, search_query=search_query,
                player_id=player_id,
            )
        return {"success": success}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        _LOGGER.exception("play_media failed")
        return {"success": False, "error": str(e)}


@mcp.tool()
async def search_media(search_query: str, limit: int = 5) -> Dict[str, Any]:
    """Search local library and Spotify (Spotty). Returns playable tracks
    with title, url, and source. Use limit to cap results (default 5)."""
    try:
        results = await client.search_media(search_query)
        return {"results": results[:limit], "count": len(results)}
    except Exception as e:
        _LOGGER.exception("search_media failed")
        return {"results": [], "count": 0, "error": str(e)}


@mcp.tool()
async def control_playback(
    action: Literal["pause", "stop", "play", "seek", "power_on", "power_off"],
    player_id: Optional[str] = None,
    position: Optional[float] = None,
) -> Dict[str, Any]:
    """Control playback. action: pause, stop, play (resume), seek (needs
    position in seconds), power_on, or power_off."""
    try:
        if action == "pause":
            ok = await client.pause_media(player_id=player_id)
        elif action == "stop":
            ok = await client.stop_media(player_id=player_id)
        elif action == "play":
            ok = bool(await (await client._get_player(player_id)).async_play())
        elif action == "seek":
            if position is None:
                raise ValueError("seek requires position")
            ok = await client.seek(position=position, player_id=player_id)
        elif action == "power_on":
            ok = await client.power_control(True, player_id=player_id)
        elif action == "power_off":
            ok = await client.power_control(False, player_id=player_id)
        else:
            raise ValueError(f"unknown action: {action}")
        return {"success": ok}
    except (ValueError, Exception) as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def manage_playlist(
    action: Literal["add", "insert", "delete", "clear", "move", "jump", "save"],
    player_id: Optional[str] = None,
    url: Optional[str] = None,
    index: Optional[int] = None,
    to_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Manage current playlist. add/insert need url, delete/jump need
    index, move needs index+to_index, clear needs nothing, save needs url
    (playlist name)."""
    try:
        return await client.manage_playlist(
            action=action, player_id=player_id, url=url,
            index=index, to_index=to_index,
        )
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        _LOGGER.exception("manage_playlist failed")
        return {"success": False, "error": str(e)}


@mcp.tool()
async def set_player(
    param: Literal["volume", "shuffle", "repeat", "mute"],
    value: Optional[int] = None,
    player_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Set a player parameter. volume: 0-100. shuffle: 0=off,1=song,2=album.
    repeat: 0=off,1=song,2=all. mute: 1=mute,0=unmute,omit=toggle."""
    try:
        if param == "volume":
            if value is None or not 0 <= value <= 100:
                raise ValueError("volume needs value 0-100")
            ok = await client.set_volume(value, player_id=player_id)
        elif param == "shuffle":
            if value is None or value not in (0, 1, 2):
                raise ValueError("shuffle needs 0, 1, or 2")
            ok = await client.set_shuffle(value, player_id=player_id)
        elif param == "repeat":
            if value is None or value not in (0, 1, 2):
                raise ValueError("repeat needs 0, 1, or 2")
            ok = await client.set_repeat(value, player_id=player_id)
        elif param == "mute":
            if value is None:
                ok = await client.mute(None, player_id=player_id)
            else:
                ok = await client.mute(bool(value), player_id=player_id)
        else:
            raise ValueError(f"unknown param: {param}")
        return {"success": ok}
    except (ValueError, Exception) as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def sync_players(
    action: Literal["sync", "unsync"],
    player_id: str,
    other_player_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Sync (needs other_player_id) or unsync (needs player_id only) players."""
    try:
        if action == "sync":
            if not other_player_id:
                raise ValueError("sync requires other_player_id")
            ok = await client.sync_players(player_id, other_player_id)
        elif action == "unsync":
            ok = await client.unsync_player(player_id)
        else:
            raise ValueError(f"unknown action: {action}")
        return {"success": ok}
    except (ValueError, Exception) as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def browse_library(
    category: Literal["genres", "artists", "albums", "titles", "years", "playlists"],
    limit: int = 20,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Browse local library. Returns items with id and title (and url where
    applicable). Use search to filter."""
    try:
        items = await client.browse_library(category=category, limit=limit, search=search)
        return {"items": items, "count": len(items)}
    except ValueError as e:
        return {"items": [], "count": 0, "error": str(e)}
    except Exception as e:
        _LOGGER.exception("browse_library failed")
        return {"items": [], "count": 0, "error": str(e)}


@mcp.tool()
async def query_lms(
    method: str,
    params: Optional[List[str]] = None,
    player_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Raw LMS CLI passthrough for advanced commands not covered by other
    tools. method is an LMS CLI command (e.g. 'rescan','serverstatus')."""
    try:
        return await client.direct_rpc(method=method, params=params, player_id=player_id)
    except Exception as e:
        _LOGGER.exception("query_lms failed")
        return {"success": False, "error": str(e)}


def _configure_transport() -> Literal["stdio", "sse", "streamable-http"]:
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    allowed: tuple[Literal["stdio", "sse", "streamable-http"], ...] = (
        "stdio", "sse", "streamable-http",
    )
    if transport not in allowed:
        raise ValueError(f"Unsupported MCP_TRANSPORT={transport!r}")
    if transport in ("sse", "streamable-http"):
        mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.getenv("MCP_PORT", "8000"))
        security = mcp.settings.transport_security
        if security is not None:
            security.enable_dns_rebinding_protection = False
    return cast(Literal["stdio", "sse", "streamable-http"], transport)


if __name__ == "__main__":
    transport = _configure_transport()
    _LOGGER.info("Starting LMS-Control MCP server (transport=%s)", transport)
    mcp.run(transport=transport)
