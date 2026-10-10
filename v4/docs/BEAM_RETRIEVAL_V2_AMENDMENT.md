# BEAM C4 retrieval v2 — amendement préexécution du 2026-10-10

Cet amendement remplace le lanceur `beam-c4-v1` avant tout résultat BEAM.
Romain a décidé de ne pas exécuter le lecteur LLM pour l'instant. Le nouveau
plan couvre exclusivement la récupération C4 contre le BM25 du même bras.

## Incident sans résultat

Deux tentatives du pilote v1 se sont arrêtées dans `frozen()` avant chargement
de Granite, création du serveur MCP, ingestion ou recherche. La première preuve
a été conservée sous
`.local-qualification/beam-c4-v1-minimal.pilot-retrieval.failed-0751` ; la
seconde sous `.local-qualification/beam-c4-v1-minimal/pilot-retrieval`.
Aucun score, contexte récupéré ou résultat intermédiaire n'a été observé.

Le second lancement enfreint littéralement la règle v1 « aucun redémarrage ».
Il n'introduit toutefois aucun biais de sélection : les deux arrêts sont
identiques, antérieurs à l'inférence et causés uniquement par l'attestation
source. Les preuves restent conservées et ne doivent pas être réutilisées.
Le plan v2 emploie un nouveau dossier exclusif.

## Cause et correction

Avec les métadonnées Git séparées dans `.local-qualification/git-state`, la
commande `git --git-dir … archive` héritait du répertoire courant du worker.
Depuis le dossier non suivi `pilot-retrieval`, Git échouait avec
`fatal: current working directory is untracked`. `verify_source()` impose
maintenant `cwd=clone`. Les deux autres lectures d'archives historiques
(`lexical_s_v2.old_reader` et `lexical_s_v3.archived_stats`) appliquent la même
règle afin de supprimer la même dépendance implicite.

La régression crée un dépôt à métadonnées séparées, se place dans
`.local-qualification/plan/pilot-retrieval` non suivi, puis exige que
`verify_source()` reconstruise et compare exhaustivement l'archive Python.

## Plan v2 figé

- Version : `beam-c4-retrieval-v2`.
- Commit source : `95276a8123838bfe9f7937f6325307d6be88ac39`.
- Dossier neuf : `.local-qualification/beam-c4-retrieval-v2`.
- SHA-256 du plan préparé localement :
  `5878e5b54d71a6d6437a42f6e637e6a79552f7fda8d99828ace427dbbb5e604e`.
- Pilote : les mêmes deux conversations et les mêmes vingt sondes choisies
  prospectivement par hash ; aucune sélection issue des tentatives échouées.
- Confirmation : 400 questions, rappel/MRR sur les 355 annotations de preuve
  non vides, bootstrap apparié par conversation et critère v1 inchangés.
- Lecteur : `skipped-by-romain-2026-10-10`, zéro appel autorisé. Les opérations
  `preflight` et `pilot-reader` ne sont plus proposées par ce lanceur.

L'attestation complète `frozen()` a été rejouée depuis un nouveau sous-dossier
non suivi après préparation du plan : version, commit, empreinte du corpus,
empreintes Granite, configuration C4 et archive source ont été vérifiés.
Aucune inférence neuronale n'a été exécutée par Codex.

## Commandes exactes pour Claude

Après import du bundle et positionnement de l'arbre sur son tip :

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
bash scripts/run_beam.sh prepare \
  --source-commit 95276a8123838bfe9f7937f6325307d6be88ac39 \
  --plan-dir .local-qualification/beam-c4-retrieval-v2
bash scripts/run_beam.sh pilot-retrieval \
  --plan-dir .local-qualification/beam-c4-retrieval-v2
```

Conserver le rapport du pilote et vérifier `complete=true`, les 20 questions,
les deux conversations, les mesures de ressources et l'absence de
`stopped.json`. Le pilote mesure la faisabilité ; il ne permet aucun réglage.
Si ces conditions sont satisfaites, lancer une seule fois la confirmation :

```bash
bash scripts/run_beam.sh retrieve \
  --plan-dir .local-qualification/beam-c4-retrieval-v2
```

Ne pas exécuter `pilot-reader`, `preflight`, Codex CLI ou un autre lecteur.
Ne pas supprimer les deux dossiers d'échec v1. Les gardes nice≥15,
swap≤8,5 GB, disque libre≥5 GB, séquentialité et zéro téléchargement restent
obligatoires.
