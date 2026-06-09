from __future__ import annotations

from raffle_core import RaffleError, common_arg_parser, parse_date, verify_record


def main() -> int:
    parser = common_arg_parser("Verify a StableOfWar raffle draw")
    args = parser.parse_args()

    try:
        ok, errors = verify_record(args.root.resolve(), parse_date(args.date))
    except RaffleError as exc:
        print(f"ERROR: {exc}")
        return 1

    if ok:
        print(f"Verified draw {args.date}")
        return 0

    print(f"Verification failed for draw {args.date}")
    for error in errors:
        print(f"- {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

