import os
from contextlib import asynccontextmanager
from pathlib import Path

import groq
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from db import add_history, get_setting, init_db, last_history, set_setting

load_dotenv()

BASE_DIR = Path(__file__).parent
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_TIMEOUT = 30.0


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
        f"La ville par défaut de l'utilisateur est : {ville}."
        if ville
        else "L'utilisateur n'a pas défini de ville par défaut."
    )
    return (
        "Tu es SkyWatch, un assistant d'observation du ciel nocturne. "
        "Réponds en français, de façon claire et concise. "
        f"{lieu} "
        "Tu n'as accès à aucun outil pour le moment : tu ne peux donc pas connaître "
        "la météo, les passages de l'ISS ou des satellites. "
        "N'invente jamais de données (horaires, hauteurs, nébulosité, etc.) : "
        "si l'utilisateur en demande, explique que tu ne peux pas les fournir pour l'instant."
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"ville": get_setting("ville")})


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


@app.post("/api/ask")
async def ask(body: AskBody):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(503, "GROQ_API_KEY manquante : renseignez-la dans le fichier .env")

    question = body.question.strip()
    if not question:
        raise HTTPException(422, "Question vide")

    client = groq.AsyncGroq(api_key=api_key, timeout=GROQ_TIMEOUT)
    try:
        completion = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": question},
            ],
        )
    except groq.APITimeoutError:
        raise HTTPException(504, "Groq n'a pas répondu à temps (30 s)")
    except groq.APIError as e:
        raise HTTPException(502, f"Erreur Groq : {getattr(e, 'message', str(e))}")
    finally:
        await client.close()

    reponse = completion.choices[0].message.content or ""
    outils: list[str] = []  # aucun outil branché avant V2
    add_history(question, reponse, outils)
    return {"reponse": reponse, "outils": outils}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8004, reload=True)
