from __future__ import annotations

import argparse
import json
import math
import os
import struct
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ALGORITHM_VERSION = "stableofwar-nist-v1"


class RaffleError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path
    config: Path
    participants: Path
    results: Path
    announcements: Path
    images: Path


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return sha256(data).hexdigest()


def load_config(root: Path) -> dict[str, Any]:
    config_path = root / "raffle.config.json"
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_paths(root: Path, config: dict[str, Any]) -> Paths:
    return Paths(
        root=root,
        config=root / "raffle.config.json",
        participants=root / config["participants_file"],
        results=root / config["results_dir"],
        announcements=root / config["announcements_dir"],
        images=root / config["images_dir"],
    )


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RaffleError(f"Invalid ISO date: {value}") from exc


def validate_draw_date(config: dict[str, Any], draw_date: date) -> None:
    start = parse_date(config["start_date"])
    end = parse_date(config["end_date"])
    if draw_date < start or draw_date > end:
        raise RaffleError(
            f"Draw date {draw_date.isoformat()} is outside "
            f"{start.isoformat()}..{end.isoformat()}"
        )


def scheduled_draw_time_utc(config: dict[str, Any], draw_date: date) -> datetime:
    hour_text, minute_text = config["draw_time"].split(":", 1)
    local_zone = ZoneInfo(config["timezone"])
    local_dt = datetime.combine(
        draw_date,
        clock_time(hour=int(hour_text), minute=int(minute_text)),
        tzinfo=local_zone,
    )
    return local_dt.astimezone(timezone.utc)


def ceil_to_next_minute(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    if value.second == 0 and value.microsecond == 0:
        return value
    return (value + timedelta(minutes=1)).replace(second=0, microsecond=0)


def target_pulse_time_utc(
    config: dict[str, Any], draw_date: date, not_before_now: bool
) -> tuple[datetime, str]:
    scheduled = scheduled_draw_time_utc(config, draw_date)
    if not not_before_now:
        return scheduled, "scheduled_draw_time"

    now_target = ceil_to_next_minute(datetime.now(timezone.utc))
    if now_target > scheduled:
        return now_target, "not_before_now"
    return scheduled, "scheduled_draw_time"


def load_participants(path: Path) -> list[str]:
    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    participants: list[str] = []
    seen: dict[str, int] = {}
    blank_lines: list[int] = []

    for line_number, raw in enumerate(raw_lines, start=1):
        name = raw.strip()
        if not name:
            blank_lines.append(line_number)
            continue
        if name in seen:
            raise RaffleError(
                f"Duplicate participant {name!r} on line {line_number}; "
                f"first seen on line {seen[name]}"
            )
        seen[name] = line_number
        participants.append(name)

    if blank_lines:
        shown = ", ".join(str(number) for number in blank_lines[:10])
        raise RaffleError(f"Blank participant lines are not allowed: {shown}")
    if not participants:
        raise RaffleError("Participant list is empty")
    return participants


def participant_list_hash(participants: list[str]) -> str:
    return sha256_hex(("\n".join(participants) + "\n").encode("utf-8"))


def date_range(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def result_path(paths: Paths, draw_date: date) -> Path:
    return paths.results / f"{draw_date.isoformat()}.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_prior_records(
    config: dict[str, Any], paths: Paths, draw_date: date
) -> list[dict[str, Any]]:
    start = parse_date(config["start_date"])
    records: list[dict[str, Any]] = []
    for prior_date in date_range(start, draw_date - timedelta(days=1)):
        path = result_path(paths, prior_date)
        if not path.exists():
            raise RaffleError(
                f"Missing prior draw record {path}. Draws must be committed in order."
            )
        records.append(load_json(path))
    return records


def prior_winners(prior_records: list[dict[str, Any]]) -> set[str]:
    winners: set[str] = set()
    for record in prior_records:
        for winner in record.get("winners", []):
            if winner in winners:
                raise RaffleError(f"Prior winner appears more than once: {winner!r}")
            winners.add(winner)
    return winners


def history_hash(prior_records: list[dict[str, Any]]) -> str:
    return sha256_hex(canonical_json_bytes(prior_records))


def parse_nist_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_nist_time(value: datetime) -> str:
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def normalize_nist_pulse(payload: dict[str, Any]) -> dict[str, Any]:
    pulse = payload.get("pulse", payload)
    required = ["chainIndex", "pulseIndex", "timeStamp", "outputValue"]
    missing = [key for key in required if key not in pulse]
    if missing:
        raise RaffleError(f"NIST pulse is missing required fields: {', '.join(missing)}")
    if pulse.get("statusCode", 0) != 0:
        raise RaffleError(f"NIST pulse statusCode is not successful: {pulse.get('statusCode')}")

    normalized = {
        "uri": pulse.get("uri"),
        "version": str(pulse.get("version", "2.0")),
        "chainIndex": int(pulse["chainIndex"]),
        "pulseIndex": int(pulse["pulseIndex"]),
        "timeStamp": pulse["timeStamp"],
        "outputValue": str(pulse["outputValue"]).upper(),
    }
    if "period" in pulse:
        normalized["period"] = int(pulse["period"])
    for optional in ("precommitmentValue", "signatureValue"):
        if optional in pulse:
            normalized[optional] = pulse[optional]
    parse_nist_timestamp(normalized["timeStamp"])
    return normalized


def read_nist_pulse_file(path: Path) -> dict[str, Any]:
    return normalize_nist_pulse(load_json(path))


def fetch_json(url: str, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "stableofwar-raffle/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RaffleError(f"Unable to fetch NIST pulse from {url}: {exc}") from exc


def pulse_index_at_or_after(latest_pulse: dict[str, Any], target_utc: datetime) -> int:
    latest_time = parse_nist_timestamp(latest_pulse["timeStamp"])
    period_ms = int(latest_pulse.get("period", 60000))
    period_seconds = period_ms / 1000
    if period_seconds <= 0:
        raise RaffleError(f"Invalid NIST pulse period: {period_ms}")
    offset = math.ceil((target_utc - latest_time).total_seconds() / period_seconds)
    return int(latest_pulse["pulseIndex"]) + offset


def fetch_nist_pulse_at_or_after(
    config: dict[str, Any],
    target_utc: datetime,
    timeout_seconds: int = 20,
    max_wait_seconds: int = 180,
) -> dict[str, Any]:
    base_url = config["nist"]["base_url"].rstrip("/")
    chain_index = int(config["nist"].get("chain_index", 2))
    target_utc = target_utc.astimezone(timezone.utc)
    deadline = time.monotonic() + max_wait_seconds
    last_error: Exception | None = None

    while True:
        try:
            latest = normalize_nist_pulse(
                fetch_json(f"{base_url}/pulse/last", timeout_seconds)
            )
            target_index = pulse_index_at_or_after(latest, target_utc)
            if target_index > int(latest["pulseIndex"]):
                last_error = RaffleError(
                    f"Target pulse {target_index} is not published yet; "
                    f"latest is {latest['pulseIndex']}"
                )
            else:
                url = f"{base_url}/chain/{chain_index}/pulse/{target_index}"
                pulse = normalize_nist_pulse(fetch_json(url, timeout_seconds))
                pulse_time = parse_nist_timestamp(pulse["timeStamp"])
                period_seconds = int(pulse.get("period", latest.get("period", 60000))) / 1000
                if pulse_time < target_utc:
                    last_error = RaffleError(
                        f"NIST pulse {pulse['pulseIndex']} is before target {format_nist_time(target_utc)}"
                    )
                elif (pulse_time - target_utc).total_seconds() >= period_seconds:
                    last_error = RaffleError(
                        f"NIST pulse {pulse['pulseIndex']} is not the first pulse after target {format_nist_time(target_utc)}"
                    )
                else:
                    pulse["lookupUrl"] = url
                    return pulse
        except Exception as exc:
            last_error = exc

        if time.monotonic() >= deadline:
            raise RaffleError(f"No NIST pulse found at or after {format_nist_time(target_utc)}: {last_error}")
        time.sleep(10)


def build_seed_material(
    config: dict[str, Any],
    draw_date: date,
    participants_hash: str,
    prior_history_hash: str,
    pulse: dict[str, Any],
) -> dict[str, Any]:
    return {
        "algorithmVersion": ALGORITHM_VERSION,
        "eventName": config["event_name"],
        "drawDate": draw_date.isoformat(),
        "participantsHash": participants_hash,
        "priorHistoryHash": prior_history_hash,
        "nist": {
            "chainIndex": pulse["chainIndex"],
            "pulseIndex": pulse["pulseIndex"],
            "timeStamp": pulse["timeStamp"],
            "outputValue": pulse["outputValue"],
        },
    }


def select_winners(
    participants: list[str],
    excluded: set[str],
    winners_per_day: int,
    final_seed: str,
) -> tuple[list[str], list[dict[str, str]]]:
    eligible = [name for name in participants if name not in excluded]
    if len(eligible) < winners_per_day:
        raise RaffleError(
            f"Only {len(eligible)} eligible participants remain for {winners_per_day} winners"
        )

    scored = []
    for index, name in enumerate(eligible):
        score_input = f"{final_seed}\0{index}\0{name}".encode("utf-8")
        scored.append((sha256_hex(score_input), index, name))
    scored.sort()
    selected = scored[:winners_per_day]
    return [name for _, _, name in selected], [
        {"name": name, "score": score, "eligibleIndex": str(index)}
        for score, index, name in selected
    ]


def record_proof_hash(record_without_hash: dict[str, Any]) -> str:
    stripped = {key: value for key, value in record_without_hash.items() if key != "proof_hash"}
    return sha256_hex(canonical_json_bytes(stripped))


def build_draw_record(
    config: dict[str, Any],
    draw_date: date,
    participants: list[str],
    prior_records: list[dict[str, Any]],
    pulse: dict[str, Any],
    target_utc: datetime,
    target_reason: str,
    generated_at_utc: datetime | None = None,
    source_commit: str | None = None,
) -> dict[str, Any]:
    excluded = prior_winners(prior_records)
    participants_hash = participant_list_hash(participants)
    prior_hash = history_hash(prior_records)
    seed_material = build_seed_material(
        config, draw_date, participants_hash, prior_hash, pulse
    )
    final_seed = sha256_hex(canonical_json_bytes(seed_material))
    winners, selection_proofs = select_winners(
        participants, excluded, int(config["winners_per_day"]), final_seed
    )
    generated_at = generated_at_utc or datetime.now(timezone.utc)

    record: dict[str, Any] = {
        "algorithmVersion": ALGORITHM_VERSION,
        "eventName": config["event_name"],
        "drawDate": draw_date.isoformat(),
        "generatedAt": format_nist_time(generated_at),
        "timezone": config["timezone"],
        "drawTime": config["draw_time"],
        "targetPulseTimeUtc": format_nist_time(target_utc),
        "targetPulseReason": target_reason,
        "participantsFile": config["participants_file"],
        "participantsCount": len(participants),
        "participantsHash": participants_hash,
        "priorWinnersCount": len(excluded),
        "excludedPriorWinners": sorted(excluded),
        "eligibleCount": len(participants) - len(excluded),
        "winnersPerDay": int(config["winners_per_day"]),
        "priorHistoryHash": prior_hash,
        "nistPulse": pulse,
        "seedMaterial": seed_material,
        "finalSeed": final_seed,
        "winners": winners,
        "selectionProofs": selection_proofs,
        "sourceCommit": source_commit,
    }
    record["proof_hash"] = record_proof_hash(record)
    return record


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def write_announcement(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    winners = "\n".join(f"{index}. {name}" for index, name in enumerate(record["winners"], start=1))
    nist_url = record["nistPulse"].get("uri") or record["nistPulse"].get("lookupUrl", "")
    source_commit = record.get("sourceCommit") or "local/uncommitted"
    text = f"""# StableOfWar raffle winners - {record['drawDate']}

{winners}

Draw details:
- Entries: {record['participantsCount']}
- Eligible today: {record['eligibleCount']}
- Prior winners excluded: {record['priorWinnersCount']}
- NIST pulse: {record['nistPulse']['pulseIndex']} ({record['nistPulse']['timeStamp']})
- NIST pulse link: {nist_url}
- Proof hash: `{record['proof_hash']}`
- Source commit: `{source_commit}`

Verification:
```bash
python scripts/verify.py --date {record['drawDate']}
```
"""
    path.write_text(text, encoding="utf-8")


FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "#": ["01010", "11111", "01010", "01010", "11111", "01010", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def set_pixel(image: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        offset = (y * width + x) * 3
        image[offset : offset + 3] = bytes(color)


def fill_rect(image: bytearray, width: int, height: int, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            set_pixel(image, width, height, xx, yy, color)


def draw_text(
    image: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    text: str,
    scale: int,
    color: tuple[int, int, int],
) -> None:
    cursor = x
    for raw_char in text:
        char = raw_char.upper()
        pattern = FONT.get(char, FONT["-"])
        for row, bits in enumerate(pattern):
            for col, bit in enumerate(bits):
                if bit == "1":
                    fill_rect(
                        image,
                        width,
                        height,
                        cursor + col * scale,
                        y + row * scale,
                        scale,
                        scale,
                        color,
                    )
        cursor += 6 * scale


def text_width(text: str, scale: int) -> int:
    return max(0, len(text) * 6 * scale - scale)


def centered_text(
    image: bytearray,
    width: int,
    height: int,
    y: int,
    text: str,
    preferred_scale: int,
    color: tuple[int, int, int],
    max_width: int,
) -> None:
    units = max(1, len(text) * 6 - 1)
    scale = max(1, min(preferred_scale, max_width // units))
    x = (width - text_width(text, scale)) // 2
    draw_text(image, width, height, x, y, text, scale, color)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, width: int, height: int, rgb: bytearray) -> None:
    rows = []
    stride = width * 3
    for y in range(height):
        rows.append(b"\x00" + bytes(rgb[y * stride : (y + 1) * stride]))
    raw = b"".join(rows)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(raw, 9))
        + png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def write_result_image(path: Path, record: dict[str, Any]) -> None:
    width, height = 900, 1200
    blue = (36, 134, 190)
    white = (248, 250, 252)
    dark = (12, 31, 48)
    accent = (0, 132, 224)
    muted = (226, 232, 240)
    image = bytearray(blue * (width * height))

    fill_rect(image, width, height, 80, 90, 740, 970, white)
    fill_rect(image, width, height, 80, 90, 740, 8, (255, 255, 255))
    fill_rect(image, width, height, 80, 420, 740, 2, muted)
    fill_rect(image, width, height, 140, 560, 620, 80, (229, 241, 250))
    fill_rect(image, width, height, 140, 640, 620, 3, accent)

    centered_text(image, width, height, 145, "STABLEOFWAR", 7, accent, 760)
    centered_text(image, width, height, 245, "WINNERS", 5, dark, 760)
    y = 330
    for winner in record["winners"]:
        centered_text(image, width, height, y, winner, 6, accent, 720)
        y += 80

    centered_text(image, width, height, 590, "DRAW DETAILS", 4, dark, 600)
    centered_text(
        image,
        width,
        height,
        700,
        f"DATE {record['drawDate']}",
        4,
        dark,
        650,
    )
    centered_text(
        image,
        width,
        height,
        760,
        f"ENTRIES {record['participantsCount']} ELIGIBLE {record['eligibleCount']}",
        3,
        dark,
        720,
    )
    centered_text(
        image,
        width,
        height,
        830,
        f"NIST PULSE {record['nistPulse']['pulseIndex']}",
        3,
        dark,
        720,
    )
    centered_text(
        image,
        width,
        height,
        900,
        f"PROOF {record['proof_hash'][:16]}",
        3,
        dark,
        720,
    )
    centered_text(image, width, height, 1010, "VERIFY ON GITHUB", 3, dark, 720)
    write_png(path, width, height, image)


def save_draw_artifacts(paths: Paths, record: dict[str, Any]) -> None:
    draw_date = parse_date(record["drawDate"])
    write_json(result_path(paths, draw_date), record)
    write_announcement(paths.announcements / f"{draw_date.isoformat()}.md", record)
    write_result_image(paths.images / f"{draw_date.isoformat()}.png", record)


def verify_record(root: Path, draw_date: date) -> tuple[bool, list[str]]:
    config = load_config(root)
    validate_draw_date(config, draw_date)
    paths = get_paths(root, config)
    participants = load_participants(paths.participants)
    path = result_path(paths, draw_date)
    if not path.exists():
        raise RaffleError(f"Draw record does not exist: {path}")
    record = load_json(path)
    errors: list[str] = []

    stored_hash = record.get("proof_hash")
    actual_hash = record_proof_hash(record)
    if stored_hash != actual_hash:
        errors.append(f"proof_hash mismatch: stored {stored_hash}, computed {actual_hash}")

    prior_records = load_prior_records(config, paths, draw_date)
    expected = build_draw_record(
        config=config,
        draw_date=draw_date,
        participants=participants,
        prior_records=prior_records,
        pulse=normalize_nist_pulse(record["nistPulse"]),
        target_utc=parse_nist_timestamp(record["targetPulseTimeUtc"]),
        target_reason=record["targetPulseReason"],
        generated_at_utc=parse_nist_timestamp(record["generatedAt"]),
        source_commit=record.get("sourceCommit"),
    )

    comparable_keys = [
        "participantsCount",
        "participantsHash",
        "priorWinnersCount",
        "excludedPriorWinners",
        "eligibleCount",
        "priorHistoryHash",
        "seedMaterial",
        "finalSeed",
        "winners",
        "selectionProofs",
        "proof_hash",
    ]
    for key in comparable_keys:
        if record.get(key) != expected.get(key):
            errors.append(f"{key} mismatch")
    return not errors, errors


def common_arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--date", required=True, help="Draw date as YYYY-MM-DD")
    return parser
