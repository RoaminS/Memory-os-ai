# Livraison à Claude — heldout-answer-beam-500k-v1

Date2026-10-10. Source exécutable figée :
**`31b7084ba766ff8d414a8ae70f692176426d9a4e`**.
Base du bundle : `2f2225212afb8cc61bd21092e2d1c8474b055d15`.
Ref exposée : **`refs/heads/fix/v4-production-foundation`**.

Le lot ne modifie aucun fichier `v4/src` : vérification exhaustive des sources
contre la copie4.0.3 qualifiée réussie. Les artefacts4.0.3 qualifiés ne sont pas
reconstruits par cette livraison. Aucune inférence, API, téléchargement de
corpus/modèle, publication, bascule ou écriture directe en production exécutée.

## Import du bundle nommé

Dans le clone de réception, arbre propre hors configurations locales conservées :

```bash
git bundle verify heldout-answer-campaign-v1.bundle
git fetch ./heldout-answer-campaign-v1.bundle \
  refs/heads/fix/v4-production-foundation
git merge --ff-only FETCH_HEAD
```

Dans le clone Codex où `.git` est en lecture seule, substituer aux commandes git
le préfixe `git --git-dir=.local-qualification/lexical-dev-git`, sans toucher `.git`.
Les fichiers locaux `.mcp.json` et `.vscode/mcp.json` sont exclus des commits.

## Tests légers de Claude, sans modèle ni appels Codex

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/v4/src:$PWD/v4"
/Users/romain/Memory-os-ai/.venv/bin/python -m pytest -q -m 'not slow' \
  -p no:cacheprovider --basetemp=.local-qualification/heldout-claude-tests v4/tests
ruff check --no-cache --config pyproject.toml src tests scripts
ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
bash -n scripts/run_answer_campaign.sh
```

Les 10 tests TCP `test_server_http_e2e.py` et `test_ops_e2e.py` sont inclus dans la
commande de Claude. Codex les exclut à cause du sandbox ; `slow` est exclu partout
pour ces checks. Le moteur est inchangé, aucune nouvelle qualification sémantique
ne fait partie du lot. Tests synthétiques dédiés : `test_answer_campaign.py` et
`test_beam_acquisition.py` ; contrats historiques : `test_beam.py`,
`test_answer_pilot.py`, `test_pilot_warning_recovery.py`.

## Ordre d'exécution

1. Lire le [protocole et les commandes exactes](ANSWER_HELDOUT_CAMPAIGN_v1.md).
2. **Téléchargement de BEAM500K en attente du GO distinct de Romain** : aucune
   commande de téléchargement avant ce GO. Fiche officielle :35 conversations,
   86,1 Mo décompressés ; taille physique du seul parquet vérifiée upstream,
   plafond110Mo, CC-BY-SA-4.0, révision3205395e. Pyarrow est déjà installé dans
   `/opt/homebrew/bin/python3.11`, aucune installation dans le runtime.
3. Après acquisition approuvée, `prepare` ne fait aucune inférence. Le préflight
   exige300–350questions et>=30groupes indépendants après exclusions. Si refus,
   conserver `audit.json/REFUSED.json`, demander un corpus réellement nouveau ;
   ne jamais relâcher les seuils ou simplement choisir d'autres probes observés.
4. Si l'audit passe, noter SHA256(plan), lancer `retrieve` puis un batch `answer`.
   Vérifier `progress.json`, reprendre manuellement avec `--resume` après arrêt
   de batch ou quota seulement. Pas de scheduler, nouveau plan, relance d'un
   appel pending/au résultat incertain ou boucle de tentatives.
5. `report` seulement quand toutes les tâches sont complètes. Remettre les preuves
   et discuter le résultat sans comparaison avec les scores GPT-4o des papiers.

Le GO de campagne couvre ce retrieval et les appels Codex après audit réussi.
L'acquisition reste le seul accord manquant. La disponibilité du quota doit être
vérifiée par l'opérateur ; le harness ne sait pas mesurer le quota restant.

## Preuves locales Codex

- Source figée31b7084 : `verify_source(clone, source_commit, code_record())` passe.
- `engine_matches_copy` : toutes les sources `v4/src` identiques à la source de
  `.local-qualification/copy-4.0.3-v2/report.json` ; aucun modèle chargé.
- Suitev4 sansTCP/slow : **1090passed, 1deselected**, 35,06s ; commande identique à
  celle ci-dessus avec `--ignore=v4/tests/test_server_http_e2e.py
  --ignore=v4/tests/test_ops_e2e.py`. Log :
  `.local-qualification/heldout-delivery-tests.log`.
- Tests ciblés campagne/acquisition : **27passed** ; contrats historiques et
  nouveaux tests campagne : **92passed**. Tout est synthétique, aucun appel CLI
  d'inférence ou de réseau dans les tests.
- Deux commandes lint ci-dessus : vert ; `bash -n` : vert.
- Les tests TCP doivent être exécutés par Claude hors sandbox. Les données
  BEAM500K ne sont pas téléchargées ; aucun plan d'exécution n'est présenté
  comme scellé ou audit réussi avant acquisition. Seul le protocole est figé.
