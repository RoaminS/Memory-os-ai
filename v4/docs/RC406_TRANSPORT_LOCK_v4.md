# 4.0.6 RC v4 — résolution compatible et échec de lancement explicite

2026-10-12. Candidate non publiée/non basculée. Aucune action sur la production,
aucun téléchargement/LLM/réseau par Codex. Préserver le trial refusé
`20261012-072322-sqlite` et son HOME privé ; ne pas reprendre une installation partielle.

## Décision

**Verrou exact de la fermeture active**, plutôt qu'une borne `<2` sur MCP seule.
La résolution fraîche a aussi changé pydantic, Starlette et les transitives ;
changer uniquement MCP déplacerait le prochain défaut. Les METADATA du runtime
installé 4.0.5 qualifié sont la source, sans importer les bibliothèques. Le
générateur parcourt Requires-Dist, marqueurs et extras jusqu'à stabilité,
vérifie les specifiers, conserve leurs SHA-256 et exclut extras/dev non demandés.

| Périmètre | Verrou / preuve |
|---|---|
| Core macOS arm64 CPython 3.11 | 61 pins exacts directs dans METADATA, même si transitifs |
| `[ops,brief]` | 4 paquets supplémentaires ; crypto 46.0.5 déjà requise par MCP/PyJWT |
| MCP/transport | MCP 1.26.0, pydantic 2.12.5/core 2.41.5, Starlette 0.52.1, uvicorn 0.41.0, sse-starlette 3.3.2, anyio 4.12.1, httpx 0.28.1 |
| MCP 2.x | Refusé ; mcp-types/httpx2/httpcore2 hors fermeture, pas ajoutés artificiellement |
| Provision/encodeur | Granite/C4, device/dtype/batch et 4 clés d'attestation numérique inchangés |
| Hors périmètre | Python 3.12+, Windows/Linux et autres extras non qualifiés par ce verrou |

Ressources : `qualified_runtime_v1.json` (versions attendues),
`qualified_runtime_closure_v1.json` (graphe/provenance METADATA),
`qualified_runtime_constraints_v1.txt` (version pins pour pip).
Ce verrou porte sur les versions et la résolution, **pas les hashes des wheels
tiers**, une signature, des vulnérabilités auditées ou une équivalence numérique.
L'actualisation exige une nouvelle fermeture et requalification, pas un auto-upgrade.

Quickstart vérifie les 65 versions avant ressources/token/service ; Python 3.11
exigé pour ce parcours. `/ready`/status exposent le profil runtime déployé sans
charger le modèle. Doctor distingue sa propre installation de celle du serveur ;
un ancien serveur sans cette attestation produit WARN, pas une fausse preuve.
Le core manuel sans extra brief peut fonctionner en mode détaillé, mais n'est
pas qualifié comme installation complète du parcours quickstart.

## Fail fast

Le job doit correspondre exactement aux argv/token/store attendus. Après
readiness absente : dernier exit non nul + arrêt sans pid, ou relancement
après cet exit, déclenche un échec explicite. Un modèle encore chargé avec
process vivant sans exit d'échec conserve le délai normal, sans faux crash.
Diagnostic : dernière portion de server.err (lecture bornée 8 KiB, résultat
≤4 KiB/24 lignes), token connu/Bearer masqués et HOME anonymisé. Il n'entre
pas dans le rapport public anonymisé de quickstart.

Seul le job nouvellement créé, plist inchangé et identité réattestée est arrêté.
Identité changée = aucun bootout. Aucun client configuré sur cette branche
d'échec ; journal, token, plist et magasin conservés. Les tests simulent les
deux états, une identité devenue étrangère et une fuite de token dans le log.

## Preuves et suite

Le checker hors ligne appliqué au **rapport réel** du trial refusé trouve
39 écarts et 6 paquets inattendus ; wheel ba6dae50… rejeté pour metadata
ancienne non verrouillée. Il aurait bloqué avant pip install si ce gate avait
été présent. L'ancienne qualification réutilisait des dépendances installées :
elle ne démontrait pas une résolution fraîche. Ce trou de qualification est corrigé.

Les nouveaux tests couvrent les marqueurs/extras du générateur, pins core et
extras, dérive/missing packages, plan pip faux/dupliqué/hors plateforme, hash
et METADATA du wheel, wrapper réseau simulé (garde/HOME privé/pas de credentials).
Codex teste sans réseau ; il ne prétend pas avoir vérifié la disponibilité des
versions épinglées sur l'index. Claude exécute full + artefacts, le nouveau gate
réseau gardé, puis le trial v3 une seule fois sur le même nouveau wheel ;
commandes et dossiers v4 dans `docs/releases/v4.0.6.md`.

4.0.4/4.0.5 publiées : le défaut concerne les **nouvelles résolutions non
contraintes**, pas les environnements déjà qualifiés. Romain décide des yanks
après remplacement 4.0.6 vérifié ; Codex ne publie ni ne retire aucune version.
