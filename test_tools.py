"""Teste chaque outil du serveur MCP isolément, via le vrai transport stdio (sans Groq).

Usage : python test_tools.py [ville]
"""
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from agent import SERVER


async def call(session, name, args):
    res = await session.call_tool(name, args)
    texte = "\n".join(c.text for c in res.content if getattr(c, "text", None))
    print(f"\n=== {name}({args}) {'[ERREUR]' if res.isError else ''}")
    print(texte[:1500])
    return json.loads(texte) if not res.isError else {}


async def main(ville: str):
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Outils :", [t.name for t in (await session.list_tools()).tools])
            g = await call(session, "geocoder", {"ville": ville})
            await call(session, "meteo_ciel", {"lat": g["lat"], "lon": g["lon"], "heures": 6})
            await call(session, "passages_iss",
                       {"lat": g["lat"], "lon": g["lon"], "fuseau": g["fuseau"], "jours": 2})
            await call(session, "satellites_visibles", {"lat": g["lat"], "lon": g["lon"]})
            await call(session, "prochain_bon_passage", {"lat": g["lat"], "lon": g["lon"]})


if __name__ == "__main__":
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    loop.run_until_complete(main(sys.argv[1] if len(sys.argv) > 1 else "Lyon"))
