# Candidate 4.0.5 v5 — correctif baseline, garde fermée

2026-10-11. Source de construction `e7d5abbbc41972359497a922b788e798b5b4c1cf`.
Le reçu final peut être un descendant sans changement des empreintes source.
Aucun push, appel API, modèle chargé, publication, bascule ou activation.
Runbook : `v4/docs/QUALIFY_4_0_5.md`, reprise précise en §5.

## Défaut et correction

Le lanceur refusait la baseline de production 4.0.4 avant sa comparaison finale.
Il accepte maintenant explicitement 4.0.2/4.0.3/4.0.4, sans accepter une baseline
future non qualifiée. Pour 4.0.4, son UUID préexistant valide doit rester identique
dans wheel/sdist/cold/warm ; aucun enrolment permis. L'enrolment éventuel reste
réservé à 4.0.3. Profils natifs, intégrité, zéro réencodage warm et classements
entre distributions restent obligatoires. Quatorze régressions ajoutées.

Le diagnostic en lecture seule des cinq rapports historiques confirme, avec la
véritable baseline 4.0.4 : recherche identique, changed_queries=[], zéro encodage
warm, UUID 286dfcb0357940468aedb9617a2ccd9c conservé. Ceci **n'est pas une nouvelle
preuve de copie**. Le rapport refusé copy-4.0.5-v1/report.json reste intact :
SHA-256 `27d598f5ee6b8de2ba8af1b17d30a451df12859625312979e8515a5d4fdaf003`.

## Artefacts exacts renouvelés

Scripts et tests étant inclus dans le sdist, il fallait reconstruire. Dossier
`.local-qualification/release-4.0.5-v5/`, installs wheel/sdist hors dépôt, aucune
installation globale ni téléchargement. Les dossiers v1–v4 sont conservés.

- Wheel : `be303b286096281522aa9e21d8792975ab572fbb0e23e5bc911d7cef9fc70396`
- Sdist : `e9767f4be195229227a1f3e24a0d8783d87e3c6ea4ad9bf08434cd233e3b31cc`
- Manifeste initial : `3ca510986698f381dddf12cd8ad583209a385109f88473c3e36e86826282f82a`

Le manifeste évoluera uniquement par enregistrement des preuves physiques ; son
hash initial ne vaut pas hash final qualifié. Ne plus reconstruire ces artefacts
pour continuer la qualification. Les 67 fichiers Python runtime du wheel sont
identiques à ceux du candidat v4. Les inventaires/empreintes src, v4/src et v4/tests
sont identiques ; C4, auto/MPS/native/batch32 et les défauts JSONL/tasks-off/registry-off
restent inchangés.

## Preuves locales

- 41 tests ciblés copie/gates/release passent, dont les 14 nouvelles régressions.
- Source : 602 v3 et 1631 v4 ; sdist : 602 v3 et 1627 v4, zéro échec.
- Couverture : v3 81,46 %, v4 86,01 %.
- Un slow neuronal et les 27 TCP exclus des unités ; sdist exclut en plus quatre
  cas archives/Git nommés par le lanceur. Ce ne sont pas des preuves TCP/neuronales.
- Probe adverse, deux lints, wheel+sdist, twine check --strict, pip check,
  imports et commandes installées passent dans deux environnements hors dépôt.
- Configs client inchangées : contrôle sémantique des entrées MCP et SHA strict
  des fichiers dépôt/consignes. Preuve locale/logs/empreintes dans le reçu JSON.
- Les preuves brief source/exact précédentes ont été vérifiées contre les
  nouveaux inventaires SHA stricts et sont réutilisables sans nouvelle mesure.
- Mesures Claude antérieures, sources moteur inchangées : S4 schema7 10k 6,7 s,
  L7b CASp95 5,5 ms/list50p95 0,52 ms. Aucun nouveau chiffre mesuré par Codex.

## Reprise externe et limites

`release_ready=false`. **Rejouer les étapes 7–8 source**, puis 9–13 copie/relais
avec ce manifeste/artéfacts v5 : 27 TCP, 1 MiniLM cached et les 602 v3 cached,
copy-4.0.5-v2 neuf comparé au runtime 4.0.4, doctor privé, relais installés.
Préalablement, enregistrer les preuves brief déjà vertes via le vérificateur (§5).
Le source-proof v4 ne peut pas être transféré au nouveau commit/compteur v3.
Ne jamais réétiqueter les rapports historiques comme des exécutions nouvelles.

Commandes complètes : QUALIFY_4_0_5.md §5 ; arrêt au premier échec, dossiers neufs,
concurrence 1 et gardes disque/swap. Publications, bascule, SQLite/tasks et jobs
restent soumis à des GOs séparés de Romain ; aucun de ces actes n'est exécuté ici.
