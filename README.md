# StableOfWar NIST-Beacon Raffle

[![StableOfWar daily raffle](https://github.com/mekstrem/stableofwar-raffle/actions/workflows/daily-draw.yml/badge.svg?branch=main)](https://github.com/mekstrem/stableofwar-raffle/actions/workflows/daily-draw.yml)

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

## Easy public verification

Anyone can verify the committed results:

```bash
git clone https://github.com/mekstrem/stableofwar-raffle.git
cd stableofwar-raffle
python scripts/verify_all.py
```

To verify one draw only:

```bash
python scripts/verify.py --date 2026-06-09
```

The badge at the top shows whether the official GitHub Actions workflow passed
on the official repository. The badge is only a convenience indicator; the
stronger proof is that the verifier passes on a specific commit from
`https://github.com/mekstrem/stableofwar-raffle`.

If someone clones or forks the repository, they can change files locally and
create different winners, but that will not change the official GitHub commit
history. Compare the Discord post's commit link and proof hash with the
official repository, not with a random copy.

## Checks

```powershell
python scripts\verify_all.py
python scripts\verify.py --date 2026-06-09
python -m unittest discover -s tests
```
