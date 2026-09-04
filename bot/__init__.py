# CryptoDokter skeleton — bot/ en backtest/ worden in de volgende
# stap gevuld (zie docs/BOT-CONSOLIDATION.md). De radar heeft prioriteit.
# LibreSSL-waarschuwing van urllib3 onderdrukken (macOS-systeempython).
import warnings as _warnings

_warnings.filterwarnings("ignore", message=".*OpenSSL.*")
_warnings.filterwarnings("ignore", message=".*LibreSSL.*")
