# Collecte P-A des 40 inputs sans gold — 13 octobre 2026

Le runner `scripts/run_brief_v5_validation.py` ne lit que le JSONL sans
`expect`, son manifeste et, après accord, un amendement de compatibilité.
Ne jamais lui transmettre `cases_v1.jsonl`. Aucun évaluateur, score, seuil
de réussite ou réglage du candidat dans ce runner.

## Sources et état

- Baseline : fdd60cb0c9fee95f2f32471e93d77a2873a3d946.
- Candidat : **324aa68b905e491129549f8a289c64e04b61ec27**, pas le HEAD du runner.
- Le manifeste d'inputs mentionne 9bd4f73 : le runner vérifie par Git que tout
  `v4/src` est identique entre ce commit et 324aa68 ; refus si différence.
- Chaque bras exporte `v4/src` et `pyproject.toml`, déclare son SHA complet,
  puis vérifie le vrai `/ready` authentifié en ASGI avant la collecte.
- P-A 10k Claude sur 324aa68 : qualifié, zéro appel neuronal ; p95 candidat
  serveur 131,6 ms / transport 134,0 ms, baseline serveur 66,0 ms. Candidat
  environ deux fois plus lent, sous les gates ; aucun gain de vitesse annoncé.
  Ce résultat n'est pas la validation des 40 golds, qui reste à Claude.

## Compatibilité à résoudre AVANT exécution

Audit statique des inputs v1 : SHA
`72d6866a28f62c5dd778c214329817594939f89af66c06b3754ff186b2d52875`.
Le gold n'a pas été lu et les 40 inputs n'ont pas été exécutés par Codex.

1. **V5-VAL-009 à 014** portent `request.client_id`, absent de SessionBriefIn
   baseline (`extra=forbid`). Le vieux relais fdd60cb ne retire pas ce champ.
   Deux politiques possibles, aucune choisie implicitement :
   - `negotiate` : retirer uniquement ce champ non annoncé avant les appels
     serveur ET relais baseline ; le garder pour le candidat. Réquête originale
     et arguments réellement transmis sont enregistrés séparément.
   - `exact` : conserver les arguments originaux pour les deux bras et les
     deux chemins ; collecter les refus baseline comme résultats bruts.
   Une décision/amendement daté de Claude est nécessaire avant `--run`.
2. Baseline n'émet pas les offsets/variantes A2 ni les compteurs A7. Le runner
   conserve les références brutes et déclare `variant=null`, `offsets=null`,
   `coverage=null`, `unit_metadata_available=false`. Il ne reconstitue jamais
   des unités en parsant les marqueurs du contenu. Pour le candidat, seules
   les métadonnées réellement émises sont reportées ; `sentence` devient le
   libellé documentaire `first_sentence` dans le sidecar, sans modifier le wire.
   Accepter cette absence dans l'amendement (`baseline_metadata=unavailable`).

En cas d'autres ambiguïtés/champs inconnus, dates sans fuseau, tags hors liste,
ID de tâche aléatoire, ETag fournie, bloc susceptible d'être tronqué au seed,
le runner refuse : corriger/re-sceller les inputs avant toute nouvelle collecte.
Le contrôle de type des deux bras précède tout seed, même avec `--run`.

## Seed et preuves produites

Magasins SQLite jetables distincts pour chaque cas et chacune des deux
répétitions ; work_state actif si des tâches sont présentes, aucun registre,
labels littéraux. Champs MemoryItem conservés ; dates ISO converties en epoch
pour les champs datés. Les défauts de dates suivent le `now` figé, pas l'horloge
de la machine. Aucun enrichissement, déduplication ou extraction de faits.
Blocs créés dans le scope/projet explicitement fourni, sans troncature.

Tâches validées TaskCreateIn puis créées via le dispatch du vrai handler
`memory_task_create`. `dated_fixture` est retiré de l'argument API et fixe
l'horloge de création/modification du seed ; absent = `now`. Si un `update`
est fourni, le vrai handler reçoit l'ETag renvoyée par la création, jamais une
ETag inventée ; la même date de fixture s'applique. Staleness évaluée à `now`.
Reopen reconstruit les index hash. Chargement/encodeur neuronal interdits ;
l'encodage hash de seed est permis, tout encodage sur le chemin brief interdit.

Chemins `server` et `relay` pour chaque répétition : vrai CallToolResult via
`server.call_tool`, puis vrai DurableRelay/contrat négocié avec transport
logiciel en mémoire, comme L1a. `relay.unresolved/errors` fournit le status
du journal simulé, pas un vrai journal de mutations. `client_id` reste une
déclaration de fixture, jamais une identité authentifiée. Pas de test TCP.

Par bras : `audit.json`, `audit-worker.log`, puis `outputs.jsonl`, `worker.log`
et `worker.json`. 40 cas ×2 répétitions = 80 lignes/bras ; chaque ligne contient
les deux chemins, soit 320 enveloppes au total. Chaque chemin conserve wire
JSON compact UTF-8 intégral, objet JSON, octets, tokens cl100k, SHA-256, unités,
notices et couverture brutes. Offsets en points de code Unicode relatifs au
corps sans préfixe warning/notices. Rien n'est normalisé dans le hash du wire.
Les deux SHA et `identical` sont reportés par cas/chemin, sans score ni gate.
L'identité aléatoire du magasin privé dans `/ready` est hors hash du brief.

`complete=true` signifie seulement que la collecte est complète. Un résultat
brief d'erreur/saturation est conservé comme tel, pas éliminé du dénominateur.
Une erreur de seed, de garde ou de process arrête la collecte ; logs et sorties
partielles sont conservés, magasins synthétiques temporaires nettoyés.

## Claude — commandes exactes, sorties fraîches

Après import du bundle nommé release/4.0.7, depuis le clone principal propre,
aucune édition/qualification concurrente. Runtime et table persistants déjà
qualifiés. Concurrence 1, nice 15, garde disque opérateur (3 Go avec override,
5 Go par défaut), swap 8,5 Go ; worker borné à 900 s, arrêt du groupe privé.
Ni modèle, ni réseau, ni inférence LLM, ni configuration client réelle.

**Audit seulement, autorisé maintenant :**

```sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1
export MEMORY_V4_MIN_FREE_BYTES=3000000000
nice -n 15 "$PYTHON" -B scripts/run_brief_v5_validation.py \
  --inputs .local-qualification/v5-val-inputs/inputs_v1.jsonl \
  --manifest .local-qualification/v5-val-inputs/manifest_v1.json \
  --inputs-sha256 72d6866a28f62c5dd778c214329817594939f89af66c06b3754ff186b2d52875 \
  --table "$HOME/.config/memory-os/cl100k_base.tiktoken" \
  --output .local-qualification/407-pa-validation-audit-claude-v1
```

**Après résolution/amendement, pas avant :** Claude écrit et scelle
`.local-qualification/v5-val-inputs/compatibility_v1.json` avec exactement
`inputs_sha256`, `approved_by`, `approved_at` (ISO avec fuseau), `reason`,
`baseline_client_id` (`negotiate` OU `exact`), `baseline_metadata` (`unavailable`).
Le choix doit être approuvé explicitement, jamais obtenu en observant des sorties.
Le rapport conserve le contenu de cet amendement avec l'empreinte du manifeste.

```sh
nice -n 15 "$PYTHON" -B scripts/run_brief_v5_validation.py --run \
  --inputs .local-qualification/v5-val-inputs/inputs_v1.jsonl \
  --manifest .local-qualification/v5-val-inputs/manifest_v1.json \
  --inputs-sha256 72d6866a28f62c5dd778c214329817594939f89af66c06b3754ff186b2d52875 \
  --amendment .local-qualification/v5-val-inputs/compatibility_v1.json \
  --table "$HOME/.config/memory-os/cl100k_base.tiktoken" \
  --output .local-qualification/407-pa-validation-collect-claude-v1
```

Si les inputs sont corrigés/re-scellés plutôt qu'amendés, fournir les nouveaux
fichiers, SHA complet et un amendement lié à ce nouveau SHA ; ne pas réutiliser
une sortie ancienne. Claude score ensuite avec son gold hors dépôt. Codex ne
voit pas le gold et n'exécute pas la campagne de validation.

## Preuves locales

20 tests verts sur fixtures **indépendantes de développement**, dont un cas
synthétique sur chacun des deux vrais exports figés, deux magasins frais,
deux chemins et les deux politiques client_id. Dates/stale, champs de seed,
restauration des hooks de test, absence de magasin en audit/refus sans amendement,
gold/SHA/champs ambigus et limites des métadonnées baseline vérifiés.
Avec les 16 tests du lanceur de performance : **36 passed en 5,47 s**.
Ruff ciblé et diff --check verts. Aucune exécution des 40 inputs, aucun score.
