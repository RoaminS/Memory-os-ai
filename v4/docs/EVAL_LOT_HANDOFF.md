# LOT EVAL — livraison Codex, 2026-10-09

Source évaluable : **271aa3e7dee5120aebd839c3175c3e7dfdbf4778**. Version moteur
inchangée par ce lot : 4.0.1. Publication PyPI et empreintes confirmées par Romain
dans sa demande ; aucune nouvelle publication, aucune intervention A3/prod par
Codex. Le reçu de publication précédent n'est pas réécrit par ce lot.

## État exact et preuves

| Tâche | Livré | Preuve acquise / preuve manquante |
|---|---|---|
| T17 | Capture chronologique, extraction sans LLM, vrai MCP stdio privé, persistance, recherche, réponse extractive, scoring séparé ; usage encodeur et timings bout en bout | Régressions légères vertes ; exécution réelle stdio + modèle attend Claude |
| T16 | BM25, dense seul, fichiers persistés, contexte complet lorsque admissible ; mêmes observations et budgets | Contrats/unitaires verts ; mesures appariées réelles attendues |
| LongMemEval | Reader streaming S/oracle, gel des entrées/source/snapshot, échecs conservés, IC groupés appariés, prêt pour les 500 cas locaux | Aucun score de performance mesuré par Codex ; pas de S local trouvé |
| T25 | Adaptateur local Add/Search réutilisé par T17, contrats testés, isolation namespace/utilisateur, ADD-only et reçu | **DRAFT** : règles/harness AML actuels indisponibles hors réseau ; éligibilité non attestée ; rien soumis |
| STORE S2 | Aucun changement moteur/transaction dans ce lot | Reporté après les preuves EVAL, S1 reste expérimental |

Tests légers (aucun modèle/serveur réel chargé) :

```sh
PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
/Users/romain/Memory-os-ai/.venv/bin/python -m pytest \
  tests/test_e2e_eval.py tests/test_evaluation_manifest.py tests/test_release_gate.py \
  -q -o cache_dir=.local-qualification/eval-lot-tests/pytest-cache --tb=short
```

Résultat : **54 passed**. Lint `ruff check src tests scripts` et
`ruff check src tests v4/src v4/tests v4/bench scripts tools --select E9,F` : verts.
`sh -n scripts/run_eval_lot.sh` : vert. Comparaison Git archive du commit annoncé
avec tous les fichiers Python core/banc : **PASS**. Tests de machine/arrêt et MCP
utilisent des doubles : ils ne remplacent pas les preuves de processus réels.

Inventaire local audité (lecture seule, pas une évaluation) :

- `/Users/romain/Memory-os-ai/v4/bench/data/longmemeval_oracle.json` :
  **500 questions, 948 sessions, 15 388 478 octets** ; tous les haystacks sont
  oracle (`haystack_session_ids == answer_session_ids`). **21 questions** sans
  preuve annotée au niveau tour restent présentes pour l'évaluation des réponses.
- SHA-256 dataset :
  `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c`.
- MiniLM déjà en cache : snapshot
  `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, **91 578 415 octets**.
  Aucune dépendance ni modèle téléchargé.
- Recherche des noms LongMemEval/leaderboard dans Downloads, Desktop, dossiers
  partagés, v4 des deux dépôts, clone et cache Hugging Face : aucun S/règlement
  courant trouvé. Ne pas extrapoler cette observation à un jeu S caché sous un
  autre nom ; fournir son chemin s'il existe.

Le lancement réel du script avec `--limit 3` a refusé **avant tout worker/modèle**,
code **2** : `nice: setpriority: Operation not permitted`, puis
`STOP: Cannot enforce nice >= 15; workload refused`. La lecture indépendante de
`sysctl vm.swapusage` est aussi refusée par le sandbox. Trace :
`.local-qualification/eval-lot-v1/guard-preflight-271aa3e.log`.
Disque observé à cette étape : 10 113 184 KiB libres (≈10,36 Go décimaux).
**Aucun score ni IC mesuré n'est publié ; aucune fausse vitesse/position top 1.**

## Claude : importer le bundle, relire, puis exécuter hors sandbox

Le bundle du lot est `.local-qualification/eval-lot-v1.bundle`. Base requise
`4281bef3f275da0874792d735a220fcf22595112` ; les livraisons intermédiaires sont
incluses. La base minimale 940a04e n'existe pas dans le dépôt alpha-ops inspecté
(probable import dans un autre dépôt/historique) : ce bundle complet évite cette
dépendance manquante, sans réécrire la branche de Claude.
Si la branche de Claude a avancé depuis, fusionner le bundle sans perdre ses
commits d'intégration multi-clients/GitHub ; ne pas cherry-pick puis annoncer le
SHA original comme source. Le code moteur n'est pas modifié par ce lot.

Priorités de revue adverse : absence de passage des annotations au client,
comptage des encodeurs/warmups, fermeture des sessions MCP séparées du worker,
garde fail-closed, conservation des dénominateurs et séparation Recall@k/
couverture du contexte complet. Protocole :
[`e2e-quotes-v1.md`](../bench/protocols/e2e-quotes-v1.md) ; contrat T25 :
[`AML_ADAPTER_DRAFT.md`](AML_ADAPTER_DRAFT.md).

Sur **ce clone Codex**, commencer par un smoke réel de trois cas, cinq systèmes,
sans autre campagne en parallèle :

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
scripts/run_eval_lot.sh 271aa3e7dee5120aebd839c3175c3e7dfdbf4778 eval-smoke-v1 \
  --limit 3 --run-seconds 600
```

Attendre la fin ; vérifier absence de défaillance dans `report.json`, IDs de
provenance, compteurs/timings non inventés, aucun processus MCP privé restant.
Un contexte complet non applicable peut être normal, mais doit être documenté.
Si modèle/interface/provenance échoue, corriger et requalifier la source avant
la campagne complète ; ne pas lancer 500 cas pour reproduire le même défaut.

Puis, si le smoke est vert et swap/disque autorisent le travail :

```sh
scripts/run_eval_lot.sh 271aa3e7dee5120aebd839c3175c3e7dfdbf4778 eval-oracle-500-v1
```

Budget gelé : 3 600 s, 120 s/cas ; chaque système séquentiel, nice effectif ≥15,
CPU/batch 4/BLAS 1 ; stop swap >8,5 Go décimaux ou disque <5 Go. Le script n'abaisse
jamais ces protections. Une campagne interrompue ne devient pas un résultat
complet. Ne pas écraser les dossiers : pour une reprise autorisée, nouveau nom.
Si 3 600 s ne suffit pas, conserver le diagnostic puis décider **avant** une
nouvelle campagne d'un budget supérieur ; ne pas retirer les questions lentes.

Si un S existe déjà localement, le même script peut l'utiliser sans téléchargement :

```sh
scripts/run_eval_lot.sh 271aa3e7dee5120aebd839c3175c3e7dfdbf4778 eval-s-v1 \
  --data /CHEMIN/LOCAL/longmemeval_s.json --variant s --limit 500
```

Ne pas exécuter le chemin indicatif sans le remplacer. Le lanceur refuse les
haystacks entièrement oracle présentés comme S. Il valide la taille de chaque
entrée, les alignements et l'absence de doublons d'identité. Aucun compte/provider
LLM requis, aucun port réseau, aucune soumission, aucun magasin existant touché.

Rendre `frozen.json`, `freeze-receipt.json`, `predictions.jsonl`, `report.json`,
`resource-samples.json`, `proofs.json` et logs (fichiers benchmark uniquement).
Les IC 95 % et deltas sont bootstrap groupés, seed 20261009, 2 000 tirages ;
moins de deux groupes observables => IC indisponible. Les champs d'exact match
extractif ne sont pas le juge LME officiel. Une performance MiniLM diagnostique
n'est pas celle du profil Granite en production.

Claude met à jour TASKS/HANDOFF et la mémoire partagée depuis son périmètre, avec
ces états et les nouvelles preuves. Codex n'a fait aucun appel mémoire/réseau
dans ce lot sous la contrainte hors réseau ; aucun fichier externe modifié.

## Décisions à obtenir de Romain (pas de nouvelle approbation du choix SQLite)

1. Fournir un corpus S déjà local ou décider d'une acquisition future hors de ce
   lot hors réseau ; fournir aussi les règles/harness AML actuels comme copie
   locale versionnée pour finir la conformité T25.
2. Fixer la métrique cible et le seuil de non-régression **avant** une évaluation
   indépendante. Le classement top 1 reste un objectif, pas un statut ; aucune
   soumission ne résulte de ce lot. Prévoir séparément réponse LLM/juge et tâches
   agentiques, puis deux semaines d'usage interclients selon le plan.
3. S2 SQLite demeure autorisé par le plan existant ; commencer ce lot après revue
   du pipeline. Les transactions moteur, migration/récupération, sauvegarde et
   qualification de panne sont encore nécessaires avant de déclarer l'ALPHA
   industriellement prête. La publication 4.0.1 et les clients branchés n'en
   constituent pas la preuve.
