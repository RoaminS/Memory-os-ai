# S2 — preuves locales, 2026-10-09

Code qualifié : `b239e1e728a3fad1b64a7091361000fb85f18bb4`, parent
`b1795a9df98a5a973b2c7e30e662408f9329625b`.
Checkout isolé : `.local-qualification/store-s2-work` dans le clone Codex ;
aucune modification du code actif de la campagne run-dev.

Runtime : Python **3.11.14**, SQLite **3.53.2** ; seuil requis 3.51.3 satisfait.
Exécution séquentielle, `PYTHONDONTWRITEBYTECODE=1`, `HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, `PYTHONPATH=v4/src`, aucun modèle neural ou réseau.

## Résultats

| Preuve | Résultat |
| --- | --- |
| Commande pytest complète de [SQLITE_S2](SQLITE_S2.md), avec basetemp `.local-tests` et interpréteur `/Users/romain/Memory-os-ai/.venv/bin/python` | **223 passed**, 3 avertissements SWIG/faiss, **25,84 s**, aucun SKIP/échec. Log `.local-s2-tests.log` livré avec le dossier de preuves. |
| Matrice d'exceptions du lot composite | **42 frontières**, toutes examinées par comparaison intégrale des lignes de cinq tables ; rollback exact avant COMMIT, issue incertaine après COMMIT. |
| Matrice de crash de processus | **42 SIGKILL aux frontières + 1 SIGKILL après acquittement fsyncé** ; zéro acquittement perdu, zéro succès pour un lot partiel. |
| Gestionnaire réel `memory_chat_save`, assertion renforcée après la suite | **1 passed**, 15 deselected, **0,40 s** ; cible de remplacement absente, des INSERT d'éléments ont effectivement eu lieu avant l'erreur, puis un ROLLBACK unique, aucune donnée/reçu partiel. Log `.local-s2-handler-tests.log`. |
| Lint CI large : `ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F` | All checks passed. |
| Lint CI complet des répertoires historiques : `ruff check --no-cache --config pyproject.toml src tests scripts` | All checks passed. |
| `git diff --check` du checkout isolé | Aucun défaut. |
| Dans le clone actif : `git --no-optional-locks diff --exit-code b1795a9 -- v4/src v4/bench` | Code retour 0 ; sources et lanceurs de la campagne préservés. Les fichiers de configuration des clients préexistants ne font pas partie du bundle. |

Les 85 injections sont les itérations de deux tests pytest, pas 85 tests pytest
indépendants. Les témoins d'acquittement sont indépendants du SQLite. Les magasins
jetables sont fermés avant copie ; chaque processus de panne est attendu avant
le suivant, avec timeout 20 s.

## Revue de Claude et acceptation

Relire `memory.py`, `sqlite_store.py`, `sqlite_engine.py`, `readiness.py`,
`test_sqlite_engine.py` et les deux documents S2. Vérifier en particulier la
transaction englobante des gestionnaires, la condamnation après erreur enfant,
le reçu comme dernière écriture, les issues COMMIT/rollback incertaines,
la reconstruction complète du retriever et l'upgrade transactionnel S1→S2.

Tous les tests ont pu s'exécuter en sandbox ; **aucun test TCP n'attend Claude**.
Rejouer les commandes de SQLITE_S2 sur le runtime cible dans un checkout isolé,
ou après la fin de run-dev. Une copie fermée S1 doit être conservée avant son
upgrade de schéma ; l'exécutable S1 ne sait pas ouvrir S2.

S3 import/export réversible strict reste à faire ; S4 backup/restore SQLite et
pannes électriques/I/O réelles restent à qualifier ; S5 regroupe les portes
industrielles. Une réussite locale S2 n'autorise pas une bascule de production.
Pas de changement de version, publication PyPI ou opération GitHub dans ce lot.
