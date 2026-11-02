#!/usr/bin/env python3
"""Connexxion Groepsvervoer -> Home Assistant.

Logt in op het ouderportaal (connexxiongroepsvervoer.nl), haalt de geplande
ritten op en geeft één JSON-object terug voor een command_line-sensor.

Alleen standaardbibliotheek. Inloggegevens uit secrets.yaml:
    connexxion_username: 'jouw@email.nl'
    connexxion_password: 'wachtwoord'
"""
import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Amsterdam")
except Exception:  # pragma: no cover
    TZ = None

AUTH_URL = "https://api.transdev.nl/api/auth/auth/"
GT_URL = "https://grouptransportation-api.ptflex.net/api/gt/"
BASE_HEADERS = {
    "Accept": "application/json, text/plain",
    "Content-Type": "application/json",
    "x-region": "6",
    "x-origin": "GT",
    "x-tenant-id": "ce2538f9-7239-4c2c-b3cb-7f58fb5d5aad",
    "accept-language": "nl",
    "User-Agent": "HomeAssistant-Connexxion/1.0",
}
TIMEOUT = 20
DAGEN = ["ma", "di", "wo", "do", "vr", "za", "zo"]
GENEGEERD = {"cancel", "cancelByGenerator"}


class FoutMelding(Exception):
    pass


# ---------------------------------------------------------------- secrets
def _read_secrets_lines(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\s*(connexxion_[A-Za-z0-9_]+)\s*:\s?(.*?)\s*$", line)
            if m:
                val = m.group(2)
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                    val = val[1:-1]
                out[m.group(1)] = val
    return out


def read_secrets(path):
    try:
        import yaml

        class _L(yaml.SafeLoader):
            pass

        _L.add_multi_constructor("!", lambda loader, suffix, node: None)
        with open(path, encoding="utf-8") as fh:
            data = yaml.load(fh, Loader=_L) or {}
        if data.get("connexxion_username") and data.get("connexxion_password"):
            return data
    except Exception:
        pass
    return _read_secrets_lines(path)


# ---------------------------------------------------------------- http
def http(method, url, headers=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw) if raw.strip() else None
        except ValueError:
            return e.code, raw[:300]


# ---------------------------------------------------------------- auth
def _tokens_from(resp):
    try:
        st = resp["Data"]["user"]["sessionTokens"]
    except (KeyError, TypeError):
        return None
    acc = st.get("accessToken") or st.get("AccessToken")
    if not acc:
        return None
    return {
        "access": acc,
        "refresh": st.get("refreshToken") or st.get("RefreshToken"),
        "username": resp["Data"]["user"].get("username"),
        "exp": _jwt_exp(acc),
    }


def _jwt_exp(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return int(json.loads(base64.urlsafe_b64decode(part))["exp"])
    except Exception:
        return int(time.time()) + 1800


def login(user, pw):
    basic = base64.b64encode(f"{user}:{pw}".encode()).decode()
    code, resp = http("POST", AUTH_URL + "login",
                      {**BASE_HEADERS, "Authorization": "Basic " + basic})
    tok = _tokens_from(resp)
    if code != 200 or not tok:
        raise FoutMelding(f"Inloggen mislukt (HTTP {code}). Controleer e-mailadres en wachtwoord in secrets.yaml.")
    return tok


def refresh(tok):
    if not tok.get("refresh") or not tok.get("username"):
        return None
    code, resp = http("POST", AUTH_URL + "refreshuser", BASE_HEADERS,
                      {"RefreshToken": tok["refresh"], "Username": tok["username"]})
    new = _tokens_from(resp) if code == 200 else None
    if new and not new.get("refresh"):
        new["refresh"] = tok["refresh"]
    return new


def get_token(user, pw, cache_path):
    tok = None
    try:
        with open(cache_path, encoding="utf-8") as fh:
            tok = json.load(fh)
        if tok.get("user") != user:
            tok = None
    except Exception:
        tok = None
    if tok and tok.get("exp", 0) - 120 > time.time():
        return tok
    new = refresh(tok) if tok else None
    if not new:
        new = login(user, pw)
    new["user"] = user
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(new, fh)
        os.chmod(cache_path, 0o600)
    except Exception:
        pass
    return new


def gt_get(path, tok):
    code, resp = http("GET", GT_URL + path,
                      {**BASE_HEADERS, "Authorization": "Bearer " + tok["access"]})
    return code, resp


# ---------------------------------------------------------------- data
def _hm(s):
    try:
        return dt.datetime.fromisoformat(s[:19]).strftime("%H:%M")
    except Exception:
        return None


def _iso(s):
    """Lokale tijd van de API (zonder zone) -> ISO met Amsterdamse offset."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s[:19])
        return (d.replace(tzinfo=TZ) if TZ else d).isoformat()
    except Exception:
        return None


def _nu():
    return dt.datetime.now(TZ) if TZ else dt.datetime.now()


def _adres(loc):
    if not isinstance(loc, dict):
        return None
    straat = " ".join(x for x in [loc.get("street"), loc.get("housenumber")] if x)
    return ", ".join(x for x in [straat, loc.get("place")] if x) or None


def _naam(customer_name):
    # "Achternaam, Voornaam" -> "Voornaam"
    if customer_name and "," in customer_name:
        return customer_name.split(",", 1)[1].strip()
    return customer_name


def rit_samenvatting(t):
    datum = (t.get("tripBusinessDay") or t.get("pickupTime") or "")[:10]
    try:
        dag = DAGEN[dt.date.fromisoformat(datum).weekday()]
    except Exception:
        dag = None
    van = t.get("institutionLocationFrom") or t.get("customerLocationFrom") or _adres(t.get("locationFrom"))
    naar = t.get("institutionLocationTo") or t.get("customerLocationTo") or _adres(t.get("locationTo"))
    return {
        "datum": datum,
        "dag": dag,
        "ophalen": _hm(t.get("pickupTime") or ""),
        "aankomst": _hm(t.get("deliveryTime") or ""),
        "ophalen_iso": _iso(t.get("pickupTime")),
        "aankomst_iso": _iso(t.get("deliveryTime")),
        "van": van,
        "naar": naar,
        "van_adres": _adres(t.get("locationFrom")),
        "naar_adres": _adres(t.get("locationTo")),
        "richting": "naar_huis" if t.get("customerLocationTo") else ("van_huis" if t.get("customerLocationFrom") else None),
        "status": t.get("tripStatus"),
        "status_tekst": t.get("tripStatusDisplayText"),
        "trip_id": t.get("tripId"),
        "_business_day": t.get("tripBusinessDay"),
    }


def voeg_live_toe(rit, tok):
    q = urllib.request.quote(rit["_business_day"] or "")
    code, resp = gt_get(f"trips/{rit['trip_id']}/vehicles-position?tripBusinessday={q}&includeGeometry=false", tok)
    if code != 200 or not isinstance(resp, dict):
        return
    vl = resp.get("vehicleLocation") or {}
    rit["chauffeur"] = vl.get("driverName") or None
    rit["kenteken"] = vl.get("licensePlate") or None
    rit["voertuig_lat"] = vl.get("latitude")
    rit["voertuig_lon"] = vl.get("longitude")
    rit["voertuig_update"] = _iso(vl.get("lastGPSTimeStamp"))
    rit["live_ophalen"] = _hm(resp.get("estimatedPickupTime") or "")
    rit["live_aankomst"] = _hm(resp.get("estimatedDeliveryTime") or "")
    if resp.get("tripStatus"):
        rit["status"] = resp["tripStatus"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", default="/config/secrets.yaml")
    ap.add_argument("--klant", type=int, help="customerId; standaard de eerste gekoppelde reiziger")
    ap.add_argument("--dagen", type=int, default=14, help="aantal werkdagen vooruit")
    ap.add_argument("--cache", default=None)
    args = ap.parse_args()

    out = {"status": "fout", "fout": None,
           "bijgewerkt": _nu().isoformat(timespec="seconds")}
    try:
        sec = read_secrets(args.secrets)
        user, pw = sec.get("connexxion_username"), sec.get("connexxion_password")
        if not user or not pw:
            raise FoutMelding("connexxion_username/connexxion_password ontbreken in secrets.yaml")
        cache = args.cache or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".token.json")
        tok = get_token(user, pw, cache)

        klant = args.klant
        if not klant:
            code, me = gt_get("users/current", tok)
            if code == 401:  # token toch verlopen: opnieuw inloggen
                tok = login(user, pw)
                code, me = gt_get("users/current", tok)
            linked = (me or {}).get("linkedTravelers") if isinstance(me, dict) else None
            if not linked:
                raise FoutMelding(f"Geen gekoppelde reiziger gevonden (HTTP {code})")
            klant = linked[0]
            out["gekoppelde_reizigers"] = linked

        code, data = gt_get(f"customer/{klant}/trips?businessDaysInFuture={args.dagen}&businessDaysInPast=1", tok)
        if code != 200 or not isinstance(data, dict):
            raise FoutMelding(f"Ritten ophalen mislukt (HTTP {code})")

        trips = [t for t in data.get("trips", []) if t.get("tripStatus") not in GENEGEERD
                 and t.get("tripRecStatus", "active") == "active"]
        trips.sort(key=lambda t: t.get("pickupTime") or "")
        ritten = [rit_samenvatting(t) for t in trips]
        if trips:
            out["reiziger"] = _naam(trips[0].get("customerName"))
        out["klant_id"] = klant

        vandaag = _nu().date().isoformat()
        nu = _nu()
        ritten_vandaag = [r for r in ritten if r["datum"] == vandaag]
        for r in ritten_vandaag:
            voeg_live_toe(r, tok)
        toekomst = [r for r in ritten if r["datum"] > vandaag or
                    (r["datum"] == vandaag and r["aankomst_iso"]
                     and dt.datetime.fromisoformat(r["aankomst_iso"]) >= nu)]
        for r in ritten:
            r.pop("_business_day", None)

        out["vandaag"] = ritten_vandaag[0] if ritten_vandaag else None
        out["vandaag_alle"] = ritten_vandaag
        out["volgende"] = toekomst[0] if toekomst else None
        out["ritten"] = [r for r in ritten if r["datum"] >= vandaag]
        out["status"] = "ok"
    except FoutMelding as e:
        out["fout"] = str(e)
    except Exception as e:  # nooit crashen: HA moet altijd JSON krijgen
        out["fout"] = f"{type(e).__name__}: {e}"
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
