# CryptoDokter Radar — vroege crypto-trend signalering (gratis/openbare bronnen)
__version__ = "0.1.0"

# LibreSSL-waarschuwing van urllib3 onderdrukken (macOS-systeempython);
# functioneel niet relevant voor onze publieke HTTPS-calls.
import warnings as _warnings

_warnings.filterwarnings("ignore", message=".*OpenSSL.*")
_warnings.filterwarnings("ignore", message=".*LibreSSL.*")
