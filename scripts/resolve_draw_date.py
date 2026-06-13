from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from raffle_core import date_range, get_paths, load_config, parse_date, result_path


def main() -> int:
    root = Path.cwd()
    config = load_config(root)
    paths = get_paths(root, config)
    today = datetime.now(ZoneInfo(config["timezone"])).date()
    start = parse_date(config["start_date"])
    end = min(today, parse_date(config["end_date"]))

    for draw_date in date_range(start, end):
        if not result_path(paths, draw_date).exists():
            print(draw_date.isoformat())
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
