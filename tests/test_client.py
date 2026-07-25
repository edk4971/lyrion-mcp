import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from client import LMSClient, _normalize_spotify_url


def _mock_player(pid="p1", name="Player One"):
    p = MagicMock()
    p.player_id = pid
    p.name = name
    p.model = "model"
    p.power = True
    p.mode = "stop"
    p.volume = 50
    p.sync_group = None
    p.current_track = None
    p.connected = True
    p.async_load_url = AsyncMock(return_value=True)
    p.async_pause = AsyncMock(return_value=True)
    p.async_stop = AsyncMock(return_value=True)
    p.async_set_volume = AsyncMock(return_value=True)
    p.async_sync = AsyncMock(return_value=True)
    p.async_unsync = AsyncMock(return_value=True)
    p.async_set_power = AsyncMock(return_value=True)
    p.async_set_muting = AsyncMock(return_value=True)
    p.async_set_shuffle = AsyncMock(return_value=True)
    p.async_set_repeat = AsyncMock(return_value=True)
    p.async_time = AsyncMock(return_value=True)
    p.async_command = AsyncMock(return_value=True)
    p.async_query = AsyncMock(return_value={"count": 1})
    p.async_update = AsyncMock(return_value=True)
    return p


def _make_client(players=None):
    """An LMSClient with a mocked, already-"connected" session/server."""
    players = players if players is not None else []
    c = LMSClient("h", 9000)
    c.lms_server = MagicMock()
    c.lms_server.async_get_players = AsyncMock(return_value=list(players))
    c.lms_server.async_status = AsyncMock(return_value={"version": "9.1.1", "ip": "1.2.3.4", "uuid": "u"})
    c.lms_server.async_browse = AsyncMock(
        return_value={"items": [{"url": "file:///music/x.mp3", "title": "X"}]}
    )
    c.lms_server.async_query = AsyncMock(return_value={"result": "ok"})
    c.session = MagicMock(closed=False)
    c.players = list(players)
    return c


class TestGetPlayer(unittest.IsolatedAsyncioTestCase):
    async def test_get_player_success(self):
        player = _mock_player("123", "Test Player")
        client = _make_client(players=[player])
        result = await client._get_player("123")
        self.assertEqual(result.player_id, "123")
        self.assertEqual(result.name, "Test Player")

    async def test_get_player_by_name_exact(self):
        player = _mock_player("aa:bb:cc:dd:ee:ff", "Kitchen Speakers")
        client = _make_client(players=[player])
        result = await client._get_player("Kitchen Speakers")
        self.assertIs(result, player)

    async def test_get_player_by_name_case_insensitive(self):
        player = _mock_player("aa:bb:cc:dd:ee:ff", "Office")
        client = _make_client(players=[player])
        result = await client._get_player("office")
        self.assertIs(result, player)

    async def test_get_player_by_name_partial(self):
        player = _mock_player("aa:bb:cc:dd:ee:ff", "Playroom Speakers")
        client = _make_client(players=[player])
        result = await client._get_player("playroom")
        self.assertIs(result, player)

    async def test_get_player_error_lists_available(self):
        player = _mock_player("aa:bb", "Office")
        client = _make_client(players=[player])
        client.lms_server.async_get_players = AsyncMock(return_value=[player])
        with self.assertRaises(ValueError) as ctx:
            await client._get_player("Nonexistent")
        self.assertIn("Office", str(ctx.exception))

    async def test_get_player_none_returns_first(self):
        player = _mock_player("123")
        client = _make_client(players=[player])
        result = await client._get_player(None)
        self.assertIs(result, player)

    async def test_get_player_not_found_refreshes(self):
        # Not in initial (empty) cache, but refresh_players() surfaces it.
        player = _mock_player("999")
        client = _make_client(players=[])
        client.lms_server.async_get_players = AsyncMock(return_value=[player])
        result = await client._get_player("999")
        self.assertIs(result, player)

    async def test_get_player_not_found_raises(self):
        client = _make_client(players=[])
        client.lms_server.async_get_players = AsyncMock(return_value=[])
        with self.assertRaises(ValueError):
            await client._get_player("missing")

    async def test_get_player_none_no_players_raises(self):
        client = _make_client(players=[])
        client.lms_server.async_get_players = AsyncMock(return_value=[])
        with self.assertRaises(RuntimeError):
            await client._get_player(None)


class TestEnsureConnected(unittest.IsolatedAsyncioTestCase):
    @patch("client.aiohttp.ClientSession")
    @patch("client.Server")
    async def test_ensure_connected_is_idempotent(self, mock_server_cls, mock_session_cls):
        mock_session = MagicMock(closed=False)
        mock_session.close = AsyncMock()
        mock_session_cls.return_value = mock_session
        mock_server = MagicMock()
        mock_server.async_status = AsyncMock(return_value={"version": "9"})
        mock_server.async_get_players = AsyncMock(return_value=[])
        mock_server_cls.return_value = mock_server

        client = LMSClient("h", 9000)
        await client.ensure_connected()
        await client.ensure_connected()  # should NOT reconnect

        self.assertEqual(mock_session_cls.call_count, 1)
        self.assertEqual(mock_server_cls.call_count, 1)
        self.assertTrue(client.connected)

    @patch("client.aiohttp.ClientSession")
    @patch("client.Server")
    async def test_ensure_connected_reconnects_after_close(self, mock_server_cls, mock_session_cls):
        mock_session = MagicMock(closed=False)
        mock_session.close = AsyncMock()
        mock_session_cls.return_value = mock_session
        mock_server = MagicMock()
        mock_server.async_status = AsyncMock(return_value={"version": "9"})
        mock_server.async_get_players = AsyncMock(return_value=[])
        mock_server_cls.return_value = mock_server

        client = LMSClient("h", 9000)
        await client.ensure_connected()
        # Simulate the session dropping.
        client.session.closed = True
        await client.ensure_connected()

        self.assertEqual(mock_session_cls.call_count, 2)


class TestPlayMedia(unittest.IsolatedAsyncioTestCase):
    async def test_play_media_url(self):
        player = _mock_player()
        client = _make_client(players=[player])
        ok = await client.play_media(url="file:///music/x.mp3")
        self.assertIs(ok, True)
        player.async_load_url.assert_awaited_once_with("file:///music/x.mp3")
        player.async_command.assert_not_called()

    async def test_play_media_spotify_url(self):
        player = _mock_player()
        client = _make_client(players=[player])
        ok = await client.play_media(url="spotify://track:6QgjcU0zLnzq5OrUoSZ3OK")
        self.assertIs(ok, True)
        player.async_load_url.assert_awaited_once_with("spotify://track:6QgjcU0zLnzq5OrUoSZ3OK")

    async def test_play_media_open_spotify_url_normalized(self):
        player = _mock_player()
        client = _make_client(players=[player])
        ok = await client.play_media(
            url="https://open.spotify.com/track/6QgjcU0zLnzq5OrUoSZ3OK"
        )
        self.assertIs(ok, True)
        # Should be normalized to spotify://track:... before loading
        player.async_load_url.assert_awaited_once_with(
            "spotify://track:6QgjcU0zLnzq5OrUoSZ3OK"
        )

    async def test_play_media_open_spotify_intl_url_normalized(self):
        player = _mock_player()
        client = _make_client(players=[player])
        ok = await client.play_media(
            url="https://open.spotify.com/intl-de/album/3xN9KNcF7zgjfNu6mQD6M?si=abc"
        )
        self.assertIs(ok, True)
        player.async_load_url.assert_awaited_once_with(
            "spotify://album:3xN9KNcF7zgjfNu6mQD6M"
        )

    async def test_play_media_track_id(self):
        player = _mock_player()
        client = _make_client(players=[player])
        ok = await client.play_media(track_id=35361)
        self.assertIs(ok, True)
        player.async_query.assert_awaited_once_with(
            "playlistcontrol", "cmd:load", "track_id:35361"
        )
        player.async_load_url.assert_not_called()

    async def test_play_media_track_id_failure(self):
        player = _mock_player()
        player.async_query = AsyncMock(return_value=None)
        client = _make_client(players=[player])
        ok = await client.play_media(track_id=35361)
        self.assertIs(ok, False)

    async def test_play_media_search_local(self):
        player = _mock_player()
        client = _make_client(players=[player])
        ok = await client.play_media(search_query="Love Me Do")
        self.assertIs(ok, True)
        player.async_load_url.assert_awaited_once_with("file:///music/x.mp3")

    async def test_play_media_search_spotify_fallback(self):
        # Local library returns nothing, but spotty returns a Spotify track.
        player = _mock_player()
        client = _make_client(players=[player])
        client.lms_server.async_browse = AsyncMock(return_value={"items": []})
        client.lms_server.async_query = AsyncMock(
            return_value={
                "loop_loop": [
                    {"name": "Artists", "isaudio": 0},
                    {"name": "Feel It Still", "url": "spotify://track:abc", "isaudio": 1},
                ]
            }
        )
        ok = await client.play_media(search_query="Feel It Still")
        self.assertIs(ok, True)
        player.async_load_url.assert_awaited_once_with("spotify://track:abc")

    async def test_play_media_search_no_results(self):
        player = _mock_player()
        client = _make_client(players=[player])
        client.lms_server.async_browse = AsyncMock(return_value={"items": []})
        # spotty also returns nothing
        client.lms_server.async_query = AsyncMock(return_value={"loop_loop": []})
        ok = await client.play_media(search_query="nothing")
        self.assertIs(ok, False)
        player.async_load_url.assert_not_called()

    async def test_play_media_no_args_raises(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.play_media()

    async def test_play_media_multiple_args_raises(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.play_media(url="file:///x", track_id=1)

    async def test_play_media_returns_bool_on_failure(self):
        player = _mock_player()
        player.async_load_url = AsyncMock(return_value=False)
        client = _make_client(players=[player])
        ok = await client.play_media(url="file:///bad")
        self.assertIs(ok, False)

    async def test_play_media_powers_on_if_off(self):
        player = _mock_player()
        player.power = False  # player is off
        client = _make_client(players=[player])
        await client.play_media(url="file:///x")
        player.async_set_power.assert_awaited_with(True)
        player.async_load_url.assert_awaited_once()

    async def test_play_media_skips_power_if_already_on(self):
        player = _mock_player()
        player.power = True
        client = _make_client(players=[player])
        await client.play_media(url="file:///x")
        player.async_set_power.assert_not_awaited()


class TestSearchMedia(unittest.IsolatedAsyncioTestCase):
    async def test_search_returns_local_and_spotify(self):
        player = _mock_player("p1", "Office")
        client = _make_client(players=[player])
        # Local library returns 1 track; spotty returns 1 audio + 1 category link.
        client.lms_server.async_browse = AsyncMock(
            return_value={"items": [{"url": "file:///music/x.mp3", "title": "X"}]}
        )
        client.lms_server.async_query = AsyncMock(
            return_value={
                "loop_loop": [
                    {"name": "Artists", "isaudio": 0},
                    {"name": "Feel It Still", "url": "spotify://track:abc", "isaudio": 1},
                ]
            }
        )
        results = await client.search_media("Feel It Still")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["source"], "library")
        self.assertEqual(results[1]["source"], "spotify")
        self.assertEqual(results[1]["url"], "spotify://track:abc")

    async def test_search_spotify_only(self):
        player = _mock_player("p1")
        client = _make_client(players=[player])
        client.lms_server.async_browse = AsyncMock(return_value={"items": []})
        client.lms_server.async_query = AsyncMock(
            return_value={
                "loop_loop": [
                    {"name": "Track", "url": "spotify://track:xyz", "isaudio": 1},
                ]
            }
        )
        results = await client.search_media("something")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "spotify")

    async def test_search_no_players_skips_spotify(self):
        client = _make_client(players=[])
        client.lms_server.async_browse = AsyncMock(
            return_value={"items": [{"url": "file:///x", "title": "X"}]}
        )
        results = await client.search_media("test")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "library")

    async def test_search_no_results(self):
        player = _mock_player("p1")
        client = _make_client(players=[player])
        client.lms_server.async_browse = AsyncMock(return_value={"items": []})
        client.lms_server.async_query = AsyncMock(return_value={"loop_loop": []})
        results = await client.search_media("nothing")
        self.assertEqual(results, [])


class TestNormalizeSpotifyUrl(unittest.TestCase):
    def test_track_url(self):
        self.assertEqual(
            _normalize_spotify_url("https://open.spotify.com/track/6QgjcU0zLnzq5OrUoSZ3OK"),
            "spotify://track:6QgjcU0zLnzq5OrUoSZ3OK",
        )

    def test_album_url_with_params(self):
        self.assertEqual(
            _normalize_spotify_url("https://open.spotify.com/album/3xN9KNcF7zgjfNu6mQD6M?si=abc"),
            "spotify://album:3xN9KNcF7zgjfNu6mQD6M",
        )

    def test_intl_url(self):
        self.assertEqual(
            _normalize_spotify_url("https://open.spotify.com/intl-de/track/abc123"),
            "spotify://track:abc123",
        )

    def test_http_url(self):
        self.assertEqual(
            _normalize_spotify_url("http://open.spotify.com/playlist/xyz"),
            "spotify://playlist:xyz",
        )

    def test_native_spotify_url_unchanged(self):
        url = "spotify://track:6QgjcU0zLnzq5OrUoSZ3OK"
        self.assertEqual(_normalize_spotify_url(url), url)

    def test_non_spotify_url_unchanged(self):
        url = "file:///music/x.mp3"
        self.assertEqual(_normalize_spotify_url(url), url)

    def test_http_stream_url_unchanged(self):
        url = "http://stream.example.com/live.mp3"
        self.assertEqual(_normalize_spotify_url(url), url)


class TestPlaybackControls(unittest.IsolatedAsyncioTestCase):
    async def test_pause_media(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.pause_media(), True)
        player.async_pause.assert_awaited_once()

    async def test_stop_media(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.stop_media(), True)
        player.async_stop.assert_awaited_once()

    async def test_set_volume(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.set_volume(42), True)
        player.async_set_volume.assert_awaited_once_with(42)

    async def test_set_volume_out_of_range_raises(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.set_volume(150)
        with self.assertRaises(ValueError):
            await client.set_volume(-1)

    async def test_sync_players(self):
        a = _mock_player("aa:bb", "Office")
        b = _mock_player("cc:dd", "Kitchen")
        client = _make_client(players=[a, b])
        self.assertIs(await client.sync_players("Office", "Kitchen"), True)
        # sync must receive the MAC address, not the display name
        a.async_sync.assert_awaited_once_with("cc:dd")

    async def test_sync_players_by_mac(self):
        a = _mock_player("aa:bb", "Office")
        b = _mock_player("cc:dd", "Kitchen")
        client = _make_client(players=[a, b])
        self.assertIs(await client.sync_players("aa:bb", "cc:dd"), True)
        a.async_sync.assert_awaited_once_with("cc:dd")


class TestDirectRpc(unittest.IsolatedAsyncioTestCase):
    async def test_direct_rpc_returns_result(self):
        client = _make_client(players=[_mock_player()])
        result = await client.direct_rpc("serverstatus", ["0", "50"])
        self.assertEqual(result, {"result": "ok"})
        client.lms_server.async_query.assert_awaited_once_with(
            "serverstatus", "0", "50", player=""
        )

    async def test_direct_rpc_scoped_to_player(self):
        client = _make_client(players=[_mock_player("abc")])
        await client.direct_rpc("status", player_id="abc")
        client.lms_server.async_query.assert_awaited_once_with("status", player="abc")

    async def test_direct_rpc_none_response(self):
        client = _make_client(players=[_mock_player()])
        client.lms_server.async_query = AsyncMock(return_value=None)
        result = await client.direct_rpc("bogus")
        self.assertIn("error", result)
        self.assertFalse(result.get("success", True))


class TestGetSystemStatus(unittest.IsolatedAsyncioTestCase):
    async def test_system_status_topology(self):
        p = _mock_player("p1", "Kitchen")
        client = _make_client(players=[p])
        status = await client.get_system_status()
        self.assertIn("players", status)
        self.assertEqual(len(status["players"]), 1)
        self.assertEqual(status["players"][0]["name"], "Kitchen")
        self.assertEqual(status["server_status"]["version"], "9.1.1")
        # Each player's status must be refreshed (async_get_players leaves fields None).
        p.async_update.assert_awaited_once()


class TestGetNowPlaying(unittest.IsolatedAsyncioTestCase):
    async def test_get_now_playing(self):
        client = _make_client(players=[_mock_player("p1")])
        client.lms_server.async_query = AsyncMock(
            return_value={
                "mode": "play",
                "power": "1",
                "mixer volume": "42",
                "playlist repeat": "0",
                "playlist shuffle": "0",
                "playlist_cur_index": "0",
                "playlist_tracks": "5",
                "time": "12.5",
                "duration": "180.0",
                "can_seek": "1",
                "playlist_loop": [{"title": "Song", "artist": "Artist", "album": "Album"}],
            }
        )
        result = await client.get_now_playing("p1")
        self.assertEqual(result["mode"], "play")
        self.assertEqual(result["title"], "Song")
        self.assertEqual(result["artist"], "Artist")
        self.assertEqual(result["duration"], "180.0")


class TestSeek(unittest.IsolatedAsyncioTestCase):
    async def test_seek_absolute(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.seek(30.5), True)
        player.async_time.assert_awaited_once_with(30.5)


class TestShuffleRepeat(unittest.IsolatedAsyncioTestCase):
    async def test_set_shuffle(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.set_shuffle(1), True)
        player.async_set_shuffle.assert_awaited_once_with("song")

    async def test_set_shuffle_bad(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.set_shuffle(9)

    async def test_set_repeat(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.set_repeat(2), True)
        player.async_set_repeat.assert_awaited_once_with("playlist")

    async def test_set_repeat_bad(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.set_repeat(9)


class TestPowerMuteUnsync(unittest.IsolatedAsyncioTestCase):
    async def test_power_on(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.power_control(True), True)
        player.async_set_power.assert_awaited_once_with(True)

    async def test_power_off(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.power_control(False), True)
        player.async_set_power.assert_awaited_once_with(False)

    async def test_mute_explicit(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.mute(True), True)
        player.async_set_muting.assert_awaited_once_with(True)

    async def test_unmute_explicit(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.mute(False), True)
        player.async_set_muting.assert_awaited_once_with(False)

    async def test_mute_toggle(self):
        player = _mock_player()
        client = _make_client(players=[player])
        player.async_query = AsyncMock(return_value={"_muting": "0"})
        self.assertIs(await client.mute(None), True)
        player.async_set_muting.assert_awaited_once_with(True)

    async def test_unsync(self):
        player = _mock_player("p1")
        client = _make_client(players=[player])
        self.assertIs(await client.unsync_player("p1"), True)
        player.async_unsync.assert_awaited_once()


class TestManagePlaylist(unittest.IsolatedAsyncioTestCase):
    async def test_add(self):
        client = _make_client(players=[_mock_player()])
        await client.manage_playlist("add", url="file:///x", player_id="p1")
        client.lms_server.async_query.assert_awaited()

    async def test_clear(self):
        client = _make_client(players=[_mock_player()])
        await client.manage_playlist("clear", player_id="p1")

    async def test_jump(self):
        client = _make_client(players=[_mock_player()])
        await client.manage_playlist("jump", index=3, player_id="p1")

    async def test_move(self):
        client = _make_client(players=[_mock_player()])
        await client.manage_playlist("move", index=0, to_index=5, player_id="p1")

    async def test_delete(self):
        client = _make_client(players=[_mock_player()])
        await client.manage_playlist("delete", index=2, player_id="p1")

    async def test_bad_action(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.manage_playlist("bogus")

    async def test_add_no_url(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.manage_playlist("add")

    async def test_move_no_index(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.manage_playlist("move", index=0)


class TestBrowseLibrary(unittest.IsolatedAsyncioTestCase):
    async def test_browse_albums(self):
        client = _make_client(players=[_mock_player()])
        client.lms_server.async_browse = AsyncMock(
            return_value={"items": [{"id": 1, "title": "Test"}]}
        )
        items = await client.browse_library("albums", limit=5)
        self.assertEqual(len(items), 1)

    async def test_browse_bad_category(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.browse_library("bogus")


class TestPlayCollection(unittest.IsolatedAsyncioTestCase):
    async def test_play_album(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.play_collection(album_id="22"), True)
        player.async_query.assert_awaited_once_with(
            "playlistcontrol", "cmd:load", "album_id:22"
        )

    async def test_play_artist_add(self):
        player = _mock_player()
        client = _make_client(players=[player])
        self.assertIs(await client.play_collection(artist_id="5", action="add"), True)
        player.async_query.assert_awaited_once_with(
            "playlistcontrol", "cmd:add", "artist_id:5"
        )

    async def test_no_id_raises(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.play_collection()

    async def test_multiple_ids_raises(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.play_collection(album_id="1", artist_id="2")

    async def test_bad_action_raises(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.play_collection(album_id="1", action="bogus")


class TestRescanLibrary(unittest.IsolatedAsyncioTestCase):
    async def test_rescan_full(self):
        client = _make_client(players=[_mock_player()])
        result = await client.rescan_library("full")
        self.assertIsInstance(result, dict)

    async def test_rescan_playlists(self):
        client = _make_client(players=[_mock_player()])
        await client.rescan_library("playlists")

    async def test_bad_mode(self):
        client = _make_client(players=[_mock_player()])
        with self.assertRaises(ValueError):
            await client.rescan_library("bogus")


if __name__ == "__main__":
    unittest.main()
