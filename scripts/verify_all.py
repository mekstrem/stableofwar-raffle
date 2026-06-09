from __future__ import annotations

import argparse
from pathlib import Path

from raffle_core import RaffleError, parse_date, verify_record


def main() -> int:
    parser = argparse.ArgumentParser("Verify all committed StableOfWar raffle draws")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    root = args.root.resolve()
    results_dir = root / "results"
    result_files = sorted(results_dir.glob("*.json"))
    if not result_files:
        print(f"No draw records found in {results_dir}")
        return 1

    failed = False
    for path in result_files:
        draw_date = parse_date(path.stem)
        try:
            ok, errors = verify_record(root, draw_date)
        except RaffleError as exc:
            print(f"{draw_date}: ERROR: {exc}")
            failed = True
            continue

        if ok:
            print(f"{draw_date}: verified")
        else:
            print(f"{draw_date}: verification failed")
            for error in errors:
                print(f"  - {error}")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
