"""Serveur MCP SkyWatch (transport stdio).

Outils : geocoder, meteo_ciel, passages_iss, satellites_visibles, prochain_bon_passage,
planetes_visibles, etoiles_visibles.
Sources : Open-Meteo (géocodage + météo, sans clé) et CelesTrak (TLE, sans clé) ;
calculs orbitaux avec skyfield.

Attention : sur un serveur stdio, stdout est réservé au protocole MCP -> ne jamais faire de print().
"""
import logging
import time
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import numpy as np
from mcp.server.fastmcp import FastMCP
from skyfield import almanac
from skyfield.api import EarthSatellite, Loader, Star, wgs84
from skyfield.magnitudelib import planetary_magnitude

from etoiles_data import CONSTELLATIONS

HTTP_TIMEOUT = 15.0            # secondes, tous les appels HTTP externes
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"
TLE_TTL = 2 * 3600             # CelesTrak demande de ne pas re-télécharger trop souvent
MIN_ALT = 10.0                 # hauteur minimale (°) pour compter un passage
SUN_DARK = -6.0                # soleil sous -6° : ciel assez sombre pour voir un satellite
STEP_S = 10                    # pas d'échantillonnage d'un passage (secondes)

logging.getLogger("httpx").setLevel(logging.WARNING)   # stderr propre côté client
log = logging.getLogger("skywatch")

mcp = FastMCP("skywatch", log_level="WARNING")
_loader = Loader(str(DATA_DIR), verbose=False)
ts = _loader.timescale()
_eph = None

PLANETES = [("Mercure", "mercury barycenter"), ("Vénus", "venus barycenter"),
            ("Mars", "mars barycenter"), ("Jupiter", "jupiter barycenter"),
            ("Saturne", "saturn barycenter"), ("Uranus", "uranus barycenter"),
            ("Neptune", "neptune barycenter")]
MAG_OEIL_NU = 5.5              # magnitude limite à l'œil nu (ciel correct)
PLANETE_MIN_ALT = 5.0          # hauteur minimale (°) pour observer une planète
PLANETES_LUMINEUSES = {"Vénus", "Jupiter"}   # visibles dès le crépuscule (soleil sous -1°)
ETOILE_SOLEIL_MAX = -12.0      # soleil sous -12° : les étoiles brillantes ressortent
PHASES_LUNE = ["Nouvelle lune", "Premier croissant", "Premier quartier", "Gibbeuse croissante",
               "Pleine lune", "Gibbeuse décroissante", "Dernier quartier", "Dernier croissant"]

CARDINAUX = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


# ---------------------------------------------------------------- utilitaires

def _cardinal(az: float) -> str:
    return CARDINAUX[int((az % 360) / 22.5 + 0.5) % 16]


def _get_json(url: str, params: dict) -> dict:
    r = httpx.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _ephemeris():
    """Éphéméride du Soleil (de421.bsp, ~17 Mo, téléchargée une seule fois dans data/)."""
    global _eph
    if _eph is None:
        _eph = _loader("de421.bsp")
    return _eph


def _fetch_tle(cache_name: str, params: dict) -> list[tuple[str, str, str]]:
    """TLE CelesTrak avec cache disque ; retombe sur un cache périmé si le réseau échoue."""
    path = DATA_DIR / f"tle_{cache_name}.txt"
    if path.exists() and time.time() - path.stat().st_mtime < TLE_TTL:
        return _parse_tle(path.read_text())
    try:
        r = httpx.get(CELESTRAK_URL, params={**params, "FORMAT": "TLE"}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        sats = _parse_tle(r.text)
        if not sats:
            raise httpx.HTTPError(f"réponse CelesTrak inattendue : {r.text[:80]!r}")
        path.write_text(r.text)
        return sats
    except httpx.HTTPError:
        if path.exists():
            return _parse_tle(path.read_text())
        raise


def _parse_tle(text: str) -> list[tuple[str, str, str]]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    sats = []
    for i in range(1, len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            sats.append((lines[i - 1].strip(), lines[i], lines[i + 1]))
    return sats


def _iss() -> EarthSatellite:
    name, l1, l2 = _fetch_tle("iss", {"CATNR": 25544})[0]
    return EarthSatellite(l1, l2, name, ts)


def _meteo_horaire(lat: float, lon: float, jours: int) -> dict:
    """Météo horaire locale : {'fuseau': str, 'heures': {'YYYY-MM-DDTHH:00': {...}}}."""
    data = _get_json(FORECAST_URL, {
        "latitude": lat, "longitude": lon,
        "hourly": "cloud_cover,visibility,is_day",
        "timezone": "auto",
        "forecast_days": max(1, min(jours + 1, 16)),
    })
    h = data["hourly"]
    heures = {
        t: {"nebulosite_pct": c, "visibilite_km": round(v / 1000, 1) if v is not None else None,
            "nuit": d == 0}
        for t, c, v, d in zip(h["time"], h["cloud_cover"], h["visibility"], h["is_day"])
    }
    return {"fuseau": data["timezone"], "heures": heures}


def _etat_ciel(nebulosite: float | None) -> str:
    if nebulosite is None:
        return "inconnu"
    if nebulosite < 20:
        return "dégagé"
    if nebulosite < 50:
        return "peu nuageux"
    if nebulosite < 80:
        return "nuageux"
    return "couvert"


def _score(hauteur: float, nebulosite: float | None, visible: bool) -> float | None:
    """Score d'observation /10 : ciel dégagé (60 %+) x hauteur du passage. 0 si invisible."""
    if not visible:
        return 0.0
    if nebulosite is None:
        return None
    ciel = 1 - nebulosite / 100
    haut = 0.4 + 0.6 * min(hauteur, 80) / 80
    return round(10 * ciel * haut, 1)


def _fmt(t, tz: ZoneInfo, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return t.astimezone(tz).strftime(fmt)


# ------------------------------------------------------- calcul des passages

def _calcul_passages(lat: float, lon: float, tz: ZoneInfo, jours: int,
                     meteo: dict | None = None, avec_points: bool = False) -> list[dict]:
    """Passages de l'ISS au-dessus de MIN_ALT sur `jours` jours, avec visibilité et score."""
    obs = wgs84.latlon(lat, lon)
    sat = _iss()
    eph = _ephemeris()
    earth, sun = eph["earth"], eph["sun"]

    t0 = ts.now()
    t1 = ts.from_datetime(t0.utc_datetime() + timedelta(days=jours))
    times, events = sat.find_events(obs, t0, t1, altitude_degrees=MIN_ALT)

    # Regroupe les événements (0 lever, 1 culmination, 2 coucher) ; un passage déjà en cours
    # au départ commence à t0.
    fenetres, debut = [], None
    for t, e in zip(times, events):
        if e == 0:
            debut = t
        elif e == 1 and debut is None:
            debut = t0
        elif e == 2:
            fenetres.append((debut if debut is not None else t0, t))
            debut = None

    passages = []
    for rise, sett in fenetres:
        duree = max(1, int((sett.tt - rise.tt) * 86400))
        n = max(2, duree // STEP_S + 1)
        tt = ts.linspace(rise, sett, n)
        alt, az, _ = (sat - obs).at(tt).altaz()
        sun_alt, _, _ = (earth + obs).at(tt).observe(sun).apparent().altaz()
        eclaire = sat.at(tt).is_sunlit(eph)
        sombre = sun_alt.degrees < SUN_DARK
        visible_mask = eclaire & sombre

        i_max = int(alt.degrees.argmax())
        hauteur_max = float(alt.degrees[i_max])
        visible = bool(visible_mask.any())
        haut_ref = float(alt.degrees[visible_mask].max()) if visible else hauteur_max

        p = {
            "debut": _fmt(rise, tz),
            "fin": _fmt(sett, tz),
            "duree_s": duree,
            "hauteur_max_deg": round(hauteur_max),
            "direction": f"de {_cardinal(az.degrees[0])} vers {_cardinal(az.degrees[-1])}",
            "direction_hauteur_max": _cardinal(az.degrees[i_max]),
            "visible_oeil_nu": visible,
        }
        if not visible:
            if not eclaire.any():
                p["raison_non_visible"] = "ISS dans l'ombre de la Terre"
            elif not sombre.any():
                p["raison_non_visible"] = "ciel trop lumineux (jour ou crépuscule)"
            else:
                p["raison_non_visible"] = "ISS éclairée seulement quand le ciel est encore clair"

        neb = None
        if meteo:
            cle = tt[i_max].astimezone(tz).strftime("%Y-%m-%dT%H:00")
            m = meteo["heures"].get(cle)
            neb = m["nebulosite_pct"] if m else None
        p["nebulosite_pct"] = neb
        p["score_sur_10"] = _score(haut_ref, neb, visible)
        if visible and neb is not None and neb >= 80:
            p["remarque"] = "passage visible en théorie, mais ciel couvert (nuages) : probablement masqué"

        if avec_points:
            p["points"] = [
                {"az": round(float(a), 1), "alt": round(float(h), 1),
                 "t": _fmt(tt[i], tz, "%H:%M:%S"), "visible": bool(visible_mask[i])}
                for i, (a, h) in enumerate(zip(az.degrees, alt.degrees))
            ]
        passages.append(p)
    return passages


def _tz(fuseau: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(fuseau)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def trajectoire_iss(lat: float, lon: float, fuseau: str) -> dict:
    """Prochain passage (visible si possible) avec sa trajectoire, pour la page /carte.
    Fonction interne, pas un outil MCP."""
    tz = _tz(fuseau)
    if tz is None:
        return {"erreur": f"Fuseau inconnu : {fuseau}"}
    try:
        passages = _calcul_passages(lat, lon, tz, 5, avec_points=True)
    except httpx.HTTPError:
        log.exception("trajectoire_iss : CelesTrak indisponible")
        return {"erreur": "Données de suivi des satellites momentanément indisponibles."}
    if not passages:
        return {"erreur": "Aucun passage de l'ISS dans les 5 prochains jours"}
    visibles = [p for p in passages if p["visible_oeil_nu"]]
    return {"passage": (visibles or passages)[0]}


# --------------------------------------------------------------- outils MCP

@mcp.tool()
def geocoder(ville: str) -> dict:
    """Convertit un nom de ville en latitude, longitude et fuseau horaire (IANA).
    À appeler en premier : lat, lon et fuseau servent aux autres outils."""
    try:
        data = _get_json(GEOCODING_URL, {"name": ville, "count": 1, "language": "fr", "format": "json"})
    except httpx.HTTPError:
        log.exception("geocoder : Open-Meteo indisponible")
        return {"erreur": "Service de géocodage momentanément indisponible."}
    res = data.get("results")
    if not res:
        return {"erreur": f"Ville introuvable : {ville}"}
    p = res[0]
    return {"nom": p["name"], "region": p.get("admin1"), "pays": p.get("country"),
            "lat": p["latitude"], "lon": p["longitude"], "fuseau": p["timezone"]}


@mcp.tool()
def meteo_ciel(lat: float, lon: float, heures: int = 12) -> dict:
    """État du ciel heure par heure à partir de maintenant : nébulosité (%), visibilité (km),
    nuit ou jour. Indique aussi la meilleure heure de la nuit (ciel le plus dégagé).
    `heures` : nombre d'heures à couvrir (1 à 48)."""
    heures = max(1, min(int(heures), 48))
    try:
        meteo = _meteo_horaire(lat, lon, 3)
    except httpx.HTTPError:
        log.exception("meteo_ciel : Open-Meteo indisponible")
        return {"erreur": "Service météo momentanément indisponible."}
    tz = _tz(meteo["fuseau"])
    maintenant = datetime.now(tz).strftime("%Y-%m-%dT%H:00")
    suite = [(t, m) for t, m in meteo["heures"].items() if t >= maintenant][:heures]
    liste = [{"heure": t.replace("T", " "), "nebulosite_pct": m["nebulosite_pct"],
              "visibilite_km": m["visibilite_km"], "nuit": m["nuit"],
              "ciel": _etat_ciel(m["nebulosite_pct"])} for t, m in suite]
    nuits = [x for x in liste if x["nuit"] and x["nebulosite_pct"] is not None]
    meilleure = min(nuits, key=lambda x: (x["nebulosite_pct"], -(x["visibilite_km"] or 0)), default=None)
    return {"fuseau": meteo["fuseau"], "heures": liste,
            "meilleure_heure_nuit": meilleure["heure"] if meilleure else None}


@mcp.tool()
def passages_iss(lat: float, lon: float, fuseau: str, jours: int = 3) -> dict:
    """Prochains passages de l'ISS au-dessus de la position (hauteur > 10°) : heure locale,
    durée, hauteur max, direction, visible à l'œil nu (ISS éclairée + ciel sombre), nébulosité
    prévue à ce moment et score d'observation /10. `fuseau` vient de geocoder ; `jours` : 1 à 7."""
    jours = max(1, min(int(jours), 7))
    tz = _tz(fuseau)
    if tz is None:
        return {"erreur": f"Fuseau inconnu : {fuseau}"}
    try:
        try:
            meteo = _meteo_horaire(lat, lon, jours)
        except httpx.HTTPError:
            meteo = None    # les passages restent utiles sans météo (score absent)
        passages = _calcul_passages(lat, lon, tz, jours, meteo)
    except httpx.HTTPError:
        log.exception("passages_iss : source indisponible")
        return {"erreur": "Données de suivi de l'ISS momentanément indisponibles."}
    return {"fuseau": fuseau, "nombre_de_passages": len(passages), "passages": passages[:12],
            "meteo_disponible": meteo is not None}


@mcp.tool()
def prochain_bon_passage(lat: float, lon: float) -> dict:
    """Meilleur créneau d'observation de l'ISS sur les 7 prochains jours (visible à l'œil nu,
    meilleur score /10 = ciel dégagé + passage haut), avec deux alternatives."""
    try:
        meteo = _meteo_horaire(lat, lon, 7)
        tz = _tz(meteo["fuseau"])
        passages = _calcul_passages(lat, lon, tz, 7, meteo)
    except httpx.HTTPError:
        log.exception("prochain_bon_passage : source indisponible")
        return {"erreur": "Données momentanément indisponibles."}
    visibles = sorted((p for p in passages if p["visible_oeil_nu"]),
                      key=lambda p: p["score_sur_10"] or 0, reverse=True)
    if not visibles:
        return {"fuseau": meteo["fuseau"],
                "message": "Aucun passage de l'ISS visible à l'œil nu dans les 7 prochains jours"}
    return {"fuseau": meteo["fuseau"], "meilleur": visibles[0], "alternatives": visibles[1:3],
            "passages_visibles_sur_7_jours": len(visibles)}


@mcp.tool()
def satellites_visibles(lat: float, lon: float) -> dict:
    """Satellites actuellement au-dessus de l'horizon (hauteur > 10°) : stations (ISS…),
    satellites brillants (Hubble…) et Starlink. Indique lesquels sont éclairés par le Soleil et
    visibles à l'œil nu. Les Starlink sont comptés mais généralement trop faibles à l'œil nu."""
    obs = wgs84.latlon(lat, lon)
    now = ts.now()
    eph = _ephemeris()
    sun_alt = (eph["earth"] + obs).at(now).observe(eph["sun"]).apparent().altaz()[0].degrees
    sombre = bool(sun_alt < SUN_DARK)

    vus, deja = [], set()
    starlink_haut = starlink_eclaires = 0
    try:
        groupes = [("stations", _fetch_tle("stations", {"GROUP": "stations"})),
                   ("brillants", _fetch_tle("visual", {"GROUP": "visual"})),
                   ("starlink", _fetch_tle("starlink", {"GROUP": "starlink"}))]
    except httpx.HTTPError:
        log.exception("satellites_visibles : CelesTrak indisponible")
        return {"erreur": "Données de suivi des satellites momentanément indisponibles."}

    for groupe, sats in groupes:
        for name, l1, l2 in sats:
            sat = EarthSatellite(l1, l2, name, ts)
            if sat.model.satnum in deja:
                continue
            try:
                alt, az, dist = (sat - obs).at(now).altaz()
                if alt.degrees < MIN_ALT:
                    continue
                eclaire = bool(sat.at(now).is_sunlit(eph))
            except Exception:   # TLE d'un satellite ayant rentré dans l'atmosphère, etc.
                continue
            deja.add(sat.model.satnum)
            if groupe == "starlink":
                starlink_haut += 1
                starlink_eclaires += eclaire
                continue
            vus.append({
                "nom": name, "groupe": groupe,
                "hauteur_deg": round(float(alt.degrees)),
                "direction": _cardinal(az.degrees),
                "distance_km": round(float(dist.km)),
                "eclaire_par_soleil": eclaire,
                "visible_oeil_nu": eclaire and sombre,
            })
    vus.sort(key=lambda s: (not s["visible_oeil_nu"], -s["hauteur_deg"]))
    return {
        "ciel_assez_sombre": sombre,
        "hauteur_soleil_deg": round(float(sun_alt)),
        "satellites": vus[:15],
        "starlink_au_dessus": starlink_haut,
        "starlink_eclaires": starlink_eclaires,
        "remarque": "Starlink : généralement trop faible pour l'œil nu (sauf trains fraîchement lancés).",
    }


@mcp.tool()
def planetes_visibles(lat: float, lon: float, fuseau: str) -> dict:
    """Planètes (Mercure à Neptune) et Lune pour la prochaine nuit :
    meilleure heure d'observation, hauteur, direction, magnitude, visible à l'œil nu ou non
    (Uranus et Neptune demandent des jumelles). Pour les étoiles et constellations : etoiles_visibles.
    `fuseau` vient de geocoder."""
    tz = _tz(fuseau)
    if tz is None:
        return {"erreur": f"Fuseau inconnu : {fuseau}"}
    eph = _ephemeris()
    observer = eph["earth"] + wgs84.latlon(lat, lon)

    t0 = ts.now()
    tt = ts.tt_jd(t0.tt + np.arange(0, 24 * 6 + 1) / 144)       # 24 h, pas de 10 min
    sun_alt = observer.at(tt).observe(eph["sun"]).apparent().altaz()[0].degrees
    # Fenêtre d'observation : du crépuscule du soir à celui du matin (soleil sous -1°)
    fenetre = np.where(sun_alt < -1.0)[0]
    if fenetre.size == 0:
        return {"message": "Pas de nuit dans les prochaines 24 h à cet endroit."}
    debut = int(fenetre[0])
    fin = debut
    while fin + 1 < len(tt) and sun_alt[fin + 1] < -1.0:
        fin += 1
    nuit = slice(debut, fin + 1)
    sun_nuit = sun_alt[nuit]

    resultats = []
    for nom, cle in PLANETES:
        alt, az, _ = observer.at(tt[nuit]).observe(eph[cle]).apparent().altaz()
        # une planète faible n'est observable qu'une fois le ciel vraiment sombre
        seuil = -1.0 if nom in PLANETES_LUMINEUSES else -4.0
        dispo = np.where(sun_nuit < seuil, alt.degrees, -90.0)
        i = int(dispo.argmax())
        haut = float(alt.degrees[i])
        mag = float(planetary_magnitude(observer.at(tt[nuit][i]).observe(eph[cle])))
        visible = dispo[i] >= PLANETE_MIN_ALT and mag <= MAG_OEIL_NU
        p = {"nom": nom, "meilleure_heure": _fmt(tt[nuit][i], tz), "hauteur_deg": round(haut),
             "direction": _cardinal(az.degrees[i]), "magnitude": round(mag, 1),
             "visible_oeil_nu": bool(visible)}
        if dispo[i] < PLANETE_MIN_ALT:
            p["remarque"] = "trop près de l'horizon (ou sous l'horizon) quand le ciel est assez sombre"
        elif mag > MAG_OEIL_NU:
            p["remarque"] = "trop faible à l'œil nu : jumelles ou télescope nécessaires"
        resultats.append(p)
    resultats.sort(key=lambda p: (not p["visible_oeil_nu"], p["magnitude"]))

    mid = (debut + fin) // 2
    phase = float(almanac.moon_phase(eph, tt[mid]).degrees)
    l_alt, l_az, _ = observer.at(tt[mid]).observe(eph["moon"]).apparent().altaz()
    return {
        "fuseau": fuseau,
        "crepuscule_du_soir": _fmt(tt[debut], tz),
        "crepuscule_du_matin": _fmt(tt[fin], tz),
        "planetes": resultats,
        "lune": {"phase": PHASES_LUNE[int((phase + 22.5) % 360 / 45)],
                 "illumination_pct": round((1 - np.cos(np.radians(phase))) / 2 * 100),
                 "hauteur_deg_milieu_de_nuit": round(float(l_alt.degrees)),
                 "direction": _cardinal(l_az.degrees)},
    }


@cache
def _etoiles() -> dict[str, list[tuple[str, float, Star]]]:
    return {
        cst: [(nom, mag, Star(ra_hours=ra / 15, dec_degrees=dec, ra_mas_per_year=pm_ra,
                              dec_mas_per_year=pm_de, parallax_mas=max(plx, 0.0)))
              for nom, _hip, mag, ra, dec, pm_ra, pm_de, plx in etoiles]
        for cst, etoiles in CONSTELLATIONS.items()
    }


@mcp.tool()
def etoiles_visibles(lat: float, lon: float, fuseau: str, heure: str = "") -> dict:
    """Constellations et étoiles brillantes au-dessus de l'horizon (Grande Ourse, Cassiopée,
    Orion, Cygne, Lyre… 25 constellations principales, étoiles de magnitude <= 3.7) : hauteur
    et direction où regarder, étoile principale. `fuseau` vient de geocoder. `heure` (HH:MM,
    heure locale, prochaine occurrence) : instant voulu, par ex. "23:00" ; par défaut le début
    de la nuit (ciel assez sombre). Ne couvre pas galaxies ni ciel profond ; ne tient compte
    ni de la pollution lumineuse ni des obstacles autour de l'observateur."""
    tz = _tz(fuseau)
    if tz is None:
        return {"erreur": f"Fuseau inconnu : {fuseau}"}
    eph = _ephemeris()
    observer = eph["earth"] + wgs84.latlon(lat, lon)

    if heure.strip():
        try:
            h = datetime.strptime(heure.strip(), "%H:%M")
        except ValueError:
            return {"erreur": f"Heure invalide : {heure!r} (format attendu HH:MM)"}
        maintenant = datetime.now(tz)
        cible = maintenant.replace(hour=h.hour, minute=h.minute, second=0, microsecond=0)
        if cible <= maintenant:
            cible += timedelta(days=1)
        t = ts.from_datetime(cible)
    else:
        t0 = ts.now()
        tt = ts.tt_jd(t0.tt + np.arange(0, 24 * 6 + 1) / 144)       # 24 h, pas de 10 min
        sun_alt = observer.at(tt).observe(eph["sun"]).apparent().altaz()[0].degrees
        sombre = np.where(sun_alt < ETOILE_SOLEIL_MAX)[0]
        if sombre.size == 0:
            return {"message": "Ciel jamais assez sombre dans les prochaines 24 h à cet endroit."}
        t = tt[int(sombre[0])]

    ici = observer.at(t)
    sun_h = float(ici.observe(eph["sun"]).apparent().altaz()[0].degrees)
    phase = float(almanac.moon_phase(eph, t).degrees)
    lune_h = float(ici.observe(eph["moon"]).apparent().altaz()[0].degrees)

    visibles, sous_horizon, brillantes = [], [], []
    for nom, etoiles in _etoiles().items():
        pos = []
        for e_nom, mag, star in etoiles:
            alt, az, _ = ici.observe(star).apparent().altaz()
            pos.append((e_nom, mag, float(alt.degrees), float(az.degrees)))
        hautes = [p for p in pos if p[2] >= MIN_ALT]
        if not hautes:
            sous_horizon.append(nom)
            continue
        az_rad = np.radians([p[3] for p in hautes])
        az_moy = float(np.degrees(np.arctan2(np.sin(az_rad).mean(), np.cos(az_rad).mean())) % 360)
        principale = min(hautes, key=lambda p: p[1])
        visibles.append({
            "constellation": nom,
            "entiere": len(hautes) == len(pos),
            "etoiles_au_dessus_horizon": f"{len(hautes)}/{len(pos)}",
            "hauteur_deg": round(sum(p[2] for p in hautes) / len(hautes)),
            "direction": _cardinal(az_moy),
            "etoile_principale": f"{principale[0]} (magnitude {principale[1]})",
        })
        brillantes += [{"nom": e_nom, "constellation": nom, "magnitude": mag,
                        "hauteur_deg": round(alt), "direction": _cardinal(az)}
                       for e_nom, mag, alt, az in hautes]
    visibles.sort(key=lambda c: (not c["entiere"], -c["hauteur_deg"]))    # entières d'abord, puis les plus hautes
    brillantes.sort(key=lambda e: e["magnitude"])

    return {
        "fuseau": fuseau,
        "heure_observation": _fmt(t, tz),
        "hauteur_soleil_deg": round(sun_h),
        "ciel_assez_sombre": sun_h < ETOILE_SOLEIL_MAX,
        "lune": {"illumination_pct": round((1 - np.cos(np.radians(phase))) / 2 * 100),
                 "hauteur_deg": round(lune_h)},
        "constellations": visibles,
        "sous_l_horizon": sous_horizon,
        "etoiles_les_plus_brillantes": brillantes[:10],
        "remarque": "Magnitude : plus la valeur est basse, plus l'étoile est brillante ; en ville "
                    "on ne voit guère que celles de magnitude < 3.5, et la Lune haute et éclairée "
                    "éteint les plus faibles.",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
