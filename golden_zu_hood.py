#!/usr/bin/env python3
"""
Golden Sneakers API  ->  Hood.de CSV (Schuhe der Marken in MARKEN, z. B. Jordan und Nike)

Aufruf (Windows / Mac / Linux, Python 3.8+, keine Zusatzpakete noetig):
    python golden_zu_hood.py
Der Token wird abgefragt (oder per Umgebungsvariable GS_TOKEN gesetzt).
Zum Testen ohne Internet:  python golden_zu_hood.py --feed-datei feed.json
Es wird nur EIN Abruf gemacht (Limit der API: 3 Abrufe pro 60 Sekunden).
"""
import argparse, csv, getpass, html, json, os, re, sys, unicodedata, urllib.parse, urllib.request, urllib.error
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP

# ---------------- Einstellungen (hier anpassen) ----------------
MARKEN         = ("jordan", "nike")   # nur diese Marken (klein geschrieben, wie brand_name im Feed)
MWST_EINKAUF   = D("0.19")     # Aufschlag auf den Netto-Einkaufspreis (Kleinunternehmer: keine Vorsteuer)
VERSAND_KOSTEN = D("10")       # Pauschale fuer DHL-Versand (EUR), wird in die Kalkulation eingerechnet
HOOD_GEBUEHR   = D("0.08")
ZIEL_MARGE     = D("0.15")
MIN_GEWINN     = D("15")       # Mindestgewinn pro Paar in EUR (nach Hood-Gebuehr, Einkauf inkl. 19 %, Versand); 0 = aus
MAX_MENGE      = 1             # Hood-Menge = min(Lieferantenbestand, MAX_MENGE); 0 = keine Begrenzung
MAX_BILDER     = 5             # Anzahl Bilder pro Artikel
# Hood-Kategorienummern (Marktplatz-Kategorie, siehe Anleitung in der Chat-Antwort). Pflicht! Sonst lehnt Hood alle Artikel ab.
KATEGORIE_HERREN = "23631"   # z. B. Mode > Schuhe > Herren > Sneaker
KATEGORIE_DAMEN  = "9139"   # Modelle mit "(Women's)" im Namen
KATEGORIE_JUNGEN  = "29530"  # Kinder-Modelle: "(GS)", "(PS)" oder "(TD)" im Namen -> standardmaessig hier
KATEGORIE_MAEDCHEN = "29519" # Kinder-Modelle, die als Maedchen-Modell gelten (siehe unten)
# Woran erkennt das Skript Maedchen-Modelle? Der Lieferant gibt das Geschlecht bei Kindern nicht an. Daher:
MAEDCHEN_STICHWOERTER = ("girl",)                 # steht so etwas im Modellnamen -> Maedchen
MAEDCHEN_DATEI = "maedchen_skus.txt"              # eigene Liste: eine SKU pro Zeile (z. B. DQ8423-602) -> Maedchen
JUNGEN_DATEI   = "jungen_skus.txt"                # eigene Liste: eine SKU pro Zeile -> Jungen (hat Vorrang vor Stichwoertern)
KINDER_LISTE   = "kinder_modelle_pruefen.txt"     # wird erzeugt: alle Kinder-Modelle mit der gewaehlten Zuordnung
MWST_SPALTE    = "0"           # Kleinunternehmer; vorher an 2-3 Artikeln bei Hood testen
BASIS_URL      = "https://www.goldensneakers.net/api/assortment-flat/"
MEDIA_URL      = "https://media.goldensneakers.net"
AUSGABE        = "hood_import_jordan_live.csv"
ENTFERNT       = "hood_import_ausverkauft_loeschen.csv"
ZUSTAND_DATEI  = "bekannte_artikel.txt"
ZUORDNUNG_DATEI = "artikel_zuordnung.json"
GESEHEN_DATEI  = "zuletzt_gesehen.json"   # nur im Automatik-Modus
BEHALTEN_TAGE  = 30                       # Automatik: Artikel, die der Lieferant nicht mehr fuehrt, bleiben so lange mit Menge 0 in der Datei
# ----------------------------------------------------------------

# Automatik-Modus (GitHub): Einstellungen kommen aus Umgebungsvariablen, nichts wird abgefragt.
AUTOMATIK = os.environ.get("AUTOMATIK") == "1"
def _env(name, standard):
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else standard
KATEGORIE_HERREN   = _env("KATEGORIE_HERREN", KATEGORIE_HERREN)
KATEGORIE_DAMEN    = _env("KATEGORIE_DAMEN", KATEGORIE_DAMEN)
KATEGORIE_JUNGEN   = _env("KATEGORIE_JUNGEN", KATEGORIE_JUNGEN)
KATEGORIE_MAEDCHEN = _env("KATEGORIE_MAEDCHEN", KATEGORIE_MAEDCHEN)
MAX_MENGE          = int(_env("MAX_MENGE", MAX_MENGE))
MIN_GEWINN         = D(_env("MIN_GEWINN", MIN_GEWINN).replace(",", ".") if isinstance(_env("MIN_GEWINN", MIN_GEWINN), str) else str(MIN_GEWINN))

SPALTEN = ['Aktion','Format','Artikel Nr','Titel','Hersteller','Marke','Beschreibung','Zustand','Menge','Preis',
           'Kategorie Nr','MwSt','DHLPacket_nat','Bild URL','Ausverkauft','Freigeschaltet']

ANGEHOBEN = [0]   # Zaehler: wie viele Preise wegen MIN_GEWINN erhoeht wurden

def verkaufspreis(ek):
    ek = D(str(ek))
    kosten = ek * (1 + MWST_EINKAUF) + VERSAND_KOSTEN
    preis = (kosten / (1 - HOOD_GEBUEHR - ZIEL_MARGE)).quantize(D("0.01"), ROUND_HALF_UP)
    if MIN_GEWINN > 0:                    # Gewinn = Preis*(1-Gebuehr) - Kosten; muss mindestens MIN_GEWINN sein
        minimum = ((kosten + MIN_GEWINN) / (1 - HOOD_GEBUEHR)).quantize(D("0.01"), ROUND_HALF_UP)
        if minimum > preis:
            ANGEHOBEN[0] += 1
            preis = minimum
    return preis

def latin1(s):
    """Text so umwandeln, dass er in ISO-8859-1 passt (Hood-Vorgabe)."""
    s = s.replace("’", "'").replace("‘", "'").replace("–", "-").replace("“", '"').replace("”", '"')
    out = []
    for ch in s:
        try: ch.encode("latin-1"); out.append(ch)
        except UnicodeEncodeError:
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode() or "?")
    return "".join(out)

def ascii_html(s):
    return s.encode("ascii", "xmlcharrefreplace").decode("ascii")

def beschreibung(name, groesse, sku, marke):
    n, s, k, m = html.escape(name), html.escape(groesse), html.escape(sku), html.escape(marke)
    t = f'''<div style="max-width:720px;margin:0 auto;font-family:Arial,Helvetica,sans-serif;color:#222;line-height:1.5;border:1px solid #e3e3e3;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#111;color:#fff;"><tr><td style="padding:22px 26px;">
<div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#f5b301;font-weight:bold;">Original Sneaker &middot; Neuware</div>
<h1 style="margin:8px 0 0 0;font-size:24px;line-height:1.25;color:#fff;font-weight:bold;">{n}</h1>
</td></tr></table>
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f7f7f7;"><tr>
<td align="center" style="padding:18px 8px;border-right:1px solid #e3e3e3;width:33%;"><div style="font-size:11px;color:#777;text-transform:uppercase;letter-spacing:1px;">Gr&ouml;&szlig;e (EU)</div><div style="font-size:26px;font-weight:bold;color:#111;">{s}</div></td>
<td align="center" style="padding:18px 8px;border-right:1px solid #e3e3e3;width:33%;"><div style="font-size:11px;color:#777;text-transform:uppercase;letter-spacing:1px;">Zustand</div><div style="font-size:26px;font-weight:bold;color:#111;">Neu</div></td>
<td align="center" style="padding:18px 8px;width:34%;"><div style="font-size:11px;color:#777;text-transform:uppercase;letter-spacing:1px;">Versand</div><div style="font-size:26px;font-weight:bold;color:#1a7f37;">Gratis</div></td>
</tr></table>
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td style="padding:24px 26px 8px 26px;">
<h2 style="margin:0 0 10px 0;font-size:17px;color:#111;border-left:4px solid #f5b301;padding-left:10px;">Produktdetails</h2>
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:14px;">
<tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#777;width:38%;">Modell</td><td style="padding:8px 0;border-bottom:1px solid #eee;font-weight:bold;">{n}</td></tr>
<tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#777;">Artikelnummer</td><td style="padding:8px 0;border-bottom:1px solid #eee;">{k}</td></tr>
<tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#777;">Schuhgr&ouml;&szlig;e</td><td style="padding:8px 0;border-bottom:1px solid #eee;">EU {s}</td></tr>
<tr><td style="padding:8px 0;border-bottom:1px solid #eee;color:#777;">Zustand</td><td style="padding:8px 0;border-bottom:1px solid #eee;">Neu, ungetragen, im Originalkarton</td></tr>
<tr><td style="padding:8px 0;color:#777;">Hersteller</td><td style="padding:8px 0;">{m}</td></tr>
</table></td></tr></table>
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td style="padding:16px 26px 8px 26px;">
<h2 style="margin:0 0 10px 0;font-size:17px;color:#111;border-left:4px solid #f5b301;padding-left:10px;">Das bekommst du</h2>
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-size:14px;">
<tr><td style="padding:5px 0;width:24px;color:#1a7f37;font-weight:bold;">&#10003;</td><td style="padding:5px 0;">Original-Ware, neu und ungetragen</td></tr>
<tr><td style="padding:5px 0;color:#1a7f37;font-weight:bold;">&#10003;</td><td style="padding:5px 0;">Versand im Originalkarton</td></tr>
<tr><td style="padding:5px 0;color:#1a7f37;font-weight:bold;">&#10003;</td><td style="padding:5px 0;">Schneller Versand per DHL</td></tr>
<tr><td style="padding:5px 0;color:#1a7f37;font-weight:bold;">&#10003;</td><td style="padding:5px 0;">Versandkostenfrei innerhalb Deutschlands</td></tr>
</table></td></tr></table>
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td style="padding:16px 26px 22px 26px;">
<div style="background:#fff8e1;border:1px solid #f5d77a;padding:12px 14px;font-size:13px;color:#5c4a00;"><b>Hinweis zur Gr&ouml;&szlig;e:</b> Bitte pr&uuml;fe vor dem Kauf, ob die angegebene EU-Gr&ouml;&szlig;e zu dir passt. Bei Fragen schreib uns gern vor dem Kauf eine Nachricht.</div>
</td></tr></table>
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f2f2f2;border-top:1px solid #e3e3e3;"><tr><td style="padding:12px 26px;font-size:12px;color:#666;">Gem&auml;&szlig; &sect; 19 UStG wird keine Umsatzsteuer berechnet und daher nicht ausgewiesen (Kleinunternehmerregelung). Alle genannten Marken sind Eigentum der jeweiligen Inhaber.</td></tr></table>
</div>'''
    return ascii_html(t.replace("\n", ""))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def _abruf(url, token, per_header):
    h = {"Accept": "application/json", "User-Agent": UA}
    if per_header: h["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
        return json.load(r)

def feed_holen(token):
    """Erst per Header (empfohlen), bei 401 einmal per ?token= (zweiter Weg laut API-Doku)."""
    versuche = [("Authorization-Header", BASIS_URL, True),
                ("URL-Parameter", BASIS_URL + "?token=" + urllib.parse.quote(token, safe=""), False)]
    letzter = None
    for name, url, hdr in versuche:
        try:
            return _abruf(url, token, hdr)
        except urllib.error.HTTPError as e:
            try: text = e.read().decode("utf-8", "replace")[:300]
            except Exception: text = ""
            if e.code == 429: sys.exit("Zu viele Abrufe (Limit: 3 pro Minute). Bitte eine Minute warten und neu starten.")
            print(f"Weg '{name}': HTTP {e.code} {e.reason} - {text or '(leer)'}")
            letzter = e.code
            if e.code not in (401, 403): break
        except urllib.error.URLError as e:
            sys.exit(f"Keine Verbindung zur API: {e.reason}")
    if letzter == 401: sys.exit("Beide Wege 401: Der Server kennt diesen Token nicht. Falsch kopiert, ersetzt oder gesperrt -> neuen Token bei admin@goldensneakers.net anfordern.")
    if letzter == 403: sys.exit("403 = Zugriff verboten. Text oben an it@goldensneakers.net schicken.")
    sys.exit("API-Fehler.")

def bild_url(u):
    if not u: return ""
    return u if u.startswith("http") else MEDIA_URL + (u if u.startswith("/") else "/" + u)

BILD_ENDUNG = re.compile(r"\.(png|jpe?g|webp|gif)(\?.*)?$", re.I)
_bild_cache = {}

def bild_pruefen(url):
    """Adressen ohne Dateiendung: Ist es wirklich ein Bild? Folgt Weiterleitungen und gibt die endgueltige Bildadresse zurueck, sonst None."""
    if url in _bild_cache: return _bild_cache[url]
    ergebnis = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
        with urllib.request.urlopen(req, timeout=25) as r:
            if str(r.headers.get("Content-Type", "")).lower().startswith("image/"):
                ergebnis = r.geturl()
    except Exception:
        ergebnis = None
    _bild_cache[url] = ergebnis
    return ergebnis

def _sku_liste(datei):
    return {z.strip().upper() for z in open(datei, encoding="utf-8").read().split() if z.strip()} if os.path.exists(datei) else set()

def kategorie(name, sku=""):
    n = name.lower()
    if "(women" in n: return KATEGORIE_DAMEN or KATEGORIE_HERREN
    if any(k in n for k in ("(gs)", "(ps)", "(td)")):
        jungen, maedchen = KATEGORIE_JUNGEN or KATEGORIE_MAEDCHEN or KATEGORIE_HERREN, KATEGORIE_MAEDCHEN or KATEGORIE_JUNGEN or KATEGORIE_HERREN
        k = sku.upper()
        if k in _sku_liste(JUNGEN_DATEI): return jungen
        if k in _sku_liste(MAEDCHEN_DATEI) or any(w in n for w in MAEDCHEN_STICHWOERTER): return maedchen
        return jungen
    return KATEGORIE_HERREN

def groesse_text(eu):
    eu = str(eu).strip()
    return eu[:-2] if eu.endswith(".0") else eu

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed-datei", help="gespeicherte Feed-JSON statt API-Abruf (zum Testen)")
    a = ap.parse_args()
    if a.feed_datei:
        feed = json.load(open(a.feed_datei, encoding="utf-8"))
    else:
        if AUTOMATIK and not os.environ.get("GS_TOKEN"): sys.exit("GS_TOKEN fehlt (GitHub-Secret 'GS_TOKEN' anlegen).")
        token = os.environ.get("GS_TOKEN") or getpass.getpass("Golden-Sneakers-Token (Eingabe bleibt unsichtbar): ")
        token = re.sub(r"\s+", "", token)             # Leerzeichen/Zeilenumbrueche aus der Mail entfernen
        token = token.strip('"\'')
        if not token: sys.exit("Kein Token eingegeben.")
        if not AUTOMATIK: print(f"Token empfangen: {len(token)} Zeichen, beginnt mit '{token[:3]}', endet mit '{token[-3:]}'")
        if not token.startswith("eyJ") or len(token) < 100:
            sys.exit("Das sieht nicht wie der Token aus der Mail aus (er beginnt mit 'eyJ' und ist mehrere hundert Zeichen lang). Bitte nochmal komplett kopieren.")
        feed = feed_holen(token)
    pruefen = not a.feed_datei
    if not isinstance(feed, list): sys.exit("Unerwartete Antwort der API: " + str(feed)[:200])

    ohne_bild, zuordnung, zeilen, nr_neu, uebersprungen = [], {}, [], set(), {"andere Marke": 0, "kein Schuh": 0, "kein Preis/Bestand": 0}
    for p in feed:
        if str(p.get("brand_name", "")).strip().lower() not in MARKEN:
            uebersprungen["andere Marke"] += 1; continue
        eu = str(p.get("size_eu", "")).strip()
        if not re.fullmatch(r"\d+(\.\d+)?( \d/\d)?", eu):          # Schuhgroessen; Kleidung/Caps (S, M, XL ...) fliegen raus
            uebersprungen["kein Schuh"] += 1; continue
        try: ek = D(str(p["offer_price"])); menge = int(p["available_quantity"])
        except (KeyError, ValueError, ArithmeticError): ek, menge = None, 0
        if not ek or ek <= 0 or menge < 1:
            uebersprungen["kein Preis/Bestand"] += 1; continue
        if MAX_MENGE: menge = min(menge, MAX_MENGE)
        g = groesse_text(eu)
        sku = str(p["sku"]).strip()
        name = latin1(str(p.get("product_name") or p.get("name") or sku).strip())
        nr = re.sub(r"[ /]", "_", f"{sku}-{g}")
        if len(nr) > 20: print(f"Hinweis: Artikel Nr laenger als 20 Zeichen (Hood-Limit): {nr}"); nr = nr[:20]
        bilder = [bild_url(p.get("image_full_url") or p.get("image"))] + [bild_url(x if isinstance(x, str) else (x or {}).get("image_full_url", "")) for x in (p.get("additional_images") or [])]
        bilder = [b for b in bilder if b]
        if pruefen:
            bilder = [b if BILD_ENDUNG.search(b) else bild_pruefen(b) for b in bilder]
            bilder = [b for b in bilder if b]
        bilder = bilder[:MAX_BILDER]
        if not bilder:                       # Hood verlangt mindestens ein gueltiges Bild
            ohne_bild.append(f"{nr} | {name}"); continue
        nr_neu.add(nr)
        zuordnung[nr] = {'sku': sku, 'size_us': str(p.get('size_us', '')).strip(), 'size_id': p.get('id')}
        zeilen.append({'Aktion': 'Synchronize', 'Format': '7', 'Artikel Nr': nr, 'Titel': f"{name} - Gr. {g}"[:100],
                       'Hersteller': latin1(str(p.get('brand_name') or name.split()[0]).strip()), 'Marke': latin1(str(p.get('brand_name') or name.split()[0]).strip()),
                       'Beschreibung': beschreibung(name, g, sku, latin1(str(p.get('brand_name') or '').strip() or name.split()[0])), 'Zustand': '6', 'Menge': str(menge),
                       'Preis': str(verkaufspreis(ek)).replace(".", ","), 'Kategorie Nr': kategorie(name, sku), 'MwSt': MWST_SPALTE,
                       'DHLPacket_nat': '0,00', 'Bild URL': "*".join(bilder), 'Ausverkauft': 'hide', 'Freigeschaltet': '1'})
    zeilen.sort(key=lambda r: r['Artikel Nr'])
    if not KATEGORIE_HERREN.strip().isdigit():
        sys.exit("Bitte oben im Skript KATEGORIE_HERREN (und DAMEN/KINDER) mit gueltigen Hood-Kategorienummern ausfuellen.")

    aktive = len(zeilen)
    if AUTOMATIK:
        # Hood holt sich nur Aenderungen: Artikel, die der Lieferant nicht mehr hat, bleiben mit Menge 0 in der Datei (statt geloescht zu werden).
        alt = list(csv.DictReader(open(AUSGABE, encoding="iso-8859-1", newline=""), delimiter=";")) if os.path.exists(AUSGABE) else []
        aktiv_alt = sum(1 for r in alt if r.get("Menge", "0") not in ("0", ""))
        if not zeilen or (aktiv_alt >= 20 and len(zeilen) < 0.5 * aktiv_alt):
            sys.exit(f"SICHERHEITSSTOPP: nur {len(zeilen)} Artikel im Feed, zuletzt waren es {aktiv_alt}. Datei wird NICHT veroeffentlicht (sonst wuerde Hood alles auf ausverkauft setzen).")
        heute = date.today()
        gesehen = json.load(open(GESEHEN_DATEI, encoding="utf-8")) if os.path.exists(GESEHEN_DATEI) else {}
        for r in zeilen: gesehen[r["Artikel Nr"]] = heute.isoformat()
        jetzt = {r["Artikel Nr"] for r in zeilen}
        behalten = 0
        for r in alt:
            nr = r["Artikel Nr"]
            if nr in jetzt: continue
            zuletzt = date.fromisoformat(gesehen.setdefault(nr, heute.isoformat()))
            if (heute - zuletzt).days <= BEHALTEN_TAGE:
                r["Menge"] = "0"; zeilen.append(r); behalten += 1
            else: gesehen.pop(nr, None)
        json.dump(gesehen, open(GESEHEN_DATEI, "w", encoding="utf-8"), indent=0)
        zeilen.sort(key=lambda r: r["Artikel Nr"])
        print(f"Automatik: {aktive} lieferbare Artikel, {behalten} nicht mehr lieferbare mit Menge 0 behalten")
    with open(AUSGABE, "w", encoding="iso-8859-1", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SPALTEN, delimiter=";", quoting=csv.QUOTE_ALL); w.writeheader(); w.writerows(zeilen)

    # Artikel, die beim letzten Lauf im Feed waren und jetzt fehlen (ausverkauft) -> Loesch-Datei
    alt = set(open(ZUSTAND_DATEI, encoding="utf-8").read().split()) if os.path.exists(ZUSTAND_DATEI) else set()
    weg = [] if AUTOMATIK else sorted(alt - nr_neu)
    if weg:
        with open(ENTFERNT, "w", encoding="iso-8859-1", newline="") as f:
            w = csv.DictWriter(f, fieldnames=['Aktion', 'Artikel Nr'], delimiter=";", quoting=csv.QUOTE_ALL)
            w.writeheader(); w.writerows({'Aktion': 'Delete', 'Artikel Nr': n} for n in weg)
    if not AUTOMATIK: open(ZUSTAND_DATEI, "w", encoding="utf-8").write("\n".join(sorted(nr_neu)))
    alte = json.load(open(ZUORDNUNG_DATEI, encoding="utf-8")) if os.path.exists(ZUORDNUNG_DATEI) else {}
    alte.update(zuordnung)                                   # fuer das Bestell-Skript (Artikel Nr -> SKU + US-Groesse)
    json.dump(alte, open(ZUORDNUNG_DATEI, "w", encoding="utf-8"), indent=1)

    from collections import Counter
    print(f"{len(zeilen)} Schuhgroessen -> {AUSGABE}  ({dict(Counter(r['Marke'] for r in zeilen))})")
    print("Uebersprungen:", ", ".join(f"{k}: {v}" for k, v in uebersprungen.items()))
    kinder = sorted({(r['Artikel Nr'].rsplit('-', 1)[0], r['Titel'].rsplit(' - Gr.', 1)[0], r['Kategorie Nr']) for r in zeilen
                     if any(k in r['Titel'].lower() for k in ('(gs)', '(ps)', '(td)'))})
    if kinder:
        open(KINDER_LISTE, "w", encoding="utf-8").write("SKU | Modell | Kategorie (Jungen=%s, Maedchen=%s)\n" % (KATEGORIE_JUNGEN, KATEGORIE_MAEDCHEN) +
            "\n".join(f"{a} | {b} | {'Maedchen' if c == KATEGORIE_MAEDCHEN != KATEGORIE_JUNGEN else 'Jungen'}" for a, b, c in kinder))
        print(f"{len(kinder)} Kinder-Modelle -> {KINDER_LISTE} (Zuordnung pruefen!)")
    if ohne_bild:
        open("ohne_gueltiges_bild.txt", "w", encoding="utf-8").write("\n".join(ohne_bild))
        print(f"{len(ohne_bild)} Artikel NICHT exportiert, weil kein gueltiges Bild (Liste: ohne_gueltiges_bild.txt)")
    if MIN_GEWINN > 0: print(f"Mindestgewinn {MIN_GEWINN} EUR pro Paar: bei {ANGEHOBEN[0]} Artikeln wurde der Preis dafuer erhoeht")
    if weg: print(f"{len(weg)} Artikel nicht mehr im Feed -> {ENTFERNT} (bei Hood hochladen, damit sie nicht weiter verkauft werden)")

if __name__ == "__main__":
    main()
