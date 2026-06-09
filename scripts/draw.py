from __future__ import annotations

import os
from datetime import timezone

from raffle_core import (
    RaffleError,
    build_draw_record,
    common_arg_parser,
    fetch_nist_pulse_at_or_after,
    get_paths,
    load_config,
    load_participants,
    load_prior_records,
    parse_date,
    read_nist_pulse_file,
    result_path,
    save_draw_artifacts,
    target_pulse_time_utc,
    validate_draw_date,
)


def main() -> int:
    parser = common_arg_parser("Create a StableOfWar raffle draw")
    parser.add_argument("--nist-pulse-file", help="Use a local NIST pulse JSON fixture")
    parser.add_argument(
        "--not-before-now",
        action="store_true",
        help="Use a pulse no earlier than the current UTC minute when that is later than the scheduled draw time",
    )
    args = parser.parse_args()

    try:
        root = args.root.resolve()
        config = load_config(root)
        draw_date = parse_date(args.date)
        validate_draw_date(config, draw_date)
        paths = get_paths(root, config)

        output_path = result_path(paths, draw_date)
        if output_path.exists():
            raise RaffleError(f"Draw record already exists: {output_path}")

        participants = load_participants(paths.participants)
        prior_records = load_prior_records(config, paths, draw_date)
        target_utc, target_reason = target_pulse_time_utc(
            config, draw_date, args.not_before_now
        )

        if args.nist_pulse_file:
            pulse = read_nist_pulse_file(root / args.nist_pulse_file)
        else:
            pulse = fetch_nist_pulse_at_or_after(config, target_utc)

        record = build_draw_record(
            config=config,
            draw_date=draw_date,
            participants=participants,
            prior_records=prior_records,
            pulse=pulse,
            target_utc=target_utc,
            target_reason=target_reason,
            source_commit=os.environ.get("GITHUB_SHA"),
        )
        save_draw_artifacts(paths, record)
        print(f"Created draw {draw_date.isoformat()}")
        print(f"Winners: {', '.join(record['winners'])}")
        print(f"Proof hash: {record['proof_hash']}")
        return 0
    except RaffleError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

