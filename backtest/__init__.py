# Backtest-engine volgt zodra de radar loopt (zie docs/BOT-CONSOLIDATION.md).
# LibreSSL-waarschuwing van urllib3 onderdrukken (macOS-systeempython).
import warnings as _warnings

_warnings.filterwarnings("ignore", message=".*OpenSSL.*")
_warnings.filterwarnings("ignore", message=".*LibreSSL.*")
