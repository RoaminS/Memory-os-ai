# B4/B5, collecteur P-B et B9 — livraison du 13 octobre 2026

Base 385141d, branche release/4.0.7. Contrats contresignés, contre-amendement
B legacy <=3 accepté. Champs/fixtures/prototype gelés dans 3a714be avant code.
B4 aa61335, B5 7c5ae27, conservation des métadonnées/sécurité 5a91b62,
collecteur/B9 ad3b2db et préflight tokenizer 534e3eb.
Version 4.0.6.dev0 ; production 4.0.5 et arbre principal non modifiés.

## État exact et preuves

- B4 : compensation par nouvelle assertion actuelle, jamais réouverture du
  passé ; CAS/digest/source historique vérifiés ; oubli/sensibilité priment.
  Champs descriptifs d'origine (entities,tags,importance,confidence,keywords,
  facets) matérialisés dans prepared_facts, évitant la perte de critical/constraint.
  Provenance liée par parent/source_revision et audit, auteur courant déclaratif.
- R1 : 16 contrôles synthétiques FR/EN scellés avant code, SHA
  2e947a69f69b0c52a345dd855d8c52438ce0ef856eb386f0520db11be382192d.
  La même heuristique examine texte ET métadonnées ; SHA/UUID/URL publics restent
  acceptés. Ce filtre n'est pas une garantie universelle de détection de secrets.
- B5 : schéma SQLite **8**, événements/reçu dans la transaction d'autorité,
  pagination SQL, cursor MAC instance/génération/scope, couverture prospective.
  Retour JSONL refusé pour changes ; export sémantique JSONL ne promet aucun
  journal de changements ni curseur SQLite. Aucun nouveau tool.
- R2 : health JSON/Markdown et doctor journal_size ; seuil logique 10 MiB,
  aucune purge automatique. CLI journal-prune offline, preview par défaut,
  autorisation maintenance nécessaire, writer lease, schéma déjà actif exigé.
- R3 : catalogue 23 outils, **10814 tokens cl100k**, +8,03 % depuis d003e54
  (10010), sous +10 %. Prototype et mesure finale identiques.
- Régressions finales : 224 tests core/transactions/batch/S4 verts (59,92 s),
  116 contrats B2/B3/B4/B5/collecteur/manifeste verts (4,10 s), puis test physique
  B9 vert (11,02 s). Ces suites se recoupent : **ne pas additionner les nombres**.
- B9 : 30 frontières atteintes, chaque occurrence testée par sortie os._exit,
  source/événement/reçu récupérés ensemble après COMMIT, rien de partiel avant.
  Baseline déjà acquittée conservée, 0 succès partiel, 0 rejeu. Ce n'est pas une
  preuve de panne électrique réelle ni de tous les scénarios possibles.
- Ruff projet + E9,F, diff --check verts. 31 TCP collectés, **pas exécutés ici**.
  Nouveau TCP : test_B4_B5_compensation_and_scoped_feed_through_relay.
- Collecteur : 10 tests de développement propres ; aucune lecture/exécution
  des 40 inputs/gold Claude. 0 neural/API/téléchargement/publication/bascule.

Les échecs de développement/logs sont conservés dans le worktree. Deux corrections
de fixtures S4 adaptent un vrai schéma5 historique (sans table8) et attendent la
version de migration actuelle. Le test de gate TCP restaure seulement le nom
injecté dans sys.modules ; aucune purge globale de modules Pydantic importés.

## Claude — qualification séquentielle après import, sans édition concurrente

~~~sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1 MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 MEMORY_V4_EMBED_MODEL=hash

"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-integrity-b4-b5-full-claude-v1

nice -n 15 "$PYTHON" -B scripts/qualify_integrity_v5_journal.py \
  --output .local-qualification/407-integrity-journal-10k-claude-v1
~~~

Chaque sortie est neuve. Échec : arrêter et préserver ; pas de reprise dans le
même dossier. Full inclut v3/v4 et le manifeste exact de **31 TCP**. Le second
job est séquentiel, guards disque/swap, 10k événements, 100 pages de 50,
p95 <=30 ms préenregistré ; aucun résultat de performance revendiqué ici.

## P-B — collecte candidate seulement, sans scorer

La baseline 385141d ne possède pas B4/B5 : un score comparatif serait trompeur.
Le collecteur livré est mono-candidat ; B2/B3 restent comparés par les régressions
de compatibilité, pas en inventant un succès sur capacités absentes. Aucun
compte de validation/gain n'est revendiqué avant la grille indépendante Claude.

Inputs : PB-001..040, 20 fr/20 en, familles EXACTES identity/correction/
compensation/journal/crash (10 chacune ; dernière chaîne = journal/crash).
Manifest au minimum cases=40, inputs_sha256. Ajouter seal/provenance côté Claude.
Fichier inputs-only ; aucun expect et aucun fichier cases_ ou sealed_v lu.
Binding produit par Claude APRÈS seal : candidate_commit (SHA complet du tip
importé), inputs_sha256, manifest_sha256, bound_at ISO zoné, bound_by.
Le corps d'un résultat preview contient aussi compensation booléen informatif.

Les noms de résultat et bind_preview restent INTEGRITY_V5_FIELDS_FROZEN_v1.
Les dates omises de seed.items prennent le now de fixture explicitement ; aucune
annotation ajoutée au gold. seed.blocks = name/text/project/scope uniquement.
Tasks/dated_fixture/update : modèles existants, ETag du seed lié comme P-A.
JSONL ne peut pas SEED des tasks ni recevoir les labels d'injection SQLite.
Des steps JSONL demandant une capacité non supportée peuvent tester son refus.

Le collecteur conserve enveloppe complète, octets/tokens/SHA, arguments,
historique items/blocs/tasks et reçus ; aucune clé MAC/bearer sauvegardée.
Deux magasins privés par case/path pour répétition. Projection déterministe :
UUID locaux reproductibles, now fixé, chemin du trial remplacé ; seuls les MAC
privés des next_cursor/since_cursor sont omis de la projection, leurs données
de binding restent comparées. Les enveloppes originales sont conservées.

fault.kind=exception seulement dans les inputs du collecteur ; point et
occurrence explicites, absence de déclenchement = refus, jamais succès caché.
process_exit est la matrice séparée B9. COMMIT ambigu : réouverture privée puis
lectures de statut, pas de mutation tant que l'incertitude n'est pas résolue.
Le relais logiciel est le vrai DurableRelay + catalogue + clientInfo, transport
in-process sans réseau ; ce chemin ne remplace pas la preuve TCP qualifiée.

Après full/performance verts ET audit des compatibilités, Claude prépare un
binding sur le tip importé, puis exécute (adapter uniquement les chemins du seal) :

~~~sh
PB_INPUTS=.local-qualification/pb-inputs/inputs_v1.jsonl
PB_MANIFEST=.local-qualification/pb-inputs/manifest_v1.json
PB_BINDING=.local-qualification/pb-inputs/binding_v1.json
PB_SHA=$(shasum -a 256 "$PB_INPUTS" | cut -d ' ' -f 1)
PB_CANDIDATE=$(git rev-parse HEAD)

nice -n 15 "$PYTHON" -B scripts/run_integrity_v5_validation.py \
  --inputs "$PB_INPUTS" --manifest "$PB_MANIFEST" --binding "$PB_BINDING" \
  --inputs-sha256 "$PB_SHA" --candidate-commit "$PB_CANDIDATE" \
  --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-pb-audit-claude-v1

nice -n 15 "$PYTHON" -B scripts/run_integrity_v5_validation.py \
  --inputs "$PB_INPUTS" --manifest "$PB_MANIFEST" --binding "$PB_BINDING" \
  --inputs-sha256 "$PB_SHA" --candidate-commit "$PB_CANDIDATE" \
  --table "$MEMORY_V4_CL100K_PATH" --run \
  --output .local-qualification/407-pb-collect-claude-v1
~~~

Pas de fabrication automatique du seal/binding ni de confirmation de tous les
impacts. Audit refusé = corriger les INPUTS avec amendement daté avant collecte,
jamais interpréter une ambiguïté. Le gold reste chez Claude. Qualification de
release, migration de schéma en production, publication/bascule restent séparées.
