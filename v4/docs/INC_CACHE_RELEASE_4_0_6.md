# Cache SQLite : refus de la copie 4.0.5 → 4.0.6

Date : 2026-10-12. Statut : cause confirmée, correctif **destiné à 4.0.7**,
non publié et non activé. Les artefacts publiés 4.0.6 restent inchangés.
Le refus de qualification 4.0.6 reste valable ; aucune garde n'est levée.

## Cause racine et preuves

`MemoryV4._encode_index_texts()` ajoutait `implementation = __version__` au
profil **interne** utilisé par `SQLiteEmbeddings.encode()`. La clé était :
`sha256(canonical_json(profil_interne) + NUL + texte_indexé)`. Le profil affiché
par `status()['encoding_profile']` ne contenait pas ce champ. Une égalité des
profils affichés ne prouvait donc pas une égalité des clés. Mon attente de zéro
encodage lors de la qualification 4.0.6 était incorrecte : j'avais omis cette
composante de la clé.

Le diagnostic lit le snapshot scellé de
`.local-qualification/copy-4.0.6-sqlite-v1` en SQLite `mode=ro&immutable=1`.
Il ne lit jamais le magasin de production et ne charge aucun modèle.
Le snapshot SHA-256 est
`00b3cc2b33dbea9c3f2326cdab16ea11e341d12fab217d37cbc8f00c3328aec8`.

| Mesure sur les preuves existantes | Résultat |
|---|---:|
| Textes demandés / textes uniques | 1 827 / 1 825 |
| Lignes initiales du cache | 1 826 |
| Textes trouvés avec le préfixe `4.0.5` | 1 827 |
| Textes trouvés avec le préfixe `4.0.6` | **0** |
| Encodages uniques manquants sous `4.0.6` | **1 825** |
| Démarrage baseline / wheel / sdist | 3,836 / 26,945 / 26,475 s |
| Réouverture wheel / sdist | 3,191 / 2,475 s, zéro encodage |
| Dix listes top-10 | Identiques |

La seule modification du champ `implementation` reproduit exactement les
1 825 misses. L'en-tête, les textes et le profil effectif correspondent déjà
aux clés 4.0.5. Ni une perte de cache dans la copie ni le basculement
`_retrieval_initializing` n'est nécessaire pour expliquer ce refus.
`--warm` rend le coût visible avant `/ready`, mais les workers ont explicitement
initialisé la recherche dans les deux versions.

Les « 2 hits » 4.0.6 sont faux : l'ancien compteur soustrayait le nombre de
**misses uniques** du nombre de **textes demandés**. Deux doublons étaient
comptés comme hits alors qu'aucune clé 4.0.6 n'existait. Le correctif distingue
les hits réels par requête et les misses uniques.

## Impact d'une bascule avec le wheel 4.0.6 publié

Sur cette copie, 4.0.6 **ajoute** 1 825 lignes de cache, sans remplacer ni
supprimer les lignes 4.0.5 : 1 826 → 3 651 lignes. Les nouveaux vecteurs
occupent 2 803 200 octets bruts ; le fichier SQLite croît de 3 891 200 octets
(20 492 288 → 24 383 488). La réserve temporaire WAL peut être différente.
L'index en mémoire utilise les vecteurs recalculés. Le surcoût constaté est
environ **23 s au premier démarrage**, pas à chaque réouverture.
Ces chiffres concernent le snapshot de 1 827 éléments, pas une garantie pour
un magasin qui aurait grandi depuis la copie.

**292 des 1 825 vecteurs uniques changent numériquement** : différence absolue
maximale `0.0078125`, cosinus minimal `0.999918395248488`. C'est compatible avec
les variations d'arrondi MPS/bfloat16 et de regroupement des lots ; la cause
exacte de ces écarts numériques n'a pas fait l'objet d'une nouvelle inférence.
Les dix classements testés restent identiques. Cela ne prouve pas l'identité
de tous les classements possibles.

Les workers conservés rapportent des tables d'autorité, une identité et des
comptages identiques. Ce défaut concerne les données dérivées : aucune perte
de faits, provenance, historique, blocs, tâches ou reçus n'a été observée.
Une panne pendant le réchauffement ne transforme pas un lot partiel en
succès acquitté ; les écritures de cache restent transactionnelles. La
production n'a pas été modifiée par Codex.

Un **nouvel utilisateur 4.0.6**, avec un cache vide, ne subit pas ce défaut
de réutilisation inter-version : le premier encodage est normalement nécessaire.
Les mises à niveau ultérieures peuvent toutefois subir le même défaut tant
qu'elles utilisent la clé liée à la version de l'application.

## Correctif destiné à 4.0.7

1. Remplacer la version d'application par `cache_schema: 2`, indépendant
   des publications. Garder intégralement le modèle, la dimension, le texte,
   la fenêtre, le batch, la précision, le device, le namespace, la révision,
   l'empreinte du code de l'encodeur et les versions du runtime attesté.
2. Adopter les clés anciennes **uniquement pour la version déclarée du
   magasin à son ouverture**, si cette version est 4.0.5 ou 4.0.6. Recalculer
   leur clé avec l'attestation **actuelle**, pas avec une attestation historique
   arbitrairement approuvée. Vérifier dimension, SHA-256, taille et finitude.
3. Copier les vecteurs existants octet pour octet sous les clés stables,
   en une transaction. Conserver toutes les anciennes lignes pour le retour
   arrière. Ne pas rechercher une autre version ancienne si la version
   déclarée ne possède pas la clé ; ne pas choisir arbitrairement entre les
   vecteurs 4.0.5 et 4.0.6 lorsqu'ils coexistent.
4. Refuser cette adoption si les poids ne sont pas attestés. Un changement
   de texte, modèle, révision, source, runtime, device, précision, dimension
   ou namespace continue d'imposer un encodage.

Limites : l'adoption ajoute elle aussi des lignes dérivées, mais **sans calcul
neural ni changement numérique**. Les anciennes clés ne sont pas purgées dans
ce lot. Une version historique inconnue reste un cache miss. La version
historique est capturée avant une mutation froide dans le même processus ;
si un ancien cache n'a jamais été adopté et qu'un autre processus a déjà
remplacé sa version de métadonnées par une version inconnue, l'encodeur
recalcule prudemment. Ce correctif ne modifie pas le profil de production.

## Preuves locales et commandes pour Claude

Les tests sont synthétiques, séquentiels, avec l'encodeur hash ; aucune nouvelle
inférence neuronale ou requête d'évaluation n'a été exécutée. Le script optionnel
de rejeu remplace l'encodeur par une attestation factice et **échoue dès qu'un
encodage serait demandé**. C'est une preuve du chemin cache, pas une mesure
de démarrage MPS ni une qualification de publication.

Résultats locaux : **153 tests SQLite/cache/S4 réussis** en 35,03 s,
dont 17 nouveaux cas ; lint des fichiers Python modifiés et `git diff --check`
réussis. Les tests TCP et la qualification réelle wheel/sdist ne sont pas
annoncés comme exécutés dans ce lot.

Sur une **nouvelle copie** du snapshot : 1 827 hits hérités, zéro miss,
zéro encodage ; 1 827 vecteurs d'index historiques identiques octet pour octet ;
toutes les tables hors cache identiques ; réouverture sans encodage ;
empreintes des preuves d'entrée inchangées.

Après import du bundle nommé, uniquement quand aucun autre run n'utilise
l'arbre, depuis le clone :

```sh
PYTHON=.local-qualification/brief-test-runtime-claude-3c-v1/bin/python
env PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  MEMORY_TEST_HASH_V3=1 MEMORY_V4_EMBED_MODEL=hash \
  "$PYTHON" -B -m pytest -q -p no:cacheprovider \
  v4/tests/test_sqlite_cache.py v4/tests/test_sqlite_cache_release.py \
  v4/tests/test_sqlite_engine.py v4/tests/test_sqlite_store.py \
  v4/tests/test_sqlite_migration.py v4/tests/s4 \
  --basetemp=.local-qualification/cache-407-tests-claude-v1 \
  --junitxml=.local-qualification/cache-407-tests-claude-v1.xml
env PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  PYTHONPATH=v4/src "$PYTHON" -B scripts/diagnose_406_cache.py \
  --evidence .local-qualification/copy-4.0.6-sqlite-v1 \
  --output .local-qualification/cache-407-diagnostic-claude-v1 \
  --reproduce-on-copy
ruff check --config pyproject.toml v4/src/memory_os_v4/memory.py \
  v4/src/memory_os_v4/sqlite_embeddings.py v4/tests/test_sqlite_cache_release.py \
  scripts/diagnose_406_cache.py
```

Conserver les sorties si un test échoue ; utiliser un nom neuf pour une reprise.
Ne jamais remplacer le snapshot d'entrée par un magasin actif.

## Décision requise

**Recommandation : garder la production 4.0.5 jusqu'à la qualification d'une
candidate 4.0.7 intégrant ce correctif.** Le présent bundle ne constitue pas
cette publication : version et artefacts 4.0.6 inchangés, pas de bascule.

Si Romain préfère malgré tout basculer sur le wheel publié 4.0.6, l'accord doit
explicitement accepter le ré-encodage initial, la croissance du cache et les
écarts numériques observés. L'ancien critère « zéro encodage » reste échoué ;
un amendement daté doit précéder une nouvelle qualification dans un dossier
neuf. Les autres critères (autorité, identité, top-10 figés, runtime, relais,
doctor, sauvegarde et rollback conservant les écritures récentes) restent
obligatoires. Codex n'autorise ni la bascule ni le changement de critère.


## Rectification du registre de preuves — 13 octobre 2026

**Classements identiques : NON DÉMONTRÉS** pour
`.local-qualification/copy-4.0.6-sqlite-v1`. Baseline, wheel, sdist et leurs
réouvertures ont renvoyé zéro hit sur chacune des dix requêtes : l'égalité des
listes vides ne démontre pas la compatibilité de recherche. Le rapport d'origine
reste inchangé ; ses booléens d'égalité ne constituent plus une preuve recevable.
La production est restée **4.0.5**, la bascule 4.0.6 a été annulée. Les autres
preuves indépendantes restent inchangées (dont les refus/cache, identité,
autorité, TCP et installations propres) ; cette rectification ne les requalifie
pas automatiquement. La future 4.0.7 exige une nouvelle copie et un seuil de
non-vacuité gelé avant exécution. Voir
[le protocole](QUALIFICATION_SEARCH_NON_VACUITY_4_0_7.md).
