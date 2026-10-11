# 4.0.6 — profil d'installation qualifié et choix du magasin

Décision Romain : 2026-10-11. Implémentation : 2026-10-12. Candidate future
4.0.6, version des sources encore 4.0.5. Pas de publication/bascule/migration.

## Décision : pins exacts + attestation, plutôt que bornes mineures

La résolution neuve non bornée de Claude donnait torch 2.14.1 / transformers
5.19.0, différents des versions des qualifications copy/brief/MPS. Des bornes
`>=2.10,<2.11` laisseraient encore passer des correctifs non qualifiés. Les
versions ci-dessous sont donc des contraintes **exactes dans le METADATA**.
Les paquets transitifs numériques/tokenizer sont contraints directement pour
éviter une dérive silencieuse ; ils étaient déjà installés/exigés.

| Paquet | Version |
|---|---|
| torch | 2.10.0 |
| sentence-transformers | 5.2.3 |
| transformers | 5.2.0 |
| numpy | 2.4.2 |
| faiss-cpu | 1.13.2 |
| huggingface-hub | 1.5.0 |
| tokenizers | 0.22.2 |
| safetensors | 0.7.0 |
| scikit-learn | 1.8.0 |
| scipy | 1.17.1 |

Preuve primaire locale : METADATA des distributions du runtime 4.0.5, lu par
`importlib.metadata` sans importer modèle/torch ; valeurs identiques dans le
runtime de qualification. Les quatre premières correspondent au profil
neural attesté ; les autres figent le voisinage installé, sans prétendre que
chacune a une campagne d'équivalence distincte.

Le ledger `memory_os_v4/resources/qualified_runtime_v1.json` est embarqué dans
le wheel/descripteur. La garde `ops.qualified_runtime` lit uniquement les
versions installées : paquet absent/différent (même patch ou suffixe local) →
`runtime_unqualified` en preview ; application refusée avant journal, token,
ressource, service ou client. Aucun downgrade/pip automatique. `/ready` conserve
son profil moteur existant ; quickstart compare aussi ses quatre versions
neuronales avant d'acquitter l'installation. Doctor vérifie **les versions du
serveur déclaré**, pas celles du processus client, et signale une dérive.

Ce n'est pas un verrou intégral des 70 paquets, une signature ni une garantie
numérique inter-machines. Les versions Python/macOS/MPS et l'intégrité des
ressources restent à qualifier. MCP/pydantic et autres transitifs restent sous
leurs contraintes existantes ; la résolution propre complète, `pip check` et
les TCP de cette candidate restent des gates. Toute future mise à jour de la
pile numérique exige mise à jour du ledger, requalification et revue explicite.

## scipy et scikit-learn : conservation justifiée

METADATA sentence-transformers **5.2.3** déclare, hors extras : transformers,
huggingface-hub, torch, numpy, **scikit-learn et scipy**, typing_extensions et
tqdm. scikit-learn 1.8.0 exige lui-même scipy>=1.10.0. Son utilitaire
`sentence_transformers/util/similarity.py` importe `sklearn.metrics` au niveau
module et utilise les conversions scipy pour le sparse. Ces dépendances ne
peuvent pas devenir des extras propres à notre application tout en conservant
ce wrapper et sa distribution intacte. Aucun calcul d'entraînement requis par
notre usage n'est revendiqué ; c'est une dépendance de chargement/packaging.
Les supprimer demande un autre chargeur, une preuve fonctionnelle et une
requalification, hors ce correctif. Aucun `--no-deps` présenté comme install
utilisateur normale pour cacher leur poids.

## SQLite+tâches pour le quickstart neuf ; JSONL explicite

```sh
# Candidate seulement ; aperçu sans écriture :
memory-os-admin quickstart --clients claude-code,codex
memory-os-admin quickstart --clients claude-code,codex --backend sqlite --report
memory-os-admin quickstart --clients claude-code,codex --backend jsonl --report
```

| Installation | Magasin / variables du service |
|---|---|
| Neuve, défaut | `~/.local/share/memory-os/store` ; `MEMORY_V4_STORE_BACKEND=sqlite_s1`, `MEMORY_V4_WORK_STATE=true` |
| Neuve, `--backend jsonl` | chemin JSONL historique ; `MEMORY_V4_STORE_BACKEND=jsonl`, `MEMORY_V4_WORK_STATE=false` |
| `MEMORY_V4_DIR` explicite | conservé ; validation du backend/cible, refus si magasin incompatible |
| Plist existant sans choix explicite | backend lu comme **indice**, jamais comme preuve de propriété ; pas de changement du plist |
| Changement explicite de backend existant | refus du plist différent ou du magasin non vide ; procédure de migration séparée |

SQLite refuse toujours le chemin réservé historique `~/.memory-os-v4` : aucune
relaxation de sécurité pour contourner cela. Redirection vers le chemin neuf
uniquement si l'ancien chemin et le plist sont absents et sans `MEMORY_V4_DIR`
explicite. Un historique existant est conservé/refusé, jamais laissé de côté
silencieusement. Le défaut global `Config.store_backend=jsonl` est **inchangé** :
le choix au quickstart est écrit explicitement dans le plist, pas imposé aux
serveurs existants. C4, auto/MPS/native/batch32, révision Granite, cache/namespace
et fichiers de l'encodeur ne changent pas. Registre inactif.

Correction nécessaire : `--warm` appelait seulement `get_engine()`, alors que
SQLite+tâches démarre volontairement sans recherche. Il appelle maintenant
`ensure_retrieval()` avant de servir. Sans `--warm`, le démarrage sans modèle
est conservé ; tests avec vraie autorité SQLite privée et encodeur hash stub,
sans modèle neuronal. Quickstart exige ready **avec recherche**, backend et
tâches concordants ; le relais prouve aussi `memory_task_list` en lecture seule
pour SQLite. Aucun task create implicite ni démo sans consentement.

L'installation renvoie `ops_env` avec les chemins effectifs (sans secret), à
utiliser pour doctor/health/backup dans un shell neuf. Elle ne change pas le
shell ou les défauts OpsSettings et ne programme pas les jobs (D2 à venir).
Avec ces variables, sauvegarde SQLite explicite :

```sh
memory-os-admin doctor
nice -n 15 memory-os-admin backup --backend sqlite --online          # simulation
nice -n 15 memory-os-admin backup --backend sqlite --online --apply  # GO opérateur
nice -n 15 memory-os-admin restore-drill --backend sqlite --apply --reconcile-forgets
```

Ces commandes ne sont **pas à exécuter sur la production de Romain pour ce lot**.
Choisir un magasin/ops/token privés pour l'essai. Pour JSONL, suivre son runbook
existant ; une migration ou activation existante conserve son GO distinct.

## Preuves, mesures et suite Claude

Le D1b `e1c8af2` a été qualifié par Claude hors sandbox : builds direct/via-sdist,
installations, garde des marqueurs et preview installé sans écritures. Cela ne
qualifie pas encore le nouveau profil/default SQLite de ce correctif.

Qualification locale du correctif : **302 tests réussis, zéro échec/skip**,
lint des fichiers modifiés et règles fatales runtime/scripts verts. Preuves :
`.local-qualification/distribution-profile-sqlite-light-v3/`. Les petits wheels
PEP 517 reproduisent maintenant les dépendances core du projet et vérifient
leurs pins dans le METADATA ; démarrage SQLite réellement privé, task API et
warmup avec encodeur hash stub, aucun chargement de modèle neuronal. La garde
de chaque divergence de version, de paquet absent et de backend existant est
testée. Les TCP/builds complets restent à Claude, sans affirmer leur résultat.

Le trial neuf a été arrêté correctement à 4.39 GB libres après 37 s de pip,
depuis 5.58 GB : **>1.2 GB écrits, installation incomplète**, pas de temps
install→handoff mesuré. Les ≈212 MB /70 paquets +≈222 MB ressources venaient de
la résolution **non qualifiée** (versions trop récentes) : ne pas publier
434 MB comme taille du profil verrouillé. Refaire dry-run/rapport de résolution
et mesure après ces pins. Prévoir davantage d'espace (8 GB libres comme cible
provisoire, pas une borne garantie), maintenir la réserve dynamique de 5 GB et
swap ≤8.5 GB. Aucun nettoyage hors du périmètre autorisé ni contournement.

Depuis la racine du clone, après import du bundle nommé et fin du tour Codex,
dossiers neufs, après libération du disque par Romain :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
.local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
scripts/qualify_distribution_d1a.py --build \
--source-commit SOURCE_PROFILE_SQLITE_COMPLET \
--build-python /opt/homebrew/bin/python3.11 \
--output .local-qualification/distribution-profile-sqlite-artifacts-claude-v1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
.local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
scripts/qualify_405_test_isolation.py --suite full \
--output .local-qualification/distribution-profile-sqlite-full-claude-v1
```

Le premier script exige maintenant pins METADATA et versions effectivement
installées concordants, puis deux previews installés : backend SQLite, tâches
true, runtime qualifié, HOME identique avant/après. Les installations de cette
qualification réutilisent les dépendances ; aucun téléchargement et aucune
preuve d'espace/temps utilisateur propre.

Ensuite Claude relance son trial propre avec artefact/SHA de ce nouveau rapport,
HOME/port/label privés et aucun cache pip. Critères à conserver : pins effectifs,
`pip check`, guard ≥5 GB/swap ≤8.5 GB, ressource hors-ligne attestée, /ready
SQLite **retrieval ready** + tâches, doctor sans FAIL, save/search via relais,
cycle create/list/CAS task dans ce seul magasin d'essai, écriture/configs réelles
de l'utilisateur inchangées, preuve JSONL optionnelle sur un second magasin
privé distinct. Aucun nouveau scénario de bench ni inférence/API sur Codex.
