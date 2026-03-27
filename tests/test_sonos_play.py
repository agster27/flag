"""
tests/test_sonos_play.py — Unit tests for sonos_play.py group-aware playback logic.

Run with:
    python -m pytest tests/
  or:
    python -m unittest discover tests/
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

# Ensure the repo root is on the path so sonos_play can be imported.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_group(members, coordinator):
    """Return a mock Sonos group with the given members and coordinator."""
    group = MagicMock()
    group.members = members
    group.coordinator = coordinator
    return group


def _make_speaker(name, is_coordinator=False):
    """Return a mock SoCo speaker."""
    speaker = MagicMock()
    speaker.player_name = name
    return speaker


def _base_config():
    return {
        "sonos_ip": "192.168.1.100",
        "volume": 30,
        "skip_restore_if_idle": True,
        "default_wait_seconds": 60,
    }


AUDIO_URL = "http://example.com/bugle.mp3"

# Common patches applied to every test to avoid file-system side effects.
COMMON_PATCHES = [
    "sonos_play.log",
    "sonos_play.time.sleep",
]


class TestStandaloneSpeaker(unittest.TestCase):
    """Scenario 1: Speaker is standalone (not in any group)."""

    def setUp(self):
        self.speaker = _make_speaker("Living Room")
        self.coordinator = self.speaker  # standalone — speaker IS coordinator
        self.speaker.group = _make_group([self.speaker], self.speaker)
        self.speaker.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_standalone_idle_skip_restore(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Idle standalone speaker with skip_restore_if_idle=True: no restore called."""
        mock_soco.return_value = self.speaker
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # group path NOT taken (only 1 member)
        self.speaker.unjoin.assert_not_called()
        self.speaker.join.assert_not_called()
        # standalone path: stop → volume → play_uri on coordinator
        self.coordinator.stop.assert_called_once()
        self.coordinator.play_uri.assert_called_once_with(AUDIO_URL)
        # idle + skip_restore_if_idle=True → no restore
        mock_snap.restore.assert_not_called()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_standalone_was_playing_restores(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Standalone speaker that was playing: snapshot is restored after bugle."""
        self.speaker.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}
        mock_soco.return_value = self.speaker
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        self.coordinator.play_uri.assert_called_once_with(AUDIO_URL)
        mock_snap.restore.assert_called_once()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_standalone_skip_restore_false_restores(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Standalone idle speaker with skip_restore_if_idle=False: restore is called."""
        config = _base_config()
        config["skip_restore_if_idle"] = False
        mock_soco.return_value = self.speaker
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sonos_play.load_config", return_value=config):
            with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
                import sonos_play
                sonos_play.main()

        mock_snap.restore.assert_called_once()


class TestGroupedSpeakerMusicPlaying(unittest.TestCase):
    """Scenario 2: Speaker is in a group and music IS playing."""

    def setUp(self):
        self.target = _make_speaker("Kitchen")
        self.other = _make_speaker("Living Room")
        self.coordinator = _make_speaker("Coordinator")
        self.coordinator.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}
        self.target.group = _make_group([self.target, self.other, self.coordinator], self.coordinator)

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_grouped_music_playing_full_flow(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Grouped speaker, music playing: pause → unjoin → play on target → join → restore."""
        mock_soco.return_value = self.target
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Coordinator should be paused (music was playing)
        self.coordinator.pause.assert_called_once()
        # Target speaker should unjoin
        self.target.unjoin.assert_called_once()
        # Bugle plays on TARGET, not coordinator
        self.target.play_uri.assert_called_once_with(AUDIO_URL)
        self.coordinator.play_uri.assert_not_called()
        # Target speaker stops after playback
        self.target.stop.assert_called_once()
        # Target rejoins coordinator
        self.target.join.assert_called_once_with(self.coordinator)
        # Snapshot restored (music was playing)
        mock_snap.restore.assert_called_once()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_grouped_volume_set_on_target(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Volume should be set on the target speaker (not the coordinator) when grouped."""
        mock_soco.return_value = self.target
        mock_snap_cls.return_value = MagicMock()

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Volume set on target speaker
        self.assertEqual(self.target.volume, 30)
        # Coordinator stop() should NOT be called in grouped path
        self.coordinator.stop.assert_not_called()


class TestGroupedSpeakerMusicNotPlaying(unittest.TestCase):
    """Scenario 3: Speaker is in a group and music is NOT playing."""

    def setUp(self):
        self.target = _make_speaker("Kitchen")
        self.other = _make_speaker("Living Room")
        self.coordinator = _make_speaker("Coordinator")
        self.coordinator.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}
        self.target.group = _make_group([self.target, self.other], self.coordinator)

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_grouped_idle_skip_restore(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Grouped idle speaker, skip_restore_if_idle=True: no restore, but unjoin/join happen."""
        mock_soco.return_value = self.target
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Music not playing → coordinator.pause() should NOT be called
        self.coordinator.pause.assert_not_called()
        # Still unjoin and join
        self.target.unjoin.assert_called_once()
        self.target.join.assert_called_once_with(self.coordinator)
        # idle + skip_restore_if_idle=True → no restore
        mock_snap.restore.assert_not_called()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_grouped_idle_skip_restore_false(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Grouped idle speaker, skip_restore_if_idle=False: restore IS called."""
        config = _base_config()
        config["skip_restore_if_idle"] = False
        mock_soco.return_value = self.target
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sonos_play.load_config", return_value=config):
            with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
                import sonos_play
                sonos_play.main()

        mock_snap.restore.assert_called_once()


class TestGroupedSpeakerIsCoordinator(unittest.TestCase):
    """Scenario 4: Target speaker IS the coordinator of a group."""

    def setUp(self):
        self.coordinator = _make_speaker("Main Speaker")
        self.other = _make_speaker("Bedroom")
        self.coordinator.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}
        # coordinator is also the speaker being targeted
        self.coordinator.group = _make_group([self.coordinator, self.other], self.coordinator)

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_coordinator_as_target(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Target == coordinator: same group flow — unjoin, play solo, rejoin, restore."""
        mock_soco.return_value = self.coordinator
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        self.coordinator.pause.assert_called_once()
        self.coordinator.unjoin.assert_called_once()
        self.coordinator.play_uri.assert_called_once_with(AUDIO_URL)
        self.coordinator.join.assert_called_once_with(self.coordinator)
        mock_snap.restore.assert_called_once()


class TestErrorHandlingDuringPlayback(unittest.TestCase):
    """Scenario 5: Error during playback still rejoins the group."""

    def setUp(self):
        self.target = _make_speaker("Kitchen")
        self.other = _make_speaker("Living Room")
        self.coordinator = _make_speaker("Coordinator")
        self.coordinator.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}
        self.target.group = _make_group([self.target, self.other], self.coordinator)
        # play_uri raises an error
        self.target.play_uri.side_effect = RuntimeError("Network error")

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_rejoin_called_even_on_playback_error(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """If play_uri raises, the finally block still calls join() to rejoin the group."""
        mock_soco.return_value = self.target
        mock_snap_cls.return_value = MagicMock()

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()  # should not raise — outer except catches it

        self.target.unjoin.assert_called_once()
        # join() must be called in finally even though play_uri raised
        self.target.join.assert_called_once_with(self.coordinator)

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_join_failure_is_logged_not_raised(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """If join() also fails, the error is swallowed and logged (does not crash the process)."""
        self.target.play_uri.side_effect = None  # playback succeeds this time
        self.target.join.side_effect = RuntimeError("Join failed")
        mock_soco.return_value = self.target
        mock_snap_cls.return_value = MagicMock()

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            # Should not raise even though join() raises
            sonos_play.main()

        self.target.join.assert_called_once()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_stop_fallback_to_group_coordinator(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """If stop() raises a coordinator-only error, fall back to group.coordinator.stop()."""
        # Reset play_uri to succeed (override setUp's side_effect)
        self.target.play_uri.side_effect = None
        # stop() raises the Sonos coordinator-only error
        self.target.stop.side_effect = Exception(
            'The method or property "stop" can only be called/used on the coordinator in a group'
        )
        mock_soco.return_value = self.target
        mock_snap_cls.return_value = MagicMock()

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()  # must not raise

        # Primary stop was attempted on the target speaker
        self.target.stop.assert_called_once()
        # Fallback: coordinator.stop() was called
        self.coordinator.stop.assert_called_once()
        # Cleanup still proceeds: join is called
        self.target.join.assert_called_once_with(self.coordinator)


class TestSleepDelaysAroundGroupChanges(unittest.TestCase):
    """Verify that time.sleep(1) is called after unjoin and after join."""

    def setUp(self):
        self.target = _make_speaker("Kitchen")
        self.other = _make_speaker("Living Room")
        self.coordinator = _make_speaker("Coordinator")
        self.coordinator.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}
        self.target.group = _make_group([self.target, self.other], self.coordinator)

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_sleep_called_after_unjoin_and_join(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """time.sleep(1) is invoked at least twice in the grouped path (after unjoin and join)."""
        mock_soco.return_value = self.target
        mock_snap_cls.return_value = MagicMock()

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # sleep(1) after unjoin, sleep(duration=5) for playback, sleep(1) after join → exactly 3 calls
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        self.assertIn(1, sleep_args)
        self.assertEqual(sleep_args.count(1), 2)


if __name__ == "__main__":
    unittest.main()

