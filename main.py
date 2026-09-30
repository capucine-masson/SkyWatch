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


def build_system_prompt() -> str:
    ville = get_setting("ville")
    lieu = (
        f"Ville par défaut de l'utilisateur : {ville} (à utiliser si aucune autre ville n'est citée)."
        if ville
        else "L'utilisateur n'a pas défini de ville par défaut : s'il n'en cite aucune, demande-la."
    )
    maintenant = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M (%Z)")
    return f"""Tu es SkyWatch, un assistant d'observation du ciel nocturne. Tu réponds en français.
Date et heure du serveur : {maintenant}. {lieu}

Outils : tu disposes d'outils qui donnent toutes les données réelles.
- Commence par `geocoder` pour obtenir lat, lon et fuseau de la ville.
- Puis, selon la question : `meteo_ciel` (état du ciel, meilleure heure), `planetes_visibles`
  (planètes et Lune de la nuit), `etoiles_visibles` (constellations et étoiles brillantes :
  Grande Ourse, Orion… ; `heure` HH:MM pour un autre moment que le début de nuit),
  `passages_iss` (passages de l'ISS), `satellites_visibles`
  (satellites au-dessus de la position), `prochain_bon_passage` (meilleur créneau ISS des 7 prochains jours).
- Pour toute question du type « que voir ce soir / cette nuit », appelle TOUJOURS `meteo_ciel`
  et combine-le avec les autres outils utiles (planètes, ISS…).

Périmètre : les outils couvrent les planètes, la Lune, l'ISS, les satellites et 25 constellations
principales (étoiles brillantes), mais PAS les galaxies, nébuleuses, amas ni le ciel profond.
Si l'utilisateur en demande (même en plus d'autre chose), ta réponse DOIT commencer par la ligne :
ℹ️ Je n'ai pas d'outil pour les galaxies, nébuleuses ni le ciel profond.
puis tu donnes ce que les outils savent faire. Pour une constellation absente de la liste de
l'outil, dis-le sans inventer sa position.
Si la question ne concerne pas du tout le ciel, réponds brièvement que tu ne peux aider que sur l'observation du ciel.

Règles strictes :
- Ne demande jamais confirmation avant d'appeler un outil : appelle-le directement, puis réponds.
- N'invente JAMAIS de données (heures, hauteurs, nébulosité, scores, magnitudes, noms d'astres).
  Utilise uniquement ce que les outils renvoient. Si un outil échoue ou ne renvoie rien, dis-le.
- N'ajoute aucun détail sur la situation de l'utilisateur qu'il n'a pas donné (balcon, jardin,
  horizon dégagé, matériel…). Si tu utilises la ville par défaut, dis-le.
- Le score /10 vient des outils (champ score_sur_10) : ne le recalcule pas.
- Pour les planètes, précise l'heure et la direction données par l'outil (certaines sont surtout
  visibles en fin de nuit) et signale celles qui exigent des jumelles.

Format de la réponse : texte brut (pas de Markdown, pas de ** ni de tableau), court et clair,
une ligne par élément avec une icône :
🌙 Ciel : état du ciel, meilleure heure, et la Lune (phase, éclairement) si elle est fournie
☁️ Météo : nébulosité et visibilité pertinentes
🪐 Planètes : celles visibles à l'œil nu, avec heure et direction
✨ Étoiles : constellations visibles avec hauteur et direction où regarder, et l'heure du calcul
🛰️ ISS / satellites : heure locale, durée, hauteur max, direction, visible à l'œil nu ou non
⭐ Score : score /10 quand un passage visible en dispose
N'écris que les lignes utiles : pas de ligne ⭐ sans score fourni par un outil. Termine par une courte recommandation."""


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
            ask_with_tools, question, build_system_prompt(), api_key
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
