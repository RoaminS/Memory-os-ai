# Qualification 4.1.0 — contrat court (avant tout script)

22 novembre 2026 — rédigé comme **contrat seulement** (rien exécuté, rien construit, rien téléchargé à la rédaction) ; les runs v1 à v3 ont depuis été exécutés et arrêtés (rév. 7).
Révision 8 (outillage seulement, aucun changement du produit) : G7 accepte, pour le seul aperçu d'installation, un inventaire vide
explicitement attesté (le premier `HOME` lexical est créé vide) ; l'ouverture opposée de G11 est exécutée par
l'environnement sémantique wheel installé, donc après l'étape `envs` (le moteur contrôle la fermeture ML avant le
profil du store : le venv lexical s'arrêtait à `semantic_dependency_missing`).
Révision 7 : `qualify_distribution_d1a.py` passe `--profile lexical` à l'aperçu `quickstart` (refus
`setup_profile_required` de 4.1.0 constaté au run v3). La révision 6 appliquait les textes du verdict Codex « outillage n° 3 » (sur `577c1b2`) : exception
`none_short_command` de M3 et relais d'exécution M3 au §3. La révision 5 appliquait les sept textes
d'amendement du verdict Codex « outillage n° 2 » (sur `54bee95`) ; la révision 4 appliquait les verdicts Codex « NON VALIDÉ » sur `7109491` puis `f85a0b6`. L'outillage
du §8 est désormais écrit et en cours de revue ; ces amendements ne le valident pas et ne
constituent aucun GO d'exécution.
Candidat : branche `feature/4.1.0-qual`, base `453a893` (le commit
final sera celui qui porte ce contrat et l'outillage relu ; tout autre HEAD refuse). **La
qualification ne produit que des preuves et `release_ready` vrai/faux : ni publication PyPI, ni
push, ni bascule de production.** Chacune exige un GO distinct de Romain. Exécution par Claude
hors sandbox, sur GO explicite. Les décisions qui reviennent à Romain sont réunies au §10.

## 1. Ce qui est qualifié

- 4.1.0 = profil de récupération explicite (`lexical` / `semantic`), empaquetage par profil,
  installation multi-clients (`quickstart --profile`, `--undo --with-service`, `uninstall`),
  correctif d'ordre de fermeture. Contrats : `LEXICAL_PROFILE_4_1_0.md`, `SETUP_4_1_0.md`.
- **4.0.9 n'est pas publiée ni qualifiée à part** : 4.1.0 porte son hygiène (`83ddead` ancêtre de
  `453a893`, contrôlé par `git merge-base --is-ancestor`). La chaîne 4.0.9 v9 n'a jamais été
  lancée : aucune preuve 4.0.9 n'existe, aucune n'est transférée.
- **La règle moteur de 4.0.9 ne s'applique plus** : 21 fichiers de `v4/src` ont changé depuis
  `9ecb4d0`, dont `memory.py`, `retrieval.py`, `sqlite_store.py`, `config.py`, `writer_lock.py`.
  **Aucun transfert automatique de preuve.** Invariants contrôlés à la place :
  `SCHEMA_VERSION == 8` et 8 sommes de migration épinglées ; SHA-256 de `embed.py`
  (`LEXICAL_PROFILE §6`) ; contraintes et catalogue sémantiques identiques à l'octet à `83ddead` ;
  version `4.1.0` partout.
- Deux profils : `lexical` = `[ops,brief]` sous `qualified_runtime_constraints_lexical_v1.txt`
  (36 verrous, aucun paquet ML) ; `semantic` = `[ops,brief,semantic]` sous
  `qualified_runtime_constraints_v1.txt` (65 verrous).
- **Lexical : pas de conversion sur place**, qualifié sur un **store neuf** (G11). **Sémantique :
  copie du store de production** comme en 4.0.9 (schéma 8→8 sans migration, baseline = production
  4.0.7), plus le refus lexical sur cette copie (`store_profile_mismatch`, aucune écriture).

## 2. Règles dures

- **Règle de Romain** : disque libre ≥ 5 Go et swap ≤ 8,5 Go (Go décimaux), mesurés en octets par
  le superviseur à chaque tour de boucle (pause de 2 s **plus** la durée des sondes : ce n'est pas
  un contrôle garanti toutes les 2 s exactement) ; **la copie du store ne démarre qu'avec le plus
  élevé de 9 200 000 000 octets et du budget dynamique calculé** (§5) ; mesure illisible = refus.
  Ne jamais supprimer ni comprimer les fichiers d'une autre session pour passer.
- Un seul job lourd à la fois ; `nice -n 15` pour tout ce que la chaîne lance ; concurrence 1.
  Le service launchd de test n'hérite PAS de ce `nice` (le plist de `quickstart` ne fixe pas
  `Nice`) : **décision ouverte de Romain** (§10). Coordination : verrou `qualify_410.lock` ; refus
  si `qualify_409.lock` existe, au départ et avant chaque étape lourde ; les chaînes 4.0.9 ne
  sont pas lancées (4.0.9 n'est pas qualifiée à part). Limite : un verrou n'empêche pas un tiers
  de lancer une autre chaîne ensuite ; règle de procédure : aucune autre chaîne pendant ce run.
- Construction depuis un arbre propre au commit final ; **chaque empreinte d'artefact est liée à
  ce commit** ; rien d'un build antérieur n'est réutilisé. **Tout `RUN_TAG` déjà existant est
  refusé** (dossier de logs, dossiers de preuve ou environnements portant ce tag) : chaque run
  exécute toutes les étapes dans des dossiers neufs ; aucune étape n'est sautée ni reprise.
  Les preuves refusées sont conservées. Arrêt au premier échec.
- Production en lecture seule : plist `com.romain.memory-os-v4` et store `~/memory-os-local/stores/sqlite-20261016-v407` lus uniquement ; aucun contrôle du service. Les onze configurations clientes du catalogue, les fichiers des LaunchAgents réels et `~/.config/memory-os` ne sont jamais écrits. Leur inventaire avant/après conserve les chemins, les états d’observation et les empreintes, sans copier ni journaliser aucun contenu secret ; le pid de production reste inchangé. Pour `~/.claude.json`, seul le périmètre MCP est comparé : définitions des serveurs et paramètres d’activation, désactivation et autorisation, globaux et par projet. La sélection est versionnée et couvre notamment `mcpServers`, `disabledMcpServers`, `enabledMcpjsonServers`, `disabledMcpjsonServers` et `enableAllProjectMcpServers`, avec leurs chemins JSON complets et la distinction entre absence et présence. Le document doit être un objet JSON valide. Le résultat atteste « périmètre MCP inchangé » et ne prétend pas établir l’identité du reste du fichier. La lecture et la canonicalisation restent en mémoire ; seules les empreintes sont conservées. Toute impossibilité de lecture ou de parcours bloque la qualification. Les liens symboliques sont refusés ou contrôlés avec leur cible autorisée et son contenu ; ils ne sont jamais assimilés à une empreinte de contenu par leur seul chemin cible.
- Hors acquisitions réseau du §6 (GO nominatif), tout tourne hors ligne (`HF_HUB_OFFLINE=1`,
  `TRANSFORMERS_OFFLINE=1`, `pip --no-index`).
- **Règle permanente de Romain** : aucun agent ne lance la validation scellée P-A ; aucun agent,
  ni la chaîne, ni aucun script ne lit ni ne liste `~/memory-os-local/brief_v5_val/` (ni
  `brient_v5_val/`). La chaîne ne lance pas P-B et ne lit pas ses scellés. Un GO de qualification
  n'est jamais une autorisation implicite de lire ces dossiers.

**Si l'espace libre s'effondre en cours de chaîne** (une mise à jour macOS l'a fait tomber à
≈ 1 Gio le 22/11 ; mesuré à la rédaction : 15,97 Go libres, swap 1,63 Go) : le superviseur arrête
l'enfant direct sous 5 Go (TERM, KILL après 30 s), l'étape échoue, la chaîne s'arrête,
`release_ready` reste faux ; verrou conservé s'il reste des survivants. Rien n'est supprimé pour
récupérer de la place, aucune relance automatique ; nouveau run seulement après nouvelle mesure,
nouveau GO et nouveau `RUN_TAG`. **Une preuve absente, tronquée ou impossible à finaliser vaut
échec. Aucun résultat partiel ne peut être interprété comme un PASS** (une chute brutale peut
empêcher d'écrire le rapport d'échec lui-même). Entre 5 et 3 Go, un descendant d'un harnais arrêté
peut finir son travail en cours (garde interne 3 Go), puis il est détecté. Un service launchd de
test peut continuer après l'arrêt du harnais : la détection finale le constate, elle ne garantit
pas son arrêt immédiat.

## 3. Supervision : modèle `qualify_409_v9`, superviseur inchangé

Décision de Romain (22/11) : même modèle, sans nouveau confinement, sans compte macOS dédié.
Chaque étape tourne au premier plan sous le superviseur ; seul l'enfant direct est signalé
(`Popen.send_signal`, pid réservé tant qu'il n'est pas récolté) ; les descendants ne sont JAMAIS
signalés, seulement relevés (pid + heure de démarrage) ; s'il en reste : verrou conservé,
intervention humaine. Détection impossible = refus. **Les deux limites d'un macOS sans privilège
sont ACCEPTÉES par Romain** : (1) aucun signal à un processus non prouvé ; (2) arrêt automatique
limité à l'enfant direct.

Le **superviseur** `ops/qualify_409_step_supervisor.py` est réutilisé **à l'octet près**
(sha256 `483c0e8d5d951adbb64c2607c782ee36e2a6af547d05df8bb07b95754e8bded2`, vérifié par la
chaîne). La **chaîne**, elle, change. Écarts par rapport à `qualify_409_v9_chain.sh` (liste fermée) :

| Élément | 4.1.0 (`qualify_410_v1_chain.sh`) |
|---|---|
| Fonction `step`, codes 0 / 20 / 21 / 22 / 24 / 130 | repris ; la branche « étape déjà réussie → sautée » devient inaccessible par le refus préalable de tout `RUN_TAG` existant |
| Verrou, balayage, commit attendu | `ops/logs/qualify_410.lock` ; `SWEEP_ROOTS` = worktree `feature-410-h2` + `qual-envs/410-` ; `RC_EXPECTED` = SHA complet du commit final relu |
| Seuil de la copie | `max(9 200 000 000, budget dynamique)`, F remesuré immédiatement avant (§5) |
| Service launchd de test | contrôles ci-dessous (détection ; aucun signal à un processus) |
| Finalisation | `release_ready` levé seulement par la vérification finale (§4) ; `CHAIN_RESULT=PASS` écrit après elle et après les contrôles de sortie |
| Instrumentation M3 | échantillons horodatés pris par la chaîne, hors superviseur (§4) |
| Relais d’exécution M3 | Pour chaque appel à `step`, l’enfant direct du superviseur est un relais qui consigne son PID et une borne monotone antérieure à `exec`, puis se remplace par la commande avec `exec`, sans processus intermédiaire persistant. Le PID et la filiation sont conservés. La sonde M3 est un enfant distinct de la chaîne et n’envoie aucun signal. Le superviseur reste inchangé et ne signale que son enfant direct. |

L’écriture du marqueur de départ ne prouve pas que `exec` a eu lieu. Une observation limitée au relais avant `exec` ne compte ni comme mesure « pendant », ni comme observation de chevauchement avec la commande. Toute impossibilité de distinguer ces états doit être traitée conformément aux règles M3, sans fabriquer de chevauchement.

**Service launchd de test** (son parent est `launchd` : il échappe au relevé des descendants).
Libellé `local.memory-os-v4.qual410`, port 18765 ; jamais un libellé commençant par
`com.romain.memory-os-v4`, jamais 8765.

Avant chaque installation, le libellé de test doit être explicitement reconnu absent dans
`gui/<uid>` et le port doit être libre. Après `quickstart --apply --demo`, G7 exige au contraire
un service présent, attesté comme appartenant au run, utilisant le plist du HOME concerné et son
interpréteur, avec le profil et la disponibilité attendus. Après chaque retour arrière ou
désinstallation, à la fin de chaque étape G7/G8 et à toute sortie contrôlée de la chaîne, le
libellé doit être absent et le port libre. Une observation impossible ou un état contraire à
celui attendu entraîne un arrêt avec verrou conservé. Aucun service préexistant n'est déchargé.

Le nettoyage normal ne peut viser qu'un service créé par ce run
et ré-attesté immédiatement avant son arrêt (plist sous le `HOME` jetable du run, programme = venv
du run) ; **un libellé préexistant n'est jamais déchargé**. Le filet de sécurité ne transforme
jamais un échec de retour arrière en succès. Conséquence pour le script d'installation : le `trap`
de `run_install_proof_v2.sh`, posé AVANT les préconditions, n'est pas repris ; il n'est armé
qu'après la création prouvée du service par le run. Après un KILL aucun `trap` ne tourne : la
détection de la chaîne couvre ce cas (verrou conservé, `launchctl bootout` du seul libellé de
test par un humain).

## 4. Portes, états et éléments mesurés

**États.** Chaque porte possède un état explicite : `pending`, `pass`, `fail`,
`transfer_accepted` ou `waived_by_romain`. Une décision ne transforme jamais un échec ou une
absence de mesure en résultat réussi. Toute dérogation précise le commit final, le périmètre, le
motif, les preuves référencées et son effet autorisé sur `release_ready`.

**`release_ready`.** Les commandes d'enregistrement laissent `release_ready=false` (le
comportement 4.0.9, où `--record-relay-proof` le positionnait déjà à vrai, est corrigé). Sa
validation finale exige toutes les preuves ou décisions admissibles, leur liaison au même commit
et aux mêmes artefacts, ainsi que les contrôles de sortie réussis. Un manifeste seul ne vaut pas
succès sans `CHAIN_RESULT=PASS` concordant. Le manifeste porte : SHA complet du candidat, profils,
artefacts, état de chaque porte, décisions de Romain et leurs empreintes, rapport G13, preuve P-B
externe, outillage du dépôt ET hors dépôt (chaîne, superviseur). Admissible pour `release_ready` :
`pass` partout, sauf deux exceptions, chacune seulement si la décision enregistrée l'autorise
expressément : G13 admet `transfer_accepted` ou `waived_by_romain` ; G12 admet uniquement `pass`
(preuve externe conforme, liée au candidat) ou `waived_by_romain` — `transfer_accepted` n'existe
pas pour G12 et ne contourne jamais ses exigences. G15 contrôle aussi les décisions
administratives requises (§10, dont la re-signature de la clause D1). Les contrôles de sortie
conditionnant `release_ready` sont terminés avant sa mise à vrai. `CHAIN_RESULT=PASS` est écrit
ensuite ; son absence ou son invalidation interdit de considérer le run comme réussi.

| # | Porte | Origine | Profil |
|---|---|---|---|
| G1 | Arbre propre au commit attendu ; `83ddead` ancêtre ; invariants du §1 ; préconditions du §9 | adaptée | — |
| G2 | Suite complète isolée (`qualify_405_test_isolation.py --suite full`) : **0 échec, 0 erreur, 0 skip** ; test lent désélectionné par le harnais puis test sémantique « cached » séparé ; deux lints. Échec persistant ⇒ arrêt et diagnostic, jamais de tolérance automatique | 4.0.9 | sémantique (source) |
| G3 | Artefacts : PEP 517 direct et via sdist, marqueurs, points d'entrée (dont `memory-os-constraints`), descripteur, twine strict, hashes tirés des octets construits ; manifeste scellé | 4.0.9 | — |
| G4 | Contrôles source : **32 identités TCP exactes** (identités, multiplicités et résultats, pas seulement le nombre), test sémantique MiniLM en cache, v3 | 4.0.9 | sémantique |
| G5 | **Résolution par profil** : `qualify_runtime_resolution.py --profile lexical` puis `semantic` ; aucun écart, aucun paquet inattendu ; lexical : `ml_packages == []` ; URL et SHA-256 des distributions résolues consignés. Prouve une résolution, pas une installation | **nouvelle** | les deux |
| G6 | **Wheel lexical installé sans ML** (`LEXICAL_PROFILE §11.2`) : tests exécutés hors dépôt, sans `PYTHONPATH` ; `memory_os_v4.__file__` sous `site-packages` ; `find_spec` de `torch`, `sentence_transformers`, `transformers`, `faiss` = `None` | **nouvelle** | lexical |
| G7 | **Installation réelle** dans un `HOME` jetable (`env -i`), venv vierge, `pip --no-deps` puis `pip -c <contraintes>`, roues seulement : localisateur sous `site-packages` ; versions = verrous ; aucun paquet hors liste (exceptions de bootstrap : `pip`, `setuptools`, `wheel` s'il est fourni par le venv) ; **aperçu sans écriture** prouvé par un inventaire incluant les SHA-256 de contenu (noms, tailles et dates ne suffisent pas) ; le nombre d'entrées est attesté (champ présent, entier ≥ 0) et un inventaire vide n'est admis que pour cet aperçu d'installation, avec l'empreinte de l'inventaire vide avant et après (le premier `HOME` lexical est créé vide : N6, aucune pré-copie) ; `--apply --demo` `ok: true` ; profil attesté par le serveur ; `doctor --json` retourne 0, ne contient aucun résultat FAIL et couvre au minimum les douze domaines suivants : `token`, `local-runtime`, `server`, `security`, `ready`, `service`, `store`, `storage`, `store-profile`, `relay-journals`, `client:codex`, `client:cursor`. Les domaines `server`, `ready`, `store-profile`, `client:codex` et `client:cursor` doivent être PASS ; les autres domaines obligatoires doivent porter un statut PASS ou WARN explicite. Les contrôles supplémentaires restent conservés et tout FAIL supplémentaire bloque également G7. Un domaine obligatoire manquant, un statut invalide ou un code retour non nul refuse la porte. Les deux entrées clientes demandées sont attestées séparément par lecture structurée. Lexical : aucune bibliothèque ML ouverte par le serveur | **nouvelle** | les deux |
| G8 | **Retour arrière** : `quickstart --undo <stamp> --with-service` (libellé déchargé, port libre, plist retiré, entrées client restaurées). **Désinstallation** dans un second HOME jetable, après une seconde installation réussie et attestée : `uninstall` en aperçu sans écriture (même inventaire à empreintes, ici non vide), puis `uninstall --apply`. Le scénario contient les entrées client créées et attestées par cette installation. La porte exige un résultat `ok=true`, `partial=false`, le retrait effectif de toutes ces entrées, l'absence du libellé chargé, un port libre et le retrait du plist de son emplacement actif, avec conservation de sa copie journalisée. Un résultat conservant le service ou une entrée attendue ne satisfait pas G8. Le champ `complete=false`, qui signale les périmètres non inspectés, n'est pas à lui seul un échec. Les contrôles de conservation des données décrits ci-dessous restent obligatoires. La désinstallation conserve les données utilisateur. La conservation exige au moins un fichier non vide observé dans chacune des catégories jeton, ressources, sauvegardes et clé. Dans le second HOME jetable, après doctor et avant les inventaires précédant l’aperçu de désinstallation, le harnais crée `memory-os-backups/qualify-410-witness.mosb` et, si aucune clé non vide n’existe, `.config/memory-os/backup.key`. Ces témoins sont des données synthétiques non secrètes, non vides, de mode 0600 ; une clé existante non vide n’est pas remplacée. Leur création est consignée. Ils attestent uniquement la conservation de fichiers par uninstall, sans prétendre constituer une sauvegarde restaurable ou une clé cryptographique valide. Les fichiers immuables gardent leurs empreintes après désinstallation. Une catégorie absente ou vide ne démontre pas sa conservation. Le store conserve son identité et son contenu logique, vérifiés après arrêt ; les transitions SQLite normales liées à la fermeture sont distinguées des modifications de données. Les journaux restent présents ; seules les mutations attendues et inventoriées du protocole de désinstallation sont admises, notamment les révocations, le nouveau journal et le déplacement du plist. Toute autre modification refuse la porte | **nouvelle** | les deux |
| G9 | **Copie installée du store de production**, schéma 8→8 : coupe par l'API de sauvegarde du candidat, source en lecture seule, workers baseline 4.0.7 / wheel / sdist 4.1.0 et réouvertures, comparaison exhaustive, instance et autorité identiques, journal inchangé, aucune migration, dix recherches figées, MPS/natif, `doctor` lecture seule 0 FAIL ; **ajout** : ouverture lexicale de la copie refusée sans aucune écriture | 4.0.9 + ajout | sémantique |
| G10 | **Sauvegarde + exercice de restauration** (S4 ×2, même archive, deux artefacts) sur la copie | 4.0.9 | sémantique |
| G11 | **Store lexical neuf** via le wheel installé, sur des données et requêtes FIXES (littéraux versionnés dans le script) : écriture, recherche, fermeture, réouverture, sauvegarde, restauration ; classements identiques aux trois moments ; marqueur `retrieval_profile` présent ; ouverture par le profil opposé (sémantique) refusée sans mutation (inventaire à empreintes inchangé, aucun fichier créé) : elle est exécutée par l'environnement sémantique wheel installé de l'étape 6 (candidat scellé, fermeture ML présente), seul à atteindre le contrôle de profil, et doit être refusée par `store_profile_mismatch` | **nouvelle** | lexical |
| G12 | **P-B (intégrité)** : la chaîne ne lance pas P-B et ne lit pas ses scellés. G12 reçoit uniquement une preuve externe fournie par Romain hors des répertoires interdits (liée au SHA final : empreintes des entrées, du scorer et du rejeu, six résultats, 40/40), enregistrée telle quelle, ou une dérogation explicite (`waived_by_romain`, qui ne revendique pas ce résultat). Sinon `pending` : blocage | 4.0.9, externalisée | sémantique |
| G13 | **P-A (brief) : jamais de rejeu.** G13 calcule, exclusivement depuis Git, l'union des fermetures statiques aux commits comparés, y compris les fichiers retirés de la fermeture. Le rapport distingue le commit de la preuve P-A (`ceedb5c`), la référence intermédiaire (`9e41ea4`) et le candidat final. Il inventorie séparément les changements des appelants `server.py` et `memory.py`, ainsi que les dépendances et ressources pertinentes. L'identité de la seule fermeture statique ne suffit pas à établir un transfert fonctionnel. **En l'état, G13 est non transférable automatiquement ; une décision motivée de Romain est requise, sans aucun rejeu P-A** | 4.0.7 | sémantique |
| G14 | **Relais installés : exactement trois cas** — wheel sémantique, sdist sémantique, wheel lexical. Le profil est transmis explicitement et attesté dans chaque résultat. Le cas lexical utilise SQLite, retire les options d'encodeur incompatibles et s'exécute dans l'environnement sans ML. Les preuves portent sur les mêmes contrôles de connexion, panne, absence de rejeu et reconnexion (plus statut local pendant panne, handshake 4.1.0) | 4.0.9, adaptée | les deux |
| G15 | Vérification finale du manifeste, rejouée en dernier, jamais sautée ; contrôle aussi les décisions administratives requises ; elle seule lève `release_ready`, contrôles de sortie terminés ; `CHAIN_RESULT=PASS` après elle | 4.0.9, corrigée | — |

**Constat G13 à `453a893`, depuis Git seulement.** Fermeture des imports relatifs statiques de
`brief*.py`. La fermeture comprend 14 fichiers à `ceedb5c`, 22 à `9e41ea4` et 23 à `453a893`.
Entre les deux premières références, huit fichiers rejoignent la fermeture ; parmi les fichiers
communs, `models.py`, `sqlite_store.py` et `store.py` changent. `config.py` la rejoint au
candidat. Liste au candidat : `brief.py`, `brief_coverage.py`, `brief_selection.py`,
`brief_tokens.py`, `brief_value.py` ; `models.py`, `projects.py`, `qualification.py`,
`temporal.py`, `text.py`, `work_state.py` ; `blocks.py`, `changes.py`, `compensation.py`,
`config.py`, `corrections.py`, `entities.py`, `extract.py`, `fact_updates.py`,
`identity_policy.py`, `llm.py`, `sqlite_store.py`, `store.py`. `9e41ea4` → `453a893` : `config.py`,
`models.py`, `sqlite_store.py` diffèrent ; les cinq `brief*.py` sont identiques. `ceedb5c` →
`9e41ea4` : voir ci-dessus ; le 40/40 reste
attribué à `ceedb5c`, et le lien historique vers 4.0.7 n'est pas une identité à l'octet. Les
appelants `server.py` et `memory.py` ont changé en 4.1.0. La fermeture est un filtre conservateur,
pas une démonstration d'équivalence. Une empreinte historique fournie par Romain est enregistrée
comme telle, sans prétendre qu'elle démontre l'équivalence.

**Mesurés et rapportés, PAS des portes** (M1 et M3 exigent des mesures exploitables ; M2 peut
être non mesuré ; M4 est une référence historique ; aucune valeur n'est un seuil implicite) :

- **M1 — durée d'installation (clause D1 de `SETUP §10`)**, par profil, sur la première
  installation : T0 = lancement de `python3.11 -m venv`, T1 = retour de `quickstart … --apply
  --demo` avec `ok: true`. Les préparations réalisables en amont (copie du wheel, pré-placement
  du modèle et des tables) sont effectuées avant T0. Le total brut vaut T1−T0. Les contrôles et
  préparations effectués dans cet intervalle sont chronométrés séparément ; toute durée nette les
  excluant est présentée distinctement du total brut. Rapportés : total brut, durée de `pip`, `install.json["timing"]` et « temps hors modèle ». Les octets transférés par `pip` ne sont pas mesurés par ce protocole : `pip_downloaded_bytes=null` et `pip_downloaded_bytes_measured=false`, dans la preuve et dans la synthèse du manifeste. `pip_cache_bytes` mesure séparément la taille logique des fichiers du cache pip propre au run ; sa méthode et son instant de mesure sont consignés. Cette taille inclut des éléments de cache et ne représente ni une mesure ni une estimation des octets téléchargés. `quickstart_download_bytes` reprend distinctement `timing.download_bytes` : N6 seulement. L’absence déclarée de mesure des transferts pip ne bloque pas M1 ; les autres mesures M1 restent obligatoires. Écarts au protocole écrits dans la preuve : machine non vierge
  (`HOME` jetable + `env -i`), wheel local (4.1.0 n'est pas sur PyPI), modèle pré-placé ; en
  variante `.pth`, la durée est libellée « variante à dépendances réutilisées ».
- M2 — P-B en profil lexical : décision indépendante de Romain (§10) ; aucun agent ni script ne
  la produit ; sans preuve externe : « non mesuré », sans effet sur `release_ready`. Aucune mesure P-A, dans aucun profil.
- M3 rapporte les durées des commandes, le minimum d’espace libre observé et le maximum de swap observé, à partir d’échantillons horodatés conservés. Les bornes de chaque commande et de chaque sonde sont enregistrées séparément ; les durées utilisent une horloge monotone. Chaque étape possède une mesure avant et une mesure après. Une mesure « pendant » exige un chevauchement temporel démontré entre la sonde et l’exécution de la commande. Pour une commande terminée avant la fin de la première sonde démarrée pendant son exécution, cette sonde peut être conservée comme observation de chevauchement, avec ses bornes explicites ; elle ne prouve pas un instant de mesure intérieur à la commande. Une sonde achevée avant le démarrage de la commande ne compte jamais. Toute exception pour une commande trop courte est signalée explicitement dans la preuve et la synthèse, sans inventer d’échantillon « pendant ». Les échecs des sondes et du sampler sont bloquants, même si aucun marqueur d’erreur ne peut être écrit. Les échantillons sont scellés et leur synthèse recalculée en finale. Les extrema sont échantillonnés, non des pics garantis. Le superviseur reste inchangé. L’exception `none_short_command` n’est admissible que si la durée majorée de la commande est inférieure ou égale à 10 secondes. Cette majoration est calculée entre la borne de départ enregistrée avant `exec` et l’horodatage de l’observation après retour du superviseur et terminaison de la sonde ; elle est publiée sous `command_seconds_upper_bound`, séparément de la durée de l’étape. L’exception conserve zéro échantillon « pendant » et figure explicitement dans la preuve et la synthèse. Une détection impossible ou un échec de sonde ne constitue jamais une exception de commande courte.
- M4 — H2 : résultat **historique** (`H2_RUN_v3.md`, rétrospectif, avec ses limites), non rejoué,
  jamais une porte du commit final.

Limites conservées : P-A/P-B synthétiques, non exhaustifs, authenticité locale non signée ; ils ne
mesurent pas l'utilité réelle.

## 5. Ordre, disque et durée (ESTIMATIONS, aucune garantie)

Sources : journaux `qualify_408rc_v7` / `v7b` du 31/10 (≈ 15 min pour la chaîne 4.0.8) et budgets
de `QUALIFY_4_0_9.md`. Le budget additionne tous les exemplaires conservés : environnements,
caches pip, roues, modèles pré-placés, stores et preuves. Une réutilisation locale est nommée
explicitement et ne suppose aucune suppression des preuves. F (DB + WAL + SHM du store source)
valait 157,9 Mo le 22/11 (78 Mo en 4.0.9) ; **F est remesuré immédiatement avant la copie ; le
départ exige au moins 9 200 000 000 octets et le budget dynamique calculé, en retenant le plus
élevé.** Un venv d'installation par profil pour G7/G8 ; les environnements wheel/sdist de G9/G14 restent
distincts. Ce venv est réutilisé nommément par les deux `HOME` jetables du profil (installation 1 :
G7 + undo + M1 ; installation 2 : uninstall, après arrêt du premier service). Modèle pré-placé :
**220 227 876 octets par `HOME` sémantique**, soit deux exemplaires conservés.

| Étape (portes) | Place en plus (estimée) | Durée (estimée) | Départ exigé |
|---|---:|---:|---|
| 1. Préconditions, arbre, suite complète, lints (G1, G2) | 0,2 Go | 5–10 min | ≥ 5 Go |
| 2. Build, manifeste, contrôles source (G3, G4) | 0,45 Go | 2–3 min | ≥ 5 Go |
| 3. Résolution ×2 (G5 ; N1, N2) | lexical < 0,1 Go ; sémantique jusqu'à 0,3 Go transitoire si pip télécharge des roues ML entières | 1–5 min | ≥ 5 Go + GO |
| 4. Lexical : venv, 2 installations, undo, uninstall, M1 (G7, G8 ; N3, N6) | 0,25–0,4 Go (venv, cache pip, 2 `HOME`, preuves) | 4–8 min | ≥ 5 Go + GO |
| 5. Wheel lexical sans ML (G6 ; N4) | 0,1 Go | 1–2 min | ≥ 5 Go + GO |
| 6. Environnements sémantiques wheel + sdist hors dépôt (`.pth`, hors ligne), puis store lexical neuf (G11) | 0,1 Go | 1–2 min | ≥ 5 Go |
| 7. Sémantique : 2 installations, undo, uninstall, M1 (G7, G8 ; N5 ou `.pth`) | avec N5 : 1,8–2,3 Go (venv 1,0–1,5 ; cache pip 0,3 ; modèles 0,44) ; sans N5 : ≈ 0,5 Go (modèles 0,44) | 6–15 min | ≥ 5 Go |
| 8. **Copie + S4 ×2 et restauration commune supplémentaire** (G9, G10) | Budget dynamique = budget 4.0.9 + 3 F, selon la formule ci-dessous ; environ 5,35 Go pour F = 157,9 Mo sous `adaptive_v1` | plafond 900 s | **max(9 200 000 000 octets ; budget dynamique)** |
| 9. Périmètre du brief depuis Git (G13) ; enregistrement des décisions et de la preuve P-B externe (G12) | < 0,01 Go | < 1 min | ≥ 5 Go |
| 10. Relais ×3, vérification finale (G14, G15) | 0,05 Go | 1–2 min | ≥ 5 Go |

F est la somme des tailles de `store.sqlite3`, `store.sqlite3-wal` et `store.sqlite3-shm` présents, remesurée immédiatement avant la copie. Le budget 4.0.9 vaut `max(7 000 000 000, 5 000 000 000 + 20 F)` sous `legacy5gb`, et `reserve(F) + work(F) + 4 F + 256 × 1024²` sous `adaptive_v1`, avec `reserve(F)=max(3 000 000 000, 2 F)` et `work(F)=6 × max(F, 1 048 576) + 32 × 1024²`. Le budget 4.1.0 ajoute 3 F : une copie pour le refus lexical, puis la restauration commune supplémentaire et son espace de travail. Cette estimation ne remplace pas la surveillance continue du disque et du swap. Aucun exemplaire de preuve n’est supprimé pour satisfaire le budget.

Cumul estimé avant la copie : ≈ 2,1 Go sans N5, ≈ 3,9 Go avec. Durée totale estimée : 30–60 min.
Les étapes réseau et les installations passent AVANT la copie, pour que son seuil soit mesuré une
fois le reste consommé. `adaptive_v1` et plafond 4 Gio : choix explicite et privé de la seule
commande de copie, comme en 4.0.9.

## 6. Acquisitions réseau N1–N6 : chacune est un GO nominatif de Romain

(Nommées N pour ne pas les confondre avec la clause D1 de `SETUP §10`.) Chaque acquisition applicable N1–N6 exige un GO nominatif de Romain lié au SHA complet du candidat et au RUN_TAG. Le dossier d’acquisition est un fichier JSON au format `qualify-410-network-dossier-v1`, généré par `release_410_checks.py network-dossier` depuis l’outillage relu du candidat. Sa génération n’effectue aucune acquisition et ne vaut pas autorisation. Il décrit exactement l’outil, l’opération, le profil, les sources, les contraintes et les acquisitions couvertes ; N4 porte les versions épinglées et le SHA-256 du fichier de pins ; N6 porte l’URL, la taille, le SHA-256 et l’unique installation destinataire de la table. Le GO désigne le fichier et son SHA-256 et reprend exactement les opérations, sources et acquisitions autorisées. Avant l’opération, le dossier est lu, haché et comparé aux paramètres effectifs. La décision et son dossier sont conservés et scellés ; leur identité est vérifiée jusqu’à G15 et lors de `--check-final`. Toute absence, divergence ou modification entraîne un refus. Un nouvel essai de l’acquisition ou un élargissement exige un nouveau GO. Les installations utilisent uniquement des roues
(`--only-binary=:all:`, aucune dépendance tierce acquise sous forme source, aucune
dépendance de construction téléchargée ; le sdist local du candidat reste construit hors ligne
pour G3/G9/G14) ; les octets installés sont reliés aux URL et SHA-256 consignés. Les environnements
supplémentaires réutilisent les acquisitions locales vérifiées, ou exigent un GO distinct. Sans GO,
l'étape ne démarre pas et sa porte reste `pending`. Sources PyPI autorisées : `pypi.org` (index,
métadonnées) et `files.pythonhosted.org` (roues). Tailles N1–N5 : estimations non vérifiées.

| GO | Quoi | Source | Taille | Contrôle |
|---|---|---|---:|---|
| N1 | Résolution lexicale (`pip install --dry-run`, rien d'installé) | PyPI | estimation < 50 Mo | 36 verrous ; URL et SHA-256 de chaque distribution résolue ; rapport pip haché |
| N2 | Résolution sémantique (idem) | PyPI | **pas seulement des métadonnées** : plusieurs roues complètes possibles, estimation jusqu'à 0,3 Go | 65 verrous ; mêmes relevés |
| N3 | Dépendances lexicales installées dans **UN** venv (cache pip du run conservé ; les deux `HOME` lexicaux et G6 réutilisent ce venv, aucune autre installation) | PyPI | estimation 30–60 Mo | `pip check`, versions `==` verrous, aucun paquet hors liste hors bootstrap ; SHA-256 de chaque roue relevé (les contraintes n'épinglent pas d'empreinte) |
| N4 | `pytest` et sa fermeture pour G6, versions exactes épinglées dans un fichier figé dans l'outillage relu, référencé par son SHA-256 dans N4 et au manifeste (aucun choix de version pendant le run), installés après le gel de G7/G8 **sous les contraintes lexicales maintenues** (les dépendances communes ne bougent pas) | PyPI | estimation < 5 Mo | versions et SHA-256 relevés ; `pip check` ; runtime qualifié re-contrôlé après |
| N5 | Dépendances sémantiques installées dans **UN** venv autonome : fermeture complète des 65 verrous, aucune dépendance réutilisée par `.pth` ; réutilisé par les deux `HOME` sémantiques | PyPI | estimation 200–300 Mo | comme N3 |
| N6 | **Table cl100k par `--download-approved`**, exercée UNE fois, dans la première installation lexicale (aucune pré-copie) | `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken` | **1 681 126 octets** | SHA-256 `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` vérifié par `provision` ; `timing.download_bytes == 1681126` |

- **Aucun autre téléchargement.** La seconde installation lexicale (uninstall) et les deux
  installations sémantiques reçoivent une table cl100k locale vérifiée ; le modèle sémantique vient
  du cache local vérifié (`~/.cache/huggingface/hub/models--ibm-granite--granite-embedding-97m-multilingual-r2`),
  copié dans chaque `HOME` et contrôlé fichier par fichier contre `provision_v1.json`. Ces
  installations attendent `download_bytes == 0`.
- **Sans N5 (variante `.pth`)** : acceptable comme preuve d'intégration installée avec dépendances
  réutilisées, sous décision explicite de Romain. Ce n'est pas une installation sémantique
  complète depuis un environnement autonome ; G5 prouve une résolution, pas l'origine des
  dépendances exposées par `.pth`. La preuve atteste alors leurs chemins et versions, l'origine du
  paquet candidat, et M1 est libellée comme durée de cette variante.

## 7. Hors périmètre

Publication PyPI, push, étiquette Git, bascule ou redémarrage de la production, modification des
configurations client réelles ; conversion sémantique ↔ lexical ; retour arrière 4.0.x d'un store
lexical (documenté comme impossible, non testé ici) ; rejeu de H2 et tout banc ; `.mcpb` de Claude
Desktop ; `memory_consolidate` / `memory_compact` ; Linux, Intel, Python ≠ 3.11 ; machine ou
compte réellement vierge ; installation depuis le sdist en profil lexical ; tout rejeu P-A ou P-B
par un agent ; qualification séparée de 4.0.9 ; compte macOS dédié ou nouveau confinement.

## 8. Scripts à écrire ensuite (noms, rôle, entrées → sorties) — rédigés depuis la rév. 6 (liste d’origine)

Dans `scripts/` (versionnés, hachés dans l'inventaire d'outillage du manifeste) :

| Script | Rôle | Entrées → sorties |
|---|---|---|
| `release_410_checks.py` | règles fermées 4.1.0 : invariants du §1, états de porte, décisions admissibles, liaison commit/artefacts, `release_ready` seulement en finale | manifeste + preuves + décisions → refus ou accord |
| `make_release_410.py` | scelle artefacts, outillage du dépôt et hors dépôt | commit, preuves d'artefacts et de suite → `release-4.1.0-<tag>/manifest.json` |
| `source_release_checks_410.py` | 32 identités TCP (multiplicités, résultats) + MiniLM en cache + v3 | manifeste → `source-proof-input.json` |
| `check_brief_perimeter_410.py` | G13 : union des fermetures aux trois commits, SHA-256 depuis les objets Git, inventaire des appelants, dépendances et ressources ; ne lit aucun scellé, n'exécute aucun brief ; ne conclut jamais seul à un transfert | `ceedb5c`, `9e41ea4`, commit final → `report.json` |
| `prepare_release_410_envs.py` | environnements sémantiques wheel + sdist hors dépôt, hors ligne (`.pth`), chemins et versions attestés | manifeste → `qual-envs/410-installed-claude-<tag>/environments.json` |
| `qualify_install_410.sh` | G7, G8, M1 pour UN profil ; contrôles launchd du §3 ; inventaires à empreintes | wheel + SHA, venv, dossier neuf, graines locales → `report.json`, journaux, `SHA256SUMS` |
| `qualify_lexical_wheel_410.py` | G6 dans le venv lexical | venv, fichiers de test → `report.json` |
| `qualify_sqlite_release_410.py` + `sqlite_release_worker_410.py` | G9, G10, G11 | manifeste, environnements, plist et store (lus) → `410-copy-claude-<tag>/report.json` |
| `verify_release_manifest.py` (modification) | branche `4.1.0` ; `--record-resolution-proof`, `--record-install-proof`, `--record-lexical-proof`, `--record-perimeter-report` (G13), `--record-external-proof` (P-B fournie par Romain), `--record-decision` ; aucun enregistrement ne lève `release_ready` | preuve ou décision → manifeste |
| `installed_relay_e2e.py`, `installed_relay_case.py` (modifications) et leur vérificateur | G14 : matrice de trois cas, profil explicite, cas lexical SQLite sans `MEMORY_V4_EMBED_MODEL=hash`, version `4.1.0` | manifeste, environnements → preuve à trois cas |

Réutilisés sans changement : `qualify_405_test_isolation.py`,
`qualify_runtime_resolution.py`, `check_runtime_resolution.py`. Réutilisé avec une modification
minimale : `qualify_distribution_d1a.py` (l'aperçu `quickstart` installé reçoit `--profile lexical` ;
assertions inchangées). **Exclus de cette chaîne** :
`score_release_410_gate.py` et `record_release_410_gate.py` (non écrits) et le script historique
`run_integrity_v5_validation.py` (conservé dans le dépôt, non supprimé, jamais appelé : la chaîne
ne lance pas P-B). Hors dépôt : `~/memory-os-local/ops/qualify_410_v1_chain.sh` (§3, avec
l'échantillonnage M3) ; superviseur 4.0.9 inchangé.

## 9. Préconditions vérifiées par la chaîne avant toute étape (tout écart = ARRÊT)

1. `RC_PY` (`qual-envs/407-installed-v2`) : contrôle complet `inspect_current` du profil
   sémantique avec les extras `ops,brief` (seul `packaging==26.3` a été relevé à la rédaction) ;
   `RC_PY` n'est jamais recréé dans la chaîne.
2. Outils de construction et de lint présents (`/opt/homebrew/bin/python3.11`, `ruff`, outils de
   build et `twine` utilisés par G3) ; cache **MiniLM** du test de G4 présent hors ligne (le
   cache Granite ne le couvre pas) ; cache Granite et table cl100k locale conformes au catalogue.
3. Baseline de la copie : interpréteur tiré du plist de production, puis contrôlé contre
   l'interpréteur attendu (`~/.memory-os-runtime/venv-4.0.7/bin/python`) ; aucune substitution.
4. Aucun `RUN_TAG` existant, aucun verrou 409/410, libellé et port de test reconnus absents.
5. Les relevés de développement (`.local-baseline/` : 12 échecs `v4/tests`, 51 échecs/erreurs
   racine à `83ddead`) n'établissent pas que ces échecs persistent dans l'environnement isolé ;
   G2 reste à zéro ; toute exception future identifierait chaque test et sa cause.

## 10. Décisions de Romain

Chaque proposition ci-dessous reçoit une réponse explicite oui/non, liée au SHA final et,
lorsqu'elle concerne une exécution, au RUN_TAG. Une absence de réponse n'est jamais un accord. Les
preuves, rapports et dossiers d'acquisition mentionnés sont identifiés par leur empreinte. Les
décisions préalables à une opération sont acquises avant son lancement ; la re-signature D1
intervient après M1 et avant G15.

1. **P-A (G13) — Oui/non :** « Pour le candidat `<SHA final>`, j'accepte que la qualification
   avance avec G13 en dérogation motivée, sans transfert automatique de P-A et sans rejeu. Le
   résultat 40/40 reste attribué à `ceedb5c`. J'accepte les limites documentées du lien
   historique, des dépendances et des appelants modifiés. Cette décision peut satisfaire
   l'exigence administrative G13 pour `release_ready`, sans présenter P-A comme réussie sur
   4.1.0. » En cas de refus, `release_ready` reste faux.
2. **P-B, preuve externe — oui/non :** « Pour `<SHA final>`, je fournis la preuve externe
   `<référence, SHA-256>` répondant aux exigences G12, hors des répertoires interdits. » Une
   réponse oui ne remplace pas la fourniture et le contrôle de conformité de la preuve. Une preuve
   conforme donne `pass`.
3. **P-B, dispense — oui/non, en l'absence de preuve conforme :** « Pour `<SHA final>`, j'accepte
   la dispense G12 pour `<motif>`, avec le statut `waived_by_romain`, et j'autorise cette décision
   à satisfaire G12 pour `release_ready`, sans revendiquer un résultat P-B sur ce candidat. » Sans
   preuve conforme ni dispense acceptée, G12 bloque.
4. **Variante sémantique — oui/non :** « Pour `<RUN_TAG>`, j'accepte la variante `.pth` de G7, sa
   couverture réduite et son libellé M1 de durée avec dépendances réutilisées. » Non impose
   l'installation autonome sous GO N5 ; sans ce GO, l'étape ne démarre pas. Le refus de N5 ne
   sélectionne jamais automatiquement `.pth`.
5. **Acquisitions N1–N6 — une réponse oui/non par acquisition applicable :** « J'autorise
   l'acquisition Nn décrite par `<dossier, SHA-256>` pour `<RUN_TAG>`, avec les opérations,
   sources et acquisitions qui y sont énumérées. » Aucun nouvel essai ni élargissement n'est
   couvert implicitement.
6. **M2 lexical — oui/non :** « Je souhaite fournir une preuve P-B lexicale externe pour
   `<SHA final>`. » Sans preuve fournie, M2 est enregistré « non mesuré » et ne bloque pas
   `release_ready`. Aucun agent ni script ne produit cette preuve.
7. **Clause D1 lexicale — oui/non, après M1 :** « Je re-signe la clause D1 pour `<SHA final>` sur
   la mesure `<référence, SHA-256>` et les écarts au protocole vierge qui y sont consignés. »
   Cette acceptation administrative est requise avant G15 ; elle n'introduit aucun seuil
   automatique de durée.
8. **Service launchd et nice — oui/non :** « Pour `<RUN_TAG>`, j'accepte une exception à `nice 15`
   limitée au service launchd de test ; les processus lancés directement par la chaîne restent
   soumis à cette règle. » Non interdit le lancement des installations jusqu'à définition et
   relecture d'une solution assurant cette priorité.
9. **Lancement — oui/non :** « J'autorise le lancement de la chaîne relue `<SHA-256>` pour
   `<SHA final>` et `<RUN_TAG>`, sous les décisions préalables applicables. Ce GO ne remplace
   aucun GO réseau et n'autorise ni publication, ni push, ni bascule de production. »
