import unittest
from unittest.mock import AsyncMock, patch

from main import (
    browse_library,
    control_playback,
    get_status,
    manage_playlist,
    play_media,
    query_lms,
    search_media,
    set_player,
    sync_players,
)
import main as main_module


EXPECTED_TOOLS = [
    "get_status",
    "play_media",
    "search_media",
    "control_playback",
    "manage_playlist",
    "set_player",
    "sync_players",
    "browse_library",
    "query_lms",
]


class TestMCPToolRegistration(unittest.IsolatedAsyncioTestCase):
    async def test_all_tools_registered(self):
        tools = await main_module.mcp.list_tools()
        names = [t.name for t in tools]
        self.assertEqual(sorted(names), sorted(EXPECTED_TOOLS))


class TestMCPToolBehavior(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._patcher = patch.object(main_module, "client")
        self.mock_client = self._patcher.start()
        self.mock_client.get_system_status = AsyncMock(
            return_value={"players": [], "server_status": {"version": "9.1.1"}}
        )
        self.mock_client.get_now_playing = AsyncMock(
            return_value={"mode": "play", "title": "Test", "artist": "Artist"}
        )
        self.mock_client.play_media = AsyncMock(return_value=True)
        self.mock_client.play_collection = AsyncMock(return_value=True)
        self.mock_client.play_radio = AsyncMock(return_value=True)
        self.mock_client.search_media = AsyncMock(
            return_value=[{"title": "T", "url": "spotify://track:abc", "source": "spotify"}]
        )
        self.mock_client.pause_media = AsyncMock(return_value=True)
        self.mock_client.stop_media = AsyncMock(return_value=True)
        self.mock_client.seek = AsyncMock(return_value=True)
        self.mock_client.power_control = AsyncMock(return_value=True)
        self.mock_client.manage_playlist = AsyncMock(return_value={"success": True})
        self.mock_client.set_volume = AsyncMock(return_value=True)
        self.mock_client.set_shuffle = AsyncMock(return_value=True)
        self.mock_client.set_repeat = AsyncMock(return_value=True)
        self.mock_client.mute = AsyncMock(return_value=True)
        self.mock_client.sync_players = AsyncMock(return_value=True)
        self.mock_client.unsync_player = AsyncMock(return_value=True)
        self.mock_client.browse_library = AsyncMock(
            return_value=[{"id": 1, "title": "Test"}]
        )
        self.mock_client.direct_rpc = AsyncMock(return_value={"result": "ok"})
        self.mock_player = AsyncMock()
        self.mock_player.async_play = AsyncMock(return_value=True)
        self.mock_client._get_player = AsyncMock(return_value=self.mock_player)

    async def asyncTearDown(self):
        self._patcher.stop()

    # get_status
    async def test_get_status_system(self):
        result = await get_status()
        self.assertIn("players", result)
        self.mock_client.get_system_status.assert_awaited_once()

    async def test_get_status_now_playing(self):
        result = await get_status("p1")
        self.assertEqual(result["mode"], "play")
        self.mock_client.get_now_playing.assert_awaited_once_with(player_id="p1")

    # play_media
    async def test_play_media_url(self):
        result = await play_media(url="file:///x", player_id="p1")
        self.assertEqual(result, {"success": True})

    async def test_play_media_collection(self):
        result = await play_media(album_id="22", player_id="p1")
        self.assertEqual(result, {"success": True})
        self.mock_client.play_collection.assert_awaited_once()

    async def test_play_media_bad_input(self):
        self.mock_client.play_media = AsyncMock(side_effect=ValueError("need source"))
        result = await play_media()
        self.assertFalse(result["success"])

    async def test_play_media_radio_explicit(self):
        result = await play_media(search_query="Foo Fighters", radio=True)
        self.assertEqual(result, {"success": True})
        self.mock_client.play_radio.assert_awaited_once_with(
            search_query="Foo Fighters", player_id=None
        )

    async def test_play_media_radio_default(self):
        # search_query without radio should default to radio
        result = await play_media(search_query="Foo Fighters")
        self.assertEqual(result, {"success": True})
        self.mock_client.play_radio.assert_awaited_once_with(
            search_query="Foo Fighters", player_id=None
        )
        self.mock_client.play_media.assert_not_awaited()

    async def test_play_media_radio_false_plays_single(self):
        # radio=False should fall through to play_media (single track)
        result = await play_media(search_query="Everlong", radio=False)
        self.assertEqual(result, {"success": True})
        self.mock_client.play_media.assert_awaited_once_with(
            url=None, track_id=None, search_query="Everlong", player_id=None
        )
        self.mock_client.play_radio.assert_not_awaited()

    # search_media
    async def test_search_media(self):
        result = await search_media("test")
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["results"]), 1)

    async def test_search_media_limit(self):
        self.mock_client.search_media = AsyncMock(
            return_value=[{"title": "T", "url": "u", "source": "s"}] * 10
        )
        result = await search_media("test", limit=3)
        self.assertEqual(len(result["results"]), 3)

    # control_playback
    async def test_control_pause(self):
        result = await control_playback("pause", player_id="p1")
        self.assertEqual(result, {"success": True})

    async def test_control_stop(self):
        result = await control_playback("stop", player_id="p1")
        self.assertEqual(result, {"success": True})

    async def test_control_play(self):
        result = await control_playback("play", player_id="p1")
        self.assertEqual(result, {"success": True})
        self.mock_player.async_play.assert_awaited_once()

    async def test_control_seek(self):
        result = await control_playback("seek", position=30, player_id="p1")
        self.assertEqual(result, {"success": True})

    async def test_control_seek_no_position(self):
        result = await control_playback("seek")
        self.assertFalse(result["success"])

    async def test_control_power_on(self):
        result = await control_playback("power_on", player_id="p1")
        self.assertEqual(result, {"success": True})

    async def test_control_power_off(self):
        result = await control_playback("power_off", player_id="p1")
        self.assertEqual(result, {"success": True})

    # manage_playlist
    async def test_playlist_add(self):
        result = await manage_playlist("add", url="file:///x")
        self.assertEqual(result, {"success": True})

    async def test_playlist_bad_action(self):
        self.mock_client.manage_playlist = AsyncMock(side_effect=ValueError("bad"))
        result = await manage_playlist("bogus")
        self.assertFalse(result["success"])

    # set_player
    async def test_set_volume(self):
        result = await set_player("volume", 50)
        self.assertEqual(result, {"success": True})

    async def test_set_volume_bad(self):
        result = await set_player("volume", 999)
        self.assertFalse(result["success"])

    async def test_set_shuffle(self):
        result = await set_player("shuffle", 1)
        self.assertEqual(result, {"success": True})

    async def test_set_shuffle_bad(self):
        result = await set_player("shuffle", 9)
        self.assertFalse(result["success"])

    async def test_set_repeat(self):
        result = await set_player("repeat", 2)
        self.assertEqual(result, {"success": True})

    async def test_mute_toggle(self):
        result = await set_player("mute")
        self.assertEqual(result, {"success": True})

    async def test_mute_explicit(self):
        result = await set_player("mute", 1)
        self.assertEqual(result, {"success": True})

    # sync_players
    async def test_sync(self):
        result = await sync_players("sync", "p1", "p2")
        self.assertEqual(result, {"success": True})

    async def test_unsync(self):
        result = await sync_players("unsync", "p1")
        self.assertEqual(result, {"success": True})

    async def test_sync_no_other(self):
        result = await sync_players("sync", "p1")
        self.assertFalse(result["success"])

    # browse_library
    async def test_browse(self):
        result = await browse_library("albums", limit=5)
        self.assertEqual(result["count"], 1)

    async def test_browse_bad(self):
        self.mock_client.browse_library = AsyncMock(side_effect=ValueError("bad"))
        result = await browse_library("bogus")
        self.assertEqual(result["count"], 0)

    # query_lms
    async def test_query_lms(self):
        result = await query_lms("serverstatus", ["0", "50"])
        self.assertEqual(result, {"result": "ok"})

    async def test_query_lms_error(self):
        self.mock_client.direct_rpc = AsyncMock(side_effect=RuntimeError("down"))
        result = await query_lms("serverstatus")
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
