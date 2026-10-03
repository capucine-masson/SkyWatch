import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import groq
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import skywatch_mcp
from agent import ask_with_tools
from db import add_history, get_setting, init_db, last_history, set_setting

load_dotenv()

BASE_DIR = Path(__file__).parent
log = logging.getLogger("uvicorn.error")
MSG_INDISPONIBLE = "Le service est momentanément indisponible. Réessayez dans un instant."


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="SkyWatch", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class VilleBody(BaseModel):
    ville: str = Field(max_length=100)


_geo_cache: dict[str, dict] = {}


def localiser(ville: str) -> dict:
    """Géocodage d'une ville, mis en cache (les échecs ne le sont pas)."""
    if ville not in _geo_cache:
        geo = skywatch_mcp.geocoder(ville)
        if "erreur" in geo:
            return geo
        _geo_cache[ville] = geo
    return _geo_cache[ville]


def build_system_prompt() -> str:
    """Le prompt est envoyé à chaque tour : le garder court ménage la limite de débit de Groq."""
    ville = get_setting("ville")
    geo = localiser(ville) if ville else {"erreur": "aucune ville"}
    if "erreur" not in geo:
        lieu = (f"Ville par défaut de l'utilisateur : {geo['nom']} (lat {geo['lat']}, lon {geo['lon']}, "
                f"fuseau {geo['fuseau']}), déjà localisée : n'appelle `geocoder` que pour une AUTRE ville.")
    elif ville:
        lieu = (f"Ville par défaut de l'utilisateur : {ville}. Appelle d'abord `geocoder` pour "
                "obtenir lat, lon et fuseau.")
    else:
        lieu = ("L'utilisateur n'a pas défini de ville par défaut : s'il n'en cite aucune, demande-la. "
                "Sinon appelle d'abord `geocoder`.")
    maintenant = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M (%Z)")
    return f"""Tu es SkyWatch, assistant d'observation du ciel nocturne. Tu réponds en français.
Date et heure du serveur : {maintenant}. {lieu}

Outils (données réelles). Question « que voir ce soir / cette nuit » : appelle UNIQUEMENT `apercu_ce_soir`
(un seul appel : météo, Lune, planètes, constellations, galaxies, ISS). Pour une question ciblée :
`meteo_ciel`, `planetes_visibles`, `etoiles_visibles` (constellations), `ciel_profond_visible` (galaxies,
nébuleuses, amas), `passages_iss`, `satellites_visibles`, `prochain_bon_passage` ; les deux outils
d'étoiles et de ciel profond acceptent `heure` (HH:MM). Plusieurs outils : appelle-les ensemble, dans un
seul tour. Ne demande jamais confirmation avant un outil.
Hors périmètre : comètes, étoiles filantes, aurores, objets absents des listes de l'outil : ajoute une
section « ## ℹ️ Hors périmètre » d'une ligne, sans rien inventer. Sans rapport avec le ciel : réponds
brièvement que tu ne peux aider que sur l'observation du ciel.

Règles :
- N'invente JAMAIS de données : utilise uniquement ce que les outils renvoient ; si un outil échoue, dis-le.
- N'ajoute rien sur la situation de l'utilisateur (balcon, horizon, matériel…). Si tu utilises la ville par défaut, dis-le.
- Score /10 : champ score_sur_10, ne le recalcule pas.
- Planètes : heure et direction de l'outil ; signale celles qui exigent des jumelles.
- Galaxies et nébuleuses : donne le champ instrument ; préviens si ciel_assez_sombre est faux ou lune_gene vrai ;
  jamais « facile » pour un objet « jumelles » ou « télescope ».
- Écris pour un débutant : directions en toutes lettres (nord-ouest, pas NO), hauteur en mots + degrés
  (« près de l'horizon » < 20°, « à mi-hauteur » 20-60°, « très haut » > 60°), explique en quelques mots
  les objets peu connus, pas de jargon (magnitude, azimut).

Format EXACT (aucun autre Markdown : pas de **, pas de tableau) : uniquement les sections utiles, dans cet ordre :
## 🌙 Ciel et Lune, ## ☁️ Météo, ## 🪐 Planètes, ## ✨ Étoiles et constellations,
## 🌌 Galaxies et nébuleuses, ## 🛰️ ISS et satellites. Sous chaque titre, 1 à 5 lignes courtes commençant par « - »
(les plus faciles ou intéressantes). Pas de section pour un outil non appelé. Aucune introduction.
Dernière ligne : « Conseil : » suivie d'une seule phrase."""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"ville": get_setting("ville")})


@app.get("/carte", response_class=HTMLResponse)
def carte(request: Request):
    return templates.TemplateResponse(request, "carte.html", {"ville": get_setting("ville")})


@app.get("/api/settings")
def read_settings():
    return {"ville": get_setting("ville")}


@app.put("/api/settings/ville")
def update_ville(body: VilleBody):
    ville = body.ville.strip()
    set_setting("ville", ville)
    return {"ville": ville}


@app.get("/api/history")
def read_history():
    return last_history(10)


@app.get("/api/trajectoire-iss")
async def trajectoire_iss(ville: str = Query("", max_length=100)):
    """Lecture seule : prochain passage de l'ISS (visible si possible) avec sa trajectoire."""
    ville = ville.strip() or get_setting("ville")
    if not ville:
        raise HTTPException(400, "Indiquez une ville (ou enregistrez une ville par défaut).")
    geo = await asyncio.to_thread(skywatch_mcp.geocoder, ville)
    if "erreur" in geo:
        raise HTTPException(404, geo["erreur"])
    res = await asyncio.to_thread(skywatch_mcp.trajectoire_iss, geo["lat"], geo["lon"], geo["fuseau"])
    if "erreur" in res:
        raise HTTPException(502, res["erreur"])
    return {"ville": geo["nom"], "pays": geo["pays"], **res}


@app.post("/api/ask")
async def ask(body: AskBody):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        log.error("GROQ_API_KEY absente : renseignez-la dans le fichier .env")
        raise HTTPException(503, MSG_INDISPONIBLE)

    question = body.question.strip()
    if not question:
        raise HTTPException(422, "Question vide")

    try:
        # Boucle Groq + client MCP dans un thread dédié (ProactorEventLoop, cf. agent.py)
        reponse, outils = await asyncio.to_thread(
            ask_with_tools, question, await asyncio.to_thread(build_system_prompt), api_key
        )
    except groq.APITimeoutError:
        log.error("Groq : délai dépassé (30 s)")
        raise HTTPException(504, "Le service met trop de temps à répondre. Réessayez dans un instant.")
    except groq.RateLimitError:
        log.exception("Groq : limite de débit atteinte (tokens par minute)")
        raise HTTPException(429, "Trop de demandes en peu de temps. Réessayez dans une minute.")
    except groq.NotFoundError:
        log.exception("Groq : modèle introuvable pour cette clé (définir GROQ_MODEL dans .env)")
        raise HTTPException(502, MSG_INDISPONIBLE)
    except Exception:
        # Détail complet dans le terminal du serveur ; le navigateur ne reçoit qu'un message générique.
        log.exception("Échec de la question (Groq / outils MCP)")
        raise HTTPException(502, MSG_INDISPONIBLE)

    add_history(question, reponse, outils)
    return {"reponse": reponse, "outils": outils}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8004, reload=True)
