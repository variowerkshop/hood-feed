#!/usr/bin/env python3
"""
Golden Sneakers API  ->  Hood.de CSV (Schuhe der Marken in MARKEN, z. B. Jordan und Nike)

Aufruf (Windows / Mac / Linux, Python 3.8+, keine Zusatzpakete noetig):
    python golden_zu_hood.py
Der Token wird abgefragt (oder per Umgebungsvariable GS_TOKEN gesetzt).
Zum Testen ohne Internet:  python golden_zu_hood.py --feed-datei feed.json
Es wird nur EIN Abruf gemacht (Limit der API: 3 Abrufe pro 60 Sekunden).
"""
import hashlib
from datetime import datetime, timezone, timedelta
import argparse, csv, getpass, html, json, os, re, sys, unicodedata, urllib.parse, urllib.request, urllib.error
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP

# ---------------- Einstellungen (hier anpassen) ----------------
MARKEN         = ("jordan", "nike", "asics", "new balance", "adidas", "salomon", "yeezy", "autry")   # nur diese Marken (klein geschrieben, wie brand_name im Feed)
MWST_EINKAUF   = D("0.00")     # Aufschlag auf den Netto-Einkaufspreis (Kleinunternehmer: keine Vorsteuer)
VERSAND_KOSTEN = D("10")       # Pauschale fuer DHL-Versand (EUR), wird in die Kalkulation eingerechnet
HOOD_GEBUEHR   = D("0.06")
ZIEL_MARGE     = D("0.15")
MIN_GEWINN     = D("15")       # Mindestgewinn pro Paar in EUR (nach Hood-Gebuehr, Einkauf inkl. 19 %, Versand); 0 = aus
MAX_MENGE      = 3             # Hood-Menge = min(Lieferantenbestand, MAX_MENGE); 0 = keine Begrenzung
MAX_BILDER     = 5             # Anzahl Bilder pro Artikel
# Hood-Kategorienummern (Marktplatz-Kategorie, siehe Anleitung in der Chat-Antwort). Pflicht! Sonst lehnt Hood alle Artikel ab.
KATEGORIE_HERREN = "23631"   # z. B. Mode > Schuhe > Herren > Sneaker
KATEGORIE_DAMEN  = "9139"   # Modelle mit "(Women's)" im Namen
KATEGORIE_JUNGEN  = "29530"  # Kinder-Modelle: "(GS)", "(PS)" oder "(TD)" im Namen -> standardmaessig hier
KATEGORIE_MAEDCHEN = "29519" # Kinder-Modelle, die als Maedchen-Modell gelten (siehe unten)