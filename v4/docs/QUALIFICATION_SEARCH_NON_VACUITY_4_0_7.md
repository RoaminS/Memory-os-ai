# Qualification de recherche non vide — 4.0.7

Amendement daté du 13 octobre 2026, scellé par le commit de ce document **avant
la relance** ; aucun résultat de copie v2 observé par Codex. Aucun changement
moteur, modèle, query, `at_time`, C4 ou version. Production reste 4.0.5.

## Critère figé et preuve

- Les dix requêtes existantes de `release_copy_worker.QUERIES` sont inchangées
  (identiques à celles de l'activation) ; un même `at_time` est fixé par plan.
- L'équivalent serveur du scope « tous projets » est **`project=None` au
  moteur**, jamais le libellé littéral `"*"`. `top_k=10`, `rerank=False` inchangés.
- **Au moins un hit réel sur chacune des dix requêtes en baseline**. Chaque
  worker doit couvrir exactement ces dix requêtes et donner 1 à 10 IDs valides,
  distincts par requête. Un seul zéro, une omission ou une requête ajoutée refuse
  la qualification, même si les autres bras sont identiques. Ce seuil diagnostique
  prouve que l'égalité porte sur des résultats ; il ne mesure pas leur pertinence.
  Exiger dix résultats aurait inutilement interdit les petits magasins.
- `search_hit_counts` expose les comptes par worker/requête (dans les rapports
  workers et le verdict agrégé). Le validateur recompte les IDs ; un compte déclaré
  divergent est refusé. Le plan contient `search_non_vacuity` : minimum=1,
  top_k=10, engine_project=null. L'activation historique expose aussi `hit_count`
  par ligne de probe ; le contrôle prepared réutilise le même validateur.
- Les égalités de classements ne sont validées qu'après ce contrôle. Les tableaux
  de tâches/blocs/reçus/journal peuvent être vides légitimement : l'inventaire
  exhaustif garde ses contrôles de schéma, identité et lignes, sans exigence
  arbitraire de contenu pour ces catégories facultatives.

## Audit des autres harnais `qualify_*.py`

| Chemin | Constat / traitement |
|---|---|
| `qualify_schema8_copy` | Dix listes vides acceptées ; scope corrigé et gate strict, comptes dans chaque worker/verdict. |
| `qualify_sqlite_release_copy` / `release_406_checks` | Même erreur de scope ; worker corrigé, gate indépendant des booléens déclarés, comptes publiés. Les anciens manifests ne sont pas retouchés. |
| `qualify_sqlite_activation` / `sqlite_activation_worker` | Troisième erreur de scope. Corrigée ; `compare_probes` refuse baseline ou candidat vide, même hors production. Le helper est copié et son SHA scellé dans le plan. |
| `qualify_sqlite_prepared_store` | Héritait de l'erreur via `probe`/`compare_probes` ; corrigé par les fonctions partagées. |
| `qualify_release_copy` (JSONL) | Scope déjà correct, mais égalité de classements vides acceptée. Gate des dix probes ajouté ; comptes par worker. |
| Brief L1a/A1/P-A | Égalités de répétition/path possibles sur un projet vide volontaire ; elles sont des contrôles de déterminisme, pas de rappel. L1a exige 100 inputs/400 mesures, A1 12 mesures et compte de pool=20 ; P-A exige des mesures/quantiles. Rien à rendre artificiellement non vide. |
| L7b/journal/S4 | List50 exige 50 tâches, population=10k ; journal exige 50 événements/page et séquences 1..5000 ; S4 exige son effectif et 5 oublis réconciliés. Aucun succès identifié par simple égalité de deux pages vides. |
| Isolation/distribution | `before == after` peut être une arborescence HOME vide pour la preuve zéro-écriture : cela est intentionnel ; checks de commandes, installation, sorties et profils distincts. Pas une preuve de clients connectés. |

Les inventaires de profils/cache/sources sont en plus confrontés à des propriétés
obligatoires (profil MPS attesté, schéma, identité, empreintes et seeds synthétiques).
Cet audit n'est pas une qualification rétroactive des runs historiques. Les scripts
manifest incluent désormais le SHA du helper : tout ancien manifeste exigeant les
anciens outils refuse les outils modifiés. Ne pas contourner ce refus ; un futur
manifeste de qualification doit être scellé explicitement. Aucun artefact4.0.6
n'est reconstruit ni publié dans ce lot.

## Claude : replay séquentiel, dossiers neufs

Après import du bundle `release/4.0.7`, arbre synchronisé, aucun autre run actif :

~~~sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1 MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export MEMORY_TEST_HASH_V3=1 MEMORY_V4_EMBED_MODEL=hash
"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-search-nonvacuity-full-claude-v1
~~~

Ensuite seulement, la commande source-copy v2 de
[SWITCH_4_0_7_SQLITE.md](SWITCH_4_0_7_SQLITE.md) (nice15, séquentielle).
Le lanceur remplace le modèle hash par le profil réel du plist. Aucun run sur la
copie de production n'est exécuté par Codex. Conserver tous les anciens rapports,
sans en modifier flags, SHA ni données ; le v2 doit démontrer les classements avec
comptes non nuls. Une nouvelle comparaison wheel/sdist4.0.7 sera exigée au RC.

## Preuves locales

- Tests ciblés : **121 passed**, 9,98 s ; `tests/test_search_qualification.py`,
  `tests/test_release_406.py`, `tests/test_release_copy.py`,
  `tests/test_sqlite_activation_tools.py`, `v4/tests/test_schema8_copy_contract.py`.
  Hash synthétique uniquement, aucune copie de production ni téléchargement.
  Preuve : `.local-qualification/nonvacuity-targeted-v2.{log,xml}` dans le
  clone persistant de livraison sous `codex_runs/schema8-nonvacuity-work`.
- Une première commande ciblée avait un répertoire parent basetemp manquant ;
  ce lancement n'est pas une preuve verte. Le replay v2 ci-dessus utilise le
  parent créé ; le test de refus accepte le nouveau diagnostic Non-vacuity.
- Deux lints projet verts (`src tests scripts` et contrôle E9/F sur
  `src tests v4/src v4/tests v4/bench scripts`), `git diff --check` vert.
- **Suite complète non exécutée (disque)** : garde effective 3 000 000 000
  octets, mesure au démarrage **2 718 740 480 octets**, zéro subprocess pytest
  lancé par la campagne complète. Refus conservé dans
  `.local-qualification/nonvacuity-full-v1/report.json`, source `4043680`.
  La livraison n'est donc **pas full-qualified**, ni TCP-qualified, ni
  release-qualified. Claude exécute la suite complète avant la copie v2,
  quand le disque et le swap satisfont les gardes. Aucune autre activité
  machine n'a été interrompue.
- Aucun changement à `src`, `v4/src`, `pyproject.toml`, version ou poids.
  Aucun ancien rapport/manifeste/artefact modifié.
