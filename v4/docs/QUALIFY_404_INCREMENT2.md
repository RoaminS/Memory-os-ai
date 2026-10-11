# Qualification incrément 2 — candidate 4.0.4, hors production

Base : `7173dc7`. Ref livrée : `refs/heads/feat/404-increment-2`.
Aucun artefact PyPI ni version publiée dans cet incrément ; le package conserve
sa version actuelle jusqu'à la qualification de release coordonnée.

## 1. Import et lecture adversaire

Depuis le clone où Claude a importé l'incrément1, sans changement local suivi à
écraser, importer la ref nommée puis fast-forward (aucun push) :

```sh
git fetch /Users/romain/Documents/Codex/2026-10-09/v/work/implementation/404-increment2.bundle refs/heads/feat/404-increment-2
git merge --ff-only FETCH_HEAD
```

Si Claude utilise comme précédemment le Git privé **du clone Codex**, utiliser
`git --git-dir=.local-qualification/lexical-dev-git` à la place de `git`.
Ne jamais faire de reset forcé ni écraser `.mcp.json`/`.vscode/mcp.json` : leurs
changements locaux préexistants sont exclus du bundle. Vérifier le SHA du bundle
avec son fichier `.sha256` adjacent et `git bundle verify` (base7173dc7 requise).

Relecture prioritaire :
- `CONTRACTS_L3_L4_v2.md` et schémas v2 : A1–A5, notamment précision A4 ;
- `ops/durable_relay.py`, `ops/request_journal.py` : adoption, poison fsync,
  absence ≠ non-exécution, statut lié au scope/hash/magasin, rotation séparée ;
- `server._h_request_status`, reçus JSONL/SQLite, identité de lignée ;
- `REVIEW_L11_v1_CODEX.md` : ne pas geler/exécuter L11 avant amendements.

## 2. Commandes exactes dans le clone Codex, hors sandbox

Commandes **séquentielles**, magasins hash synthétiques uniquement, aucun modèle,
aucune API, aucune connexion à la production. Les nouveaux tests TCP lancent leurs
propres serveurs sur ports éphémères, avec token, magasin et journaux jetables.
Le runner refuse un output existant, les tests absents, échoués ou ignorés.
Conserver tout dossier échoué ; choisir un nouveau suffixe pour une nouvelle
qualification après correction, sans remplacer les preuves.

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
PATH=/opt/homebrew/bin:$PATH PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python scripts/qualify_404_increment2.py --suite full --output .local-qualification/404-increment2-claude-v1
PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python scripts/measure_relay_journal_404.py --output .local-qualification/404-increment2-fsync-claude-v1 --samples 100
```

Le runner vérifie disque≥5GB, swap≤8,5GiB lorsque sysctl est accessible (il l'est
hors sandbox). Dans le sandbox, l'indisponibilité de sysctl est enregistrée ;
seuls tests légers hash locaux y sont exécutés. Aucun test neural ou entraînement.
Le venv du dépôt historique est **utilisé en lecture seule** comme interpréteur ;
les imports du candidat sont imposés par PYTHONPATH. Pas d'installation dans ce
venv, ni de modification de la production, de ses fichiers ou de ses jobs.

## 3. TCP attendus : 15 cas, dont trois nouveaux

- Dix `test_server_http_e2e.py` : partage, idempotence, auth/Host/Origin,
  maintenance et ready. Le statut emprunte le même middleware authentifié.
- Deux `test_ops_e2e.py` : vrai relais, mutation/recherche, reconnexion.
- `test_l3_tcp.py::test_C07_thirty_restart_rotation_cycles` :30 redémarrages et
  rotations0600, contrôle≤5s après ready, puis fichier0644/absent refusé ; timings
  individuels `cycles.json` dans le répertoire temporaire du test.
- `test_l3_tcp.py::test_C02_C04_C08_disconnect_and_new_relay_adopts_journal[partial-pending]` :
  arrêt du processus après écriture partielle JSONL ; nouveau relais retrouve le
  pending, jamais de double écriture.
- Même test `[committed-completed]` : arrêt après commit avant réponse ; statut
  retrouve le résultat, aucune réexécution. Le journal client original est conservé.

Ce ne sont pas les tests des dix applications hôtes : L4 complet, L11 et la
qualification sur copie de production restent des étapes distinctes. Un résultat
vert ici n'autorise aucune publication ni bascule.

## 4. Travail parallèle Claude

1. Contresigner A4 : rotation automatique du journal actif ; entretien **distinct**
   des journaux clos, simulation par défaut, commande dédiée dans OPERATIONS.md.
   Préparer le job local futur sans l'activer ; l'adoption demeure read-only.
2. Préparer l'alignement des instructions (activer seulement avec serveur/relais
   compatibles) : après incertitude, statut sans ID, puis ID ; absent≠non-exécuté ;
   aucune réémission. Ajouter MEMORY_V4_CLIENT_ID dans les futurs snippets épinglés.
3. Garder une seule source de relais : celle du package. Revoir les wrappers
   locaux pour qu'ils ne réintroduisent ni replay, ni canonicalisation cliente.
4. Appliquer R1–R8 à L11 ; confirmer volumes/budget/notations/plafonds avant gel.
   Ne pas lancer L11, L14, ni appeler de modèle au titre de cette qualification.
5. Conserver Q0 v2 ; L1a devra préserver le nouveau signal d'incertitude dans `text`.

Accord Romain encore nécessaire : publication/bascule4.0.4 et changements locaux
associés ; budget et exécution des campagnes en pause selon leurs autorisations
spécifiques. Aucun nouveau GO n'est requis pour ces tests synthétiques autorisés.

## 5. Preuves locales Codex

`404_INCREMENT2_EVIDENCE.json` fige les empreintes des rapports/logs et du manifeste
source. Suite légère finale : **1233 tests passés, zéro échec/skip**, un test slow
neural exclu intentionnellement ; lint projet/v4 vert. 15 tests TCP collectés,
**non exécutés** dans le sandbox. Tests ciblés :283 passés avant derniers contrôles,
puis52 passés sur journal/doctor ; la suite finale couvre tous les changements.

100 append prepared+sending :p50≈0,087ms, p95≈0,110ms, max≈0,435ms. Mesure locale
synthétique fsync, pas un engagement durable-ack L9 ni une preuve de coupure secteur.
Swap non observable dans le sandbox ; disque disponible observé≈7,3GB avant suite.

Les essais préliminaires échoués restent sous `.local-qualification` :
`inc2-focus-a` (selfcheck à compléter pour le nouvel outil), `inc2-focused-b`
(attente de snippet Grok avant ajout du client ID), `404-increment2-unit-v1`
(isolation des journaux de tests : surcharge globale désormais supprimée).
La qualification finale ci-dessus succède aux corrections ; aucun résultat
expérimental ou campagne neural n'a été exécuté ni relancé.
