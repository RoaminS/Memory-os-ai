# Candidate 4.0.5 — qualification locale complète, garde fermée

2026-10-11. Construction source `0becca215d038314c2d08c009c456c0f7b8e21a8` ; le reçu final peut être
un descendant sans changement des empreintes. Aucun push, publication ou bascule.
Production JSONL 4.0.4, SQLite/L7b/registre inactifs, C4 et auto/MPS/native/batch32
conservés. Runbook et commandes exactes : `v4/docs/QUALIFY_4_0_5.md`.

## Artefacts exacts

Dossier privé du clone `.local-qualification/release-4.0.5-v4/` : artefacts,
manifest/SHA256SUMS/logs/environments. Les venv wheel/sdist sont hors dépôt ;
dépendances présentes, sans installation globale ni téléchargement. Ne pas
reconstruire pour poursuivre les preuves.

- `memory_os_ai-4.0.5-py3-none-any.whl` : `b71c8c12a22c8d3bb04fef175fd9ef4be41ff0966ef1a3de84fd987e59056889`
- `memory_os_ai-4.0.5.tar.gz` : `5aad8f95b30094901121ca9dcc7b820a75b2fdb7925cef3e9ee84d1157498ef6`

Manifeste initial SHA-256 : `c87a652480b6615e366914b01a06e8bec8cba92e0704dffb729355a1f0b58cc8`.
Le manifeste évoluera uniquement lors de l'enregistrement des preuves physiques.
Ce hash initial ne vaut pas hash du manifeste finalement qualifié.

## Preuves et limites

- 588 tests v3 et 1631 v4 source, zéro échec ; couvertures dans le reçu JSON.
- Source v4 : un slow neuronal exclu, réservé à Claude. Sdist installée :
  588 v3 et 1627 v4, cinq désélections = ce slow + quatre tests dépendant des
  archives Git, explicitement listés dans le lanceur. Aucun défaut runtime masqué.
- Probe adverse, deux lints, wheel+sdist, twine check --strict, pip check,
  imports/commandes installées et tests de démarrage passent. Aucun réseau/modèle/API.
- Configurations MCP locales après ces étapes identiques sémantiquement au
  snapshot pré-unités ; fichiers dépôt/consignes à SHA strict inchangés.
  SHA/logs/proof dans LOCAL_QUALIFICATION.json et le dossier privé.
- Régression SQL sur 10k : seulement 50 JSON décodés/page, index sans tri
  temporaire, p95<30ms. Fixture locale de la phase SQL : 0,36ms ; la mesure
  officielle complète doit être rejouée. Critère 30ms scellé avant Claude.
- Cold : construction d'encodeur interdite, CRUD/CAS/brief/rollback/réouverture
  passent, cache/profil préservés ; échec de recherche laisse tasks ready.
  Hashstub de lazy hydration ne qualifie pas la sémantique Granite. Nouveau TCP
  cold/T1 à exécuter ; les deux W06 précédents confirmaient déjà le clientInfo.

`release_ready=false`. Restent : 27 TCP, un cached MiniLM, 588 v3 cached réels,
400 mesures exactes/enveloppe MCP, copies JSONL comparées au runtime 4.0.4,
profils/cache/timing/search/doctor, relais installés et deux mesures S4/L7b 10k.
Commandes dans QUALIFY_4_0_5.md ; séquentielles après fin du tour Codex. Cette
preuve locale n'acquitte ni TCP ni copie industrielle ni activation.

Dossiers conservés : v1 refus note de version absente, v2/v3 local verts mais
supersédés par les correctifs de métadonnée de seuil et d'export du helper de garde.
Seuls les artefacts v4 ci-dessus sont candidats. Aucun seuil modifié après mesure.
GOs distincts pour publication/bascule, SQLite/tâches, jobs/RPO/destination externe.
