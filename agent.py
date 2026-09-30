"""Client MCP + boucle de tool calling Groq (max 6 tours)."""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import groq
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_DIR = Path(__file__).parent
log = logging.getLogger("uvicorn.error")   # visible dans le terminal du serveur, jamais côté navigateur
DEFAULT_MODEL = "llama-3.3-70b-versatile"   # surchargeable via GROQ_MODEL dans .env
GROQ_TIMEOUT = 30.0
MAX_TURNS = 6
TOOL_TIMEOUT = 120.0        # 1er appel : téléchargement unique de l'éphéméride (~17 Mo)
MAX_TOOL_CHARS = 8000

SERVER = StdioServerParameters(
    command=sys.executable, args=[str(BASE_DIR / "skywatch_mcp.py")], cwd=str(BASE_DIR)
)


def to_groq_tools(tools) -> list[dict]:
    return [
        {"type": "function",
         "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema}}
        for t in tools
    ]


async def run_tool(session: ClientSession, name: str, raw_args: str) -> str:
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return "Erreur : arguments JSON invalides."
    try:
        res = await asyncio.wait_for(session.call_tool(name, args), TOOL_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("Outil %s : délai dépassé", name)
        return "Erreur : l'outil n'a pas répondu à temps."
    texte = "\n".join(c.text for c in res.content if getattr(c, "text", None))
    if res.isError:
        log.warning("Outil %s en erreur : %s", name, texte)   # détail côté serveur uniquement
        return "Erreur : l'outil a échoué, ne donne aucune donnée pour cette partie."
    return texte[:MAX_TOOL_CHARS]


async def chat_with_tools(question: str, system_prompt: str, api_key: str) -> tuple[str, list[str]]:
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question}]
    outils: list[str] = []
    model = os.getenv("GROQ_MODEL") or DEFAULT_MODEL
    client = groq.AsyncGroq(api_key=api_key, timeout=GROQ_TIMEOUT)
    try:
        async with stdio_client(SERVER) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = to_groq_tools((await session.list_tools()).tools)

                for tour in range(MAX_TURNS):
                    # Dernier tour sans outils : force une réponse finale.
                    extra = {"tools": tools, "tool_choice": "auto"} if tour < MAX_TURNS - 1 else {}
                    try:
                        completion = await client.chat.completions.create(
                            model=model, messages=messages, **extra
                        )
                    except groq.BadRequestError as e:
                        if "tool_use_failed" in str(e) and tour < MAX_TURNS - 1:
                            continue    # appel d'outil mal formé par le modèle : on réessaie
                        raise
                    msg = completion.choices[0].message
                    if not msg.tool_calls:
                        return msg.content or "", outils

                    messages.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {"id": c.id, "type": "function",
                             "function": {"name": c.function.name, "arguments": c.function.arguments}}
                            for c in msg.tool_calls
                        ],
                    })
                    for call in msg.tool_calls:
                        outils.append(call.function.name)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": await run_tool(session, call.function.name, call.function.arguments),
                        })
        return "Je n'ai pas réussi à conclure après plusieurs essais, reformulez votre question.", outils
    finally:
        await client.close()


def ask_with_tools(question: str, system_prompt: str, api_key: str) -> tuple[str, list[str]]:
    """Appel bloquant, à lancer via asyncio.to_thread.

    Sous Windows, `uvicorn --reload` impose une boucle asyncio (Selector) qui ne sait pas lancer de
    sous-processus : on crée donc explicitement une ProactorEventLoop pour piloter le serveur MCP.
    """
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(chat_with_tools(question, system_prompt, api_key))
    except Exception as e:
        # anyio enrobe les erreurs dans des ExceptionGroup : on remonte la vraie cause
        while getattr(e, "exceptions", None):
            e = e.exceptions[0]
        raise e
    finally:
        loop.close()
