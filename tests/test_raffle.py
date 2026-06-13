from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import urllib.error
from io import BytesIO
from datetime import timezone
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from raffle_core import (  # noqa: E402
    NistPulseUnavailable,
    RaffleError,
    build_draw_record,
    fetch_json,
    get_paths,
    load_config,
    load_participants,
    load_prior_records,
    parse_date,
    read_nist_pulse_file,
    record_proof_hash,
    result_path,
    save_draw_artifacts,
    scheduled_draw_time_utc,
    target_pulse_time_utc,
    pulse_index_at_or_after,
    validate_draw_date,
    verify_record,
)


class RaffleTests(unittest.TestCase):
    def test_participant_file_has_expected_unique_count(self) -> None:
        participants = load_participants(ROOT / "participants.txt")
        self.assertEqual(187, len(participants))
        self.assertEqual(187, len(set(participants)))

    def test_date_guard_allows_only_event_window(self) -> None:
        config = load_config(ROOT)
        validate_draw_date(config, parse_date("2026-06-09"))
        validate_draw_date(config, parse_date("2026-06-15"))
        with self.assertRaises(RaffleError):
            validate_draw_date(config, parse_date("2026-06-08"))
        with self.assertRaises(RaffleError):
            validate_draw_date(config, parse_date("2026-06-16"))

    def test_scheduled_draw_time_is_1600_utc_in_june(self) -> None:
        config = load_config(ROOT)
        scheduled = scheduled_draw_time_utc(config, parse_date("2026-06-09"))
        self.assertEqual("2026-06-09T16:00:00+00:00", scheduled.isoformat())

    def test_nist_pulse_index_calculation(self) -> None:
        latest = {
            "pulseIndex": 1825057,
            "timeStamp": "2026-06-09T19:52:00.000Z",
            "period": 60000,
        }
        target = scheduled_draw_time_utc(load_config(ROOT), parse_date("2026-06-09"))
        self.assertEqual(1824825, pulse_index_at_or_after(latest, target))

    def test_pulse_after_trigger_ignores_scheduled_draw_time(self) -> None:
        config = load_config(ROOT)
        target, reason = target_pulse_time_utc(
            config, parse_date("2026-06-15"), False, pulse_after_trigger=True
        )
        self.assertEqual("workflow_trigger_time", reason)
        self.assertEqual(0, target.second)
        self.assertEqual(0, target.microsecond)

    def test_nist_pulse_not_available_is_temporary_error(self) -> None:
        error = urllib.error.HTTPError(
            url="https://beacon.nist.gov/beacon/2.0/chain/2/pulse/1829145",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=BytesIO(b"Pulse Not Available."),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(NistPulseUnavailable):
                fetch_json(error.url, 1)

    def test_draw_and_verify_with_fixture(self) -> None:
        with self.temp_repo() as root:
            config = load_config(root)
            paths = get_paths(root, config)
            participants = load_participants(paths.participants)
            pulse = read_nist_pulse_file(
                root / "tests" / "fixtures" / "nist-pulse-2026-06-09.json"
            )
            draw_date = parse_date("2026-06-09")
            record = build_draw_record(
                config=config,
                draw_date=draw_date,
                participants=participants,
                prior_records=[],
                pulse=pulse,
                target_utc=scheduled_draw_time_utc(config, draw_date),
                target_reason="scheduled_draw_time",
            )
            save_draw_artifacts(paths, record)

            ok, errors = verify_record(root, draw_date)
            self.assertTrue(ok, errors)
            announcement_path = paths.announcements / "2026-06-09.md"
            self.assertTrue(announcement_path.exists())
            announcement = announcement_path.read_text(encoding="utf-8")
            self.assertIn("StableOfWar raffle - 2026-06-09", announcement)
            self.assertIn("Today's winners are in the image below.", announcement)
            self.assertIn("each pilot can only win once", announcement)
            self.assertIn("https://github.com/mekstrem/stableofwar-raffle", announcement)
            self.assertTrue((paths.images / "2026-06-09.png").exists())

    def test_draw_and_verify_with_lookup_url_in_nist_pulse(self) -> None:
        with self.temp_repo() as root:
            config = load_config(root)
            paths = get_paths(root, config)
            participants = load_participants(paths.participants)
            pulse = read_nist_pulse_file(
                root / "tests" / "fixtures" / "nist-pulse-2026-06-09.json"
            )
            pulse["lookupUrl"] = "https://beacon.nist.gov/beacon/2.0/chain/2/pulse/1000001"
            draw_date = parse_date("2026-06-09")
            record = build_draw_record(
                config=config,
                draw_date=draw_date,
                participants=participants,
                prior_records=[],
                pulse=pulse,
                target_utc=scheduled_draw_time_utc(config, draw_date),
                target_reason="scheduled_draw_time",
            )
            save_draw_artifacts(paths, record)

            ok, errors = verify_record(root, draw_date)
            self.assertTrue(ok, errors)

    def test_prior_winners_are_excluded(self) -> None:
        with self.temp_repo() as root:
            config = load_config(root)
            paths = get_paths(root, config)
            participants = load_participants(paths.participants)

            first_date = parse_date("2026-06-09")
            first_record = build_draw_record(
                config,
                first_date,
                participants,
                [],
                read_nist_pulse_file(root / "tests" / "fixtures" / "nist-pulse-2026-06-09.json"),
                scheduled_draw_time_utc(config, first_date),
                "scheduled_draw_time",
            )
            save_draw_artifacts(paths, first_record)

            second_date = parse_date("2026-06-10")
            second_record = build_draw_record(
                config,
                second_date,
                participants,
                load_prior_records(config, paths, second_date),
                read_nist_pulse_file(root / "tests" / "fixtures" / "nist-pulse-2026-06-10.json"),
                scheduled_draw_time_utc(config, second_date),
                "scheduled_draw_time",
            )
            self.assertTrue(set(first_record["winners"]).isdisjoint(second_record["winners"]))

    def test_tampering_with_participants_fails_verification(self) -> None:
        with self.temp_repo() as root:
            config = load_config(root)
            paths = get_paths(root, config)
            participants = load_participants(paths.participants)
            draw_date = parse_date("2026-06-09")
            record = build_draw_record(
                config,
                draw_date,
                participants,
                [],
                read_nist_pulse_file(root / "tests" / "fixtures" / "nist-pulse-2026-06-09.json"),
                scheduled_draw_time_utc(config, draw_date),
                "scheduled_draw_time",
            )
            save_draw_artifacts(paths, record)

            text = paths.participants.read_text(encoding="utf-8")
            paths.participants.write_text(text.replace(participants[0], "Tampered Name", 1), encoding="utf-8")

            ok, errors = verify_record(root, draw_date)
            self.assertFalse(ok)
            self.assertIn("participantsHash mismatch", errors)

    def test_tampering_with_winner_fails_proof_hash(self) -> None:
        with self.temp_repo() as root:
            config = load_config(root)
            paths = get_paths(root, config)
            participants = load_participants(paths.participants)
            draw_date = parse_date("2026-06-09")
            record = build_draw_record(
                config,
                draw_date,
                participants,
                [],
                read_nist_pulse_file(root / "tests" / "fixtures" / "nist-pulse-2026-06-09.json"),
                scheduled_draw_time_utc(config, draw_date),
                "scheduled_draw_time",
            )
            save_draw_artifacts(paths, record)

            path = result_path(paths, draw_date)
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["winners"][0] = "Tampered Name"
            path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")

            ok, errors = verify_record(root, draw_date)
            self.assertFalse(ok)
            self.assertTrue(any("proof_hash mismatch" in error for error in errors))

    def test_missing_draw_record_is_clean_error(self) -> None:
        with self.temp_repo() as root:
            with self.assertRaisesRegex(RaffleError, "Draw record does not exist"):
                verify_record(root, parse_date("2026-06-09"))

    def temp_repo(self):
        return TempRepo(ROOT)


class TempRepo:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.tmpdir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        for name in [
            "raffle.config.json",
            "participants.txt",
            "scripts",
            "tests",
        ]:
            src = self.source / name
            dst = root / name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        (root / "results").mkdir()
        (root / "announcements").mkdir()
        (root / "images").mkdir()
        return root

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self.tmpdir is not None
        self.tmpdir.cleanup()
