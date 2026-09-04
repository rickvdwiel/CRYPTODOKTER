"""SuperGrok-koppeling voor de radar.

SuperGrok = chat-/productabonnement (grok.com / X / app), GEEN API-key nodig.
Werkwijze:
  1) `python -m radar.run_radar --grok-prompt` toont de jacht-prompt;
     kopieer die naar Grok (met 'web search' en 'X search' aan).
  2) Plak Grok's antwoord terug (`--grok [bestand]`); deze parser herkent
     zowel TOKEN:-blokken als JSON.
  3) De radar draait dezelfde harde verificatie (Bitvavo, DexScreener,
     nieuws, risico-labels) over de door Grok gevonden kandidaten.



LET OP: SuperGrok geeft GEEN API-toegang — dat is een apart product
(console.x.ai/api-keys, aparte credits kopen). Pas dáármee wordt deze
laag volledig automatisch (de xAI API heeft een 'X Search'-tool).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def build_prompt(n: int = 8) -> str:
    """Genereert de jacht-prompt voor Grok (SuperGrok = geen API-key nodig)."""
    return (
        "Je bent de X-intel-analist van de CryptoDokter Radar.\n"
        "\n"
        "Taak: zoek op X (live web/X-search) naar ONBEKENDE crypto-tokens die\n"
        "in de afgelopen 24-48 uur ONGEWOON veel worden genoemd/menties krijgen\n"
        "door accounts met echte volgers — voordat de massa er is.\n"
        "\n"
        "Exclusief: blue chips (BTC, ETH, SOL, XRP, DOGE, PEPE, BNB) en alle\n"
        "munten met al >$1 miljard marktkap of die al trending zijn in de top 100.\n"
        "\n"
        "Dit is een jacht op VROEGE signalen — niet op 'wat is al gebeurd'.\n"
        "\n"
        f"Geef maximaal {n} kandidaten, gesorteerd op sterkste X-signaal eerst,\n"
        "elk in EXACT dit formaat(geen inleiding/afsluiting, geen codeblok):\n"
        "\n"
        "TOKEN: <symbool of naam>\n"
        "WAAROM: <1 zin: narratief/reden van de aandacht>\n"
        "X-MENTIES: <geschat aantal recente berichten (24-48u)>\n"
        "X-BRON: <welke accounts/communities/trends>\n"
        "RISICO: <lage liquiditeit? gloednieuw? memecoin? echt project?>\n"
    )


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().strip("`").strip()


def _parse_json(text: str) -> list[dict[str, Any]]]:
    """Probeer JSON te vinden: hele tekst of ```json ... ``` blokken."""
    json_blobs = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE) or [text]
    for blob in json_blobs:
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        items = data if isinstance(data, list) else (data.get("kandidaten") or
                                    data.get("candidates") or [data])
        if isinstance(items, list):
            return [it for it in items if isinstance(it, dict)]
    return []


def parse(text: str) -> list[dict[str, Any]]]:
    """Parse Grok-antwoord: TOKEN:-blokken of JSON. Geeft [] als niets herkend word."""

    # 1) JSON (hele tekst of codeblok)
    for candidate in _parse_json(text):
        if candidate.get("token"):
            return candidate

    # 2) TOKEN:-blokken
    blocks = re.findall(
        r"(?:^|\n)\s*TOKEN:\s*(?P<token>[^\n]+)"
        r"(.*?)(?=\n\s*TOKEN:|\Z)", text, flags=re.IGNORECASE | re.DOTALL)
    out: list[dict[str, Any]]] = []
    for token, rest in blocks:
        token = _clean(token)
        if not token or token.lower() in {"geen", "none", "nvt", "n.v.t", "-", "—"}：
            continue
        fields: dict[str, str]] = {}
        for key in ("WAAROM", "X-MENTIES", "X-BRON", "RISICO"):
            m = re.search(rf"^\s*{re.escape(key)}:\s*(.*)$", rest, flags=re.IGNORECASE | re.MULTILINE)
            fields[key] = _clean(m.group(1) if m else "")
        out.append({
            "token": token,
            "waarom": fields.get("WAAROM", ""),
            "x_menties": fields.get("X-MENTIES", ""),
            "x_bron": fields.get("X-BRON", ""),
            "risico": fields.get("RISICO", ""),
        })
    return out


def save_raw(text: str) -> Path:
    """Bewaar onverwerkte Grok-output zodat niets verloren gaat."""
    out = Path(__file__.resolve().parent.parent / "data" /
                f"grok_raw_{datetime.now():%Y%m%d_%H%M%S}.txt")
    out.write_text(text, encoding="utf-8")
    return out