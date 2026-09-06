# Paper-bot scheduler — trackrecord opbouwen

Doel: wekenlang `--tick` en `--scan` draaien op de **papieren** portefeuille
(€20 YOLO-paper start), zodat je ziet of de strategie werkt vóór er ooit echt geld in gaat.

## Foreground (snel testen)

```bash
source .venv/bin/activate
python -m bot.scheduler --once --dry-run   # één tick + droge scan
python -m bot.scheduler --tick-only        # alleen exits
python -m bot.scheduler                    # blijft lopen (uurlijks tick, dagelijks scan)
```

Logs: `data/scheduler.log` (rotating, max ~5×5 MB). Staat in `.gitignore` via `data/*.log`
als je die regel toevoegt; anders handmatig negeren.

## macOS launchd

Zie `deploy/nl.cryptodokter.paperbot.plist`. Pas de paden aan, dan:

```bash
mkdir -p ~/Library/Logs/CryptoDokter
cp deploy/nl.cryptodokter.paperbot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/nl.cryptodokter.paperbot.plist
launchctl list | grep cryptodokter
```

Stoppen: `launchctl unload ~/Library/LaunchAgents/nl.cryptodokter.paperbot.plist`

## Regels

- Geen API-keys, geen echte orders.
- Koopfilter blijft `bot/config.py` (score ≥ 35, liquiditeit ≥ $25k; YOLO-paper: €20 / max 2 posities).
- Na wijzigingen: `python -m unittest discover -s tests -t . -q`
