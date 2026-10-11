# D1a — extras, identité installée et ressources explicites

Historique D1a du 2026-10-11 ; état au 2026-10-12 : sources **4.0.6 candidate**, non publiée/non déployée. Les preuves précédentes portent sur les commits pré-release ; requalification des artefacts 4.0.6 et trial neuf requis. Disque initial ~3 GB, remonté à ~5.7 GB en fin de tour. Tests synthétiques permis ; builds du projet différés, car le swap n’est pas attestable dans le sandbox. Aucun changement du serveur, du magasin ou des clients de production.

## 1. Revue des livrables de Claude

Plan de distribution contresigné : mention reportée selon la copie signée de Claude. Validation externe `mcp-publisher validate server.json` : valide (1.4.1), sans login/publication. Le consommateur du registre reste à qualifier en D4.

README et `docs/CLIENTS.md` intégrés avec corrections : `[ops,brief]`, 11 entrées dont Windsurf distinct de Devin, `quickstart` annoncé futur et non commandé comme existant, setup ne démarre pas de service et n’écrit pas les configs client, tâches opt-in, preuves limitées au déploiement observé. Les scores LME/BEAM et leurs profils/limites sont explicites. Documentation sans lien public vers des fichiers encore absents du dépôt gelé ; matrice embarquée sous `share/doc/memory-os-ai`. La page PyPI actuelle ne change pas tant que Romain ne publie pas une release.

**Contrat quickstart accepté avec amendements Q1–Q6 :**

1. Preview : aucune écriture, y compris répertoire, token, journal, modèle, cache ou téléchargement. Il peut lire les seules configs nécessaires et les ressources ; aucun fichier d’auth des assistants. Ne pas appeler `setup`/`token create` en preview : ces commandes créent le token. Employer les générateurs purs et `plan`. Ne pas déduire « réseau disponible » d’une inference ou requête authentifiée.
2. Download : consentement séparé et explicite pour les ressources absentes, montant affiché, même en mode non interactif. `--apply` seul ne télécharge pas. Contrôle ressources/identité avant toute activation.
3. Undo : comparer la valeur **sémantique de notre entrée** au résultat appliqué ; si notre entrée a été modifiée depuis, refuser et proposer une revue. Restaurer uniquement l’entrée, conserver les autres changements. Le contrôle entre preview et application reste strict sur le fichier avant mutation ; pas de succès sur une config devenue invalide.
4. Propriété du service : port, label launchd et HTTP 200 ne suffisent pas. Vérifier executable/plist, token, identité de build et magasin avant `--repair` ; ne jamais arrêter/réparer un service étranger. Une readiness work-only ne prouve pas que le modèle et la recherche sont disponibles.
5. Sauvegardes/configs : mode 0700/0600 ; elles peuvent contenir des secrets d’autres entrées. Les diffs/rapports n’affichent que notre entrée, avec valeurs sensibles masquées, aucun bearer ou configuration étrangère. `--report` anonymise chemins personnels/identifiants, ne contient ni mémoire ni contenu des backups.
6. Demo : projet unique `memory-os-demo-<id>` avec consentement, jamais collision/nettoyage d’un projet existant. Une écriture incertaine exige résolution du reçu, sans rejeu. Configuré, connecté et usage observé restent trois niveaux distincts.

Périmètre Claude : développer dans `~/memory-os-local/distribution/quickstart/` les fichiers destinés à `v4/src/memory_os_v4/ops/quickstart.py` et `v4/tests/test_quickstart.py`, avec le contrat amendé. Ne pas modifier l’arbre Codex pendant un tour. Codex relira/intégrera et branchera la CLI. Mise à jour décision Romain du 2026-10-11 : le quickstart 4.0.6 neuf choisit SQLite+tâches par défaut, JSONL sur option ; aucune migration implicite ou modification du défaut Config des serveurs existants.

## 2. Interface pour quickstart et utilisateur du wheel

API publique interne, sans modèle ni client :

```python
from memory_os_v4.ops.provision import plan, apply, ProvisionError
preview = plan(resources=("granite", "cl100k"), cache_dir=cache, table_path=table)
# preview : ready, files[path/bytes/sha256/target/present], revision,
# catalog_sha256, download_bytes, peak_additional_bytes_upper_bound, env.
result = apply(preview, download_approved=explicit_consent)
# result.ok ET result.ready requis ; exception/refus ou ok=false bloque quickstart.
```

`plan` : aucune création ni réseau ; vérification SHA-256 séquentielle des ressources présentes (pas de modèle chargé). `apply` revalide l’aperçu, refuse fichier/révision contradictoire et verrou existant, télécharge séquentiellement uniquement avec consentement, vérifie tailles/SHA/Git blobs, conserve staging/provenance en cas d’échec. Écrit le ref HF atomiquement après vérification de tous les téléchargements. Pas de démarrage/arrêt de serveur, token ou config client.

CLI **candidate uniquement, absente du PyPI 4.0.5 actuel** :

```sh
memory-os-admin provision --resources granite,cl100k
memory-os-admin provision --resources granite,cl100k --apply --download-approved
```

Granite : révision `835ad14087e140460703cf0fae09f97d469d65c2`, 220 227 876 octets, Apache-2.0. Inventaire dérivé en lecture seule du snapshot opérateur qualifié, avec SHA-256 et Git/LFS blobs. **Avant téléchargement de fichiers modèle absents, validation obligatoire des métadonnées officielles de cette révision**, tailles/blobs/licence ; aucun accès aux fichiers d’auth Hugging Face. Cette vérification distante est simulée dans les tests ; sa compatibilité réelle reste à prouver par Claude. Pas d’ONNX/OpenVINO (~1.2 GB pour tout le dépôt), code distant ou pickle téléchargé. [Source officielle](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/tree/835ad14087e140460703cf0fae09f97d469d65c2).

cl100k : 1 681 126 octets, SHA `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`, [source officielle](https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken), tiktoken MIT. Modèle+table ≈222 MB ; **ce n’est pas le volume des dépendances Python**. Acquisition macOS : disque ≥5 GB, swap mesurable ≤8.5 GB, garde répétée, cap total 300 MB et 600 s. Échec conservé, aucun fallback réseau pendant l’encodage.

Chemins par défaut : cache `HF_HUB_CACHE`, sinon `$HF_HOME/hub` ou `~/.cache/huggingface/hub` ; table `MEMORY_V4_CL100K_PATH` ou `$XDG_CONFIG_HOME/memory-os/cl100k_base.tiktoken`. Utiliser `result.env` dans le plist : chemins absolus, offline=1. `setup` propage désormais table/cache explicites. La commande ne définit pas ces variables dans le shell parent.

## 3. Extras et identité

Core conserve numpy/FAISS/MCP/transformers/sentence-transformers/huggingface_hub, donc torch transitif. `[documents]` fournit PDF v4 ; `[legacy-v3]` fournit OCR/Office/PDF/requests historiques ; `[audio]`, `[doc-legacy]` et `[cloud-*]` dépendent désormais de v3 pour préserver leur usage historique. OCR nécessite aussi Tesseract système, `.doc` les outils externes historiques ; les extras ne les installent pas. `[brief]`/`[ops]` restent distincts. Le paquet v3 exporte ses classes paresseusement ; son CLI sans extra répond avec une commande utile, son help n’instancie pas le moteur.

Hooks setuptools : `memory_os_build.py` + `setup.py`. Source commit **vérifié par archive Git**, inventaire runtime/entrées de packaging scellé, génération `_build_identity.json` dans le payload installé. Sdist contient `_memory_os_build_source.json` : reconstruction sans Git acceptée si inventaire intact. Pas d’inventaire circulaire, horodatage volatile ou chemin personnel. Editable conserve le mode développement sans descripteur de release.

Build : `MEMORY_OS_BUILD_SOURCE_COMMIT=<sha complet>` ; pour un export propre sans Git local, `MEMORY_OS_BUILD_GIT_DIR=<métadonnées Git accessibles>` permet la vérification par archive. `MEMORY_V4_BUILD_COMMIT` est une déclaration **runtime**, pas un substitut à la preuve du builder. Le SHA construit est celui des sources, avant un éventuel commit ultérieur du reçu de qualification.

Installed : descripteur manquant/modifié/extra/version erronée → `build_unqualified`, même avec env. Valide → `identity_source=embedded_descriptor`, env concordante tolérée/contradictoire refusée ; doctor explique qu’elle est inutile. Source : `env_declared`, `git_checkout` propre, sinon `unqualified`. `/ready` revérifie les fichiers d’une identité embarquée à chaque requête ; `/health` conserve l’identité constatée au démarrage (liveness). Pas de signature fournisseur ; remplacer simultanément payload/descripteur reste possible. Aucun changement du modèle, C4, auto/MPS/native/batch32 ou namespace de vecteurs.

## 4. Preuves et commandes Claude

Tests locaux légers : 228 réussites, 0 échec/skip (script sans `--build`), XML/logs sous `.local-qualification/distribution-d1a-light-v2/`. Il couvre aussi deux vrais mini-artefacts setuptools de quelques KB, dont une reconstruction depuis sdist sans Git. Cela **ne remplace pas** le build du projet ni les environnements propres. Les fixtures jetables sont nettoyées seulement après succès ; logs/XML et tous les fichiers d’un run échoué sont conservés.

Suite racine sans modèle (`MEMORY_TEST_HASH_V3=1`) : 630 pass, 2 refus environnementaux dans `test_sqlite_activation_tools.py`, dont les outils/tests n’ont pas changé : disque <5 GB, avant toute activation. Après libération externe du disque, reprise ciblée **2/2 pass**, soit les 632 cas couverts sur ces deux runs. Log/XML initiaux conservés dans `.local-qualification/distribution-d1a-v1/v3-legacy.*`, reprise `legacy-guard-retry.*`. Lint runtime/nouveaux fichiers avec config racine : vert ; règles fatales E9/F de src/v4/src/scripts : vertes. Le lint E/F sur tous les anciens tests expose aussi des violations de style préexistantes, hors ce lot.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
  .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
  scripts/qualify_distribution_d1a.py \
  --output .local-qualification/distribution-d1a-claude-v1
```

Après retour disque ≥5 GB, swap ≤8.5 GB, interpréteur ayant **déjà** build/setuptools≥77/wheel/pytest :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
  .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
  scripts/qualify_distribution_d1a.py --build --source-commit SOURCE_D1A_COMPLET \
  --build-python /opt/homebrew/bin/python3.11 \
  --output .local-qualification/distribution-d1a-artifacts-claude-v2
```

Le script ne télécharge aucune dépendance. Il vérifie wheel direct, wheel issu de sdist, SHA-256/descripteurs identiques, installations hors dépôt, cinq entrypoints et preview installé sans modèle. Ces smokes réutilisent les dépendances existantes : **ne pas annoncer une installation core propre ni un gain disque mesuré**. Le runtime brief local n’a pas `wheel`, mais `/opt/homebrew/bin/python3.11` a déjà setuptools 80.9.0, wheel 0.45.1, build 1.4.0 : `--build-python` sépare cet interpréteur des tests/venvs privés. Aucun téléchargement de dépendance implicite ; swap/disk contrôlés avant et pendant les subprocess de build/install.

Restent avant release : installs propres core / ops+brief / v3+ops+brief, `pip check`, volumes reçus/venv/cache/pic/température/durée ; vérification upstream et acquisition sur cache neuf avec GO, puis semantic/copy/relay/TCP/full v3/v4/T05 après quickstart. Aucune mesure lourde avec le disque actuel. Fixtures téléchargées uniquement sur dossier privé neuf ; aucune manipulation de cache/service de production. Ressources présentes : preview hors réseau puis contrôle SHA local suffit ; absent : consentement requis, pas de téléchargement demandé par ce livrable.

## 5. Correctif PEP 517 (2026-10-11)

La qualification réelle D1a v1 a refusé le build direct : `setuptools.build_meta`
exécute `setup.py` sans ajouter son dossier aux imports. Le mini-test précédent
appelait `python setup.py …` : cette invocation ajoutait le dossier implicitement,
donc ne couvrait pas le défaut. Le refus et les preuves v1 restent conservés.

`setup.py` ajoute maintenant son propre dossier absolu avant l'import du hook,
sans dépendre du cwd ou du `PYTHONPATH` de l'opérateur. L'attestation reste stricte.
`MANIFEST.in` incluait déjà explicitement `memory_os_build.py`.

Nouvelle régression : export synthétique propre par `git archive`, sans `.git`,
`PYTHONPATH` vide, mêmes paramètres `[build-system]` que le projet ; invocation
exacte `python -m build --no-isolation --wheel` avec preuve Git externe scellée,
puis `--sdist` et reconstruction du wheel depuis le sdist sans Git ni variables
de build. Elle vérifie la présence et les octets du helper dans le sdist, ainsi
que les descripteurs identiques et le commit attendu. Avant correctif : échec
reproduit `ModuleNotFoundError` ; après : succès. Qualification locale légère v3 :
229 tests réussis, zéro échec/skip ; lint des fichiers modifiés et règles fatales
runtime/scripts verts. Preuves : `.local-qualification/distribution-d1a-light-v3/`
et `.local-qualification/distribution-d1a-pep517-fix-v1/`. Aucun installateur de dépendance,
modèle, téléchargement ou appel d'inférence. `--build-python` sert aussi à ce test ;
hors qualification, `MEMORY_TEST_BUILD_PYTHON` permet de choisir un interpréteur
déjà équipé. Le test refuse si cet interpréteur manque des outils requis.

Relancer la commande `--build` ci-dessus dans le dossier **v2 neuf**, avec le SHA
complet du bundle correctif à la place de `SOURCE_D1A_COMPLET`. Ce succès miniature
ne qualifie pas les artefacts complets : la garde disque/swap et les preuves
wheel/sdist/installations de Claude restent nécessaires. Ne pas réutiliser le
dossier refusé `distribution-d1a-artifacts-claude-v1`.

## 6. Marqueurs des extras (2026-10-11)

Claude a qualifié les builds direct et via-sdist au commit `29b5d4d`, puis
l'installation a refusé les métadonnées : `v3` est une version PEP 440 valide,
ce qui déclenche une comparaison de versions dans certains évaluateurs de
marqueurs `extra`. L'extra candidat devient **`legacy-v3`**, dans les dépendances
transitives, messages et documentation. La release PyPI 4.0.5 ne change pas.

`scripts/check_extra_markers.py` est une garde hors réseau : refuse tout nom
 d'extra interprétable comme version et évalue chaque marqueur du METADATA
pour tous les extras déclarés et l'extra vide, avec `packaging` ET la copie
vendue par pip. La régression PEP 517 utilise les vrais groupes optionnels
sur un petit paquet synthétique ; les deux wheels sont vérifiés. La qualification
complète applique cette même garde avant la première installation. Preuve locale :
31 tests D1a réussis, sans modèle ; preuve finale du projet à refaire dans un
dossier neuf `distribution-d1b-artifacts-claude-v1`.
