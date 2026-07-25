import logging
import re
from typing import Any, Dict, List, Optional

import aiohttp
from pysqueezebox import Server, Player

_LOGGER = logging.getLogger(__name__)


def _normalize_spotify_url(url: Optional[str]) -> Optional[str]:
    """
    Convert open.spotify.com share links to the native spotify:// URI that
    LMS plays directly. Share links get 'exploded' by LMS, which causes
    pysqueezebox's load confirmation to time out (false negative).
    """
    if not url:
        return url
    m = re.match(r"https?://open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist|artist|show|episode)/([a-zA-Z0-9]+)", url)
    if m:
        return f"spotify://{m.group(1)}:{m.group(2)}"
    return url


class LMSClient:
    """
    High-level client for interacting with a Lyrion Media Server (LMS).
    Wraps pysqueezebox functionality and provides direct JSON-RPC fallback.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: Optional[str] = None,
        password: Optional[str] = None,
        https: bool = False,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.https = https
        self.prefix = "https" if self.https else "http"
        self.session: Optional[aiohttp.ClientSession] = None
        self.lms_server: Optional[Server] = None
        self.players: List[Player] = []

    @property
    def connected(self) -> bool:
        """True when an active session/server connection exists."""
        return (
            self.lms_server is not None
            and self.session is not None
            and not self.session.closed
        )

    async def connect(self) -> None:
        """Initialize (or re-initialize) the connection to the LMS server."""
        await self.close()

        auth = None
        if self.username and self.password:
            auth = aiohttp.BasicAuth(self.username, self.password)

        self.session = aiohttp.ClientSession(auth=auth)
        self.lms_server = Server(
            session=self.session,
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            uuid=None,
            name=None,
            https=self.https,
        )
        # Wait for initial status so the server is actually reachable.
        await self.lms_server.async_status()
        # Fetch initial list of players.
        self.players = (await self.lms_server.async_get_players()) or []
        _LOGGER.info("Connected to LMS at %s://%s:%s", self.prefix, self.host, self.port)

    async def ensure_connected(self) -> None:
        """Connect lazily and only reconnect when the existing session is gone."""
        if self.connected:
            return
        await self.connect()

    async def refresh_players(self) -> List[Player]:
        """Re-fetch the current list of players from the server."""
        assert self.lms_server is not None
        self.players = (await self.lms_server.async_get_players()) or []
        return self.players

    async def get_system_status(self) -> Dict[str, Any]:
        """
        Returns a 'System Topology' summary.
        Includes all players, their sync groups, and current playback status.
        """
        await self.ensure_connected()
        await self.refresh_players()

        status_summary: Dict[str, Any] = {
            "players": [],
            "server_status": {},
        }

        for player in self.players:
            # async_get_players() returns player objects with unpopulated status
            # fields (power, mode, volume, etc. all default to None). async_update()
            # fetches the current playback state from the server for each player.
            try:
                await player.async_update()
            except Exception as e:
                _LOGGER.warning("Failed to update status for player %s: %s", player.player_id, e)
            track = player.current_track or {}
            status_summary["players"].append(
                {
                    "id": player.player_id,
                    "name": player.name,
                    "power": player.power,
                    "mode": player.mode,
                    "volume": player.volume,
                    "synced": bool(player.sync_group),
                    "title": track.get("title"),
                    "artist": track.get("artist"),
                }
            )

        assert self.lms_server is not None
        server_status = await self.lms_server.async_status()
        if server_status:
            status_summary["server_status"] = {
                "version": server_status.get("version"),
                "ip": server_status.get("ip"),
                "uuid": server_status.get("uuid"),
            }

        return status_summary

    async def play_media(
        self,
        url: Optional[str] = None,
        track_id: Optional[int] = None,
        search_query: Optional[str] = None,
        player_id: Optional[str] = None,
    ) -> bool:
        """
        Play a piece of media on the target player. Exactly one of
        ``url``, ``track_id`` or ``search_query`` must be provided.

        - ``url``: play the given stream/file URL directly.
        - ``track_id``: play a library track by its numeric track id.
        - ``search_query``: search the library and play the first result.
        """
        provided = [v for v in (url, track_id, search_query) if v is not None and v != ""]
        if not provided:
            raise ValueError(
                "play_media requires one of 'url', 'track_id' or 'search_query'."
            )
        if len(provided) > 1:
            raise ValueError(
                "play_media accepts only one of 'url', 'track_id' or 'search_query'."
            )

        target_player = await self._get_player(player_id)

        try:
            # Ensure the player is powered on so playback actually starts.
            # playlist play/load commands don't auto-power-on (unlike bare 'play').
            if not target_player.power:
                await target_player.async_set_power(True)

            if url:
                return bool(await target_player.async_load_url(_normalize_spotify_url(url)))
            if track_id is not None:
                # LMS plays a library track by id via the playlistcontrol command.
                # Use async_query (not async_command) because playlistcontrol returns
                # a result dict (e.g. {"count": 1}) on success, which async_command
                # would misinterpret as failure (it only treats {} as success).
                result = await target_player.async_query(
                    "playlistcontrol", "cmd:load", f"track_id:{int(track_id)}"
                )
                return result is not None
            # search_query branch — guaranteed non-None by the validation above
            assert search_query is not None
            first_url = await self._search_media(search_query, target_player)
            if not first_url:
                _LOGGER.info("play_media search returned no results for %r", search_query)
                return False
            return bool(await target_player.async_load_url(first_url))
        except Exception as e:
            _LOGGER.error("Error in play_media: %s", e)
            return False

    async def play_radio(
        self, search_query: str, player_id: Optional[str] = None
    ) -> bool:
        """
        Play Spotify Artist Radio for the given search query. Navigates the
        Spotty app menu to find the artist, then loads their radio (a
        Spotify recommendations playlist of ~200 tracks).
        """
        target_player = await self._get_player(player_id)
        pid = target_player.player_id

        try:
            # Ensure powered on
            if not target_player.power:
                await target_player.async_set_power(True)

            # 1) Search spotty for the artist
            search = await self.direct_rpc(
                "spotty",
                ["items", "0", "5", "item_id:1.0", f"search:{search_query}"],
                player_id=pid,
            )
            # Find the "Artists" category link in search results
            artists_cat = None
            for item in (search or {}).get("loop_loop") or []:
                if item.get("name") == "Artists":
                    artists_cat = item.get("id")
                    break
            if not artists_cat:
                _LOGGER.info("play_radio: no Artists category for %r", search_query)
                return False

            # 2) Drill into Artists to get the first artist
            artists = await self.direct_rpc(
                "spotty", ["items", "0", "3", f"item_id:{artists_cat}"], player_id=pid
            )
            first_artist = ((artists or {}).get("loop_loop") or [None])[0]
            if not first_artist:
                _LOGGER.info("play_radio: no artists found for %r", search_query)
                return False
            artist_id = first_artist.get("id")

            # 3) Drill into the artist to find "Artist Radio" (item_id ends with .4)
            detail = await self.direct_rpc(
                "spotty", ["items", "0", "10", f"item_id:{artist_id}"], player_id=pid
            )
            radio_id = None
            for item in (detail or {}).get("loop_loop") or []:
                if "radio" in (item.get("name") or "").lower():
                    radio_id = item.get("id")
                    break
            if not radio_id:
                _LOGGER.info("play_radio: no radio option for artist %r", first_artist.get("name"))
                return False

            # 4) Play the radio — loads ~200 recommended tracks
            await self.direct_rpc(
                "spotty", ["playlist", "play", f"item_id:{radio_id}"], player_id=pid
            )
            _LOGGER.info(
                "play_radio: started artist radio for %r", first_artist.get("name")
            )
            return True
        except Exception as e:
            _LOGGER.error("Error in play_radio: %s", e)
            return False

    async def search_media(self, search_query: str) -> List[Dict[str, Any]]:
        """
        Search both the local LMS library and the Spotty (Spotify) app for
        playable tracks matching ``search_query``.

        Returns a list of dicts with keys: title, url, source ('library' or
        'spotify'). Local library results are listed first.
        """
        await self.ensure_connected()
        results: List[Dict[str, Any]] = []
        assert self.lms_server is not None

        # 1) Local library search
        browse = await self.lms_server.async_browse(
            "titles", limit=10, search_query=search_query
        )
        for item in (browse or {}).get("items") or []:
            url = item.get("url")
            if url:
                results.append(
                    {
                        "title": item.get("title", ""),
                        "url": url,
                        "source": "library",
                    }
                )

        # 2) Spotty (Spotify) app search — requires a player context
        if self.players:
            player_id = self.players[0].player_id
            spotty = await self.direct_rpc(
                "spotty",
                ["items", "0", "20", "item_id:1.0", f"search:{search_query}", "want_url:1"],
                player_id=player_id,
            )
            for item in (spotty or {}).get("loop_loop") or []:
                # Skip non-audio category links (Artists, Albums, Playlists, etc.)
                if not item.get("isaudio"):
                    continue
                url = item.get("url")
                if url:
                    results.append(
                        {
                            "title": item.get("name", ""),
                            "url": url,
                            "source": "spotify",
                        }
                    )

        return results

    async def _search_media(
        self, search_query: str, player: Player
    ) -> Optional[str]:
        """Find the first playable URL for a search query (local then Spotify)."""
        results = await self.search_media(search_query)
        if not results:
            return None
        first = results[0]
        _LOGGER.info(
            "play_media search %r matched %r (%s)",
            search_query,
            first.get("title"),
            first.get("source"),
        )
        return first.get("url")

    async def get_now_playing(self, player_id: Optional[str] = None) -> Dict[str, Any]:
        """Return now-playing info: mode, title, artist, album, position,
        duration, volume, shuffle, repeat, and playlist index/count."""
        await self.ensure_connected()
        result = await self.direct_rpc(
            "status", ["-", "1", "tags:acdl"], player_id=player_id
        )
        if not result:
            return {}
        track = (result.get("playlist_loop") or [{}])[0]
        return {
            "mode": result.get("mode"),
            "power": result.get("power"),
            "volume": result.get("mixer volume"),
            "shuffle": result.get("playlist shuffle"),
            "repeat": result.get("playlist repeat"),
            "position": result.get("time"),
            "duration": result.get("duration"),
            "playlist_index": result.get("playlist_cur_index"),
            "playlist_tracks": result.get("playlist_tracks"),
            "title": track.get("title"),
            "artist": track.get("artist"),
            "album": track.get("album"),
        }

    async def seek(self, position: float, player_id: Optional[str] = None) -> bool:
        """
        Seek to an absolute position (seconds) in the current track.
        Use negative values to seek backwards from the end.
        """
        player = await self._get_player(player_id)
        return bool(await player.async_time(position))

    async def manage_playlist(
        self,
        action: str,
        player_id: Optional[str] = None,
        url: Optional[str] = None,
        index: Optional[int] = None,
        to_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Manage the current playlist of a player.

        ``action`` is one of:
        - "add": append a track URL to the end of the playlist
        - "insert": insert a track URL after the current track
        - "delete": delete the track at ``index``
        - "clear": stop and clear the entire playlist
        - "move": move track from ``index`` to ``to_index``
        - "jump": jump to track at ``index`` (starts playing)
        - "save": save current playlist as ``url`` (name)
        """
        valid = {"add", "insert", "delete", "clear", "move", "jump", "save"}
        if action not in valid:
            raise ValueError(f"action must be one of {sorted(valid)}")
        if action in ("add", "insert", "save") and not url:
            raise ValueError(f"action '{action}' requires a url")
        if action in ("delete", "jump") and index is None:
            raise ValueError(f"action '{action}' requires an index")
        if action == "move" and (index is None or to_index is None):
            raise ValueError("action 'move' requires index and to_index")

        assert url is not None or action not in ("add", "insert", "save")

        if action == "add":
            return await self.direct_rpc(
                "playlist", ["add", _normalize_spotify_url(url)], player_id=player_id
            )
        if action == "insert":
            return await self.direct_rpc(
                "playlist", ["insert", _normalize_spotify_url(url)], player_id=player_id
            )
        if action == "delete":
            return await self.direct_rpc(
                "playlist", ["delete", str(index)], player_id=player_id
            )
        if action == "clear":
            return await self.direct_rpc("playlist", ["clear"], player_id=player_id)
        if action == "move":
            return await self.direct_rpc(
                "playlist", ["move", str(index), str(to_index)], player_id=player_id
            )
        if action == "jump":
            return await self.direct_rpc(
                "playlist", ["index", str(index)], player_id=player_id
            )
        return await self.direct_rpc("playlist", ["save", url], player_id=player_id)

    async def browse_library(
        self,
        category: str,
        limit: int = 50,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Browse the local LMS library by category.

        ``category`` is one of: genres, artists, albums, titles, years,
        playlists. Optional ``search`` narrows results.
        """
        valid = {"genres", "artists", "albums", "titles", "years", "playlists"}
        if category not in valid:
            raise ValueError(f"category must be one of {sorted(valid)}")
        await self.ensure_connected()
        assert self.lms_server is not None
        browse = await self.lms_server.async_browse(
            category, limit=limit, search_query=search
        )
        # Trim to minimal fields the LLM needs: id + title (+ url for titles)
        items = []
        for item in (browse or {}).get("items") or []:
            entry = {"id": item.get("id"), "title": item.get("title", "")}
            if category == "titles" and item.get("url"):
                entry["url"] = item.get("url")
            items.append(entry)
        return items

    async def play_collection(
        self,
        player_id: Optional[str] = None,
        album_id: Optional[str] = None,
        artist_id: Optional[str] = None,
        genre_id: Optional[str] = None,
        track_id: Optional[str] = None,
        playlist_id: Optional[str] = None,
        action: str = "load",
    ) -> bool:
        """
        Play a collection of tracks by ID using LMS playlistcontrol.

        Provide exactly one ID filter (album_id, artist_id, genre_id,
        track_id, or playlist_id). ``action`` is "load" (replace, default),
        "add" (append), or "insert" (after current track).
        """
        ids = [v for v in (album_id, artist_id, genre_id, track_id, playlist_id) if v]
        if not ids:
            raise ValueError(
                "play_collection requires one of album_id, artist_id, genre_id, "
                "track_id, or playlist_id"
            )
        if len(ids) > 1:
            raise ValueError("play_collection accepts only one ID filter at a time")
        if action not in ("load", "add", "insert"):
            raise ValueError("action must be 'load', 'add', or 'insert'")

        player = await self._get_player(player_id)

        # Ensure powered on for 'load' action (starts playback).
        if action == "load" and not player.power:
            await player.async_set_power(True)

        params = [f"cmd:{action}"]
        if track_id:
            params.append(f"track_id:{track_id}")
        elif album_id:
            params.append(f"album_id:{album_id}")
        elif artist_id:
            params.append(f"artist_id:{artist_id}")
        elif genre_id:
            params.append(f"genre_id:{genre_id}")
        elif playlist_id:
            params.append(f"playlist_id:{playlist_id}")

        result = await player.async_query("playlistcontrol", *params)
        return result is not None

    async def rescan_library(self, mode: str = "full") -> Dict[str, Any]:
        """
        Trigger a library rescan. ``mode`` is one of:
        "full" (default), "playlists", "onlinelibrary".
        """
        valid = {"full", "playlists", "onlinelibrary"}
        if mode not in valid:
            raise ValueError(f"mode must be one of {sorted(valid)}")
        if mode == "full":
            return await self.direct_rpc("rescan", [])
        return await self.direct_rpc("rescan", [mode])

    async def get_scan_status(self) -> Dict[str, Any]:
        """Return current library scan progress."""
        await self.ensure_connected()
        result = await self.direct_rpc("rescanprogress", [])
        return result if isinstance(result, dict) else {}

    async def pause_media(self, player_id: Optional[str] = None) -> bool:
        """Pause the media on the specified player."""
        player = await self._get_player(player_id)
        return bool(await player.async_pause())

    async def stop_media(self, player_id: Optional[str] = None) -> bool:
        """Stop the media on the specified player."""
        player = await self._get_player(player_id)
        return bool(await player.async_stop())

    async def set_volume(self, volume: int, player_id: Optional[str] = None) -> bool:
        """Set the volume (0-100) on the specified player."""
        if not 0 <= int(volume) <= 100:
            raise ValueError("volume must be between 0 and 100")
        player = await self._get_player(player_id)
        return bool(await player.async_set_volume(int(volume)))

    async def sync_players(self, player_id: str, other_player_id: str) -> bool:
        """Sync the specified player with another player."""
        player = await self._get_player(player_id)
        # Resolve other_player_id to a MAC address (LMS sync needs the player ID,
        # not the display name).
        other_player = await self._get_player(other_player_id)
        return bool(await player.async_sync(other_player.player_id))

    async def unsync_player(self, player_id: str) -> bool:
        """Remove a player from its sync group."""
        player = await self._get_player(player_id)
        return bool(await player.async_unsync())

    async def set_shuffle(
        self, mode: int, player_id: Optional[str] = None
    ) -> bool:
        """Set shuffle mode: 0=off, 1=by song, 2=by album."""
        shuffle_names = {0: "none", 1: "song", 2: "album"}
        if mode not in shuffle_names:
            raise ValueError("shuffle mode must be 0 (off), 1 (song), or 2 (album)")
        player = await self._get_player(player_id)
        return bool(await player.async_set_shuffle(shuffle_names[mode]))

    async def set_repeat(
        self, mode: int, player_id: Optional[str] = None
    ) -> bool:
        """Set repeat mode: 0=off, 1=single song, 2=entire playlist."""
        repeat_names = {0: "none", 1: "song", 2: "playlist"}
        if mode not in repeat_names:
            raise ValueError("repeat mode must be 0 (off), 1 (song), or 2 (all)")
        player = await self._get_player(player_id)
        return bool(await player.async_set_repeat(repeat_names[mode]))

    async def power_control(
        self, state: bool, player_id: Optional[str] = None
    ) -> bool:
        """Power a player on (True) or off (False)."""
        player = await self._get_player(player_id)
        return bool(await player.async_set_power(state))

    async def mute(
        self, mute: Optional[bool] = None, player_id: Optional[str] = None
    ) -> bool:
        """
        Mute or unmute a player. Pass None or omit to toggle.
        """
        player = await self._get_player(player_id)
        if mute is None:
            # toggle
            current = await player.async_query("mixer", "muting", "?")
            current_val = str(current.get("_muting", "0")) if current else "0"
            target = False if current_val == "1" else True
        else:
            target = mute
        return bool(await player.async_set_muting(target))

    async def direct_rpc(
        self,
        method: str,
        params: Optional[List[Any]] = None,
        player_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a raw JSON-RPC/CLI query to LMS.

        ``method`` is the LMS CLI command (e.g. "serverstatus", "songinfo").
        ``params`` are additional positional CLI parameters.
        ``player_id`` scopes the query to a player (empty string = server scope).
        Returns the raw result dict from LMS, or an error dict.
        """
        await self.ensure_connected()
        assert self.lms_server is not None
        params = params or []
        # LMS CLI params are strings; coerce for safety.
        str_params = [str(p) for p in params]
        result = await self.lms_server.async_query(
            method, *str_params, player=player_id or ""
        )
        if result is None:
            return {"success": False, "error": f"No response from LMS for {method}"}
        return result

    async def _get_player(self, player_id: Optional[str]) -> Player:
        """
        Get the player object by ID or name, or return the first player if
        ID is None. Matches MAC address exactly, or player name
        case-insensitively (partial match). Refreshes the player list once
        if the requested id/name is not cached.
        """
        await self.ensure_connected()

        def find(pid: Optional[str]) -> Optional[Player]:
            if pid is None:
                if not self.players:
                    return None
                return self.players[0]
            pid_lower = pid.lower()
            for p in self.players:
                # Exact MAC match
                if p.player_id == pid:
                    return p
                # Case-insensitive name match (exact or partial)
                if p.name and (
                    p.name.lower() == pid_lower
                    or pid_lower in p.name.lower()
                    or p.name.lower() in pid_lower
                ):
                    return p
            return None

        player = find(player_id)
        if player is not None:
            return player

        # Player not in cache: refresh and try once more.
        await self.refresh_players()
        player = find(player_id)
        if player is None:
            if player_id is None:
                raise RuntimeError("No players connected to LMS.")
            raise ValueError(
                f"Player '{player_id}' not found. "
                f"Available: {[p.name for p in self.players]}"
            )
        return player

    async def close(self) -> None:
        """Close the session and LMS connection."""
        if self.session:
            await self.session.close()
        self.session = None
        self.lms_server = None
        self.players = []
