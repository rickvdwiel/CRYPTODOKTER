# CryptoDokter paper-bot. LibreSSL-waarschuwing van urllib3 onderdrukken
# (macOS-systeempython).
import warnings as _warnings

_warnings.filterwarnings("ignore", message=".*OpenSSL.*")
_warnings.filterwarnings("ignore", message=".*LibreSSL.*")
