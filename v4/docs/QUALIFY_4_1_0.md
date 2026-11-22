# Qualification 4.1.0 — contrat court (avant tout script)

22 novembre 2026 — **contrat seulement** : aucun script écrit, rien exécuté, rien construit, rien
téléchargé. À relire par Codex AVANT l'écriture de l'outillage. Candidat : branche
`feature/4.1.0-qual`, base `453a893` (le commit final sera celui qui porte ce contrat et
l'outillage relu ; tout autre HEAD refuse). **La qualification ne produit que des preuves et
`release_ready` vrai/faux : ni publication PyPI, ni push, ni bascule de production.** Chacune
exige un GO distinct de Romain. Exécution par Claude hors sandbox, sur GO explicite.

## 1. Ce qui est qualifié

- 4.1.0 = profil de récupération explicite (`lexical` / `semantic`), empaquetage par profil,
  installation multi-clients (`quickstart --profile`, `--undo --with-service`, `uninstall`),
  correctif d'ordre de fermeture. Contrats : `LEXICAL_PROFILE_4_1_0.md`, `SETUP_4_1_0.md`.
- **4.0.9 n'est pas publiée à part** : 4.1.0 porte son hygiène (`83ddead` est un ancêtre de
  `453a893`, contrôlé par `git merge-base --is-ancestor`). La chaîne 4.0.9 v9 n'a jamais été
  lancée : aucune preuve 4.0.9 n'existe, aucune n'est transférée.
- **La règle moteur de 4.0.9 ne s'applique plus** (diff limité à `__init__.py` + `ops/health.py`
  depuis `9ecb4d0`) : 21 fichiers de `v4/src` ont changé, dont `memory.py`, `retrieval.py`,
  `sqlite_store.py`, `config.py`, `writer_lock.py`. Conséquence : **aucun transfert de preuve**,
  P-A comprise (l'argument « périmètre identique à l'octet » de 4.0.9 est caduc). Invariants
  contrôlés à la place : `SCHEMA_VERSION == 8` et 8 sommes de migration épinglées ;
  SHA-256 de `embed.py` (valeur de `LEXICAL_PROFILE §6`) ; contraintes et catalogue sémantiques
  historiques identiques à l'octet à `83ddead` ; version `4.1.0` partout.
- Deux profils, chacun avec ses extras et ses contraintes :
  `lexical` = `[ops,brief]` sous `qualified_runtime_constraints_lexical_v1.txt` (36 verrous, aucun
  paquet ML) ; `semantic` = `[ops,brief,semantic]` sous `qualified_runtime_constraints_v1.txt`.
- **Lexical : pas de conversion sur place.** Il est qualifié sur un **store neuf** (écriture,
  recherche, fermeture, réouverture, marqueur `retrieval_profile`, sauvegarde + exercice de
  restauration). **Sémantique : copie du store de production** comme en 4.0.9 : schéma 8→8 sans
  migration, baseline = production 4.0.7, et en plus le refus lexical sur cette copie
  (`store_profile_mismatch`, inventaire et SHA-256 inchangés, aucun fichier créé).

## 2. Règles dures

- **Règle de Romain** : disque libre ≥ 5 Go et swap ≤ 8,5 Go (Go décimaux), mesurés en octets
  toutes les 2 s ; **la copie du store ne démarre qu'avec ≥ 9,2 Go libres** ; mesure illisible =
  refus. Ne jamais supprimer ni comprimer les fichiers d'une autre session pour passer.
- Un seul job lourd à la fois ; `nice -n 15` ; concurrence 1 ; au plus une chaîne active (verrou).
- Construction depuis un arbre propre au commit final ; **chaque empreinte d'artefact est liée à
  ce commit** ; rien d'un build antérieur n'est réutilisé (README/CHANGELOG ont changé en
  `453a893`). Nouveaux dossiers de preuve à chaque `RUN_TAG`, jamais d'écrasement ; les preuves
  refusées sont conservées. Arrêt au premier échec ; reprise = nouveau `RUN_TAG`, tout est refait.
- Production en lecture seule : plist `com.romain.memory-os-v4` et store
  `~/memory-os-local/stores/sqlite-20261016-v407` LUS uniquement ; aucun contrôle du service ;
  `~/Library/LaunchAgents` réel et configurations client réelles jamais écrits (contrôle
  avant/après par empreinte, pid de production inchangé).
- Hors téléchargements listés au §6 (GO nominatif), tout tourne hors ligne
  (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `pip --no-index`).

**Si l'espace libre s'effondre en cours de chaîne** (une mise à jour macOS l'a fait tomber à
≈ 1 Gio le 22/11 ; libre mesuré à la rédaction : 15,97 Go, swap 1,63 Go) : le superviseur arrête
l'enfant direct sous 5 Go (TERM, KILL après 30 s), l'étape est marquée en échec, la chaîne
s'arrête, `release_ready` reste faux. Rien n'est supprimé pour récupérer de la place, aucune
relance automatique. Les preuves partielles restent en place. Reprise seulement après une nouvelle
mesure ≥ seuils et un nouveau GO, avec un nouveau `RUN_TAG` (aucune étape n'est reprise à moitié).
Entre 5 et 3 Go, un descendant d'un harnais arrêté peut finir son travail en cours (garde interne
3 Go), puis il est détecté. Cas propre à 4.1.0 : voir « service de test » ci-dessous.

## 3. Supervision : modèle `qualify_409_v9` repris TEL QUEL

Décision de Romain (22/11) : même modèle, sans nouveau confinement, sans compte macOS dédié.
Chaque étape tourne au premier plan sous le superviseur ; seul l'enfant direct est signalé
(`Popen.send_signal`, pid réservé tant qu'il n'est pas récolté) ; les descendants ne sont JAMAIS
signalés, seulement relevés (pid + heure de démarrage) ; s'il en reste : verrou conservé,
intervention humaine. Détection impossible = refus.

**Les deux limites d'un macOS sans privilège sont ACCEPTÉES par Romain** : (1) aucun signal à un
processus non prouvé ; (2) arrêt automatique limité à l'enfant direct.

Écart minimal par rapport à l'outillage 4.0.9 (liste fermée) :

| Élément | 4.0.9 | 4.1.0 |
|---|---|---|
| `ops/qualify_409_step_supervisor.py` | sha256 `483c0e8d…8bded2` | **réutilisé à l'octet près** (aucun paramètre de verrou ni de chemin dedans) ; son empreinte est vérifiée par la chaîne |
| Chaîne | `qualify_409_v9_chain.sh` | nouvelle `qualify_410_v1_chain.sh`, même fonction `step`, mêmes codes (0 / 20 / 21 / 22 / 24 / 130) |
| Verrou | `ops/logs/qualify_409.lock` | `ops/logs/qualify_410.lock` ; refus aussi si `qualify_409.lock` existe |
| Balayage (`SWEEP_ROOTS`) | branche 409 + `qual-envs/409-installed-claude-` | worktree `feature-410-h2` + `qual-envs/410-` |
| `RC_EXPECTED` | `83ddead…` | commit final 4.1.0 relu |
| Seuil de départ de la copie | 9,2 Go | 9,2 Go (inchangé) |

**Seul ajout, dicté par l'installation réelle (détection uniquement, aucun signal)** : un service
launchd de test n'est pas un descendant (son parent est `launchd`) et échappe au relevé. Après
chaque étape d'installation, et à la sortie de la chaîne, celle-ci vérifie que le libellé de test
n'est plus chargé et que le port de test n'écoute plus ; sinon verrou conservé, intervention
humaine (`launchctl bootout` du SEUL libellé de test). Le script de preuve retire lui-même son
libellé dans son `trap` (comme `run_install_proof_v2.sh`) ; après un KILL ce `trap` ne tourne pas :
c'est le cas que la détection couvre. Libellé `local.memory-os-v4.qual410`, port 18765, jamais un
libellé commençant par `com.romain.memory-os-v4`, jamais 8765.

## 4. Portes (bloquent `release_ready`) et éléments mesurés

Une porte en échec, absente ou non rejouée sur le commit final laisse `release_ready=false`.

| # | Porte | Origine | Profil |
|---|---|---|---|
| G1 | Arbre propre au commit attendu ; `83ddead` ancêtre ; invariants du §1 | adaptée | — |
| G2 | Suite complète isolée (`qualify_405_test_isolation.py --suite full`) : 0 échec, 0 erreur, 0 skip ; test lent désélectionné par le harnais puis test sémantique « cached » séparé ; deux lints | 4.0.9 | sémantique (env. source) |
| G3 | Artefacts : PEP 517 direct et via sdist, marqueurs, points d'entrée (dont `memory-os-constraints`), descripteur, twine strict, hashes tirés des octets construits ; manifeste scellé | 4.0.9 | — |
| G4 | Contrôles source : identités TCP exactes, test sémantique en cache, v3 | 4.0.9 | sémantique |
| G5 | **Résolution par profil** contre les contraintes : `qualify_runtime_resolution.py --profile lexical` puis `semantic` ; aucun écart, aucun paquet inattendu ; lexical : `ml_packages == []` | **nouvelle** | les deux |
| G6 | **Wheel lexical installé sans ML** (`LEXICAL_PROFILE §11.2`, « non fait » au CHANGELOG) : tests exécutés hors dépôt, sans `PYTHONPATH` ; `memory_os_v4.__file__` sous `site-packages` ; `find_spec` de `torch`, `sentence_transformers`, `transformers`, `faiss` = `None` | **nouvelle** | lexical |
| G7 | **Installation réelle** dans un `HOME` jetable (`env -i`), venv vierge, `pip --no-deps` puis `pip -c <contraintes>` : localisateur sous `site-packages`, versions = verrous, aperçu sans écriture (inventaire identique), `--apply --demo` `ok: true`, profil attesté par le serveur, `doctor` 0 FAIL ; lexical : aucune bibliothèque ML ouverte par le serveur | **nouvelle** | les deux |
| G8 | **Retour arrière et désinstallation** : `quickstart --undo <stamp> --with-service` (libellé déchargé, port libre, plist retiré, entrées client restaurées) ; dans une seconde installation jetable, `uninstall` (aperçu sans écriture) puis `uninstall --apply` : service arrêté, entrées retirées, **données intactes** (store, jeton, ressources, journaux : empreintes identiques) | **nouvelle** | les deux |
| G9 | **Copie installée du store de production**, schéma 8→8 : coupe par l'API de sauvegarde du candidat, source en lecture seule, workers baseline 4.0.7 / wheel / sdist 4.1.0 et réouvertures, comparaison exhaustive, instance et autorité identiques, journal inchangé, aucune migration, dix recherches figées, MPS/natif, `doctor` lecture seule 0 FAIL ; **ajout** : ouverture lexicale de la copie refusée sans aucune écriture | 4.0.9 + ajout | sémantique |
| G10 | **Sauvegarde + exercice de restauration** (S4 ×2, même archive, deux artefacts) sur la copie | 4.0.9 | sémantique |
| G11 | **Store lexical neuf** via le wheel installé : écriture, recherche, fermeture, réouverture, mêmes résultats ; marqueur présent ; ouverture sémantique refusée ; sauvegarde + restauration | **nouvelle** | lexical |
| G12 | **P-B** (intégrité, synthétique) : nouvelle liaison au commit final, 40/40, six portes, rejeu vrai ; scorer, cas et seuils inchangés, gold jamais lu | 4.0.9 | sémantique |
| G13 | **P-A** (brief, synthétique) : à REJOUER sur le commit final, 40/40, scellés et scorer inchangés (voir §8, point 1) | 4.0.7, plus transférable | sémantique |
| G14 | Relais installés : inventaire, panne, reconnexion sans rejeu, statut local pendant panne, handshake 4.1.0 ; wheel ET sdist | 4.0.9 | sémantique + wheel lexical |
| G15 | Vérification finale du manifeste, rejouée en dernier, jamais sautée ; `CHAIN_RESULT=PASS` écrit seulement après elle | 4.0.9 | — |

**Mesurés et rapportés, PAS des portes** :

- **M1 — durée d'installation, clause D1** (`SETUP §10`, que ce texte définit comme une mesure et
  non comme un seuil) : pour chaque profil, T0 = `python3.11 -m venv`, T1 = `quickstart … --apply
  --demo` rendant `ok: true` ; rapportés : total, durée de `pip` à part, `install.json["timing"]`
  (étapes, `download_bytes`), « temps hors modèle ». Écarts au protocole à écrire dans la preuve :
  la machine n'est pas vierge (compte jetable simulé par `HOME` jetable + `env -i`), le wheel est
  local (4.1.0 n'est pas sur PyPI), le modèle sémantique est pré-placé. D1 est re-signée par
  Romain sur ces chiffres ; la qualification ne la tranche pas.
- M2 — P-A et P-B en profil lexical (les protocoles scellés ont été acquis sur le moteur
  sémantique) : résultat rapporté, sans seuil.
- M3 — pic disque, pic de swap et durée de chaque étape (relevés du superviseur).
- M4 — rappel H2 : déjà mesuré (`H2_RUN_v3.md`), NON rejoué, hors qualification.

Limites conservées de 4.0.9 : P-A/P-B synthétiques, non exhaustifs, authenticité locale non
signée ; ces portes ne mesurent pas l'utilité réelle.

## 5. Ordre, disque et durée (ESTIMATIONS)

Sources : journaux `qualify_408rc_v7` / `v7b` du 31/10 (horodatages de fichiers, ≈ 15 min pour la
chaîne 4.0.8 entière) et budgets de `QUALIFY_4_0_9.md`. F = DB + WAL + SHM du store source,
mesuré le 22/11 : 157,9 Mo (78 Mo en 4.0.9). Aucun chiffre ci-dessous n'est une garantie.

| Étape (portes) | Place en plus (estimée) | Durée (estimée) | Départ exigé |
|---|---:|---:|---|
| 1. Arbre, suite complète, lints (G1, G2) | 0,2 Go | 5–10 min (≈ 3 min en 4.0.8, suite plus petite) | ≥ 5 Go |
| 2. Build, manifeste, contrôles source (G3, G4) | 0,45 Go | 2–3 min | ≥ 5 Go |
| 3. Résolution ×2 (G5, réseau) | < 0,1 Go | 1–3 min | ≥ 5 Go + GO §6 |
| 4. Installation réelle lexicale + undo + uninstall + D1 (G7, G8, M1, réseau) | 0,15–0,3 Go | 3–6 min | ≥ 5 Go + GO §6 |
| 5. Wheel lexical sans ML, store lexical neuf (G6, G11) | 0,1 Go | 1–2 min | ≥ 5 Go |
| 6. Environnements sémantiques wheel + sdist hors dépôt (`.pth`, hors ligne) | 0,1 Go | 1–2 min | ≥ 5 Go |
| 7. Installation réelle sémantique + undo + uninstall + D1 (G7, G8, M1) | 1,0–1,6 Go si GO D5, sinon 0,1 Go | 5–12 min | ≥ 5 Go |
| 8. **Copie + S4 ×2** (G9, G10) | budget du harnais `adaptive_v1` ≈ 3 + 10 F + 0,30 = **4,9 Go exigés libres** ; écrit ≈ 10 F ≈ 1,6 Go au pire | ≤ 9 min observés en 4.0.8, plafond 900 s | **≥ 9,2 Go** |
| 9. P-B, P-A (G12, G13, M2) | 0,1 Go | 1–3 min | ≥ 5 Go |
| 10. Relais, vérification finale (G14, G15) | 0,05 Go | < 1 min | ≥ 5 Go |

Cumul estimé avant la copie : ≈ 1,2 Go sans D5, ≈ 2,7 Go avec. Durée totale estimée : 25–50 min.
Les étapes réseau et l'installation sémantique passent AVANT la copie pour que le seuil de 9,2 Go
soit mesuré une fois le reste consommé. Politique de stockage `adaptive_v1` et plafond 4 Gio :
choix explicite et privé de la seule commande de copie, comme en 4.0.9.

## 6. Téléchargements : chacun est un GO nominatif de Romain

Sans GO pour une ligne, l'étape correspondante ne démarre pas et sa porte reste non acquise.
Aucun autre accès réseau n'est permis.

| GO | Quoi | Source | Taille | Contrôle |
|---|---|---|---:|---|
| D1 | Résolution lexicale (`pip install --dry-run`, rien d'installé) | `https://pypi.org/simple` | métadonnées ; roues entières seulement si une métadonnée manque (estimation < 50 Mo) | versions = 36 verrous, rapport pip et son SHA-256 consignés |
| D2 | Résolution sémantique (idem) | PyPI | métadonnées (estimation ; une roue `torch` serait ≈ 80 Mo) | versions = verrous sémantiques |
| D3 | Dépendances lexicales réellement installées (36 paquets) | PyPI | estimation 30–60 Mo | `pip check`, versions `==` verrous, aucun paquet hors liste ; SHA-256 de chaque roue RELEVÉ dans la preuve (les contraintes ne portent pas d'empreinte épinglée à l'avance) |
| D4 | `pytest` et sa fermeture, pour G6 seulement, installés après le gel de G7/G8 | PyPI | estimation < 5 Mo | versions et SHA-256 relevés ; hors contraintes, déclaré comme tel |
| D5 | Dépendances sémantiques réellement installées (fermeture ML : `torch`, `scipy`, …) | PyPI | estimation 200–300 Mo | comme D3 |
| D6 | **Table cl100k par `--download-approved`**, dans l'installation lexicale (aucune pré-copie) | `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken` | **1 681 126 octets** | SHA-256 `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` vérifié par `provision` ; `timing.download_bytes == 1681126` |

- **Modèle sémantique : aucun téléchargement.** Il vient du cache local vérifié
  (`~/.cache/huggingface/hub/models--ibm-granite--granite-embedding-97m-multilingual-r2`), copié
  dans le `HOME` jetable et contrôlé fichier par fichier contre `provision_v1.json` ; la table
  cl100k de l'installation sémantique est pré-placée de même (la branche de téléchargement est
  exercée une seule fois, en D6). L'installation sémantique attend donc `download_bytes == 0`.
- **Sans GO D5** : variante sans réseau, environnement sémantique par `.pth` vers les dépendances
  déjà qualifiées (procédé 4.0.9). G7 sémantique est alors acquise avec la limite écrite au
  manifeste « dépendances sémantiques non réinstallées depuis PyPI ; résolution prouvée par G5 ».
  Accepter cette limite est une décision de Romain, pas de la chaîne.

## 7. Hors périmètre

Publication PyPI, push, étiquette Git, bascule ou redémarrage de la production, modification des
configurations client réelles ; conversion sémantique ↔ lexical ; retour arrière 4.0.x d'un store
lexical (documenté comme impossible, non testé ici) ; H2 et tout banc ; `.mcpb` de Claude
Desktop ; `memory_consolidate` / `memory_compact` ; Linux, Intel, Python ≠ 3.11 ; machine ou
compte réellement vierge ; installation depuis le sdist en profil lexical (le sdist est couvert
par G3 et par l'environnement sémantique).

## 8. Scripts à écrire ensuite (noms, rôle, entrées → sorties) — aucun n'existe

Dans `scripts/` (versionnés, hachés dans l'inventaire d'outillage du manifeste) :

| Script | Rôle | Entrées → sorties |
|---|---|---|
| `release_410_checks.py` | règles fermées 4.1.0 : invariants du §1 à la place de la règle « santé seule », preuves requises par profil, `validate_local` / `validate_copy` / `main` | manifeste + preuves → refus ou accord |
| `make_release_410.py` | scelle artefacts et inventaire d'outillage | commit, preuve d'artefacts, preuve de suite → `release-4.1.0-<tag>/manifest.json` |
| `source_release_checks_410.py` | TCP exact + test sémantique en cache + v3 | manifeste → `source-proof-input.json` |
| `prepare_release_410_envs.py` | environnements sémantiques wheel + sdist hors dépôt, hors ligne (`.pth`) | manifeste → `qual-envs/410-installed-claude-<tag>/environments.json` |
| `qualify_install_410.sh` | G7, G8, M1 pour UN profil (`--profile`), dérivé de `run_install_proof_v2.sh` | wheel + SHA, dossier neuf, graines locales → `report.json`, journaux, `SHA256SUMS` |
| `qualify_lexical_wheel_410.py` | G6 dans le venv lexical de G7 | venv, fichiers de test → `report.json` |
| `qualify_sqlite_release_410.py` + `sqlite_release_worker_410.py` | G9, G10, G11 | manifeste, environnements, plist et store (lus) → `410-copy-claude-<tag>/report.json` |
| `score_release_410_gate.py`, `record_release_410_gate.py` | P-B (et P-A) liés au commit final | scellés, scorer haché, collecte → preuve de porte |
| `verify_release_manifest.py` (modification) | branche `4.1.0` + enregistrements `--record-resolution-proof`, `--record-install-proof`, `--record-lexical-proof` | preuve → manifeste |

Réutilisés sans changement : `qualify_405_test_isolation.py`, `qualify_distribution_d1a.py`,
`qualify_runtime_resolution.py`, `check_runtime_resolution.py`, `run_integrity_v5_validation.py`,
`installed_relay_e2e.py` (`installed_relay_case.py` : une ligne, §9 point 6). Hors dépôt :
`~/memory-os-local/ops/qualify_410_v1_chain.sh` (§3) ; superviseur 4.0.9 inchangé.

## 9. Points ouverts pour Codex et Romain (rien n'est tranché ici)

1. **P-A** : `run_brief_v5_validation.py` a ses deux commits (baseline, candidat) écrits en dur ;
   le rejouer sur 4.1.0 demande une variante. Si elle exige de toucher aux scellés, au scorer ou
   au gold : ARRÊT, décision de Romain. Alternative à arbitrer : transfert motivé (les fichiers
   `brief*.py` sont inchangés, mais `memory.py`, `retrieval.py` et `sqlite_store.py` ne le sont pas).
2. **Suite complète** : les relevés de développement (`.local-baseline/`) portent 12 échecs
   (`v4/tests`) et 51 échecs/erreurs (racine) déjà présents à `83ddead`. La porte G2 exige zéro
   dans l'environnement de qualification ; s'ils y persistent, c'est un ARRÊT, pas une tolérance.
3. `score_release_409_gate.py` appelle `verify_engine_version_only` (règle « santé seule ») :
   non réutilisable tel quel. Le nombre d'identités TCP exactes (32 en 4.0.9) est à rétablir.
4. `RC_PY` (`qual-envs/407-installed-v2`) doit satisfaire les contraintes sémantiques 4.1.0 : seul
   `packaging==26.3` a été relevé (présent) ; le contrôle complet (`inspect_current`) est une
   précondition de la chaîne ; sinon ARRÊT, `RC_PY` n'est pas recréé dans la chaîne.
5. Variante sans GO D5 (§6) et lecture de M1 quand `pip` ne télécharge pas tout : à accepter ou non.
6. G14 : `installed_relay_case.py` ne liste pas `4.1.0` (`COMPACT_BRIEF_AND_LOCAL_STATUS_VERSIONS`
   s'arrête à `4.0.9`) : modification d'une ligne à faire et à relire, comme pour 4.0.9. Son
   fonctionnement face à un service lexical n'est pas vérifié.
7. Baseline de la copie = interpréteur nommé par le plist de production (attendu `venv-4.0.7`) ;
   tout autre valeur lue = ARRÊT.
