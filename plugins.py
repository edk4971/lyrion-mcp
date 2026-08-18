"""
Streaming-plugin adapters for the Lyrion MCP server.

LMS exposes third-party music services (Spotify/Spotty, Deezer, TIDAL, ...)
through OPML/XMLBrowser "app" menus reachable over the CLI as
``<plugin> items ...``.  Every service has a slightly different menu shape, so
each concrete :class:`StreamingPlugin` knows how to:

* enumerate playable tracks for a search query (``search_media``)
* build a "radio"/endless-playlist experience for an artist search
  (``play_radio``)
* normalize share/native URLs into a form LMS plays directly
  (``normalize_url``)

The :class:`PluginRegistry` keeps an ordered list of enabled plugins and is
driven by the ``LYRION_PLUGINS`` environment variable (comma-separated).  By
default every known plugin is enabled so the server keeps working whatever is
installed on the LMS instance - unknown/uninstalled plugins simply return no
results.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from client import LMSClient

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers shared by OPML-style plugins
# ---------------------------------------------------------------------------

def _rpc_items(result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize an LMS ``<plugin> items`` response into a list of item dicts."""
    if not isinstance(result, dict):
        return []
    items = result.get("loop_loop") or result.get("items") or []
    return [item for item in items if isinstance(item, dict)]


def _find_item(
    items: List[Dict[str, Any]],
    names: Optional[set] = None,
    name_contains: Optional[str] = None,
    types: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    names = names or set()
    types = types or set()
    needle = (name_contains or "").lower()
    for item in items:
        item_name = str(item.get("name") or item.get("title") or "").strip().lower()
        item_type = str(item.get("type") or "").strip().lower()
        if item_name in names or item_type in types:
            return item
        if needle and needle in item_name:
            return item
    return None


def _is_audio(item: Dict[str, Any]) -> bool:
    return bool(item.get("isaudio")) or str(item.get("type") or "").lower() == "audio"


# ---------------------------------------------------------------------------
# Plugin base class
# ---------------------------------------------------------------------------

class StreamingPlugin(ABC):
    """Base contract for an LMS streaming-service plugin adapter."""

    #: short id used in LMS CLI commands and ``source`` fields, e.g. "deezer"
    plugin_id: str = ""
    #: human-friendly name shown in ``source`` fields, e.g. "deezer"
    source_name: str = ""

    def __init__(self, client: "LMSClient"):
        self.client = client

    @property
    def lms(self) -> "LMSClient":
        return self.client

    # -- search -----------------------------------------------------------

    @abstractmethod
    async def search_tracks(
        self, search_query: str, player_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return playable track dicts: {title, url, source, media_type}."""

    # -- radio ------------------------------------------------------------

    @abstractmethod
    async def play_radio(
        self, search_query: str, player_id: str
    ) -> bool:
        """Start an endless/radio mix for the first artist matching the query."""

    # -- URL normalization ------------------------------------------------

    def normalize_url(self, url: Optional[str]) -> Optional[str]:
        """Rewrite a share/native URL into the form LMS plays directly."""
        return url

    def owns_url(self, url: Optional[str]) -> bool:
        """True if ``url`` belongs to this service (used for ref dispatch)."""
        return False

    # -- shared helpers ---------------------------------------------------

    def _result(self, title: str, url: str, media_type: str = "track") -> Dict[str, Any]:
        return {
            "title": title,
            "url": url,
            "source": self.source_name,
            "media_type": media_type,
        }

    async def _items(
        self,
        player_id: str,
        item_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
        want_url: bool = True,
    ) -> Dict[str, Any]:
        params: List[str] = ["items", "0", str(limit)]
        if want_url:
            params.append("want_url:1")
        if item_id:
            params.append(f"item_id:{item_id}")
        if search:
            params.append(f"search:{search}")
        return await self.client.direct_rpc(
            self.plugin_id, params, player_id=player_id
        )


# ---------------------------------------------------------------------------
# Spotify (Spotty)
# ---------------------------------------------------------------------------

class SpotifyPlugin(StreamingPlugin):
    plugin_id = "spotty"
    source_name = "spotify"

    _SHARE_RE = re.compile(
        r"https?://open\.spotify\.com/(?:intl-\w+/)?"
        r"(track|album|playlist|artist|show|episode)/([a-zA-Z0-9]+)"
    )

    def owns_url(self, url: Optional[str]) -> bool:
        if not url:
            return False
        return url.startswith("spotify://") or bool(self._SHARE_RE.match(url))

    def normalize_url(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return url
        m = self._SHARE_RE.match(url)
        if m:
            return f"spotify://{m.group(1)}:{m.group(2)}"
        return url

    async def search_tracks(
        self, search_query: str, player_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        result = await self._items(
            player_id, item_id="1.0", search=search_query, limit=limit
        )
        out: List[Dict[str, Any]] = []
        for item in _rpc_items(result):
            if not _is_audio(item):
                continue
            url = item.get("url")
            if not url:
                continue
            out.append(self._result(item.get("name", ""), url))
        return out

    async def play_radio(self, search_query: str, player_id: str) -> bool:
        try:
            search = await self._items(player_id, item_id="1.0", search=search_query, limit=5)
            artists_cat = None
            for item in _rpc_items(search):
                if (item.get("name") or "").lower() == "artists":
                    artists_cat = item.get("id")
                    break
            if not artists_cat:
                _LOGGER.info("spotty: no Artists category for %r", search_query)
                return False

            artists = await self._items(player_id, item_id=str(artists_cat), limit=3)
            first_artist = _rpc_items(artists)[0] if _rpc_items(artists) else None
            if not first_artist:
                _LOGGER.info("spotty: no artists found for %r", search_query)
                return False
            artist_id = first_artist.get("id")

            detail = await self._items(player_id, item_id=str(artist_id), limit=10)
            radio_id = None
            for item in _rpc_items(detail):
                if "radio" in (item.get("name") or "").lower():
                    radio_id = item.get("id")
                    break
            if not radio_id:
                _LOGGER.info("spotty: no radio for %r", first_artist.get("name"))
                return False

            await self.client.direct_rpc(
                "spotty",
                ["playlist", "play", f"item_id:{radio_id}"],
                player_id=player_id,
            )
            _LOGGER.info("spotty: started artist radio for %r", first_artist.get("name"))
            return True
        except Exception as e:
            _LOGGER.error("spotty play_radio failed: %s", e)
            return False


# ---------------------------------------------------------------------------
# Deezer
# ---------------------------------------------------------------------------

class DeezerPlugin(StreamingPlugin):
    plugin_id = "deezer"
    source_name = "deezer"

    #: root menu ids discovered on a live LMS 9.x instance
    _SEARCH_OUTLINE_ID = "8"
    _SEARCH_CATEGORIES = {
        "playlists": "8.0",
        "artists": "8.1",
        "albums": "8.2",
        "songs": "8.3",
        "smart_radio": "8.4",
        "podcasts": "8.5",
    }

    _DEEZER_RE = re.compile(r"deezer://.+")

    def owns_url(self, url: Optional[str]) -> bool:
        if not url:
            return False
        return bool(
            self._DEEZER_RE.match(url)
            or "deezer.com/" in url
        )

    def normalize_url(self, url: Optional[str]) -> Optional[str]:
        # The Deezer protocol handler accepts deezer://... URLs as-is.
        return url

    async def search_tracks(
        self, search_query: str, player_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        songs_id = self._SEARCH_CATEGORIES["songs"]
        result = await self._items(
            player_id, item_id=songs_id, search=search_query, limit=limit
        )
        out: List[Dict[str, Any]] = []
        for item in _rpc_items(result):
            if not _is_audio(item):
                continue
            url = item.get("url")
            if not url:
                continue
            out.append(self._result(item.get("name", ""), url))
        return out

    async def play_radio(self, search_query: str, player_id: str) -> bool:
        try:
            artists_id = self._SEARCH_CATEGORIES["artists"]
            artists = await self._items(
                player_id, item_id=artists_id, search=search_query, limit=5
            )
            first_artist = _rpc_items(artists)[0] if _rpc_items(artists) else None
            if not first_artist:
                _LOGGER.info("deezer: no artists found for %r", search_query)
                return False

            artist_id = first_artist.get("id")
            if not artist_id:
                return False

            detail = await self._items(player_id, item_id=str(artist_id), limit=20)
            radio = _find_item(_rpc_items(detail), name_contains="radio")
            if not radio:
                _LOGGER.info("deezer: no radio for artist %r", first_artist.get("name"))
                return False

            radio_url = radio.get("url")
            if not radio_url:
                _LOGGER.info("deezer: radio item has no url for %r", first_artist.get("name"))
                return False

            await self.client.direct_rpc(
                "playlist", ["play", radio_url], player_id=player_id
            )
            _LOGGER.info("deezer: started artist radio for %r", first_artist.get("name"))
            return True
        except Exception as e:
            _LOGGER.error("deezer play_radio failed: %s", e)
            return False


# ---------------------------------------------------------------------------
# TIDAL (best-effort, mirrors the lyrion-mcp-tidal fork)
# ---------------------------------------------------------------------------

class TidalPlugin(StreamingPlugin):
    plugin_id = "tidal"
    source_name = "tidal"

    _ITEM_REF_PREFIX = "lms://tidal/"
    _TIDAL_URL_RE = re.compile(r"^(?:tidal|wimp)://")
    _TIDAL_WEB_RE = re.compile(r"^https?://(?:\w+\.)?tidal\.com/")
    _TIDAL_TRACK_RE = re.compile(r"tidal://track:([0-9]+)(?:[.?].*)?$")

    def owns_url(self, url: Optional[str]) -> bool:
        if not url:
            return False
        return bool(
            self._tidal_item_id_from_ref(url)
            or self._TIDAL_URL_RE.match(url)
            or self._TIDAL_WEB_RE.match(url)
        )

    def normalize_url(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return url
        m = self._TIDAL_TRACK_RE.match(url)
        if m:
            return f"tidal://{m.group(1)}"
        return url

    @classmethod
    def _tidal_item_ref(cls, item_id: Optional[str]) -> Optional[str]:
        if not item_id:
            return None
        from urllib.parse import quote
        return f"{cls._ITEM_REF_PREFIX}{quote(str(item_id), safe='')}"

    @classmethod
    def _tidal_item_id_from_ref(cls, url: Optional[str]) -> Optional[str]:
        if not url or not url.startswith(cls._ITEM_REF_PREFIX):
            return None
        from urllib.parse import unquote
        item_id = unquote(url[len(cls._ITEM_REF_PREFIX):])
        return item_id or None

    async def search_tracks(
        self, search_query: str, player_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        try:
            root = await self._items(player_id, limit=50, want_url=True)
            search_item = _find_item(_rpc_items(root), names={"search"}, types={"search"})
            if not search_item:
                return results
            search_id = search_item.get("id")
            if not search_id:
                return results

            menu = await self._items(
                player_id, item_id=str(search_id), search=search_query, limit=50
            )
            menu_items = _rpc_items(menu)

            track_results = self._extract_tracks(menu_items)
            for category in self._search_categories(menu_items, "track"):
                category_id = category.get("id")
                if not category_id:
                    continue
                tracks = await self._items(
                    player_id, item_id=str(category_id), limit=limit
                )
                cat_results = self._extract_tracks(_rpc_items(tracks))
                if not cat_results:
                    tracks = await self._items(
                        player_id, item_id=str(category_id),
                        search=search_query, limit=limit,
                    )
                    cat_results = self._extract_tracks(_rpc_items(tracks))
                track_results.extend(cat_results)
                if cat_results:
                    break
            results.extend(track_results)
        except Exception as e:
            _LOGGER.info("tidal search failed for %r: %s", search_query, e)
        return results

    async def play_radio(self, search_query: str, player_id: str) -> bool:
        # The TIDAL plugin does not expose a stable artist-radio menu, so fall
        # back to playing the first matching track.
        tracks = await self.search_tracks(search_query, player_id)
        if not tracks:
            return False
        first = tracks[0]
        url = self.normalize_url(first.get("url"))
        if not url:
            return False
        await self.client.direct_rpc("playlist", ["play", url], player_id=player_id)
        _LOGGER.info("tidal: playing first match for %r", search_query)
        return True

    @staticmethod
    def _search_categories(items: List[Dict[str, Any]], media_type: str) -> List[Dict[str, Any]]:
        labels = {
            "artist": {"artists", "artist"},
            "album": {"albums", "album"},
            "track": {"songs", "song", "tracks", "track"},
        }.get(media_type, set())
        preferred = []
        fallback = []
        for item in items:
            name = str(item.get("name") or item.get("title") or "").strip().lower()
            if name in labels:
                preferred.append(item)
            elif media_type == "track" and name in {"everything"}:
                fallback.append(item)
        return preferred + fallback

    @classmethod
    def _extract_tracks(cls, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        for item in items:
            url = item.get("url") or item.get("play") or item.get("favorites_url")
            url = cls._normalize_url_static(url) if url else None
            if not cls._owns_url_static(url):
                continue
            if not _is_audio(item) and not re.match(r"^(?:tidal|wimp)://\d+", url or ""):
                continue
            title = str(item.get("name") or item.get("title") or item.get("line1") or "")
            artist = str(item.get("artist") or item.get("line2") or "")
            if artist and artist not in title:
                title = f"{title} - {artist}" if title else artist
            results.append({"title": title, "url": url, "source": "tidal", "media_type": "track"})
        return results

    @staticmethod
    def _normalize_url_static(url: Optional[str]) -> Optional[str]:
        if not url:
            return url
        m = TidalPlugin._TIDAL_TRACK_RE.match(url)
        if m:
            return f"tidal://{m.group(1)}"
        return url

    @staticmethod
    def _owns_url_static(url: Optional[str]) -> bool:
        return TidalPlugin.owns_url(TidalPlugin, url)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_KNOWN_PLUGINS: Dict[str, type] = {
    "spotify": SpotifyPlugin,
    "spotty": SpotifyPlugin,
    "deezer": DeezerPlugin,
    "tidal": TidalPlugin,
}


def parse_plugin_list(spec: Optional[str]) -> List[str]:
    """Parse a comma-separated ``LYRION_PLUGINS`` value into plugin ids.

    Empty/None => all known plugins (preserves prior behaviour).
    """
    if not spec or not spec.strip():
        return ["spotify", "deezer", "tidal"]
    out: List[str] = []
    for token in spec.split(","):
        token = token.strip().lower()
        if not token:
            continue
        out.append(token)
    return out or ["spotify", "deezer", "tidal"]


class PluginRegistry:
    """Ordered collection of enabled :class:`StreamingPlugin` adapters."""

    def __init__(self, client: "LMSClient", plugin_ids: Optional[List[str]] = None):
        self.client = client
        self._plugins: List[StreamingPlugin] = []
        ids = plugin_ids if plugin_ids is not None else ["spotify", "deezer", "tidal"]
        for pid in ids:
            cls = _KNOWN_PLUGINS.get(pid.lower())
            if cls and not any(isinstance(p, cls) for p in self._plugins):
                self._plugins.append(cls(client))

    @property
    def plugins(self) -> List[StreamingPlugin]:
        return list(self._plugins)

    def plugin_for_url(self, url: Optional[str]) -> Optional[StreamingPlugin]:
        if not url:
            return None
        for plugin in self._plugins:
            try:
                if plugin.owns_url(url):
                    return plugin
            except Exception:
                continue
        return None

    def normalize_url(self, url: Optional[str]) -> Optional[str]:
        owner = self.plugin_for_url(url)
        if owner:
            return owner.normalize_url(url)
        return url

    async def search_tracks(
        self, search_query: str, player_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for plugin in self._plugins:
            try:
                results.extend(await plugin.search_tracks(search_query, player_id, limit=limit))
            except Exception as e:
                _LOGGER.info("plugin %s search failed: %s", plugin.plugin_id, e)
        return results

    async def play_radio(self, search_query: str, player_id: str) -> bool:
        for plugin in self._plugins:
            try:
                if await plugin.play_radio(search_query, player_id):
                    return True
            except Exception as e:
                _LOGGER.info("plugin %s radio failed: %s", plugin.plugin_id, e)
        return False