# StableOfWar NIST-Beacon Raffle

This repository runs the audited StableOfWar daily raffle.

- Event: `StableOfWar`
- Schedule: June 9-15, 2026, inclusive
- Draw time: 18:00 Europe/Stockholm
- Winners: 3 per day
- Public seed source: NIST Randomness Beacon v2
- Rule: a person can win at most once during the event

## How the draw is verified

Each draw is deterministic from committed inputs plus a public NIST Beacon pulse:

- event name
- draw date
- participant list hash
- prior draw history hash
- NIST `chainIndex`
- NIST `pulseIndex`
- NIST `timeStamp`
- NIST `outputValue`

The script hashes those values into a final seed, ranks all eligible names by
SHA-256 score, and selects the first 3 names. Because the participant list,
history, NIST pulse, and result record are committed, anyone can rerun the
verifier and detect tampering.

## Run locally

```powershell
python scripts\draw.py --date 2026-06-09 --nist-pulse-file tests\fixtures\nist-pulse-2026-06-09.json
python scripts\verify.py --date 2026-06-09
python -m unittest discover -s tests
```

For a real draw, omit `--nist-pulse-file` so the script fetches the NIST pulse:

```powershell
python scripts\draw.py --date 2026-06-09 --not-before-now
python scripts\verify.py --date 2026-06-09
```

Use `--not-before-now` for the first manual June 9 run if setup happens after
18:00 Stockholm. It forces the seed pulse to be no earlier than the current
minute, so the pulse is still chosen after the committed participant list exists.

## GitHub setup

1. Create a public GitHub repository.
2. Push this local repository to it.
3. Enable GitHub Actions.
4. For the first June 9 run, if the scheduled time has already passed, start
   the `StableOfWar daily raffle` workflow manually with:
   - `draw_date`: `2026-06-09`
   - `not_before_now`: `true`

The workflow commits these artifacts for each draw:

- `results/YYYY-MM-DD.json`
- `announcements/YYYY-MM-DD.md`
- `images/YYYY-MM-DD.png`

Post the Markdown message or PNG image manually in Discord. Include the GitHub
commit link so people can verify the draw.

