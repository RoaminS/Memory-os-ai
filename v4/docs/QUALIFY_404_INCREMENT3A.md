# Qualification 4.0.4 — incrément 3a (L4 + L7a)

2026-10-11. Base **3450bce**, code final **228d726**. Branche nommée
`feat/404-increment-3a`. Bundle `404-increment3a.bundle`.
Aucune activation, publication, campagne d'inférence ou écriture directe en production.
Le numéro empaqueté reste celui de la candidate existante ; aucune 4.0.4 déclarée qualifiée.

## Livré / preuves locales

- L4 : annulation d'attente propagée au propriétaire, abandon avant envoi,
  budget des contrôles, diagnostics expurgés, identité de handshake bloquante.
- L7a : registre validé opt-in ; écritures MCP canoniques, alias lus ensemble
  sans réécriture, filtres partagés et ambiguïtés de blocs refusées.
- Contrat des hooks : `PROJECT_IDENTITY_L7a_v1.md`, schéma de registre associé.
- Préparation L1a : `BRIEF_L1a_SCENARIOS_v1.md` (proposition de 100 cas, pas un gel fictif).
- **1273 tests légers passent**, zéro erreur/échec/skip, 1 test slow explicitement
  désélectionné ; lint projet + v4 verts ; baseline hash générée, aucune mesure
  neuronale. Rapport local : `.local-qualification/404-increment3a-unit-final-v2/report.json`.
  Détails/digests : `404_INCREMENT3A_EVIDENCE.json`.
- Le premier essai complet (`...unit-v1`) conserve ses deux échecs de fixtures :
  champ absent du Config historique et API history spécifique SQLite.
  Corrections explicites ; `...unit-final` (1272) puis `...unit-final-v2` (1273)
  conservés. Le second passage ajoute la non-régression de refus project_unknown.

## À exécuter par Claude après import

Depuis la racine du checkout contenant la branche importée (base 3450bce).
Avec le Git privé du clone de Codex, import possible sans écrire dans `.git` :

```sh
git --git-dir=.local-qualification/lexical-dev-git bundle verify 404-increment3a.bundle
git --git-dir=.local-qualification/lexical-dev-git fetch ./404-increment3a.bundle refs/heads/feat/404-increment-3a
git --git-dir=.local-qualification/lexical-dev-git merge --ff-only FETCH_HEAD
PATH=/opt/homebrew/bin:$PATH PYTHONDONTWRITEBYTECODE=1 \
  /Users/romain/Memory-os-ai/.venv/bin/python scripts/qualify_404_increment3a.py \
  --suite full --output .local-qualification/404-increment3a-claude-v1
```

Sortie fraîche obligatoire ; ne pas effacer ni rejouer dans un dossier échoué.
Le runner séquentiel exige **≥1291 tests dont ≥18 TCP**, aucun skip/échec et les
scénarios nommés obligatoires. Rapports `ok=true`, `tcp_qualified=true` attendus.
Tout magasin/jeton/journal créé est jetable sous le dossier de preuve. L'interpréteur
externe est lu seulement ; offline/hash, garde disque 5 GB et swap 8,5 GiB.
La sonde swap est inaccessible dans le sandbox, à contrôler hors sandbox.

Les 18 TCP comprennent les 10 tests serveur HTTP, 2 tests ops et les 6 scénarios
`test_l3_tcp.py` :

1. C07, 30 cycles arrêt/rotation/redémarrage ; chaque contrôle ≤5 s après ready,
   délais individuels dans `pytest-tmp/**/cycles.json`, absence/perms invalides refusées.
2. C02/C04/C08, lot partiel pending, statut local pendant panne, adoption sans rejeu.
3. C02/C04/C08, lot committed, reçu completed après coupure, pas de double effet.
4. L4 identité serveur changée : blocage persistant, statut local/catégorie disponibles.
5. L7a write alias → read canonical, isolation d'un autre projet, projet inconnu
   refusé avec code explicite, reçu scope canonique.
6. L4 jeton syntaxiquement valide mais refusé (401), diagnostic explicite, restauration
   du bon jeton et contrôle réussi sans redémarrage du client.

Pas de preuve TCP revendiquée par Codex ; attendre le rapport de Claude.

## Travail parallèle attendu de Claude / suite 3b

- Préparer (sans activer) un registre d'IDs/alias/contextes à valider avec Romain,
  puis adapter SessionStart/Stop au contrat L7a. ID explicite pour chaque appel ;
  inconnu = notice seule et zéro capture automatique. Ne pas envoyer `context_id`
  comme argument MCP inconnu. Préserver les request_id/arguments des lots incertains.
- Remplir les 100 cas FR/EN selon le format donné ; envoyer pour revue avant gel
  de `v4/tests/fixtures/brief_l1a/scenarios_v1.jsonl` et son manifeste SHA-256.
- L5 puis L1a restent **3b**, non livrés par 3a : capacités réellement déployées,
  hash du registre dans ready/doctor, puis enveloppe compacte/tokenizer offline,
  références expansibles, validité homogène et mode détaillé explicite.
- L11 reste en attente budget/jeu scellé ; L14 toujours en pause.
- Aucune permission supplémentaire nécessaire pour ces tests jetables. Activation
  du registre/candidate, qualification sur copie de production puis bascule et
  publication restent soumises aux GOs distincts de Romain.
