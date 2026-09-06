# Start paper-scheduler op Mac (5 min)

1. `git pull origin main`
2. `source .venv/bin/activate` (of venv + `pip install -r requirements.txt`)
3. `python -m bot.run_bot --reset`
4. Kies één:
   - Foreground: `python -m bot.scheduler` (Terminal open / tmux)
   - Achtergrond: paden in `deploy/nl.cryptodokter.paperbot.plist` + `launchctl load` (zie `docs/SCHEDULER.md`)
5. Check: `tail -f data/scheduler.log` en `python -m bot.run_bot --status`

Papier-only. Geen Bitvavo-keys.
