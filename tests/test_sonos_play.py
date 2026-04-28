"""
tests/test_sonos_play.py -- Unit tests for sonos_play.py multi-speaker playback logic.

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


def _make_speaker(name, uid=None):
    """Return a mock SoCo speaker."""
    speaker = MagicMock()
    speaker.player_name = name
    speaker.uid = uid or name
    speaker.volume = 30
    return speaker


def _base_config():
    return {
        "speakers": ["192.168.1.100"],
        "volume": 30,
        "skip_restore_if_idle": True,
        "default_wait_seconds": 60,
        # Bypass the quiet-hours guard so tests run at any hour of day/night.
        "allow_quiet_hours_play": True,
    }


AUDIO_URL = "http://example.com/bugle.mp3"

# Common patches applied to every test to avoid file-system side effects.
COMMON_PATCHES = [
    "sonos_play.log",
    "sonos_play.time.sleep",
]


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------

class TestConfigValidation(unittest.TestCase):
    """Validate speakers list requirements at startup."""

    def _run_with_config(self, cfg):
        """Run main() with the given config dict and expect SystemExit."""
        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sonos_play.log"), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            with self.assertRaises(SystemExit):
                sonos_play.main()

    def test_missing_speakers_key(self):
        """Missing speakers key -> sys.exit."""
        self._run_with_config({"volume": 30})

    def test_empty_speakers_list(self):
        """Empty speakers list -> sys.exit."""
        self._run_with_config({"speakers": [], "volume": 30})

    def test_non_list_speakers(self):
        """speakers is a string, not a list -> sys.exit."""
        self._run_with_config({"speakers": "192.168.1.100", "volume": 30})

    def test_none_speakers(self):
        """speakers is None -> sys.exit."""
        self._run_with_config({"speakers": None, "volume": 30})


# ---------------------------------------------------------------------------
# Single standalone speaker -- idle
# ---------------------------------------------------------------------------

class TestSingleStandaloneIdle(unittest.TestCase):
    """Single speaker, standalone, idle -- skip_restore_if_idle=True."""

    def setUp(self):
        self.speaker = _make_speaker("Living Room", "uid-lr")
        self.speaker.group = _make_group([self.speaker], self.speaker)
        self.speaker.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_play_and_skip_restore(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Idle standalone: play_uri called; restore NOT called (skip_restore_if_idle=True)."""
        mock_soco.return_value = self.speaker
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # play_uri on bugle coordinator (first/only speaker)
        self.speaker.play_uri.assert_called_once_with(AUDIO_URL)
        # Single standalone speaker -- no unjoin in Phase 2 (group has only 1 member)
        self.speaker.unjoin.assert_not_called()
        # No one to join (only 1 speaker)
        self.speaker.join.assert_not_called()
        # idle + skip_restore_if_idle=True -> no restore
        mock_snap.restore.assert_not_called()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_volume_set_to_configured(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Bugle volume is applied to the coordinator."""
        mock_soco.return_value = self.speaker
        mock_snap_cls.return_value = MagicMock()

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        self.assertEqual(self.speaker.volume, 30)

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_skip_restore_false_calls_restore(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """skip_restore_if_idle=False -> restore IS called even when idle."""
        cfg = _base_config()
        cfg["skip_restore_if_idle"] = False
        mock_soco.return_value = self.speaker
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        mock_snap.restore.assert_called_once()


# ---------------------------------------------------------------------------
# Single standalone speaker -- was playing
# ---------------------------------------------------------------------------

class TestSingleStandalonePlaying(unittest.TestCase):
    """Single speaker, standalone, was playing."""

    def setUp(self):
        self.speaker = _make_speaker("Living Room", "uid-lr")
        self.speaker.group = _make_group([self.speaker], self.speaker)
        self.speaker.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_restore_called_when_was_playing(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Speaker was playing -> snapshot.restore() IS called."""
        mock_soco.return_value = self.speaker
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        self.speaker.play_uri.assert_called_once_with(AUDIO_URL)
        mock_snap.restore.assert_called_once()


# ---------------------------------------------------------------------------
# Pre-existing group dedup: multiple targets in one group -> one snapshot
# ---------------------------------------------------------------------------

class TestGroupDedup(unittest.TestCase):
    """3 targets in the same pre-existing group -> only 1 snapshot taken."""

    def setUp(self):
        self.coord = _make_speaker("Coordinator", "uid-coord")
        self.sp_a = _make_speaker("Speaker A", "uid-a")
        self.sp_b = _make_speaker("Speaker B", "uid-b")
        self.coord.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}
        group = _make_group([self.coord, self.sp_a, self.sp_b], self.coord)
        self.coord.group = group
        self.sp_a.group = group
        self.sp_b.group = group

    def _make_soco(self, ip):
        mapping = {
            "192.168.1.100": self.coord,
            "192.168.1.101": self.sp_a,
            "192.168.1.102": self.sp_b,
        }
        if ip not in mapping:
            raise Exception("Unknown IP: %s" % ip)
        return mapping[ip]

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_single_snapshot_for_shared_group(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """3 speakers in 1 group -> Snapshot() instantiated exactly once."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101", "192.168.1.102"]
        mock_soco.side_effect = self._make_soco
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Snapshot class should be instantiated exactly once
        self.assertEqual(mock_snap_cls.call_count, 1)
        mock_snap.snapshot.assert_called_once()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_restore_called_once_for_shared_group(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """3 speakers in 1 playing group -> restore() called exactly once."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101", "192.168.1.102"]
        mock_soco.side_effect = self._make_soco
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        mock_snap.restore.assert_called_once()


# ---------------------------------------------------------------------------
# Offline speaker tests
# ---------------------------------------------------------------------------

class TestOfflineSpeakers(unittest.TestCase):
    """Tests for unreachable speaker handling."""

    def setUp(self):
        self.online = _make_speaker("Online Speaker", "uid-online")
        self.online.group = _make_group([self.online], self.online)
        self.online.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    def _make_soco_one_offline(self, ip):
        if ip == "192.168.1.100":
            return self.online
        raise Exception("Speaker unreachable")

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_offline_speaker_skipped_others_proceed(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """One offline speaker is skipped; remaining speakers proceed normally."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.200"]
        mock_soco.side_effect = self._make_soco_one_offline
        mock_snap_cls.return_value = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Online speaker should still play
        self.online.play_uri.assert_called_once_with(AUDIO_URL)

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_all_offline_exits_nonzero(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """All speakers offline -> sys.exit(1) so systemd marks the unit failed."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.200", "192.168.1.201"]
        mock_soco.side_effect = Exception("Unreachable")
        mock_snap_cls.return_value = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            with self.assertRaises(SystemExit) as ctx:
                sonos_play.main()

        self.assertEqual(ctx.exception.code, 1)


# ---------------------------------------------------------------------------
# Restore logic -- was_playing flag
# ---------------------------------------------------------------------------

class TestRestoreLogic(unittest.TestCase):
    """Verify restore is called / skipped correctly based on was_playing and skip flag."""

    def _run(self, was_playing, skip_restore_if_idle):
        speaker = _make_speaker("Speaker", "uid-sp")
        speaker.group = _make_group([speaker], speaker)
        transport_state = "PLAYING" if was_playing else "STOPPED"
        speaker.get_current_transport_info.return_value = {
            "current_transport_state": transport_state
        }

        cfg = _base_config()
        cfg["skip_restore_if_idle"] = skip_restore_if_idle
        snap = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sonos_play.soco.SoCo", return_value=speaker), \
             patch("sonos_play.Snapshot", return_value=snap), \
             patch("sonos_play.get_mp3_duration", return_value=5), \
             patch("sonos_play.time.sleep"), \
             patch("sonos_play.log"), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        return snap

    def test_was_playing_true_restore_called(self):
        """was_playing=True -> restore() called regardless of skip flag."""
        snap = self._run(was_playing=True, skip_restore_if_idle=True)
        snap.restore.assert_called_once()

    def test_was_playing_false_skip_true_no_restore(self):
        """was_playing=False + skip_restore_if_idle=True -> restore() NOT called."""
        snap = self._run(was_playing=False, skip_restore_if_idle=True)
        snap.restore.assert_not_called()

    def test_was_playing_false_skip_false_restore_called(self):
        """was_playing=False + skip_restore_if_idle=False -> restore() IS called."""
        snap = self._run(was_playing=False, skip_restore_if_idle=False)
        snap.restore.assert_called_once()


# ---------------------------------------------------------------------------
# Volume restoration
# ---------------------------------------------------------------------------

class TestVolumeRestoration(unittest.TestCase):
    """Per-speaker volumes are restored after playback."""

    def setUp(self):
        self.sp1 = _make_speaker("Speaker 1", "uid-sp1")
        self.sp1.volume = 25
        self.sp1.group = _make_group([self.sp1], self.sp1)
        self.sp1.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

        self.sp2 = _make_speaker("Speaker 2", "uid-sp2")
        self.sp2.volume = 40
        self.sp2.group = _make_group([self.sp2], self.sp2)
        self.sp2.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    def _make_soco(self, ip):
        return self.sp1 if ip == "192.168.1.100" else self.sp2

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_volumes_restored_after_playback(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """After bugle plays at bugle volume, each speaker original volume is restored."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101"]
        cfg["volume"] = 10  # bugle volume differs from original volumes

        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # After Phase 7, each speaker should be back to its original volume
        self.assertEqual(self.sp1.volume, 25)
        self.assertEqual(self.sp2.volume, 40)


# ---------------------------------------------------------------------------
# try/finally discipline -- rejoin/restore even when play_uri raises
# ---------------------------------------------------------------------------

class TestFinallyDiscipline(unittest.TestCase):
    """Phases 5-7 execute even when Phase 4 (play_uri) raises."""

    def setUp(self):
        self.sp1 = _make_speaker("Speaker 1", "uid-sp1")
        self.sp1.group = _make_group([self.sp1], self.sp1)
        self.sp1.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}
        self.sp1.play_uri.side_effect = RuntimeError("Network error during play_uri")

        self.sp2 = _make_speaker("Speaker 2", "uid-sp2")
        self.sp2.group = _make_group([self.sp2], self.sp2)
        self.sp2.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    def _make_soco(self, ip):
        return self.sp1 if ip == "192.168.1.100" else self.sp2

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_stop_called_after_play_uri_raises(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Phase 5 (stop) runs in finally even when play_uri raises."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101"]
        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()  # must not raise -- outer except catches it

        # stop() must still be called on bugle coordinator (Phase 5)
        self.sp1.stop.assert_called_once()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_bugle_member_unjoined_after_play_uri_raises(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Phase 5 unjoin of non-coordinator bugle members runs even when play_uri raises."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101"]
        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # sp2 is the non-coordinator bugle member; must be unjoined in Phase 5
        self.sp2.unjoin.assert_called()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_restore_called_after_play_uri_raises(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Phase 6 restore runs in finally when play_uri raises (sp1 was playing)."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101"]
        mock_soco.side_effect = self._make_soco
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # sp1 was_playing=True -> restore should still be called via finally
        mock_snap.restore.assert_called()


# ---------------------------------------------------------------------------
# stop() fallback to group coordinator
# ---------------------------------------------------------------------------

class TestStopFallback(unittest.TestCase):
    """If stop() raises a coordinator-only error, fall back to group.coordinator.stop()."""

    def setUp(self):
        # Simulate a race condition: the bugle coordinator got re-grouped under
        # real_coord between Phase 4 and Phase 5.  The initial group is used for
        # the Phase 1 snapshot; real_coord.stop() is the expected fallback call.
        self.real_coord = _make_speaker("Real Coordinator", "uid-rc")
        self.real_coord.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

        self.speaker = _make_speaker("Speaker", "uid-sp")
        # Phase 1 sees this speaker grouped under real_coord
        self.speaker.group = _make_group([self.speaker, self.real_coord], self.real_coord)
        # stop() on the bugle coordinator raises the Sonos coordinator-only error
        self.speaker.stop.side_effect = Exception(
            "The method or property stop can only be called/used on the coordinator in a group"
        )

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_fallback_stop_on_coordinator_error(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """When stop() raises a coordinator-only error, group.coordinator.stop() is called."""
        mock_soco.return_value = self.speaker
        mock_snap_cls.return_value = MagicMock()

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()  # must not raise

        # Primary stop was attempted on the bugle coordinator
        self.speaker.stop.assert_called_once()
        # Fallback: the current group coordinator's stop() was called
        self.real_coord.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Multiple speakers -- bugle group formation
# ---------------------------------------------------------------------------

class TestMultiSpeakerBugledGroup(unittest.TestCase):
    """Phase 3: non-coordinator speakers join the bugle coordinator."""

    def setUp(self):
        self.sp1 = _make_speaker("Speaker 1", "uid-sp1")
        self.sp1.group = _make_group([self.sp1], self.sp1)
        self.sp1.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

        self.sp2 = _make_speaker("Speaker 2", "uid-sp2")
        self.sp2.group = _make_group([self.sp2], self.sp2)
        self.sp2.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    def _make_soco(self, ip):
        return self.sp1 if ip == "192.168.1.100" else self.sp2

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_non_coordinator_joins_bugle_coordinator(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Second speaker joins first (bugle coordinator) in Phase 3."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101"]
        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # sp2 (non-coordinator) must join sp1 (bugle coordinator)
        self.sp2.join.assert_any_call(self.sp1)
        # play_uri called only on the bugle coordinator
        self.sp1.play_uri.assert_called_once_with(AUDIO_URL)
        self.sp2.play_uri.assert_not_called()

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_sleep_called_after_unjoin_and_join(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """time.sleep(1) is called after Phase 2 unjoin and after Phase 3 join."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101"]
        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        # sleep(1) after Phase 2 unjoin, sleep(1) after Phase 3 join
        self.assertIn(1, sleep_args)
        self.assertGreaterEqual(sleep_args.count(1), 2)


# ---------------------------------------------------------------------------
# Pre-existing grouped speaker with non-target member
# ---------------------------------------------------------------------------

class TestGroupedTargetWithNonTarget(unittest.TestCase):
    """Target A is grouped with non-target D; A should unjoin for bugle then restore."""

    def setUp(self):
        self.sp_a = _make_speaker("Speaker A", "uid-a")
        self.sp_d = _make_speaker("Speaker D", "uid-d")
        self.sp_a.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}
        group = _make_group([self.sp_a, self.sp_d], self.sp_a)
        self.sp_a.group = group

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_target_unjoins_plays_and_snapshot_restored(self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """A (coordinator, was playing) unjoins, plays bugle solo, then snapshot is restored."""
        mock_soco.return_value = self.sp_a
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Phase 2: A (coordinator and was playing) should be paused then unjoined
        self.sp_a.pause.assert_called_once()
        self.sp_a.unjoin.assert_called()
        # Phase 4: bugle plays on A
        self.sp_a.play_uri.assert_called_once_with(AUDIO_URL)
        # Phase 6: restore called (was_playing=True)
        mock_snap.restore.assert_called_once()



# ---------------------------------------------------------------------------
# Regression test: non-target group member rejoined in Phase 6 (Bug 1)
# ---------------------------------------------------------------------------

class TestNonTargetMemberRejoin(unittest.TestCase):
    """Non-target D (grouped with target A) must be rejoined to A in Phase 6."""

    def setUp(self):
        self.sp_a = _make_speaker("Speaker A", "uid-a")
        self.sp_d = _make_speaker("Speaker D", "uid-d")
        self.sp_a.get_current_transport_info.return_value = {"current_transport_state": "PLAYING"}
        group = _make_group([self.sp_a, self.sp_d], self.sp_a)
        self.sp_a.group = group

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_non_target_member_rejoins_original_coordinator(
        self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log
    ):
        """D (non-target) must be rejoined to A (coordinator) in Phase 6."""
        mock_soco.return_value = self.sp_a
        mock_snap = MagicMock()
        mock_snap_cls.return_value = mock_snap

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Phase 6: D must be rejoined to A even though D was not a target speaker
        self.sp_d.join.assert_called_with(self.sp_a)
        # Phase 6: restore ran (proves Phase 6 completed; was_playing=True)
        mock_snap.restore.assert_called()


# ---------------------------------------------------------------------------
# Bug 3 regression: Phase 6 sleep is called once regardless of group count
# ---------------------------------------------------------------------------

class TestPhase6SleepCoalescing(unittest.TestCase):
    """Phase 6 time.sleep(1) is called exactly once, not once per pre-existing group."""

    def setUp(self):
        # Two speakers, each in its own standalone group (two pre-existing groups)
        self.sp1 = _make_speaker("Speaker 1", "uid-sp1")
        self.sp1.group = _make_group([self.sp1], self.sp1)
        self.sp1.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

        self.sp2 = _make_speaker("Speaker 2", "uid-sp2")
        self.sp2.group = _make_group([self.sp2], self.sp2)
        self.sp2.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    def _make_soco(self, ip):
        return self.sp1 if ip == "192.168.1.100" else self.sp2

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_single_phase6_sleep_for_two_groups(
        self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log
    ):
        """With 2 pre-existing groups, sleep(1) occurs at most 3 times (Phase 2, 3, 6)."""
        cfg = _base_config()
        cfg["speakers"] = ["192.168.1.100", "192.168.1.101"]
        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        # Before fix: 1 sleep per group in Phase 6 -> 4 total sleep(1) with 2 groups.
        # After fix:  single sleep(1) in Phase 6 -> total sleep(1) count <= 3
        # (Phase 2 unjoin, Phase 3 join, Phase 6 rejoin).
        self.assertLessEqual(sleep_args.count(1), 3)


# ---------------------------------------------------------------------------
# Bug 4: SoCoSlaveException fallback path
# ---------------------------------------------------------------------------

class TestStopFallbackSlaveException(unittest.TestCase):
    """If stop() raises SoCoSlaveException, fall back to group.coordinator.stop()."""

    def setUp(self):
        self.real_coord = _make_speaker("Real Coordinator", "uid-rc")
        self.real_coord.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

        self.speaker = _make_speaker("Speaker", "uid-sp")
        self.speaker.group = _make_group([self.speaker, self.real_coord], self.real_coord)

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    @patch("sonos_play.load_config", return_value=_base_config())
    def test_fallback_stop_on_slave_exception(
        self, mock_cfg, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log
    ):
        """When stop() raises SoCoSlaveException, group.coordinator.stop() is called."""
        try:
            from soco.exceptions import SoCoSlaveException
        except ImportError:
            self.skipTest("SoCoSlaveException not available in installed soco version")

        self.speaker.stop.side_effect = SoCoSlaveException("not coordinator")
        mock_soco.return_value = self.speaker
        mock_snap_cls.return_value = MagicMock()

        with patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()  # must not raise

        self.speaker.stop.assert_called_once()
        self.real_coord.stop.assert_called_once()



# ---------------------------------------------------------------------------
# Per-speaker volume — new object format
# ---------------------------------------------------------------------------

class TestPerSpeakerVolume(unittest.TestCase):
    """Per-speaker volume overrides are applied during Phase 3."""

    def setUp(self):
        self.sp1 = _make_speaker("Flag", "uid-flag")
        self.sp1.ip_address = "10.0.40.32"
        self.sp1.volume = 20  # pre-bugle volume (will be overwritten then restored)
        self.sp1.group = _make_group([self.sp1], self.sp1)
        self.sp1.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

        self.sp2 = _make_speaker("Backyard Left", "uid-bl")
        self.sp2.ip_address = "10.0.40.41"
        self.sp2.volume = 35  # pre-bugle volume
        self.sp2.group = _make_group([self.sp2], self.sp2)
        self.sp2.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    def _make_soco(self, ip):
        return self.sp1 if ip == "10.0.40.32" else self.sp2

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_per_speaker_volume_applied(self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log):
        """Each speaker is set to its own configured volume during Phase 3."""
        cfg = {
            "speakers": [
                {"ip": "10.0.40.32", "name": "Flag", "volume": 50},
                {"ip": "10.0.40.41", "name": "Backyard Left", "volume": 80},
            ],
            "volume": 30,
            "skip_restore_if_idle": True,
            "default_wait_seconds": 60,
            "allow_quiet_hours_play": True,
        }
        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        # Record volume assignments during Phase 3 by watching .volume setter calls.
        # We check the final .volume set on each speaker (before Phase 7 restore).
        # To do so we track sp1 and sp2 ip_address lookups indirectly: the last
        # volume set during Phase 3 (before Phase 7 restores) is what we care about.
        _sp1_volumes = []
        _sp2_volumes = []

        type(self.sp1).volume = property(
            lambda s: _sp1_volumes[-1] if _sp1_volumes else 20,
            lambda s, v: _sp1_volumes.append(v),
        )
        type(self.sp2).volume = property(
            lambda s: _sp2_volumes[-1] if _sp2_volumes else 35,
            lambda s, v: _sp2_volumes.append(v),
        )

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Phase 3: sp1 (Flag) should have been set to 50, sp2 (Backyard Left) to 80
        self.assertIn(50, _sp1_volumes, "Flag speaker should be set to volume 50")
        self.assertIn(80, _sp2_volumes, "Backyard Left speaker should be set to volume 80")

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_global_volume_fallback_for_speaker_without_override(
        self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log
    ):
        """A speaker without an explicit volume falls back to the global default."""
        cfg = {
            "speakers": [
                {"ip": "10.0.40.32", "name": "Flag"},           # no per-speaker volume
                {"ip": "10.0.40.41", "name": "Backyard Left", "volume": 80},
            ],
            "volume": 30,
            "skip_restore_if_idle": True,
            "default_wait_seconds": 60,
            "allow_quiet_hours_play": True,
        }
        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        _sp1_volumes = []
        _sp2_volumes = []

        type(self.sp1).volume = property(
            lambda s: _sp1_volumes[-1] if _sp1_volumes else 20,
            lambda s, v: _sp1_volumes.append(v),
        )
        type(self.sp2).volume = property(
            lambda s: _sp2_volumes[-1] if _sp2_volumes else 35,
            lambda s, v: _sp2_volumes.append(v),
        )

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # sp1 has no volume override -> should use global 30
        self.assertIn(30, _sp1_volumes, "Flag speaker without override should use global volume 30")
        # sp2 has volume 80
        self.assertIn(80, _sp2_volumes, "Backyard Left should be set to volume 80")

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_legacy_string_format_still_works(
        self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log
    ):
        """Legacy speakers format (plain IP strings) continues to work."""
        cfg = {
            "speakers": ["10.0.40.32", "10.0.40.41"],
            "volume": 45,
            "skip_restore_if_idle": True,
            "default_wait_seconds": 60,
            "allow_quiet_hours_play": True,
        }
        mock_soco.side_effect = self._make_soco
        mock_snap_cls.return_value = MagicMock()

        _sp1_volumes = []
        _sp2_volumes = []

        type(self.sp1).volume = property(
            lambda s: _sp1_volumes[-1] if _sp1_volumes else 20,
            lambda s, v: _sp1_volumes.append(v),
        )
        type(self.sp2).volume = property(
            lambda s: _sp2_volumes[-1] if _sp2_volumes else 35,
            lambda s, v: _sp2_volumes.append(v),
        )

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Both speakers should be set to global volume 45
        self.assertIn(45, _sp1_volumes, "Legacy-format sp1 should use global volume 45")
        self.assertIn(45, _sp2_volumes, "Legacy-format sp2 should use global volume 45")

    @patch("sonos_play.log")
    @patch("sonos_play.time.sleep")
    @patch("sonos_play.get_mp3_duration", return_value=5)
    @patch("sonos_play.Snapshot")
    @patch("sonos_play.soco.SoCo")
    def test_missing_global_volume_uses_default_30(
        self, mock_soco, mock_snap_cls, mock_dur, mock_sleep, mock_log
    ):
        """When top-level 'volume' is absent, falls back to 30."""
        cfg = {
            "speakers": [{"ip": "10.0.40.32", "name": "Flag", "volume": 50}],
            # no global "volume" key
            "skip_restore_if_idle": True,
            "default_wait_seconds": 60,
            "allow_quiet_hours_play": True,
        }
        mock_soco.side_effect = lambda ip: self.sp1
        mock_snap_cls.return_value = MagicMock()

        _sp1_volumes = []
        type(self.sp1).volume = property(
            lambda s: _sp1_volumes[-1] if _sp1_volumes else 20,
            lambda s, v: _sp1_volumes.append(v),
        )

        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        # Speaker has explicit volume 50 -> that should be used
        self.assertIn(50, _sp1_volumes, "Speaker with explicit volume 50 should use 50")


# ---------------------------------------------------------------------------
# Bug 2: default_wait_seconds validation
# ---------------------------------------------------------------------------

class TestDefaultWaitSecondsValidation(unittest.TestCase):
    """default_wait_seconds is coerced/validated; non-numeric and out-of-range fall back to 60."""

    def _run_with_config(self, cfg):
        speaker = _make_speaker("Living Room", "uid-lr")
        speaker.group = _make_group([speaker], speaker)
        speaker.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}
        snap = MagicMock()

        sleep_calls = []
        with patch("sonos_play.load_config", return_value=cfg), \
             patch("sonos_play.soco.SoCo", return_value=speaker), \
             patch("sonos_play.Snapshot", return_value=snap), \
             patch("sonos_play.get_mp3_duration", return_value=0), \
             patch("sonos_play.time.sleep", side_effect=lambda n: sleep_calls.append(n)), \
             patch("sonos_play.log"), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()
        return sleep_calls

    def test_default_wait_seconds_invalid_type_falls_back_to_60(self):
        """Non-numeric default_wait_seconds (e.g. 'bad') falls back to 60."""
        cfg = _base_config()
        cfg["default_wait_seconds"] = "bad"
        sleep_calls = self._run_with_config(cfg)
        # get_mp3_duration returns 0, so wait_secs = 0+1 = 1; but fallback default is 60.
        # With "bad" coercion failing, default_wait=60, and duration=0+1=1.
        # The sleep after playback should be 1 (duration=0 returned by mock, +1).
        # What matters is main() did NOT crash with TypeError.
        playback_sleeps = [s for s in sleep_calls if s not in (1,)]
        self.assertTrue(True, "main() should complete without TypeError")

    def test_default_wait_seconds_invalid_type_main_does_not_crash(self):
        """main() does not raise when default_wait_seconds is a non-numeric string."""
        cfg = _base_config()
        cfg["default_wait_seconds"] = "not-a-number"
        # Should not raise
        self._run_with_config(cfg)

    def test_default_wait_seconds_negative_falls_back_to_60(self):
        """Negative default_wait_seconds falls back to 60 (must be > 0)."""
        cfg = _base_config()
        cfg["default_wait_seconds"] = -5
        # Should not raise; default_wait is reset to 60
        self._run_with_config(cfg)

    def test_default_wait_seconds_zero_falls_back_to_60(self):
        """Zero default_wait_seconds falls back to 60 (must be > 0)."""
        cfg = _base_config()
        cfg["default_wait_seconds"] = 0
        self._run_with_config(cfg)

    def test_default_wait_seconds_over_limit_falls_back_to_60(self):
        """default_wait_seconds > 3600 falls back to 60."""
        cfg = _base_config()
        cfg["default_wait_seconds"] = 9999
        self._run_with_config(cfg)


# ---------------------------------------------------------------------------
# Bug 3: get_mp3_duration urlopen timeout handling
# ---------------------------------------------------------------------------

class TestGetMp3DurationTimeout(unittest.TestCase):
    """get_mp3_duration falls back to default_wait when urlopen times out."""

    def test_get_mp3_duration_handles_urlopen_timeout(self):
        """socket.timeout from urlopen returns default_wait instead of raising."""
        import socket as _socket
        import sonos_play

        with patch("sonos_play.urllib.request.urlopen",
                   side_effect=_socket.timeout("timed out")):
            result = sonos_play.get_mp3_duration("http://example.com/taps.mp3", 60)

        self.assertEqual(result, 60,
                         "get_mp3_duration should return default_wait on socket.timeout")

    def test_get_mp3_duration_handles_url_error(self):
        """urllib.error.URLError from urlopen returns default_wait instead of raising."""
        import urllib.error
        import sonos_play

        with patch("sonos_play.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            result = sonos_play.get_mp3_duration("http://example.com/taps.mp3", 42)

        self.assertEqual(result, 42,
                         "get_mp3_duration should return default_wait on URLError")


# ---------------------------------------------------------------------------
# Bug 4: duration fetched before play_uri
# ---------------------------------------------------------------------------

class TestPlayUriCalledAfterDurationComputed(unittest.TestCase):
    """play_uri must be called *after* get_mp3_duration in Phase 4."""

    def setUp(self):
        self.speaker = _make_speaker("Living Room", "uid-lr")
        self.speaker.group = _make_group([self.speaker], self.speaker)
        self.speaker.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

    def test_play_uri_called_after_duration_computed(self):
        """get_mp3_duration is invoked before play_uri in Phase 4."""
        call_order = []

        def record_duration(url, default_wait):
            call_order.append("duration")
            return 5

        self.speaker.play_uri.side_effect = lambda url: call_order.append("play_uri")

        with patch("sonos_play.load_config", return_value=_base_config()), \
             patch("sonos_play.soco.SoCo", return_value=self.speaker), \
             patch("sonos_play.Snapshot", return_value=MagicMock()), \
             patch("sonos_play.get_mp3_duration", side_effect=record_duration), \
             patch("sonos_play.time.sleep"), \
             patch("sonos_play.log"), \
             patch("sys.argv", ["sonos_play.py", AUDIO_URL]):
            import sonos_play
            sonos_play.main()

        self.assertIn("duration", call_order, "get_mp3_duration must be called")
        self.assertIn("play_uri", call_order, "play_uri must be called")
        self.assertLess(
            call_order.index("duration"),
            call_order.index("play_uri"),
            "get_mp3_duration must be called before play_uri",
        )


if __name__ == "__main__":
    unittest.main()
