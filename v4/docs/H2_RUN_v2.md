# H2 — parité lexical / sémantique sur BEAM 100K : exécution (v2)

> **Remplacé par [H2_RUN_v3.md](H2_RUN_v3.md).** Le gel v2 (`h2-410-plan-v2`, manifeste `6181b179…8d0309`) a été
> jugé NON VALIDÉ par Codex le 22/11 (bytecode du superviseur, origines de modules, interruption du
> scoreur) et n'a jamais été exécuté sur BEAM ; il est conservé comme preuve.

Contrat : [LEXICAL_PROFILE_4_1_0 §10](LEXICAL_PROFILE_4_1_0.md). Mesure rétrospective, sans réglage, sans
indépendance statistique. Ce document décrit le harnais `v4/bench/e2e/beam_h2.py` dans sa révision de gel 2,
qui répond au verdict Codex « NON VALIDÉ » sur le gel v1. Il fait partie des fichiers attestés par le gel :
il ne contient donc pas l'empreinte du manifeste, consignée dans `H2_FREEZE_v2.md` et dans
`receipt.json` du dossier du plan.

**Aucune récupération n'a été exécutée sur BEAM et aucun rappel n'a été calculé.** Le gel v1
(`h2-410-plan-v1`) n'a jamais été lancé ; il est conservé comme preuve.

## Ce qui est gelé

| Élément | Valeur |
|---|---|
| Corpus | `beam_100k.jsonl`, `613b85b9…fa18a3`, révision `3205395e…82dbb03` ; 20 conversations, 5 732 messages, 400 questions, 355 scorées |
| Bras | `lexical` (`Config.lexical`, `lex-4.1.0-v1`) puis `semantic` (profil par défaut : Granite `835ad140…d469d65c2`, C4) ; `sqlite_s1` neuf par bras et par conversation ; récence désactivée |
| Écart de configuration | exactement `retrieval_profile` et `use_dense` |
| Résolution attendue du bras sémantique | `device="auto"` → **MPS**, `embed_dtype="native"` → **`torch.bfloat16`** ; les valeurs de configuration restent les défauts (auto, lot 32, natif), seule l'attente est gelée |
| Métrique | Recall@10 des tours annotés après empaquetage (top 10, 12 000 octets), citations de 1 024 caractères |
| Statistique | delta apparié lexical − sémantique ; bootstrap par conversation entière, 2 000 réplicats, seed `20261010`, percentile 95 % (rangs 49 et 1949 des tirages triés) |
| Seuil | rappel lexical ≥ rappel sémantique − 2/100 **et** borne basse de l'IC95 ≥ −3/100, en arithmétique exacte |
| Décision | seuil atteint avec 400/400 exécutées dans les deux bras et 0 échec → le lexical peut devenir le défaut recommandé ; sinon le sémantique reste recommandé, lexical = profil « léger », écart publié. Jamais de revendication de rappel supérieur |

Le dossier du plan (hors Git) contient `manifest.json` (validé par `bench/evaluation_manifest.py`, mode
`retrospective_no_selection`), `frozen.json` (verrou au format `--freeze`), `plan.json`, `splits.json`,
`requirements.lock`, `runtime.json` (empreinte de contenu du runtime), `source.tar` et `receipt.json`.

## Budgets : unités effectives

Le validateur impose les champs `ingest_tokens`, `context_tokens` et `query_tokens`. Ce protocole n'a
**aucun budget en jetons** : les valeurs portées par ces champs sont les limites en **octets UTF-8**
elles-mêmes (plus aucune estimation « octets / 4 »), et le bloc `budgets.units` du manifeste le déclare
champ par champ.

| Champ du manifeste | Valeur | Unité et contrôle |
|---|---:|---|
| `memory_bytes`, `ingest_tokens` | 16 000 000 | octets UTF-8 de citations ingérées par conversation ; contrôlé avant capture |
| `context_tokens` | 12 000 | octets UTF-8 du contexte empaqueté, au plus 10 citations ; appliqué par `pack()` dans les deux bras |
| `query_tokens` | 4 096 | octets UTF-8 de la question, transmise telle quelle aux deux bras ; contrôlé au gel (question la plus longue : 369 octets) et avant chaque recherche |
| `model_calls` | 100 000 | appels d'encodeur du bras sémantique (plafond préalable ; v2 en comptait 16 075) ; contrôlé après chaque conversation puis avant le score ; 0 appel LLM |
| `wall_seconds` | 28 800 | les deux bras ensemble ; 3 600 s sans progression arrêtent aussi |

`512` est `embed_max_seq_length` : la troncature **interne à l'encodeur sémantique**, un réglage du
système consigné dans sa configuration, pas le budget de requête commun.

## Commandes

Depuis le worktree, par le lanceur uniquement (interpréteur
`${MEMORY_EVAL_PYTHON:-~/.memory-os-runtime/venv-4.0.8/bin/python}` en lecture seule, hors ligne, mode
« chemin sûr » `-P`, `nice 15`). `SOURCE`, `PLAN` et `SHA` sont ceux de `H2_FREEZE_v2.md`.

```bash
cd /Users/romain/memory-os-local/codex_runs/feature-410-h2

# Gel : lecture, empreintes et comptages uniquement ; refuse un dossier existant non vide.
bash scripts/run_beam_h2.sh prepare --plan-dir PLAN \
  --data /Users/romain/Memory-os-ai/v4/bench/data/beam/beam_100k.jsonl --source-commit SOURCE

# Autocontrôle sur 4 conversations INVENTÉES (aucune donnée BEAM ; charge Granite ; "synthetic": true) :
bash scripts/run_beam_h2.sh selfcheck --plan-dir NOUVEAU_DOSSIER --source-commit SOURCE

# LA mesure, une seule fois, après le verdict Codex sur le gel :
bash scripts/run_beam_h2.sh retrieve --plan-dir PLAN --manifest-sha256 SHA
```

`retrieve` retourne 0 si la mesure est complète (seuil atteint ou non), 1 si elle est incomplète ou
invalide, 2 sur refus avant tout worker.

## Garanties d'exécution (révision 2)

1. **Tous les contrôles finaux précèdent le score.** Après les deux bras, le superviseur rejoue
   l'attestation complète du gel (contenu du runtime compris), le garde-fou de ressources (swap, disque)
   et le délai total, puis vérifie les attestations des workers ; alors seulement il calcule les scores,
   en mémoire, et écrit `questions.jsonl` puis `result.json` en dernier. Toute erreur — garde final,
   dérive du gel, attestation manquante, défaut du scoreur, interruption — laisse un `result.json`
   explicite avec `threshold_met: false`, sans aucun rappel et sans `questions.jsonl`.
2. **Device et dtype.** Avant toute ingestion, sur un store vide, le worker sémantique charge l'encodeur
   et refuse si la résolution n'est pas MPS / `torch.bfloat16` ou si la révision n'est pas la révision
   gelée. Le relevé d'usage doit rapporter le même device et le même dtype.
3. **Relevé d'usage obligatoire.** Un relevé absent, mal formé, sans au moins un appel par question, ou
   au-dessus du plafond invalide la mesure ; il n'est jamais assimilé à zéro appel. Le crochet de
   comptage tokenise une seconde fois et écrit un fichier à chaque appel : les durées enregistrées sont
   descriptives, **aucune revendication de temps** n'est faite.
4. **Runtime attesté par contenu.** `venv-4.0.8` enchaîne par `zz_shared_deps.pth` vers
   `/Users/romain/Memory-os-ai/.venv/lib/python3.11/site-packages`. Le gel enregistre `sys.path` et le
   SHA-256 de **chaque fichier** de la bibliothèque standard, des deux `site-packages`, de l'exécutable
   et de `pyvenv.cfg` (`runtime.json`, environ 29 000 fichiers, 5 s). Le superviseur le vérifie au
   départ et avant le score ; chaque worker le vérifie avant toute ingestion puis, à la fin, exige que
   chaque module chargé provienne d'un fichier gelé (ou d'un fichier attesté du clone) au contenu
   inchangé. Les caches de bytecode ne sont jamais lus (`PYTHONPYCACHEPREFIX` vers un dossier vide,
   `-B`) ; le répertoire courant n'est jamais sur `sys.path` (`-P`).
5. **Granite lié au cache gelé.** L'environnement du worker est reconstruit : toute variable héritée
   `HF_*`, `HUGGINGFACE_*`, `TRANSFORMERS_*`, `SENTENCE_TRANSFORMERS_*`, `XDG_CACHE_HOME`, `TORCH_*`,
   `PYTORCH_*`, `PYTHON*`, `DYLD_*`, `LD_*`, `MEMORY_*` est retirée, `HF_HOME` et `HF_HUB_CACHE` sont
   fixés au cache gelé, et le worker refuse si son environnement diffère. Le chargement reste par nom
   (profil par défaut), hors ligne. Avant toute ingestion le worker prouve que ce qui est **chargé** est
   le contenu des fichiers hachés dans le plan : chaque tenseur du modèle chargé est comparé à
   `model.safetensors`, le tokenizer chargé à `tokenizer.json`, le pooling à `1_Pooling/config.json`, et
   le nom du modèle doit se résoudre vers l'instantané gelé.
6. **Source attestée.** Tous les fichiers suivis de `v4/src` et `v4/bench/e2e` (ressources comprises),
   le validateur, le protocole v1, le lanceur, `v4/tests/test_beam_h2_410.py`, ce document, le contrat
   `LEXICAL_PROFILE_4_1_0.md` et `BEAM_PROTOCOL_v1.md` doivent être identiques au commit source ; aucun
   fichier étranger ne peut se trouver dans les arbres importables. Leurs empreintes sont dans
   `plan.json` (`attested_files`), celles des tests et documents aussi dans `receipt.json`, et leurs
   versions dans `source.tar`.
7. **Bras lexical sans ML.** Interpréteur neuf ; toute tentative d'import de `torch`,
   `sentence_transformers`, `transformers` ou `faiss` est bloquée, consignée, et fait échouer le bras.
   Sockets IP interdites dans les deux bras.

## Sorties (`<plan>/retrieval/`)

- `lexical/`, `semantic/` : `rankings.jsonl` (par question : tours retenus après empaquetage, sans
  catégorie ni annotation), `attestation.json` (environnement, runtime, origines des modules chargés ;
  sémantique : révision, device, dtype, liaison à l'instantané, usage), `worker.log`, `progress`,
  `semantic/usage.json` ; `refused.json` si un refus survient avant ingestion.
- `resources.json` : swap et disque libre au début et au contrôle final (écrit avant le score).
- `questions.jsonl` et `result.json` : écrits en dernier ; `result.json` porte `executed` / `expected` /
  `failed`, `scored`, `recall_at_10`, `paired_delta` (moyenne, IC95, valeurs exactes),
  `point_criterion_met`, `ci_criterion_met`, `threshold_met`, `decision`, `invalid_reasons`.

## Une seule tentative ; aucune reprise sans revue

Un dossier `retrieval/` existant est refusé. **Il n'existe aucune permission générale de re-gel.** Dès
qu'un worker a écrit des classements, une observation existe, même si rien n'a été scoré. Après un
arrêt, toute nouvelle tentative exige : une revue explicite de l'incident par Codex, la conservation
intégrale des traces (dossier du plan, journaux, classements), et l'absence de toute sélection fondée
sur les sorties. Sans cette revue, la décision par défaut s'applique : le sémantique reste recommandé.

Invalident la mesure : toute modification du corpus, des bras, du seuil, de la statistique, du code, du
runtime ou de l'instantané Granite ; un bras en échec ; une question manquante ; un arrêt de ressources
(swap > 8,5 Go, disque libre < 5 Go, 8 h au total, 1 h sans progression) ; une attestation manquante.

## Différences avec la campagne v2 (BEAM C4), au-delà des bras nommés par le §10

1. `memory_chat_save` / `memory_search` appelés par `server.dispatch` dans le processus du bras, et non
   par MCP stdio (le serveur privé d'évaluation n'accepte que JSONL) ; aucune qualification du transport
   MCP n'en découle. Même espace de capture, mêmes citations, même recherche (`kinds=["fact"]`).
2. Bras sémantique = défauts du commit (auto, lot 32, natif, longueur 512), pas le CPU / lot 4 de v2 ;
   variables MPS du service (`PYTORCH_ENABLE_MPS_FALLBACK=0`, seuils 0,4 / 0,3).
3. Seuil et intervalle en fractions exactes (mêmes tirages que v2, égalité testée) ; seul le rappel est
   calculé.
4. L'audit de recoupement LongMemEval-S et l'empreinte du parquet ne sont pas rejoués (non requis par le
   §10) ; `PROVENANCE.json` et l'empreinte du JSONL le sont.

## Durée et mémoire attendues (non mesurées sur BEAM)

Lexical : quelques minutes. Sémantique sur MPS : 10 à 30 min (29 s médian par conversation dans
`mps-beam-reuse-v1`, lot 4 float32 ; le profil natif / lot 32 n'a pas été chronométré). Un device autre
que MPS est refusé avant ingestion. Granite pèse 220 Mo ; RSS attendu de 1,5 à 3 Gio. Les attestations
de runtime ajoutent environ 5 s par vérification complète (cinq au total).

## Tests

`v4/tests/test_beam_h2_410.py`, fixtures synthétiques uniquement : gel rétrospectif ; refus sur chaque
élément modifié ; verdict mécanique ; bootstrap ; worker lexical réel sans ML ; et, pour la révision 2,
un test par refus nouveau : garde final après les deux bras, dérive vue à la fin, défaut du scoreur,
interruption, runtime modifié à version constante, module chargé hors des chemins gelés, fichier
étranger ou ressource modifiée dans la source, environnement hérité, device / dtype / révision
divergents, relevé d'usage absent ou invalide, poids / tokenizer / pooling différents de l'instantané.
