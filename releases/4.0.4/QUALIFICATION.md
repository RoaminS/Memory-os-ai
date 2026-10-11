# Candidate 4.0.4 — reçu local, garde fermée

Source des artefacts : `621f5b8d89abd0f994e8946f2382146c094a7801`. Le commit contenant ce reçu est
postérieur ; aucun moteur/script/scénario qualifié n'est modifié par ce reçu.
Version4.0.4, jamais publiée/basculée par Codex. Production4.0.3 conservée.

Dossier de release privé : `.local-qualification/release-4.0.4-v5/` : artefacts,
manifest.json, SHA256SUMS, commandes, logs, couverture, environments.json.
Les artefacts et venv hors dépôt sont dans le clone partagé, hors bundle ;
les SHA ci-dessous désignent exactement les fichiers à qualifier/utiliser.
Ne pas reconstruire après la qualification physique pour publier.

## Preuves locales

- Runtime tiktoken complet (`tiktoken_ext`), deux tests de régression ; vrai
  compteur offline0.12.0, table autorisée SHA223921b7…, «hello world»=2 tokens.
- V3 source :572 pass, couverture81.83% ; v4 :1490 pass, couverture87.40%.
  Un test neuronal marqué slow exclu ; aucun autre skip.
- Sdist extrait hors dépôt :572 v3 /1486 v4 pass. Quatre tests nécessitant
  l'historique Git sont explicitement exclus, plus le test slow ; la suite source
  complète couvre ces quatre cas. Liste dans `scripts/qualify_4_0_4_local.py`.
- Lint projet et correction v4/bench/scripts, sonde adverse A6 :verts.
- Wheel +sdist installés dans deux venv distincts hors dépôt, offline/no-deps,
  `[ops,brief]`, dépendances qualifiées locales via overlay, pip check vert.
  Trois serveurs MCP stdio démarrés avec hash, cinq entry points vérifiés,
  admin/relais/backup/drill synthétiques, licences/docs/contrat JSON inclus.
- `python -m build --no-isolation` et `twine check --strict` :verts.
- Zéro modèle encodé, zéro appel LLM/API, aucun service live contrôlé.

## Empreintes

- `memory_os_ai-4.0.4-py3-none-any.whl` : `1c2dfc5a0ce415016bd3490a553ca142928fd68fab1fff8a82dead6f3f862fc8`
- `memory_os_ai-4.0.4.tar.gz` : `51c4ca048ebc043e61a76037d91049937e10354c4f56a0ec357847f2efcaa216`

`LOCAL_QUALIFICATION.json` contient les SHA des logs et du manifeste initial.
Le manifeste privé évoluera seulement lors de l'enregistrement des preuves
physiques ; son SHA initial n'est pas celui du manifeste finalement qualifié.

## Ce que Claude doit faire

1. Relire les changements runtime, gardes release, schémas/fixtures packagés,
   enrôlement L3 sur copie et runbook rollback préservant les écritures récentes.
2. Exécuter intégralement `v4/docs/QUALIFY_404_INCREMENT3C.md` :nouveau runtime,
   full1510 dont20 TCP, puis400 mesures exactes et vérificateur combiné.
3. Exécuter les commandes de `v4/docs/QUALIFY_4_0_4.md` :lier ces preuves au
   manifeste,20 TCP+MiniLM cached+572 v3 réels, copies baseline4.0.3/wheel/sdist/
   reopen, doctor privé, relais installés, vérificateur final.
4. Renvoyer preuves/SHA et éventuelle revue de classements. `release_ready`
   reste false avant tous ces succès. Publication et bascule nécessitent chacune
   un GO explicite de Romain ; registre INACTIF, SQLite opt-in, C4/profil conservés.

## Échecs et ressources préservés

V1 :schéma L3/L4 absent du sdist, corrigé ; v2 :T05 local vert mais remplacé
par le contrôle précis de l'ajout UUID L3 ; v3 :refus disque avant test ;
v4 :arrêt par garde disque pendant pytest sdist, signal ResourceStop reçu dans
un test SQLite, résultat non qualifié. V5 :tous les contrôles locaux verts.
Les logs/artifacts/refus restent dans leurs dossiers, jamais présentés comme
réussis. Les échecs Claude3b/exact-v1 ne sont pas modifiés.

Les temporaires **des tests réussis** de Codex ont été archivés sans perte,
avec vérification SHA de chaque fichier, dans
`.local-qualification/disposable-archives-3c/manifest.json` (archive+SHA,
chemin original, restore_parent). Logs/XML/couverture restent en place.
Ces archives permettent la restitution des scratch stores à leur emplacement
original après validation SHA, sans aucune action sur la production.
La garde disque5GB est désormais vérifiée pendant chaque commande du lanceur ;
les jobs qualifiés hors sandbox gardent aussi nice15/concurrence1/swap8.5GB.
