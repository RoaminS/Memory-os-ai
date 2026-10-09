# SQLite — P1/P2/P3 et S3 hors ligne

Date : 2026-10-09. Codex ; revue adverse attendue de Claude.
Base du lot : `61eda7b`. Travail uniquement dans `store-s2-work` ; le checkout
principal reste à `b1795a9` pour la campagne run-dev. Ne pas y intégrer le bundle
avant la fin de cette campagne. Le drapeau reste `sqlite_s1`, défaut `jsonl`.
Schéma courant **5**, upgrades transactionnels depuis les schémas 1–4 ; aucune
bascule de production ni publication. Les documents S1/S2 restent les preuves
historiques des versions précédentes ; ce document décrit les amendements.

## P1 — cache d'embeddings

Table dérivée `embedding_cache(cache_key, dim, vector, vector_sha256)` : float32
little-endian, empreinte SHA-256 du BLOB, forme et finitude vérifiées. La clé
SHA-256 porte sur le **texte exact indexé**, le modèle, la dimension, max_seq_length,
le device résolu, la version du package et une namespace d'encodage. Aucun cache
de requête n'est persisté. Vecteurs représentatifs **et chunks** utilisent ce cache.

`MEMORY_V4_SQLITE_EMBEDDING_NAMESPACE` vaut `v1` par défaut. L'opérateur doit la
changer si les poids locaux/révision du modèle changent sous un même nom : une
étiquette de modèle ne prouve pas l'identité de ses poids. La version du package
invalide aussi le cache lors d'une nouvelle version d'implémentation.

- Cache complet et valide : démarrage/rollback = lectures des vecteurs, **aucun
  réencodage du corpus existant**. BM25, entités, facettes et FAISS sont toujours
  reconstruits : leur coût demeure linéaire, sans modèle neural.
- Cache absent, invalide ou profil différent : seuls les vecteurs manquants sont
  recalculés ; une nouvelle namespace invalide volontairement tout son profil.
- Les nouveaux vecteurs rejoignent la transaction de la mutation ; un rollback
  ne les conserve pas. Le réchauffement dérivé au démarrage peut avoir sa propre
  transaction sans reçu métier. Le reçu métier reste la dernière écriture de sa
  transaction. Cache supprimable/reconstructible, jamais autorité ni donnée de
  migration ; son nettoyage et les performances de modèle restent à qualifier.

Preuves : démarrage et rollback avec `HashEmbedder.encode` interdit par le test,
corpus multi-vector de 10 éléments ; vecteurs identiques ; corruption d'une ligne
sur quatre ⇒ un seul encodage ; namespace différente ⇒ quatre encodages ; lot
annulé ⇒ aucune nouvelle ligne de cache conservée.

### Mesure 1 000 / 10 000 et extrapolation Granite

Protocole `sqlite-lifecycle-hash-v1` figé : deux tailles, Hash dim=384, textes
synthétiques fixes d'environ 60 mots, ingestion par lots de 250, cache supprimé
pour une mesure froide puis trois démarrages chauds et trois rollbacks après oubli
d'un élément. Temps total, temps d'encodage froid, nombre de textes encodés et
temps de reconstruction lors du rollback sont enregistrés ; médianes p50 et
observations brutes conservées. Il s'agit d'une mesure d'ingénierie locale,
**pas** d'un résultat de leaderboard ni d'un IC statistique.

Le lanceur impose nice≥15 et les seuils existants (swap≤8,5 milliards d'octets,
disque≥5 milliards d'octets), sans option de contournement ; sondes avant/après
phases, lots et appels de l'encodeur. Exécution séquentielle, aucun modèle neural
ou téléchargement. Un arrêt laisse `complete=false`, sans compléter les mesures.

**Mesures 1 000/10 000 en attente de Claude hors sandbox** : la sandbox refuse
`sysctl vm.swapusage` et l'application de nice ; l'essai du lanceur s'est arrêté
avant toute création de magasin, `results=[]`. Les unités prouvent le chemin de
mesure sur quatre éléments seulement (froid=4, chaud/rollback=0 encodage).
Ne remplacer cette absence de mesure par aucun chiffre prétendument observé.

Extrapolation, avec hypothèses explicites : si `L` est le chargement du modèle,
`c` son coût moyen d'encodage par élément de ce corpus et `m` le nombre de vecteurs
manquants, alors

```
Ancien démarrage ≈ coût des index + L + N*c
Nouveau démarrage chaud ≈ lecture cache + coût des index + L + m*c
Ancien rollback ≈ coût de rechargement/index + N*c
Nouveau rollback chaud ≈ lecture cache + coût de rechargement/index + m*c
```

À cache valide `m=0` ; le modèle résident n'est pas rechargé lors du rollback.
Exemples **hypothétiques**, pour la seule composante d'encodage intégral évitée :

| Hypothèse c (non mesurée avec Granite) | N=1 000 | N=10 000 |
| --- | ---: | ---: |
| 5 ms/élément | 5 s | 50 s |
| 20 ms/élément | 20 s | 200 s |
| 50 ms/élément | 50 s | 500 s |

Ajouter les médianes hash du rapport aux coûts du modèle uniquement après avoir
mesuré `L`, `c`, longueur des textes et dimension réelle de Granite. Hash384 ne
mesure ni chargement Granite ni son coût MPS/CPU ; la taille réelle des BLOB/FAISS
et la durée des index changent avec la dimension et le corpus. L'encodage des
nouveaux éléments et les index sont toujours calculés sous le verrou moteur.

## P2 — rétention et observabilité

Les **nouveaux reçus implicites** ont `explicit=false`, état complete, empreinte,
séquence, opération et date, **sans champ result**. Au plus **256** restent en DB
et en mémoire : les plus anciens sont supprimés dans la transaction suivante,
avant l'écriture du reçu final. La suppression et le compteur de séquence sont
annulés si la mutation échoue. Les séquences restent monotones après rétention.
Il n'y a aucune garantie de rejeu pour ces identifiants internes.

Les reçus **explicites** sont conservés sans expiration, avec résultat et
immutabilité UPDATE/DELETE : l'idempotence annoncée reste valable. Les reçus S2
sans marqueur et tous les reçus complets importés sont traités comme explicites,
conservativement ; leur ancien caractère implicite ne peut pas être déduit avec
certitude. Leur croissance est donc intentionnelle, à dimensionner ; aucun TTL
explicite n'est inventé dans ce lot. Les pending legacy restent bloqués.

`memory_status`, `memory-os-admin doctor` (local, aucun HTTP), scan/report health
exposent : explicites/implicites, limite, octets UTF-8 JSON des reçus, taille DB/WAL,
compteurs/octets pending legacy. Les octets JSON sont **logiques**, les octets
DB/WAL couvrent aussi les autres tables ; un DELETE ne réduit pas immédiatement
le fichier DB (pages libres réutilisables). Doctor est une sonde de compteurs et
de structure SQLite, pas une certification industrielle des sauvegardes/audits.

Preuves : limite réduite à 3 dans un test, sept requêtes implicites à réponse
10 000 caractères ⇒ trois reçus sans réponse ; explicite rejouable, DELETE
explicite refusé ; erreur au reçu final ⇒ rétention/compteur annulés ; doctor
refuse une DB malformée sans traceback. Sauvegarde admin SQLite reste refusée,
comme prévu jusqu'à S4.

## P3 — version applicative

Les flush SQLite **et JSONL**, ainsi que l'initialisation JSONL, dérivent
`meta.version` de `memory_os_v4.__version__`, importé paresseusement pour éviter
le cycle d'imports. Test avec version du package changée : valeur persistée puis
relue identique sur les deux backends. L'import S3 préserve les métadonnées source
et leur version comme provenance ; la mutation suivante utilise la version du
package courant. Aucun changement de version/package publié dans ce lot.

## S3 — migration, comparaison et retour arrière

`python -m memory_os_v4.sqlite_migrate import SOURCE CIBLE` est une **simulation
par défaut** ; `--apply` est explicite. Chemins de production/original-repository
refusés ; utiliser des copies. Source et destination disjointes, parent existant,
pas d'écrasement. `writer.lock` existant est verrouillé via un descripteur **en
lecture seule** ; écrivain actif refusé. L'outil ne crée aucun fichier source.
Migration hors ligne qualifiée macOS ; Windows refusé. Publication exclusive
atomique via Darwin `renamex_np(RENAME_EXCL)` ou Linux `renameat2(NOREPLACE)`
(branche Linux non exécutée ici, aucun fallback qui écrase).

Inventaire strict borné à **128 MiB** par copie : fichiers réguliers, pas de liens
symboliques/durs ; JSON sans doublons/NaN, journaux terminés, lignes malformées et
ambiguïtés d'identité refusées. Tous les fichiers et traces A2 présents dans la
copie sont archivés avec leur SHA-256, sans transformation de leurs octets.
Les permissions/horodatages du filesystem et répertoires vides ne font pas partie
du contrat de restitution : fichiers restitués en 0600, répertoire en 0700.

La base provisoire importe toutes les versions d'éléments dans l'ordre global,
avec IDs, provenance et champs inconnus ; ces champs restent conservés après
oubli/correction/flush d'accès. Les anciens blocs sans scope deviennent `default`
avec normalisation tracée. Dates d'éléments absentes ⇒ zéro déterministe, aucune
date actuelle inventée ; les octets originaux restent disponibles. Changement
project/kind pour un même ID ⇒ refus, réconciliation explicite requise.

`legacy_request_log` garde **toutes** les versions JSONL et leurs champs ; les
derniers complets fournissent le résultat rejouable, les derniers pending vont
dans `legacy_pending_requests`, immuables, jamais convertis en succès. Les
reçus complets projetés contiennent aussi le record original. `legacy_files`
conserve chaque fichier original et sa trace. Ces tables sont append-only.

Un BEGIN IMMEDIATE inclut l'import et son reçu final. Puis comparaison exhaustive
des versions, ordre, champs normalisés, têtes, blocs, états/réponses des reçus,
métadonnées **et tous les octets originaux**, integrity_check/FK/hashes. DB fermée
et checkpointée, manifeste fsyncé, empreinte de la source revérifiée avant
publication. Aucun sélecteur/config de backend n'est changé.

Les stages interrompus portent un intent avec source/empreinte. Une relance les
reconnaît et les conserve pour inspection ; elle construit une nouvelle cible,
sans append dans la base partielle. Cible déjà publiée et inchangée ⇒ vérification
et retour idempotent ; cible avec nouvelles écritures ⇒ refus de réimport.

- `export CIBLE RESTITUTION --original` : copie originale byte-à-byte, uniquement
  si l'autorité SQLite est encore identique au manifeste. Elle restaure dans un
  **nouveau** dossier ; elle ne bascule pas le service.
- Après nouvelles écritures, ce mode refuse une perte de données. `export CIBLE
  EXPORT` conserve tous les historiques actuels/unknowns, réponses explicites et
  pending, plus les fichiers originaux séparés pour réconciliation. **Aucun
  downgrade automatique ni équivalence des garanties JSONL/SQLite promise.**
- Export/reprise SQLite en lecture immutable seulement sous lease hors ligne,
  DB checkpointée, WAL non vide refusé. Aucun WAL/SHM créé dans la source.

Preuves synthétiques : égalité byte-à-byte de chaque fichier, recherche Hash à
trois dates, timeline et brief identiques (chemin de déploiement et métriques
SQLite exclus, horloge fixée), unknowns après mutation, pending bloqués,
rejeu complet, corruption/troncature/NaN/doublons/liens et écrivain actif refusés,
publication ne remplaçant pas une destination concurrente. Exceptions aux **58
frontières** d'import/publication ; SIGKILL avant/après COMMIT et avant/après
publication : source identique, cible absente ou complète, reprise sans doublon.

## Rejouer et portes restantes

Depuis un checkout isolé du bundle (jamais l'arbre actif de run-dev) :

```sh
./scripts/run_store_checks.sh
./scripts/run_store_lifecycle.sh --output .local-qualification/lifecycle-claude-v1
```

`MEMORY_STORE_TEST_PYTHON` permet de choisir le runtime cible sans téléchargement.
Un runtime SQLite <3.51.3 donne des SKIP : il ne qualifie pas ces lots.
Le second script doit être exécuté **hors sandbox**, avec une sortie nouvelle ;
exiger `complete=true`, deux tailles et zéro encodage aux phases chaudes.
La preuve 1 000/10 000 reste à compléter et à consigner par Claude.

S4 (backup SQLite via API, restore indépendant, purge, vrai disque plein, I/O et
coupure électrique), performances Granite et S5 industriel restent ouverts.
La limite 128 MiB et la branche Linux doivent être requalifiées avant extension.
Aucune autorisation de bascule production n'est déduite de ces tests sur copies.
