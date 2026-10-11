# P1 — S4 incrément 2b puis fin des matrices et L7b candidat

2026-10-11. Base `9834083`. Version de paquet inchangée 4.0.4 ; développement
pour 4.0.5. Aucun push, publication, activation SQLite/registre, changement de
service ou nouvelle intégration de modèle. Les dossiers de refus restent conservés.

## 1. Qualification corrigée — 2b

Commit `61d9876`, ref `feat/sqlite-s4-increment2b`. Les fichiers applicatifs des
clients sont attestés par le SHA de **l'entrée MCP complète canonicalisée**
`memory-os-ai`, sans sérialiser ses valeurs dans le rapport. Remplacement de
fichier/inode, changement de formatage et état de session sans lien ne sont plus
des dommages MCP. Commande, arguments, environnement, URL et autres paramètres
de l'entrée restent protégés. Fichier invalide/illisible = refus, même stable.
Les fichiers du dépôt `.mcp.json`, `.vscode/mcp.json`, AGENTS/CLAUDE restent
attestés par SHA du fichier entier. Les consignes utilisateur AGENTS aussi.

Le test TCP de redémarrage vérifie le contrat avant envoi :
`preflight_unavailable`, `not_started`, `mutation_sent=false`, pas de retry
automatique. L'incertitude après envoi reste couverte séparément par L3/W06.
15 tests ciblés locaux passent ; le test TCP corrigé doit être rejoué par Claude.

## 2. S4 : retour depuis l'autorité courante, jamais l'ancien snapshot seul

Commit S4 `393a05a`. `sqlite_migrate export --rollback` travaille hors ligne,
sous lease, vers un dossier neuf/disjoint, source jamais modifiée. Après migration
et nouvelles écritures, l'export conserve toutes les versions d'items (corrections,
oublis, ajouts), blocs, reçus retenus, incertains legacy et originaux archivés.
Il crée une nouvelle identité, `restored_from_instance_id`, événement de
récupération et reçu final avec sequence/source-cut. Publication exclusive après
comparaison exhaustive et fsync ; interruption = pas de succès partiel.
`--original` et `--rollback` sont incompatibles. L'export n'active aucun moteur.

```sh
# Uniquement une copie arrêtée/checkpointée ; jamais ~/.memory-os-v4.
python -B -m memory_os_v4.sqlite_migrate export \
 .local-qualification/COPIE_SQLITE_ARRETEE \
 .local-qualification/RETOUR_JSONL_NEUF --rollback
```

Les tâches/historiques L7b sont préservés dans `task-current.jsonl` et
`task-versions.jsonl`, inactifs en JSONL. Leur présence interdit une réimportation
implicite qui les ignorerait : conversion explicite future nécessaire. Les blocs
CAS exportés portent `inactive_block_cas` ; le candidat JSONL refuse leur écriture
legacy sans empêcher les ajouts d'items. Un runtime antérieur ne connaissant pas
ce garde doit rester **entièrement read_only** ; aucune reprise en écriture sur
ce runtime n'est qualifiée. Toutes les nouvelles écritures après un retour sont
une nouvelle autorité à exporter, jamais à remplacer par une sauvegarde ancienne.

Rotation : vérification immédiate du SHA/lien avant suppression ; pinned,
inconnus et altérés conservés. Cinq frontières SIGKILL supplémentaires vérifient
la conservation du pinned et de la source et une reprise sûre. Une rotation
interrompue peut être partielle ; elle n'invalide pas le backup qualifié conservé.
Les archives au schéma connu 5 sont vérifiées exhaustivement avant migration
du **restore privé uniquement** vers 6, puis diff autorisé/attesté. Schéma futur,
source plus ancienne que le snapshot ou migrations divergentes = refus.
L'export hors ligne courant exige le schéma 6 ; qualifier la mise à niveau sur
copie d'une source 5 avant cet export. Aucune réparation de l'autorité source.

## 3. Couverture S4 et limite de qualification

| Cas | Preuve automatisée | Preuve extérieure requise |
|---|---|---|
| B01–B03 | writers après cut, intégrité/crypto/path, timeout/erreurs | copies industrielles et cut/RPO |
| B04 | `test_sqlite_engine` : exceptions et SIGKILL à toutes frontières S2/COMMIT/ACK | suite complète hors sandbox |
| B05 | backup SIGKILL, cache et publication, 5 frontières de rotation | suite complète |
| B06 | `s4/test_rollback` : après migration, ajouts/correction/oubli/blocs/reçus, identité, chaque frontière d'export | runbook et qualification copie |
| B07 | restore réel SQLite privé, ancien request_id/ETag refusé | identity_changed/reconnexion clients |
| B08 | précédent drill 10k réussi par Claude, schema6 à rejouer | script S4 10k, ≤300 s, 0 réencodage |
| B09 | fenêtre read_only : lectures possibles, mutations non envoyées | TCP réel existant |

SIGKILL/ENOSPC simulé ne prouvent pas toutes les pannes électriques. Une archive
préserve les commits jusqu'à son cut ; pas de promesse zéro perte après destruction
du disque. Rapprochement des oublis depuis l'autorité disponible, quarantaine si
preuve manquante ; effacement physique/archives/tâches confidentielles relève de
L12. Attestation de déploiement, destination externe et RPO restent des portes.

## 4. L7b livré, opt-in uniquement

Scénarios Claude scellés **avant implémentation**, commit `83d12b5` :
`v4/tests/fixtures/work_l7b/{scenarios_v1.jsonl,manifest_v1.json}`,
SHA `af31604abcac8979f951ca650c2e3eaa200c830c18f15c9b861bb7e0904ea0ff`.
Les 20 scénarios restent intacts. Les simulations W06 ne sont pas comptées comme
TCP ; deux nouveaux tests effectuent les coupures réelles avant/après commit.

`MEMORY_V4_WORK_STATE=1` **et** backend `sqlite_s1` requis. Valeur par défaut false.
Schéma 6 attesté : `task_versions` append-only et projection `task_current`, FK,
checksums/intégrité, index de projet. Aucune table nouvelle dans la production JSONL.

- Outils create/get/list/update, block_compare_and_set, preview. Projet obligatoire,
  aucun wildcard, résolution L7a existante ; alias lus sans réécriture/fusion.
- UUID, statut open/blocked/done, owner déclaratif, next_action, preuve/résolution,
  dates UTC serveur, auteur/provenance et histoire. Limites 16 KiB/commande,
  32 preuves, pagination 1–100 ; curseur lié à portée/filtres/cut, refus si changé.
- Une transaction BEGIN IMMEDIATE, version/projection et reçu final avant succès.
  Conflit retourné et reçu conservé ; pas de modification du contenu. No-op vérifie
  CAS, garde la révision et conserve le reçu. ETag lié à identité, portée,
  **type d'objet**, objet et révision ; ancien store = identity_changed.
- blocked exige cause/action ; done exige résolution/preuve ; réouverture explicite
  vers open avec motif, done→blocked refusé. Owner jamais transféré implicitement.
- Relais remplit owner à la création et auteur des mutations depuis clientInfo
  downstream ; modèle ne remplace pas cette valeur. Appel direct reste déclaratif,
  non attesté. Le jeton partagé et clientInfo ne constituent aucune ACL individuelle.
- Stale strictement >14 jours, jamais auto-close. Doublons titre/Jaccard≥0,85 dans
  la portée : maximum5 avertissements non bloquants. Preview/import todo =
  propositions valides uniquement, aucun reçu/état créé ni import automatique.
- Lectures sans reçu et sans appel d’encodage sur le moteur ouvert. Brief : projection open/blocked, blocages d'abord,
  références `task:UUID`, cadrage DATA et budget entier L1a conservé. `task_get`
  détaille une référence. L1b et la matrice clients complète restent à qualifier.
- Ancien block_set existant en CAS mode refusé ; block_get expose les ETags.
  Backup/inventaire/recovery/export couvrent aussi les tables L7b ; drill privé
  vérifie jusqu'à trois tâches par API quand elles sont présentes.
- Ready/doctor annoncent les capacités réellement activées. Backend incompatible
  ou flag off = refus explicite, préflight avant envoi, aucun magasin alternatif.

### Preuves locales finales et limite restante

`.local-qualification/p1-s4-l7b-codex-final-v1/report.json` : `ok=true`,
585 v3 +1622 v4, zéro échec/erreur/skipped, `client_configs_unchanged=true`,
`neural_inference=false`, `tcp_qualified=false`, `release_qualified=false`.
Les deux commandes lint ci-dessous passent. Les 28 tests L7b comprennent les
20 scénarios scellés, 100 courses CAS et les interruptions aux frontières SQL.
La preuve W06 réelle exige les TCP ; les mesures 10k ne sont pas exécutées ici.

**Limite froide à traiter avant activation L7b :** les opérations SQL n'appellent
pas l'encodeur, mais le constructeur du serveur MemoryV4 complet charge encore
le modèle configuré. Le fonctionnement en cas de modèle indisponible au démarrage
n'est donc pas qualifié. Le test qui interdit `encode()` après ouverture ne le
prouve pas. Le contrat « sans modèle » reste une porte distincte ; ne pas
présenter les tâches comme un service autonome sans encodeur ni activer L7b avant
une preuve dédiée. Aucun changement tardif du profil d'encodage n'est inclus.

## 5. Critères et commandes Claude — nouveaux dossiers uniquement

Après import, **attendre la fin du tour Codex**. Exécutions séquentielles.
Runtime existant, aucun téléchargement ; gardes physiques TCP/mesures maintenues.

```sh
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4
export MEMORY_V4_CL100K_PATH=/Users/romain/.config/memory-os/cl100k_base.tiktoken
P1_PY=.local-qualification/brief-test-runtime-claude-3c-v1/bin/python

"$P1_PY" -B scripts/qualify_sqlite_s4_increment1.py --tcp \
 --output .local-qualification/p1-s4-l7b-core-claude-v1
"$P1_PY" -B scripts/qualify_405_test_isolation.py --suite full \
 --output .local-qualification/p1-s4-l7b-full-claude-v1
```

Minimums : 165 cas core avec 4 TCP ciblés ; suite globale ≥1648 v4 (26 TCP) et
585 v3, zéro échec/skipped, entrées clientes inchangées. Les contrôles app-owned
sont sémantiques, ceux du dépôt sont stricts. Lint de qualification :

```sh
ruff check --no-cache --config pyproject.toml src tests scripts
ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
```

Deux mesures Hash maximum, séquentielles, nice15, disque≥5 Go/marge S4 et
swap≤8,5 Go attestés. Remplacer git-dir si le Git d'import diffère :

```sh
P1_GIT=.local-qualification/lexical-dev-git
P1_SOURCE=$(git --git-dir="$P1_GIT" rev-parse refs/heads/feat/p1-s4-l7b-next)
nice -n 15 "$P1_PY" -B scripts/qualify_sqlite_s4_lifecycle.py \
 --items 10000 --git-dir "$P1_GIT" --source-commit "$P1_SOURCE" \
 --output .local-qualification/p1-s4-schema6-10k-claude-v1
nice -n 15 "$P1_PY" -B scripts/qualify_l7b_lifecycle.py \
 --git-dir "$P1_GIT" --source-commit "$P1_SOURCE" \
 --output .local-qualification/p1-l7b-10k-claude-v1
```

Critère L7b préexistant conservé : 10k tâches, 100 CAS et lectures top50,
p95 serveur CAS≤100 ms et top50≤100 ms ; brief≤6000 octets/1500 tokens, zéro vecteur
créé, budget total300 s. Le seed bulk est une fixture, pas un benchmark create.
Critère S4 : 9995/10000 items,5 oublis, ready200,0 réencodage,drill≤300 s.
Ces performances sont **des cibles**, pas des résultats locaux annoncés.

## 6. À relire / décisions restantes

Claude : revue adversaire de provenance déclarative/CAS et erreurs, schéma5→6,
rollback et sidecars, puis preuves TCP/mesures. Hooks : tester capabilities ; lire
tasks open/blocked, garder ETag/reason/evidence dans le brouillon, afficher conflit,
relire avant fusion explicite avec nouveau request_id. Statut L3 obligatoire si
incertain ; aucun replay. Q0 et registre inactif demeurent ; aucun hook auto-write.

Romain : GO distinct pour copie industrielle/migration/bascule SQLite, tâches,
jobs et publication. Copier/qualifier avant activation, préserver nouvelles
écritures, geler les writers et revalider le cut au changement de pointeur.
Destination hors disque et RPO à décider. Aucune intégration de modèle ouverte
avant distribution (PLAN §8), aucune campagne d'inférence dans cette livraison.
