# H2 — parité lexical / sémantique sur BEAM 100K : exécution (v1)

Statut : **harnais livré et manifeste `h2-lexical-parity-v1` gelé ; aucune récupération exécutée sur
BEAM, aucun rappel calculé.** Contrat : [LEXICAL_PROFILE_4_1_0 §10](LEXICAL_PROFILE_4_1_0.md). Mesure
rétrospective, sans réglage, sans indépendance statistique. La revue Codex du gel précède toute exécution.

## Ce qui est gelé

| Élément | Valeur |
|---|---|
| Commit source du harnais | `63b8c2b0fc0550cc60481f6d4da5d45f0d2900bd` |
| Dossier du plan (hors Git) | `/Users/romain/memory-os-local/codex_runs/h2-410-plan-v1` |
| SHA-256 de `manifest.json` | `067fc008144d225c04f29505c7f4ec69cab25c808905e6b8ac995c20aa47c059` |
| SHA-256 de `plan.json` (porté par le manifeste) | `994a92fc0f811f61b36638d7a64b293910bd04050a80a1f4ca02f9e66e476a02` |
| Corpus | `beam_100k.jsonl`, `613b85b9…fa18a3`, révision `3205395e…82dbb03` ; 20 conversations, 5 732 messages, 400 questions, 355 scorées |
| Bras | `lexical` (`Config.lexical`, `lex-4.1.0-v1`) puis `semantic` (profil par défaut : Granite `835ad140…d469d65c2`, C4) ; `sqlite_s1` neuf par bras et par conversation ; récence désactivée |
| Écart de configuration entre bras | exactement `retrieval_profile` et `use_dense` (vérifié par le code, configuration complète dans le manifeste) |
| Métrique | Recall@10 des tours annotés après empaquetage (top 10, 12 000 octets), citations de 1 024 caractères |
| Statistique | delta apparié lexical − sémantique ; bootstrap par conversation entière, 2 000 réplicats, seed `20261010`, percentile 95 % (rangs 49 et 1949 des tirages triés, comme `beam_campaign.summarize`) |
| Seuil | rappel lexical ≥ rappel sémantique − 2/100 **et** borne basse de l'IC95 ≥ −3/100, en arithmétique exacte |
| Décision | seuil atteint avec 400/400 exécutées dans les deux bras et 0 échec → le lexical peut devenir le défaut recommandé ; sinon le sémantique reste recommandé, lexical = profil « léger », écart publié. Jamais de revendication de rappel supérieur |

Le dossier du plan contient `manifest.json` (validé par `bench/evaluation_manifest.py` en mode
`retrospective_no_selection`), `frozen.json` (verrou au format `--freeze`), `plan.json`, `splits.json`
(partition unique `evaluation`, 400 identifiants opaques), `requirements.lock`, `source.tar`
(`git archive` du commit) et `receipt.json`.

## Commandes

Depuis le worktree, avec l'interpréteur `${MEMORY_EVAL_PYTHON:-~/.memory-os-runtime/venv-4.0.8/bin/python}`
(lecture seule), hors ligne, `nice 15` :

```bash
cd /Users/romain/memory-os-local/codex_runs/feature-410-h2

# Déjà fait (lecture, empreintes et comptages uniquement ; refuse un dossier existant non vide) :
bash scripts/run_beam_h2.sh prepare \
  --plan-dir /Users/romain/memory-os-local/codex_runs/h2-410-plan-v1 \
  --data /Users/romain/Memory-os-ai/v4/bench/data/beam/beam_100k.jsonl \
  --source-commit 63b8c2b0fc0550cc60481f6d4da5d45f0d2900bd

# Contrôle du verrou, sans moteur :
~/.memory-os-runtime/venv-4.0.8/bin/python v4/bench/evaluation_manifest.py \
  /Users/romain/memory-os-local/codex_runs/h2-410-plan-v1/manifest.json \
  --check-lock /Users/romain/memory-os-local/codex_runs/h2-410-plan-v1/frozen.json

# Facultatif, recommandé avant la mesure : même gel, lanceur, workers et scoreur sur 4 conversations
# INVENTÉES (aucune donnée BEAM ; charge Granite ; résultat marqué "synthetic": true, jamais H2) :
bash scripts/run_beam_h2.sh selfcheck \
  --plan-dir /Users/romain/memory-os-local/codex_runs/h2-410-selfcheck-v1 \
  --source-commit 63b8c2b0fc0550cc60481f6d4da5d45f0d2900bd

# LA mesure, une seule fois, après le verdict Codex sur le gel :
bash scripts/run_beam_h2.sh retrieve \
  --plan-dir /Users/romain/memory-os-local/codex_runs/h2-410-plan-v1 \
  --manifest-sha256 067fc008144d225c04f29505c7f4ec69cab25c808905e6b8ac995c20aa47c059
```

`retrieve` retourne 0 si la mesure est complète (seuil atteint ou non), 1 si elle est incomplète ou
invalide, 2 sur refus avant tout worker.

## Sorties (`<plan>/retrieval/`)

- `lexical/`, `semantic/` : `rankings.jsonl` (par question : identifiants des tours retenus après
  empaquetage, sans catégorie ni annotation), `attestation.json`, `worker.log`, `progress` ;
  `semantic/usage.json` (appels d'encodeur, device et dtype réellement utilisés). Les stores sont
  supprimés après chaque conversation.
- `questions.jsonl` : une ligne par question avec le rappel de chaque bras — **écrit seulement si les
  deux bras sont complets**.
- `result.json` : `executed` / `expected` / `failed`, `scored`, `recall_at_10` des deux bras, `paired_delta`
  (moyenne, IC95, valeurs exactes), `point_criterion_met`, `ci_criterion_met`, `threshold_met`,
  `decision`, attestations des bras (lexical : `ml_free`, modules ML chargés ou tentés ; sémantique :
  révision, device, dtype), empreintes des fichiers.
- `resources.json` : swap et disque libre au début et à la fin.

## Ce que `retrieve` refuse

Avant tout worker, entre les bras, puis dans chaque worker : empreinte du manifeste différente de
`--manifest-sha256` ; verrou, plan, partitions, archive source ou verrou d'environnement modifiés ;
valeurs gelées du code (seuil, seed, réplicats, budgets, capture) différentes du plan ; commit source ou
arbre de travail différents (`v4/src`, `v4/bench/e2e`, `bench/evaluation_manifest.py`, lanceur) ; SHA-256
du corpus ; configuration effective d'un bras ; paquets de l'interpréteur ; fichiers de l'instantané
Granite. Un dossier `retrieval/` existant est refusé : une tentative par manifeste, jamais de reprise.

Dans les workers : le bras lexical exige un interpréteur neuf, bloque et consigne toute tentative
d'import de `torch`, `sentence_transformers`, `transformers` ou `faiss`, et échoue s'il y en a une ; le
bras sémantique exige `HF_HUB_OFFLINE=1`, atteste la révision Granite chargée et échoue si elle diffère.
Les sockets IP sont interdites dans les deux.

## Ce qui invalide la mesure

- Toute modification du corpus, des bras, du seuil, de la statistique ou du code après observation.
- Un bras en échec, une question manquante, un arrêt de ressources (swap > 8,5 Go, disque libre < 5 Go,
  8 h au total, 1 h sans progression) : `threshold_met` est faux, **rien n'est scoré**, le sémantique
  reste recommandé. Aucune relance sous ce manifeste.
- Un arrêt avant le scoreur n'a rien observé (les classements existent, aucun rappel n'est calculé). Un
  nouveau gel dans un nouveau dossier n'est légitime que pour un défaut d'outillage, déclaré, avec les
  mêmes valeurs gelées, et le dossier en échec conservé.

## Différences avec la campagne v2, au-delà des bras nommés par le §10

1. Outils `memory_chat_save` / `memory_search` appelés par `server.dispatch` dans le processus du bras,
   et non par MCP stdio : le serveur privé d'évaluation n'accepte que des stores JSONL. Même espace de
   capture (`beam-c4-v1-capture`), mêmes citations, même recherche (`kinds=["fact"]`, une requête).
2. Bras sémantique = défauts du commit (`device="auto"`, lot 32, dtype natif, longueur 512), pas le
   CPU / lot 4 de v2 ; variables MPS du service (`PYTORCH_ENABLE_MPS_FALLBACK=0`, seuils 0,4 / 0,3)
   consignées. Le device et le dtype résolus sont enregistrés, pas imposés.
3. Le scoreur tourne dans le superviseur, après les deux bras : aucun score partiel n'existe.
4. Seuil et intervalle calculés en fractions exactes (mêmes tirages que v2, égalité testée) : `≥`
   ne dépend pas d'un arrondi binaire. Seul le rappel est calculé (pas de MRR).
5. L'audit de recoupement LongMemEval-S et l'empreinte du parquet ne sont pas rejoués (non requis par
   le §10) ; `PROVENANCE.json` et l'empreinte du JSONL le sont.
6. Plafond de 100 000 appels d'encodeur (v2 en comptait 16 075) : dépassement = mesure invalide.

## Durée et mémoire attendues (lecture du code et des temps v2, non mesurées ici)

- Lexical : quelques minutes, sans modèle.
- Sémantique : 10 à 30 min si `auto` résout MPS (29 s médian par conversation dans `mps-beam-reuse-v1`,
  lot 4 float32 ; le profil natif / lot 32 n'a pas été chronométré dans ce harnais) ; 4 à 5 h si le
  device résolu est le CPU (14 813 s d'ingestion en v2). Granite pèse 220 Mo ; RSS attendu de 1,5 à
  3 Gio, allocations MPS plafonnées par le seuil 0,4.

## Tests

`v4/tests/test_beam_h2_410.py` (35 tests, fixtures synthétiques uniquement) : gel et validation en mode
rétrospectif ; refus sur plan, manifeste, verrou, partitions, corpus, commit, code, environnement,
configuration d'un bras et constantes modifiés ; verdict mécanique (atteint, bornes exactes, critère
ponctuel seul, IC seul, IC absent, échec, question manquante) ; déterminisme du bootstrap et identité
avec `beam_campaign.summarize` ; worker lexical réel sans import ML ; bras en échec jamais scoré.
