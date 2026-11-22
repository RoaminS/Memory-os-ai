# Changelog

## 4.1.0 — en développement (branche feature/4.1.0-lexical, 2026-11-21) ; non publiée / non déployée / non qualifiée

- Profil de récupération explicite : `semantic` (défaut, moteur inchangé) ou `lexical` (politique `lex-4.1.0-v1`,
  SQLite, aucun encodeur ni vecteur, aucun import ML). Sélection par `Config.lexical()` ou
  `MEMORY_V4_RETRIEVAL_PROFILE=lexical`, jamais déduite des dépendances installées.
- Marqueur `retrieval_profile` dans les métadonnées d'un store lexical ; sonde en lecture seule avant toute
  ouverture mutante, refaite sous bail d'écriture. Refus : `store_profile_unverifiable`, `store_profile_mismatch`,
  `store_profile_contaminated`. Aucune conversion implicite. Un store lexical n'a pas de retour arrière 4.0.x.
- Finalisation automatique du moteur (ramasse-miettes, sortie de l'interpréteur sans `close()`) : la connexion
  SQLite est désormais fermée AVANT le relâchement du bail d'écriture, comme dans `close()`. Correction d'ordre
  pure, pour les deux profils (sémantique compris) ; aucune donnée stockée, schéma ni migration modifiés.
- Un store existant dont le profil ne peut pas être lu (fichier illisible, métadonnées invalides, paire
  `-wal`/`-shm` incomplète, fichier annexe orphelin) est désormais refusé aussi par le profil sémantique.
- Empaquetage : fermeture ML dans l'extra `[semantic]` ; métadonnées à bornes compatibles ; verrous exacts dans
  les contraintes par profil (`qualified_runtime_constraints_v1.txt`, `qualified_runtime_constraints_lexical_v1.txt`).
  Ouvrir le profil sémantique sans la fermeture ML lève `semantic_dependency_missing`.
- Runtime qualifié par profil ; états `qualified` / `missing_dependency` / `drift` ; capacités : encodeur
  `absent_by_profile` en lexical.
- `bench/evaluation_manifest.py` : mode `retrospective_no_selection` pour une mesure sur corpus déjà observé.
- Inchangés : schéma SQLite 8, `embed.py`, cache d'embeddings, comportement du profil sémantique.
- Installation multi-clients (phase 1a-setup, `v4/docs/SETUP_4_1_0.md`) : `quickstart --profile {lexical,semantic}`
  (obligatoire pour une installation neuve, relu d'un service existant, jamais converti), SQLite chargé vérifié
  avant toute écriture et attesté par le serveur (`capabilities.store.sqlite_version`), codes de refus stables,
  entrée cliente existante jamais écrasée en silence (`--replace-existing`), diagnostics structurés sans queue de
  log, `quickstart --undo --with-service`, `memory-os-admin uninstall` (données jamais supprimées), localisateur
  `memory-os-constraints` et portes de résolution par profil. `doctor` accepte un déploiement lexical.
- Mesure H2 (`h2-lexical-parity-v1`, rétrospective, BEAM 100K gelé, 400 questions par bras dont 355 scorées,
  20 conversations) : Recall@10 lexical 44,089 % / sémantique 45,764 %, écart −1,675 point, IC95 bootstrap par
  conversation [−3,747 ; +0,404] ; seuil conjoint non atteint → le sémantique reste recommandé, lexical = profil
  « léger ». Ni test indépendant, ni supériorité, ni conclusion sur les réponses générées ou les tâches d'agent.
- Non fait : preuve du wheel lexical installé, copie de store qualifiée, preuve d'installation réelle et
  mesure de la clause D1 pour le profil lexical. Contrat moteur : `v4/docs/LEXICAL_PROFILE_4_1_0.md`.

## 4.0.9 — candidate à chaîne finale (2026-11-21) ; état à la préparation : non publiée / non déployée

- Contenu fonctionnel = 4.0.9.dev1 (a745b40, rapport de santé) ; aucun changement moteur, brief, schéma ni encodeur.
  Le correctif brief 4.0.9.dev2 (branche fix/4.0.9-brief-focus) est EXCLU : il amende l'ordre scellé P-A et
  reste expérimental.
- Documentation : README (statut, commandes d'installation, boutons Cursor/VS Code, notes de version) et
  docs/CLIENTS.md, server.json (registre MCP) et la note docs/releases/v4.0.9.md (embarquée) ne disent plus « candidate, not published » pour une version publiée ; 4.0.8 est notée
  publiée sur PyPI le 31/10/2026 (fait vérifié sur PyPI le 21/11 ; la publication n'avait pas été consignée ici).
- Le numéro 4.0.9 avait été préparé le 31/10 sur une autre arborescence (commit 06199a2, sources absentes de
  ce dépôt, suite complète en échec, ops/qualify_409_c1_chain.sh orphelin) : cette candidate-ci le remplace.
- Précision sur l'entrée 4.0.9.dev1 ci-dessous : l'instantané unique et l'isolation d'une fiche corrompue concernent
  la lecture des chaînes de corrections, pas le scan SQLite général du rapport ; le seuil de 10 compte des cibles
  remplacées non acquittées (relecture Codex du 21/11).
- Qualification installée, publication PyPI et bascule : à faire, sur décision de Romain.

## 4.0.9.dev1 — autonomie du rapport de santé, 2026-11-20, locale, non publiée

- `ops/health.py` : sonde `/health` et `/ready` à 15 s (l'audit d'intégrité de `/ready` prend ~4,4 s ; l'ancien
  délai implicite de 3 s donnait « status -1 » sur un serveur sain). Jeton toujours obligatoire.
- Les corrections client explicites (`replaces`, même projet, cible fermée et non supprimée, successeur non
  expiré, au plus 3 cibles, audit `explicit_replaces` quand présent, chaînes suivies jusqu'au maillon vivant) sont
  signalées en info, jamais écrites dans la base d'acquittement. Plus de 10 en 24 h → anomalie (motif de masse).
- Lecture SQLite en un seul instantané `mode=ro` + `query_only` ; une fiche corrompue n'affecte qu'elle-même.
- Revue adversariale (Claude, contexte neuf) : 1 bloquant + 3 à corriger, tous traités et couverts par des tests.

## 4.0.8 RC v3 — correctifs de qualification du2026-10-26, non publiée

- Packaging : référence supprimée vers une note RC inexistante, note finale4.0.7
  incluse. Contrôles statiques des data-files, points d'entrée et sources sdist,
  exécutés aussi avant le build de qualification.
- Tests du budget legacy et des plafonds isolés des variables du shell ; options
  adaptive_v1/plafond explicite limitées à la copie privée dans le runbook.
- Runtime sous v4/src inchangé depuis le gel9ecb4d0. Nouveaux artefacts/manifeste
  et chaîne installée complète requis ; aucun report de release_ready.

## 4.0.8 RC v2 — amendement du2026-10-26, version inchangée / non publiée

- S4 : plafond64MiB remplacé par4GiB par défaut, override explicite validé64MiB–16GiB,
  bornes snapshot/archive/décrypté/décompressé/drill dérivées ; marges exposées doctor/health.
- Archive MOSB1 AES-GCM inchangée, lecture/écriture par flux et authentification
  avant extraction ; BLOB legacy hachés par flux. Test réel au-delà64MiB,
  bornes inclusives et interop deux sens ; qualification installée encore requise.
- Réserve5Go conservée par défaut ; proposition adaptive_v1 explicite, plancher3Go
  et travail proportionnel6F+32MiB, pas d’activation automatique des jobs.
- Diagnostic privé :20452/28281 versions sont des stats d’accès seules ;
  7016clés actuelles+3504legacy expliquent10520entrées cache. Pas de purge,
  pas de modification de persistance d’accès ni de schéma. Projection~82Mo/mois,
  conditionnelle à deux observations et non garantie.
- Coupe de qualification réalisée par S4 candidat (nouveau cap), baseline4.0.7
  intacte sur snapshot privé immutable. Nouveaux artefacts et preuves v2 exigés ;
  aucun report automatique du release_ready de v1, aucune publication/bascule.


## 4.0.8 — candidate à chaîne finale (2026-10-16), non publiée / non déployée

- Provenance déclarative relayée selon le schéma pour toutes les mutations connues ;
  champ accepté par les modèles, jamais authentification. Reçus anciens sans
  provenance compatibles ; événements historiques unknown non réécrits.
- Doctor accepte les versions PEP 440 rc/dev/post ; les contrôles d’identité restent requis.
- /ready : audits positifs SQLite/descripteur réutilisés au maximum une seconde,
  invalidation par génération/écriture/empreinte ; états critiques vérifiés à chaque GET.
  Un défaut mémoire non suivi est détecté au plus tard au prochain audit (≤1 s).
- Qualification source Claude sur 6182aa5 : 796 v3, 2174 v4 dont 32 TCP physiques,
  zéro échec ; deux lints verts, configurations clientes inchangées.
  Fixture synthétique 3500 : p50/p95 chaud 126,1/158,5 → 16,1/16,8 ms ;
  après TTL max sur cinq 181,6 → 172,4 ms, aucun gain isolé revendiqué ; zéro appel neuronal.
- SQLite reste schéma8, encodeur/profil/C4 et dépendances inchangés. Pas de migration
  prévue 4.0.7→4.0.8 ; preuve exhaustive et cache/recherches à confirmer sur copie installée.
- Le report depuis le code fonctionnel qualifié 6182aa5 ne change sous v4/src que
  __version__. Les artefacts 4.0.8 doivent être construits/requalifiés ; release_ready
  reste false avant source, P-B RC, copie/S4 et relais installés. Les résultats
  synthétiques antérieurs ne prouvent ni gain d’usage, ni classement concurrentiel.
- Identité projet : libellés sensibles à la casse conservés ; liaison explicite
  racine→projet proposée côté hook, sans fusion automatique ni modification du hook.


## 4.0.7 — candidate à chaîne de version finale (2026-10-14), non publiée / non déployée

- La chaîne finale 4.0.7 remplace 4.0.7rc1 uniquement pour la requalification :
  doctor refuse les versions préliminaires PEP 440 (rc/dev), sans effet sur les
  versions finales. Défaut connu, correction avec tests prévue pour 4.0.8 ;
  doctor.py reste inchangé afin de préserver le report des gates A/B.
- Depuis f9d3668, sous v4/src, seule __version__ change. Les fonctionnalités et
  limites P-A/P-B décrites ci-dessous restent identiques : gate A 40/40 sur
  ceedb5c, gate B 40/40 sur f9d3668, schéma8 et journal prospectif, compensation
  explicite, CAS des révisions et correctif cache ; aucun gain d'usage revendiqué.
- Limites du gate B conservées : MAC exact non vérifié ; rejeu-oracle candidat
  pour les défauts non sécuritaires ; classement de l'oubli/requête non indépendant ;
  scope synthétique/hash/relais simulé non exhaustif ; authenticité locale
  conditionnelle, non signée. Le report sur ce commit exige une nouvelle collecte.
- RC1 : suites/source/artefacts et cinq workers sur copie étaient verts (migration
  0,57 s, 0 miss/encodage, dix requêtes × dix hits et classements identiques), mais
  le FAIL du doctor a refusé la campagne et laissé release_ready=false. Les
  preuves RC1 restent conservées et ne qualifient pas ces nouveaux artefacts.
- Requalification COMPLÈTE 4.0.7 en dossiers v2 : nouveaux wheel/sdist/descriptor,
  31 TCP, source/sémantique, copie7→8, doctor, cache/reopen, S4 backup/drill,
  recollecte P-B et relais. Aucun artefact RC renommé, aucune publication/bascule ;
  production4.0.5 maintenue jusqu'au GO distinct de Romain.

## 4.0.7rc1 — candidate ALPHA (2026-10-14), non publiée / non déployée

- Brief compact négocié à corps unique, unités entières ou première phrase/référence,
  parts du budget, priorité tâches/décisions/contraintes et saturation quantifiée.
  Gate P-A acquis sur ceedb5c : 40/40 cas scellés, performance de source p95 193,9 ms
  au rerun préenregistré ; pas de gain d'usage réel démontré.
- Intégrité B2/B3/B4/B5/B9 qualifiée sur f9d3668 : corrections avec CAS de révisions,
  impacts retournés, aperçu lié à l'application, compensation explicite sans
  résurrection d'une donnée oubliée, journal prospectif paginé et tests de panne.
- Gate P-B : 40/40 synthétiques, 160 lignes, 0 invalidation inattendue/lot partiel.
  Limites : MAC exact exclu ; rejeu-oracle candidat pour les défauts non sécuritaires ;
  classement de l'oubli par requête non évalué indépendamment ; hash/relais simulé,
  périmètre non exhaustif ; authenticité locale conditionnelle, non signée.
- SQLite 7→8 : autorité/histoire/reçus/tâches conservés, journal initial vide et
  coverage_start prospective. Pas de downgrade automatique en 4.0.5 après écritures8.
- Correctif du cache d'embeddings : réutilisation conditionnelle au profil attesté
  identique, sans ré-encodage prouvé sur la répétition source d'une copie. La bascule
  4.0.6 avait été annulée ; la production reste 4.0.5. Preuve installée RC à produire.
- Non-vacuité : les dix recherches figées exigent ≥1 hit chacune dans chaque worker.
  Les anciennes égalités sur listes vides ne démontraient pas l'identité des résultats.
- Réglages cosine historiques inertes supprimés des nouveaux plists ; confinement
  visible selon les règles réellement appliquées, pas selon ces anciennes variables.
- README PyPI macOS/Python3.11 explicite, extras [ops,brief], libellé projet commun,
  liens HTTPS Cursor/VS Code ciblant uniquement la future stable 4.0.7.
- Cette RC ne modifie sous v4/src que __version__. Le report des gates sur le commit
  RC exige la recollecte et la qualification installée ; aucune publication/bascule.

## 4.0.6 — publiée sur PyPI (2026-10-12), non déployée sur la production locale

- D0 : métadonnées et liens publics corrigés, manifeste MCP stdio relayé en
  4.0.6 (brouillon non publié), Docker explicitement historique v3, README et
  matrice de 11 clients distinguant configuration, connexion et usage réel.
- D1 : dépendances documents/historique déplacées vers `[documents]` et
  `[legacy-v3]` ; le core neuronal conserve PyTorch/Granite. Les commandes v3
  signalent l'extra manquant ; aucun gain de taille annoncé sans mesure neuve.
- Identité de build embarquée et vérifiée depuis wheel/sdist, source/payload
  contrôlés ; `build_unqualified` si descriptor absent ou modifié. Identité
  exposée dans ready/doctor ; ce contrôle n'est pas une signature de confiance.
- Profil qualifié épinglé exactement : torch 2.10.0, sentence-transformers
  5.2.3, transformers 5.2.0, numpy 2.4.2 et dépendances numériques/tokenizer.
  Quickstart refuse un profil installé différent avant activation. La liste
  de versions ne prouve pas l'équivalence entre toutes les machines.
- Provision explicite Granite/cl100k avec révisions/empreintes, aperçu sans
  écriture, téléchargement uniquement après consentement ; offline par défaut.
- Quickstart macOS : choix des clients, aperçu sans écriture, `--apply`, consentements
  téléchargement/demo séparés, configurations sauvegardées/journalisées,
  restauration sémantique de notre entrée ; refus des fichiers invalides,
  services étrangers et magasins non vides. Aucun job backup planifié implicitement.
- Décision Romain du 2026-10-11 : **installation neuve via quickstart = SQLite
  + tâches**, alternative `--backend jsonl`. Aucune migration implicite des
  installations existantes ; défaut Config JSONL et registre canonique inactif conservés.
- Correctifs de packaging/refus : helper PEP 517 disponible dans le build direct
  et depuis sdist ; extra `legacy-v3` évite le marqueur de version ambigu `v3` ;
  contrôle de tous les marqueurs produits ; identité invalide refusée avant token.
  `--warm` charge réellement la recherche SQLite ; mode froid sans modèle préservé,
  test séparé hash/warm. Qualification TCP par les **28 identités attendues**.
- Préparation initiale `4d0e746` : versions alignées, notes et docs de release
  embarquées, sans nouveau comportement moteur.
- Tests de l'adaptateur d'activation historique : contrat de version 4.0.5
  simulé explicitement dans les fixtures de migration/panne. La garde réelle
  reste 4.0.5 uniquement ; 4.0.6 refusée avant accès au magasin, sans élargir
  la qualification de cet outil. Les installations neuves n'en ont pas besoin.
- Décision Romain du 2026-10-12 : garde disque provision/quickstart centralisée
  dans `min_free_bytes()`, défaut conservateur **5 GB**. Dérogation opérateur
  `MEMORY_V4_MIN_FREE_BYTES` en octets décimaux entiers, minimum **3 GB** ;
  valeur inférieure/invalide refusée explicitement, jamais ignorée. Aperçu :
  max(seuil effectif, pic de provisioning + 1 GB), seuils exposés dans le rapport.
  Plafond de swap 8,5 GB inchangé. Dérogation non recommandée aux utilisateurs,
  non persistée dans les configurations du service ou des clients.
- Harnais de qualification source, artefacts et résolution alignés sur cette
  même garde ; seuil effectif `min_free_bytes` dans chaque rapport. La
  dérogation validée est conservée dans les sous-processus du harnais D1.
- Correction du défaut réel d'installation neuve des 4.0.4/4.0.5 et de la
  candidate pré-fix : `mcp>=1.26` pouvait résoudre MCP 2.3.0, API `list_tools`
  absente. MCP **1.26.0** et fermeture exacte du runtime qualifié désormais
  imposés dans METADATA : 61 core + 4 pour brief, dont transport/pydantic,
  cryptography/filelock et autres transitives. Contraintes/catalogue embarqués,
  contrôle préflight et doctor local/déployé ; périmètre macOS arm64 CPython 3.11.
  Aucun support MCP 2.x ni garantie de sécurité par le seul pinning.
- Quickstart : échec de lancement détecté via runs/dernier code de sortie,
  diagnostic borné de server.err avec token masqué ; arrêt du seul nouveau
  job réattesté, avant modification des configs clients. Évite l'attente de 180 s
  sur l'import SDK en échec ; conserve journal/token/plist/fixtures.
- Gate avant trial : vérification hors ligne du plan pip, puis résolution réseau
  gardée par Claude sur le wheel exact. Le rapport réel du trial du 2026-10-12
  est rejeté avec 39 écarts et 6 paquets inattendus, sans aucune requête réseau
  par le contrôleur. Le nouvel essai reste requis, pas encore déclaré réussi.
- Correction du deuxième défaut de clean install : HF 1.5.0 lit refs/main
  sans strip ; provision écrit désormais exactement la révision sans LF.
  Ref existante comparée en bytes, format différent refusé sans réparation
  silencieuse. Test réel HF cache reader hors ligne sur fichiers synthétiques,
  sans modèle. `embed.py`/encoder_source_sha256 et profil qualifié inchangés ;
  passage d'une revision explicite au chargeur différé à une qualification dédiée.
- Qualification de ce commit/ces artefacts et essai d'installation neuve encore
  requis. Aucun gain de productivité mesuré : L11 non exécuté ; LME-S/BEAM
  descriptifs, profils/limites explicités, aucun classement officiel revendiqué.
  Publication, bascule et GitHub restent soumis aux décisions distinctes de Romain.

## 4.0.5 — ALPHA publiée / déployée (2026-10-11)

- Isolation HOME/XDG/AppData des tests v3/v4 et garde d'écriture dans le HOME réel ;
  correction des callbacks setup historiques pouvant modifier les configs utilisateur.
- Templates MCP relayés ; erreurs préflight retryables avant envoi distinctes des
  résultats incertains après envoi. Aucun rejeu automatique des mutations.
- Qualification : comparaison sémantique de notre entrée dans les fichiers clients,
  SHA strict des fichiers du dépôt. Suites, TCP, copie et relais installés qualifiés.
- SQLite S4 et L7b livrés opt-in : sauvegarde/drill, rollback après nouvelles écritures,
  tâches/CAS, historique et reçus atomiques ; liste filtrée/paginée en SQL au schéma7.
  Mesure 10k par Claude : CAS p95 5,5 ms, list50 p95 0,52 ms ; preuve synthétique.
- Démarrage SQLite/tâches à froid sans modèle ; recherche initialisée explicitement ;
  ready expose retrieval_ready et capabilities.policies.write_mode.
- Romain a approuvé publication/bascule puis activation séparée SQLite+tâches le
  2026-10-11. Cette activation concerne son installation, pas le défaut Config
  du package. Registre inactif et profil auto/MPS/native/batch32/C4 conservés.
- Incident tests 4.0.4 documenté : ne pas exécuter cette ancienne suite avec le HOME réel.

## 4.0.4 — ALPHA (2026-10-11), qualifiée / déployée localement

- Incident de qualification : la suite v3 livrée dans cette sdist peut remplacer
  la configuration Claude Desktop par le serveur v3 et écrire d'autres configs
  locales. Ne pas exécuter les tests de la sdist 4.0.4 sur un profil utilisateur
  réel ; utiliser un utilisateur/conteneur jetable. Correctif prévu en 4.0.5.
  La configuration locale a été restaurée par Claude ; le service 4.0.4 n'est
  pas la cause. Voir `v4/docs/INCIDENT_TEST_HOME_2026_10_11.md`.
- Qualification complète et bascule locale réalisées par Claude avec le GO de
  Romain ; publication PyPI réalisée par Romain, empreintes vérifiées par Claude.

- Verrou serveur de maintenance ; instructions clients alignées, opérations
  destructrices interdites sans activation explicite par l'opérateur.
- Journal durable du relais, statut local des requêtes incertaines même pendant
  panne du catalogue, adoption read-only, rotation bornée sans purge d'incertains.
  Aucun rejeu automatique ; limites de transactions JSONL explicites.
- Rotation du jeton et reconnexion bornées, erreurs explicites, détection d'un
  changement d'identité serveur ; `serverInfo.version` amont conservée.
- Capacités déployées dans `/ready` et doctor ; observations client datées,
  identité/profil/version contrôlés ; diagnostic distinct des preuves de connexion.
- Brief compact par défaut : enveloppe entière ≤6000 octets / ≤1500 tokens
  cl100k_base, validité/portée filtrées, références expansibles, detailed explicite.
  Extra `[brief]` et table locale SHA-256 vérifiée requis, aucun téléchargement
  implicite ; correction du runtime jetable incluant `tiktoken_ext`.
- Registre canonique IDs/alias livré mais INACTIF selon Romain ; aucune fusion
  automatique, aucune réécriture historique. Création de nouveaux projets à
  fluidifier avant activation séparée.
- C4, JSONL et profil auto→MPS/native/batch32/512 inchangés ; SQLite opt-in.
- Compteurs/latences/budgets de qualification locaux ; aucune nouvelle campagne
  ni prétention leaderboard. Qualification source, exact tokens, copie4.0.3→4.0.4,
  et relais installés requise avant GO distincts de publication et bascule.
- Diagnostic BEAM : annotations manquantes distinguées des IDs inexistants ;
  refus pour groupes indépendants insuffisants avant lecture des annotations.

## 4.0.3 — ALPHA (2026-10-10), non publiée / déployée localement

- Bascule exécutée par Claude à 22:36 avec le GO de Romain : sauvegarde qualifiée
  de 1050 éléments, `/ready` 4.0.3, doctor 15 PASS / 0 FAIL. Publication PyPI non autorisée.

- Défauts conservés : C4 scoped_literal + garde 3 ; device auto (MPS sur le Mac
  déployé), précision native, batch 32 ; JSONL par défaut, SQLite S2/S3 opt-in.
- Profil d'encodage attesté : dtypes/devices des paramètres, révision modèle
  connue, versions runtime, empreinte du code encodeur ; visible dans status/ready.
  Un profil inconnu/incomplet/différent reconstruit tous les vecteurs avant lecture.
  Une révision non attestable interdit la réutilisation entre instances.
- Encodage et ajout groupés disponibles explicitement ; aucun résultat CPU/MPS
  historique ne démontre l'équivalence stricte. Pas d'accélération nouvelle activée.
- Pilote de réponses descriptif BEAM observé, C4/BM25 : Luna low lecteur, Astra high
  juge, ChatGPT via codex exec uniquement ; 10 questions, 40 appels maximum, aucun
  score publié avant exécution. Ce juge n'est pas le GPT-4o des publications.
- Qualification des distributions et reconstruction sur copies privées exigées,
  garde fermée jusqu'aux preuves TCP, relais installé et copie/profil/doctor.
  Publication et bascule restent deux GO distincts de Romain.

## 4.0.2 — ALPHA (2026-10-10), publiée sur PyPI

- Doctor installation + registre neuf clients + setup auto-jeton ; erreurs de configuration
  contrôlées, diagnostic SQLite conservé via `doctor --store-only`.
- C4 scoped_literal/garde 3 proposés par défaut ; rollback explicite legacy/0.
  LongMemEval-S : 500 exécutées, 479 avec preuve, 466 clusters ; ΔRecall +0,042
  IC95 [+0,022;+0,061], ΔMRR +0,012 [+0,008;+0,0164]. MiniLM, questions recoupant
  l’oracle : résultat non indépendant, pas une exactitude de réponse ni un classement.
- SQLite S2 transactions/cache/reçus et S3 migration hors ligne réversible livrés opt-in ;
  JSONL reste par défaut. Backup/restore SQLite S4 restent à qualifier.
- Qualification de deux nouveaux artefacts ; garde fermée tant que manquent les preuves
  du relais installé. Activation de production réservée à Romain.
- Voir `docs/releases/v4.0.2.md`, `v4/docs/RELEASE_4_0_2_DECISIONS.md`
  et `releases/4.0.2/QUALIFICATION.md`.
- Évaluation post-publication BEAM 100K : le critère préenregistré C4 contre BM25
  n'est pas atteint (ΔRecall +0,017, IC95 [-0,004;+0,040] ; ΔMRR +0,008
  [+0,003;+0,015]). Aucun score d'exactitude ni rang leaderboard revendiqué.

## 4.0.1 — ALPHA intermédiaire, publication soumise à qualification finale

- Identité de distribution et des deux moteurs : 4.0.1, sans suffixe rc.
- HTTP local : Host/Origin validés par le SDK MCP ; bearer obligatoire côté serveur,
  chargé depuis un fichier privé ; `/health` et `/ready` authentifiés, identité de build.
- Faits : remplacement sur identité explicite ; déduplication conservatrice par
  texte normalisé identique et portée compatible, jamais par seule similarité.
- Un écrivain moteur par magasin et reçus de requêtes JSONL. Résultat incertain
  après panne intermédiaire : aucune promesse de transaction commune aux fichiers.
- A2 : réparation ciblée sur plan versionné de douze invalidations INC-001/001b,
  simulation par défaut, historique/provenance préservés et trace vérifiable.
- SQLite S1 expérimental derrière Store et drapeau explicite : magasins neufs,
  historique, transactions et tests de panne de processus ; aucune migration.
- Import exact des clients/ops Claude : relais, admin token/health/backup/drill/
  hook/setup et extra `[ops]`. Revues A6 R1–R7 et
  B1–B5 levées ; qualification du relais installé exigée avant publication. Les
  garanties industrielles complètes restent à réaliser en SQLite S2–S5.
- Notices d'exploitation, règles agents et changelog inclus dans les distributions.

La revue A6 ne constitue pas seule un GO de publication. Lire
`v4/docs/A6_REVIEW_6d3f749.md` et `docs/releases/v4.0.1.md` avant toute utilisation
des outils ops. Aucun déploiement/réparation de production effectué par ce lot.

## 4.0.0

Version v4 déjà publiée sur PyPI selon l'opérateur ; moteur v4 et compatibilité v3.
Les corrections ci-dessus répondent aux incidents et à la qualification ultérieure.
