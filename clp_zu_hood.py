#!/usr/bin/env python3
"""CLP-Katalog -> Hood-CSV (Format 7). Laeuft stuendlich per GitHub Actions.
Einstellungen kommen aus Umgebungsvariablen (GitHub: Settings > Secrets and variables > Actions)."""
import csv, io, os, sys, json, re, urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

def _env(name, standard):
    v = os.environ.get(name, "")
    return v.strip() if v and v.strip() else standard

CLP_URL        = _env("CLP_URL", "")                     # Adresse der CLP-Reseller-CSV (als Secret speichern!)
CLP_DATEI      = _env("CLP_DATEI", "")                   # alternativ: lokale Datei (zum Testen)
KATEGORIEN     = [x.strip().lower() for x in _env("CLP_KATEGORIEN", "Sonstiges").split(",") if x.strip()]
RABATT         = D(_env("RABATT", "0").replace(",", "."))        # Haendlerrabatt in % auf den Datei-Preis
AUFSCHLAG      = D(_env("AUFSCHLAG", "31").replace(",", ".")) / 100  # % auf (Einkauf + Versand)
HOOD_GEBUEHR   = D(_env("HOOD_GEBUEHR", "0.06"))
MIN_GEWINN     = D(_env("MIN_GEWINN", "6").replace(",", "."))   # Euro, nach Hood-Gebuehr
PREIS_ENDUNG   = _env("PREIS_ENDUNG", "95")                      # Preise enden auf ,95
MAX_MENGE      = int(_env("MAX_MENGE", "15"))
MIN_BESTAND    = int(_env("MIN_BESTAND", "5"))                   # darunter gilt der Artikel als nicht lieferbar
MAX_VERSAND    = D(_env("MAX_VERSAND", "6"))                     # nur Paketversand (5,77 EUR); Spedition 23,66/47,31 wird ausgelassen
PREISVORSCHLAEGE = _env("PREISVORSCHLAEGE", "0")
VOLL_WOCHENTAG = _env("VOLL_WOCHENTAG", "6")                     # Sonntag: alle Artikel senden
VOLL_UHR       = _env("VOLL_UHR", "3")                           # UTC
SNEAKER_LOESCHEN = _env("SNEAKER_LOESCHEN", "0") == "1"          # einmalig: alte Sneaker-Artikel bei Hood loeschen

AUSGABE   = "hood_import_jordan_live.csv"    # Dateiname unveraendert, damit der Hood-Abruf nicht neu eingerichtet werden muss
STAND     = "clp_stand.csv"
OHNE_KAT  = "ohne_kategorie.txt"
SNEAKER_STAND = "hood_vollstand.csv"
SNEAKER_MARKE = "sneaker_geloescht.json"
KAT_DATEI = "kategorien.txt"
KOPF = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hood_kopf.txt"), encoding="utf-8").read().strip().split(";")

def schoen(preis):
    cent = D("0." + PREIS_ENDUNG.zfill(2)[-2:])
    p = preis.quantize(D("1"), rounding="ROUND_FLOOR") + cent
    return p if p >= preis else p + 1

def num(s):
    s = (s or "").strip().replace(",", ".")
    try: return D(s)
    except Exception: return D(0)

def verkaufspreis(ek, versand):
    kosten = ek * (1 - RABATT / 100) + versand
    preis = kosten * (1 + AUFSCHLAG)
    minimum = (kosten + MIN_GEWINN) / (1 - HOOD_GEBUEHR)
    return schoen(max(preis, minimum))

def fmt(d): return f"{d:.2f}".replace(".", ",")

def sauber(s):
    for a, b in {"–": "-", "—": "-", "„": '"', "“": '"', "”": '"', "’": "'", "€": "EUR"}.items(): s = s.replace(a, b)
    return s

def kategorien_laden():
    pfad = os.path.join(os.path.dirname(os.path.abspath(__file__)), KAT_DATEI)
    regeln = []
    for z in open(pfad, encoding="utf-8").read().splitlines():
        z = z.split("#")[0].strip()
        if "|" in z:
            k, n = z.rsplit("|", 1)
            if k.strip() and n.strip().isdigit(): regeln.append((k.strip().lower(), n.strip()))
    return regeln

def katalog_laden():
    if CLP_DATEI: roh = open(CLP_DATEI, "rb").read()
    else:
        if not CLP_URL: sys.exit("CLP_URL fehlt (Secret in GitHub anlegen).")
        roh = urllib.request.urlopen(urllib.request.Request(CLP_URL, headers={"User-Agent": "hood-feed"}), timeout=120).read()
    text = roh.decode("iso-8859-1")
    csv.field_size_limit(10**8)
    zeilen = list(csv.DictReader(io.StringIO(text, newline=""), delimiter=";"))
    if len(zeilen) < 1000 or "clp_item_no" not in zeilen[0]:
        sys.exit(f"SICHERHEITSSTOPP: CLP-Datei sieht falsch aus ({len(zeilen)} Zeilen). Hood-Datei bleibt unveraendert.")
    return zeilen

def hood_zeile(c, kat):
    nr = "CLP-" + c["clp_item_no"]
    farbe = (c.get("color") or "").strip()
    titel = sauber(c["product_name"].strip() + (f" ({farbe})" if farbe else ""))[:100]
    bilder = [c.get(f"picture_url_{i}", "").strip() for i in range(1, 11)]
    menge = min(int(c["quantity"] or 0), MAX_MENGE)
    o = {h: "" for h in KOPF}
    o.update({"Format": "7", "Aktion": "Synchronize", "Artikel Nr": nr, "EAN": c.get("ean", ""), "MPN": c["clp_item_no"],
        "Hersteller": "CLP Trading GmbH", "Sonderanfertigung": "0", "Titel": titel,
        "Untertitel": (f"Farbe: {farbe} | " if farbe else "") + "Schneller Versand aus Deutschland",
        "Beschreibung": c["description_de"], "Kategorie Nr": kat, "Kategorie Nr 2": "0",
        "Shop Kategorie": "0", "Shop Kategorie 2": "0", "Shop Kategorie 3": "0",
        "Menge": str(menge), "Zustand": "neu", "Preis": fmt(verkaufspreis(num(c["price_with_VAT_without_discount"]), num(c["delivery_costs_germany"]))),
        "MwSt": "0", "Zahlungsarten": "22", "DHLPacket_nat": "0,00", "Bild URL": "*".join(b for b in bilder if b),
        "Freigeschaltet": "1", "Preisvorschläge": PREISVORSCHLAEGE, "wiedereinstellen": "0", "ausverkauft": "hide",
        "Hersteller Name": "CLP Trading GmbH", "Hersteller Adresse": "Triftstraße 4", "Hersteller PLZ": "31832",
        "Hersteller Stadt": "Springe", "Hersteller Land": "DE", "Hersteller E-Mail": "info@clp.de"})
    return o

def lesen(pfad):
    if not os.path.exists(pfad): return {}
    with open(pfad, encoding="iso-8859-1", newline="") as f:
        return {r["Artikel Nr"]: r for r in csv.DictReader(f, delimiter=";")}

def schreiben(pfad, zeilen):
    with open(pfad, "w", encoding="iso-8859-1", newline="", errors="xmlcharrefreplace") as f:
        w = csv.DictWriter(f, fieldnames=KOPF, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader(); w.writerows(zeilen)

def main():
    jetzt = datetime.now(timezone.utc)
    regeln = kategorien_laden()
    katalog = katalog_laden()
    neu, ohne, uebersprungen = {}, {}, {"Lager": 0, "Spedition": 0, "Kategorie": 0}
    for c in katalog:
        name = c["product_name"]
        kat = next((n for k, n in regeln if k in name.lower()), None)
        gewuenscht = c["clp_item_category"].lower() in KATEGORIEN or kat is not None
        if not gewuenscht: continue
        if int(c["quantity"] or 0) < MIN_BESTAND: uebersprungen["Lager"] += 1; continue
        if num(c["delivery_costs_germany"]) > MAX_VERSAND or not c["delivery_costs_germany"]: uebersprungen["Spedition"] += 1; continue
        if kat is None:
            typ = re.sub(r"^(V2 |\d+er )", "", name).split(" ")[0]
            ohne[typ] = ohne.get(typ, 0) + 1; uebersprungen["Kategorie"] += 1; continue
        z = hood_zeile(c, kat); neu[z["Artikel Nr"]] = z

    alt = lesen(STAND)
    if alt and len(neu) < 0.5 * sum(1 for r in alt.values() if r["Menge"] not in ("0", "")) :
        sys.exit(f"SICHERHEITSSTOPP: nur {len(neu)} Artikel, zuletzt waren es mehr. Datei wird NICHT veroeffentlicht.")
    if not neu: sys.exit("SICHERHEITSSTOPP: keine Artikel ausgewaehlt (Kategorien/Stichwoerter pruefen).")

    stand = dict(neu)
    for nr, r in alt.items():                      # nicht mehr lieferbar -> Menge 0 (einmal senden, dann im Stand behalten)
        if nr not in neu:
            r = dict(r); r["Menge"] = "0"; stand[nr] = r
    voll = jetzt.weekday() == int(VOLL_WOCHENTAG) and jetzt.hour == int(VOLL_UHR)
    senden = sorted((r for nr, r in stand.items() if voll or alt.get(nr) != r), key=lambda r: r["Artikel Nr"])

    # einmalig: alte Sneaker-Angebote bei Hood loeschen
    loeschen = []
    if SNEAKER_LOESCHEN:
        if not os.path.exists(SNEAKER_MARKE) and os.path.exists(SNEAKER_STAND):
            with open(SNEAKER_STAND, encoding="iso-8859-1", newline="") as f:
                nrs = sorted({r["Artikel Nr"] for r in csv.DictReader(f, delimiter=";") if r.get("Artikel Nr")})
            json.dump({"nrs": nrs, "bis": (jetzt + timedelta(hours=4)).isoformat()}, open(SNEAKER_MARKE, "w"))
        if os.path.exists(SNEAKER_MARKE):
            m = json.load(open(SNEAKER_MARKE))
            if jetzt < datetime.fromisoformat(m["bis"]):
                loeschen = [dict({h: "" for h in KOPF}, **{"Format": "7", "Aktion": "Delete", "Artikel Nr": n}) for n in m["nrs"]]
    senden += loeschen
    if not senden: senden = [stand[sorted(stand)[0]]]      # Datei nie leer; bleibt gleich -> Hood importiert nichts

    schreiben(STAND, [stand[k] for k in sorted(stand)])
    schreiben(AUSGABE, senden)
    open(OHNE_KAT, "w", encoding="utf-8").write("Produkttyp | Anzahl Artikel   -> Hood-Kategorienummer in kategorien.txt eintragen\n" +
        "\n".join(f"{t} | {n}" for t, n in sorted(ohne.items(), key=lambda x: -x[1])) + "\n")
    print(f"{len(neu)} Artikel aktiv, {len(senden)} Zeilen in der Hood-Datei ({'Tagesabgleich' if voll else 'nur Aenderungen'}), "
          f"{len(loeschen)} Sneaker-Loeschungen; uebersprungen: {uebersprungen}")

if __name__ == "__main__": main()
