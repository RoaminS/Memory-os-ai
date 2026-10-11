# Candidate4.0.5 — qualification sur copie, aucune activation

2026-10-11. Production4.0.4 conservée. SQLite/L7b/registre restent opt-in et
inactifs. Auto/MPS/native/batch32, C4(scoped_literal,guard3), jeton et pointeur
production inchangés. Codex prépare/build/teste sans inference ni réseau ; Claude
exécute les TCP, modèles déjà présents et copies après la fin du tour Codex.
Publication/bascule/SQLite/tâches = GOs distincts de Romain.

## 1. Contrats et seuils figés avant qualification

- T1 confirmé par les deux W06 TCP qualifiés du candidat précédent : owner rempli
  depuis clientInfo downstream, modèle ne l'écrase pas ; identité déclarative,
  non authentifiée. Nouveau TCP à froid confirme également le chemin relayé.
- L7b liste SQL : index de portée/status/owner/date, limites appliquées avant JSON,
  fusion bornée ; non-stale blocked → non-stale open → stale, date desc/UUID tie.
  Cut projet monotone + identité/filtres/alias et instant de stale figé au curseur.
  Toute écriture tâche dans la portée invalide ; expiration24h, aucune pagination
  silencieuse sur une nouvelle snapshot. Projection transactionnelle reconstruisible.
- 10k : CASp95≤100ms conservé, **list50p95<30ms** (avant nouveau résultat),
  brief≤6000octets/1500tokens, 0vecteurs, ≤300s. Hash/synthétique, pas seuil neural.
- Démarrage cold sans modèle : SQLite+L7b uniquement ; aucun appel constructeur
  d'encodeur. CRUD/liste/brief/CAS/réouverture/rollback restent opérationnels,
  profils/cache existants préservés. ready_work_only, retrieval_ready=false,
  encoding.loaded=false, profile_source=historical_store_metadata. Recherche et
  ingestion déclenchent l'attestation réelle ; indisponibilité = erreur explicite,
  tâches restent accessibles. Pas d'annonce de recherche neuronale prête à froid.
- /ready expose capabilities.policies.write_mode et les anciennes policies.writes.
- Schéma7 : autorité versions6 conservée, projection7 ajoutée par migration
  connue transactionnelle sur copie ; backups5/6/7 attestés avant upgrade privée.
  Aucun magasin de production SQLite ouvert/migré par ces commandes.

## 2. Suite complète, exact tokens et mesures synthétiques

Importer la ref nommée, préserver les configs privées, attendre fin du tour Codex.
Tous les dossiers ci-dessous doivent être neufs ; arrêt au premier échec, aucun
restart automatique ni effacement d'une preuve. Scripts testés hors réseau.

```sh
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src:$PWD/v4/src:$PWD/v4"
export MEMORY_V4_CL100K_PATH=/Users/romain/.config/memory-os/cl100k_base.tiktoken
unset MEMORY_V4_PROJECT_REGISTRY
P405_PY="$PWD/.local-qualification/brief-test-runtime-claude-3c-v1/bin/python"
P405_RELEASE=.local-qualification/release-4.0.5-v1
"$P405_PY" -B scripts/qualify_405_test_isolation.py --suite full \
 --output .local-qualification/405-full-claude-v1
"$P405_PY" -B scripts/qualify_404_increment3c.py --suite full \
 --output .local-qualification/405-brief-source-claude-v1
"$P405_PY" -B scripts/qualify_brief_l1a.py \
 --output .local-qualification/405-brief-exact-claude-v1
```

Attendu :587v3,1658v4 (27TCP), zéro échec/skipped, configs MCP inchangées ;
400mesures exactes + enveloppe MCP. Nouveau TCP cold inclus ; W06 avant/après
commit et owner clientInfo inclus. Unités locales ne sont jamais une preuve TCP.

Deux mesures Hash maximales, séquentielles, gardes swap≤8,5GB/disque≥5GB+marge :

```sh
P405_GIT=.local-qualification/lexical-dev-git
P405_SOURCE=$(git --git-dir="$P405_GIT" rev-parse refs/heads/feat/405-release-candidate)
nice -n 15 "$P405_PY" -B scripts/qualify_sqlite_s4_lifecycle.py --items 10000 \
 --git-dir "$P405_GIT" --source-commit "$P405_SOURCE" \
 --output .local-qualification/405-s4-schema7-10k-claude-v1
nice -n 15 "$P405_PY" -B scripts/qualify_l7b_lifecycle.py \
 --git-dir "$P405_GIT" --source-commit "$P405_SOURCE" \
 --output .local-qualification/405-l7b-sql-10k-claude-v1
```

Adapter git-dir au Git d'import si nécessaire, sans changer le source_commit.
Les seuils10k sont déjà figés dans le lanceur. Revue des sources préalable aux
mesures et de l'activation client ; aucun hook auto-write. /ready work_state reste
le garde du hook SessionStart (limit8, statutsblocked/open).

## 3. Artefacts exacts — copie JSONL et relais installés

Le manifeste/artefacts/environnements locaux sont construits une seule fois.
Ne pas reconstruire pour poursuivre la qualification ; source_commit peut être
un ancêtre du reçu final, mais chaque empreinte source/script doit correspondre.
Séquence suivante après succès des preuves exactes :

```sh
"$P405_PY" -B scripts/verify_release_manifest.py "$P405_RELEASE/manifest.json" \
 --record-brief-source .local-qualification/405-brief-source-claude-v1/report.json \
 --record-brief-exact .local-qualification/405-brief-exact-claude-v1/report.json
"$P405_PY" -B scripts/source_release_checks.py \
 --manifest "$P405_RELEASE/manifest.json" --output "$P405_RELEASE/source-proof.json"
"$P405_PY" -B scripts/verify_release_manifest.py "$P405_RELEASE/manifest.json" \
 --record-source-proof "$P405_RELEASE/source-proof.json"
"$P405_PY" -B scripts/qualify_release_copy.py \
 --manifest "$P405_RELEASE/manifest.json" --environments "$P405_RELEASE/environments.json" \
 --baseline-python /Users/romain/.memory-os-runtime/venv-4.0.4/bin/python \
 --output-root .local-qualification/copy-4.0.5-v1
"$P405_PY" -B scripts/verify_release_manifest.py "$P405_RELEASE/manifest.json" \
 --record-copy-proof .local-qualification/copy-4.0.5-v1/report.json
"$P405_PY" -B scripts/installed_relay_e2e.py \
 --manifest "$P405_RELEASE/manifest.json" --environments "$P405_RELEASE/environments.json" \
 --output "$P405_RELEASE/installed-relay-proof.json"
"$P405_PY" -B scripts/verify_release_manifest.py "$P405_RELEASE/manifest.json" \
 --record-relay-proof "$P405_RELEASE/installed-relay-proof.json"
"$P405_PY" -B scripts/verify_release_manifest.py "$P405_RELEASE/manifest.json"
```

Source :27TCP+1cachedMiniLM+suitev3cached complète (compteur exact manifeste),
zero skip, aucune config client modifiée. Modèles présents uniquement, nice15,
concurrence1. Copier la production **en lecture seule**, empreintes avant/après,
écriture concurrente→refus, aucun retry automatique. Baseline installée4.0.4,
wheel/sdist/reopen sur copies indépendantes ; profils/items/histoires/provenances/
reçus/cut et UUID existants conservés. Recherche identique entre distributions et
reopen, zéro réencodage au reopen ; doctor privé0FAIL avec backendJSONL, tâches/
CAS/registrefalse et write_mode annoncé. En cas d'écart baseline, revue spécifique
Claude liée au SHA du rapport comme QUALIFY_4_0_4.md, aucune équivalence automatique.

La preuve SQLite/L7b sur magasin industriel est **séparée** de cette preuve JSONL.
Elle suit RUNBOOK_SQLITE_ACTIVATION_v1.md et window_v1.sh de Claude, après revue des
nouveaux champs et GOs ; leur simple présence n'autorise aucune activation.

## 4. Promotion ultérieure et retour préservant les écritures

release_ready=false jusqu'aux preuves exactes/source/copie/relais ; true signifie
qualification, jamais autorisation de publier ou basculer. Après GO distinct :
arrêter tous les writers, prendre backup vérifié au dernier cut, revalider profils/
identité/items/receipts, installer l'artefact qualifié sans rebuild, conserver
4.0.4 et pointeur, basculer et vérifier ready/doctor/round-trip via relais.
SQLite/L7b/registre restent inactifs ; profil d'encodage/C4 inchangé.

Retour4.0.4 JSONL : arrêter4.0.5, sauvegarder **l'autorité courante comprenant les
nouvelles écritures**, qualifier une copie avec le runtime4.0.4, revenir au pointeur
runtime sans remplacer les fichiers live par l'ancien snapshot. Si la copie ne
passe pas, rester arrêté et réconcilier. Retour SQLite/tâches n'est pas ce parcours :
exportrollback conserve les sidecars inactifs et exige son GO/runbook propre.
Aucune promotion ou retour n'est exécuté par Codex. Aucune publication autorisée.
