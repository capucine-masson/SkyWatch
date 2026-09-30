Je veux construire une web app « SkyWatch » : « Que voir ce soir dans le ciel ? »

## Pitch
L'utilisateur pose une question en langage naturel (« Qu'est-ce que je peux observer ce soir depuis chez moi ? »).
Un LLM (Groq) choisit et combine lui-même des outils exposés par un serveur MCP maison, qui enveloppe une API
de suivi de satellites et une API météo. La réponse donne les passages de l'ISS et de satellites, l'état du
ciel et la meilleure heure pour regarder.

## Stack imposée (minimale exprès)
- Backend : Python + FastAPI, lancé en local avec `python main.py` (uvicorn démarré depuis le bloc `if __name__ == "__main__"`)
- Serveur MCP : fichier séparé `skywatch_mcp.py`, SDK `mcp` en version < 2 (FastMCP), transport stdio
- LLM : Groq (llama-3.3-70b-versatile) avec tool calling. Le backend FastAPI est le client MCP : il liste les
  outils du serveur, les passe à Groq, exécute les appels demandés, boucle (max 6 tours)
- Persistance : SQLite, accès direct (module sqlite3, requêtes paramétrées, pas d'ORM) : table `history`
  (questions/réponses) et table `settings` (ville favorite)
- Frontend : HTML/Jinja2 + JS minimal (fetch, pas de framework JS)
- Secrets : clé Groq dans `.env` chargé via python-dotenv, `.env` dans le .gitignore, fournir `.env.example`.
  Pas de validation stricte au démarrage, juste ne jamais mettre la clé en dur.
- Fournir un `requirements.txt`

## Design
- App moderne, onirique (« rêverie ») : fond dégradé sombre, étoiles discrètes, transitions douces,
  éléments qui glissent/apparaissent (CSS uniquement), respecter `prefers-reduced-motion`
- Couleurs : #360568 (fond profond), #5B2A86 (accents/boutons), #7785AC (texte secondaire, bordures),
  #9AC6C5 (highlights, icônes)
- Polices (Google Fonts) : titres = Asar, corps = Actor, notes / mots ou bouts de phrases = Azeret Mono
- Chat au centre de la page

## Méthode
- Ne code rien avant de m'avoir proposé un découpage en étapes avec une estimation de durée (1h au total).
  Attends ma validation.
- À la fin de chaque étape : résumé technique (routes, tables, fichiers, outils MCP) et attends mon feu vert.

## Étapes (ordre de priorité strict)
V0 - Squelette FastAPI + SQLite + .env (+ .gitignore, .env.example, requirements.txt)
V1 - Interface : champ de question + bouton « Demander » ; réponse affichée avec la liste des outils appelés ;
     champ « ville par défaut » enregistré en base et injecté dans le prompt système ; historique des
     10 dernières questions sous le formulaire. Le chat appelle Groq sans outils à ce stade.
V2 - Serveur MCP (APIs sans clé) et branchement au chat :
     - `geocoder(ville)` : ville -> lat, lon, fuseau (Open-Meteo geocoding)
     - `meteo_ciel(lat, lon, heures)` : nébulosité et visibilité par heure (Open-Meteo forecast)
     - `passages_iss(lat, lon, fuseau, jours)` : heure locale, durée, hauteur max, direction, visible à
       l'œil nu (TLE depuis CelesTrak, calcul avec skyfield)
V3 - Features :
     - `satellites_visibles(lat, lon)` : satellites au-dessus de la position (Starlink, Hubble…)
     - Score d'observation /10 combinant hauteur de passage et ciel dégagé
     - Sortie stylée : petit résumé clair avec icônes (🌙 ☁️ 🛰️)
     - `prochain_bon_passage(lat, lon)` : meilleur créneau des 7 prochains jours
     - Mini page HTML : carte du ciel simple avec la trajectoire de l'ISS

## Sécurité et qualité (par défaut, sans que je le redemande)
- Actions qui modifient l'état en POST/PUT/DELETE, jamais en GET
- Pas d'innerHTML non échappé côté JS (utiliser textContent)
- Bouton désactivé pendant un appel LLM (anti double-clic)
- Timeouts explicites : 15 s sur les appels HTTP externes, 30 s sur Groq
- Injection SQL impossible : requêtes paramétrées uniquement
- Le LLM ne doit jamais inventer de données : uniquement celles renvoyées par les outils
- Sous Windows, `uvicorn --reload` impose une boucle asyncio qui ne sait pas lancer de sous-processus :
  créer explicitement une ProactorEventLoop pour appeler le serveur MCP
- Tester chaque outil MCP isolément avant de le brancher à Groq