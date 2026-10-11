# D1c — refus setup et contrat cold/warm corrigés

2026-10-12. Au-dessus de `3074ed8`, version des sources encore 4.0.5 pour la
future candidate 4.0.6. Aucun changement de production, téléchargement ou
inférence neuronale côté Codex. Le rapport refusé
`.local-qualification/distribution-profile-sqlite-full-claude-v1` reste conservé.

## 1. Setup S4 : erreur d'identité = refus propre avant token

`Config.from_env()` et `build_identity()` peuvent refuser une déclaration
malformée/contradictoire par `ValueError`. La prévalidation des templates SQLite
capture cette erreur et retourne **2**, JSON `ok=false`,
`code=sqlite_s4_not_configured`, message générique « token unchanged ».
Elle ne normalise pas une déclaration invalide, ne crée aucun token/répertoire
et n'affiche pas les valeurs fournies. Les erreurs d'autres types ne sont pas
masquées. Le refus existant backend non-SQLite/source non qualifiée reste intact.

Régressions : vrai SHA invalide/majuscule/tronqué ; erreur d'identité ou config
injectée ; aucun appel de token create et arborescence inchangée ; réponse JSON
et absence de valeurs sensibles dans stdout/stderr. Le module S4 complet passe.

## 2. TCP : séparer service vivant, autorité work prête et recherche chaude

Le harness reçoit `warm=True|False`. Le test cold avec modèle délibérément
indisponible lance **sans `--warm`**, attend `/health`, puis ouvre l'autorité work
par `memory_task_list` authentifié via le relais. Il vérifie ensuite `/ready` :
`ready_work_only`, tâches actives, retrieval/encoding non chargés, write_mode
read_write, owner injecté depuis clientInfo. `/ready` seul ne doit pas créer un
moteur absent ; attendre son 200 avant toute lecture aurait bloqué 30 s.

Test TCP distinct `test_L7b_warm_available_hash_ready_before_first_tool` :
SQLite+tâches avec encodeur hash réellement disponible hors réseau, `--warm`,
ready/retrieval/encoding actifs **avant** le premier outil, puis search vide via
relais. Il ne qualifie pas Granite ni la performance MPS. Les tests existants
unitaires de warmup sur une vraie autorité SQLite avec factory hash conservent
la preuve d'hydratation unique. Les autres scénarios du harness restent warm.

Nouvelle preuve sans TCP dans la sandbox : Popen/HTTP/run simulés, exécution du
vrai code inline de construction argv et vérification cold→`/health` sans warm,
warm→`/ready` avec warm. Aucun bind, serveur réel ou assistant lancé.

## 3. Preuves locales et commandes Claude

- Qualifier léger : **307 tests réussis**, zéro échec/skip ; XML/logs dans
  `.local-qualification/distribution-d1c-refusal-cold-light-v1/`.
- `v4/tests/s4/test_backup.py` : **44 réussites**, zéro échec, XML/logs dans
  `.local-qualification/distribution-d1c-refusal-cold-v1/s4.*` ; fixtures de
  succès nettoyées par le cap S4 existant (16 MiB/cas), preuves conservées.
- Lint fichiers modifiés et règles fatales runtime/scripts verts.
- Aucun verdict TCP/build complet de Codex : sandbox/mesure swap insuffisantes.

Après import du bundle nommé, fin du tour Codex et garde disque/swap satisfaite,
depuis la racine du clone, **dossiers neufs** :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
.local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
scripts/qualify_405_test_isolation.py --suite full \
--output .local-qualification/distribution-d1c-refusal-cold-full-claude-v1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
.local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
scripts/qualify_distribution_d1a.py --build \
--source-commit SOURCE_REFUSAL_COLD_COMPLET \
--build-python /opt/homebrew/bin/python3.11 \
--output .local-qualification/distribution-d1c-refusal-cold-artifacts-claude-v1
```

La première commande contient notamment les deux cas TCP cold/warm et le refus
S4 corrigé. Les artefacts doivent être reconstruits : `ops/cli.py` a changé,
les empreintes de `3074ed8` ne qualifient pas ce nouveau payload. Aucun test
ne supprime/renomme le rapport refusé précédent. Relire côté Claude le changement
d'attente `/health` pour cold et le premier read work, puis poursuivre son trial
v2 selon le protocole déjà relu et les GOs existants, pas sur la production.
