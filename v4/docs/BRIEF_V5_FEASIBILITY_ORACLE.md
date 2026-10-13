# Oracle de faisabilité P-A — contrat et commandes

Outil : `scripts/brief_v5_feasibility_oracle.py`. Serializer, variantes
extractives et compteurs figés à **324aa68**, table cl100k offline qualifiée.
Pas de moteur, de magasin, de modèle, de réseau ou de candidat exécuté.
`brief.build/rebudget`, `brief_selection.select/rebudget_units` sont interdits
dans le worker. Seuls l'assemblage du wire, les notices comptables, les variantes
documentées et le calcul de population sont réutilisés.

## Spécification explicite, indépendante de la sélection

Claude prépare un JSONL **oracle-only** dans un dossier privé, jamais le gold
`cases_v1.jsonl`. Chaque ligne décrit le scope/pool déjà vérifié dans la fixture,
les compteurs éligibles, les protections effectives et les avis de base.
L'oracle ne les devine pas depuis le texte ou les sorties du candidat.

```json
{
  "id": "DEV-ORACLE",
  "project": "P",
  "budget_tokens": 200,
  "eligible": {"fact": 2},
  "pool": [
    {"id": "required", "kind": "fact", "text": "First complete sentence. Second complete sentence.", "protected": true},
    {"id": "other", "kind": "fact", "text": "Other complete sentence.", "protected": false}
  ],
  "notices": [],
  "relay": {"unresolved": [], "errors": []},
  "requirements": [
    {"id": "required", "allowed": ["full", "first_sentence"], "required_text": "First complete sentence."}
  ]
}
```

`protected` : valeur de la fixture après validation de scope/validité/sensibilité
et des tags constraint+critical ; tâche open/blocked non stale. Ce n'est pas
la qualification « critique » du gold si l'item n'a pas les tags correspondants.
IDs bloc/task avec leurs préfixes exacts ; texte task pré-rendu selon task_text
avec les valeurs de la fixture. Ordre du pool explicite, non réordonné par le
ranking. Populations par kind couvrant le pool, limites 40 mémoire/16 blocs/
10 tâches. Une unité hors scope, invalide ou sensible ne peut être incluse.
Les avis liés aux populations hors pool doivent être explicités dans notices,
dont critical_omitted si pertinent ; l'oracle ne connaît pas leurs textes.

Les formes énumérées sont les représentations documentées du format, pas
les branches que le sélecteur actuel empruntera. Un témoin faisable uniquement
avec totaux positionnels doit être identifié comme tel ; il ne prouve pas
qu'une frame à métadonnées complètes tient. L'oracle ne remplace donc pas un
choix explicite de représentation obligatoire dans le protocole/gold v2.

1–6 exigences, chacune dans le pool, allowed explicite parmi full,
first_sentence, reference. Une référence n'est jamais admissible pour une
exigence required_text non vide. Pas de phrase tronquée arbitrairement :
first_sentence reprend l'extraction conservative du format réel. Si le texte
est une unique phrase complète, utiliser full ; il n'a pas de variante sentence
plus courte. Aucune variante sentence pour une tâche ou du code clôturé.

Pour 001/002/038, reporter les champs des inputs sans expect, avec populations
et unités requises **déterminées avant toute sélection**. Pour plusieurs
exigences simultanées, les fournir ensemble : ne pas additionner des résultats
individuels en oubliant le coût partagé ou les avis d'autres omissions.

## Ce qui est mesuré

Exhaustif jusqu'à 729 combinaisons de variantes requises, sans unité facultative.
Pour chaque combinaison : formes coverage complètes par kind, totaux nommés,
totaux positionnels. Les formes réduites conservent coverage_detail_omitted ;
deux ordres documentés d'avis reflètent une réduction préalable du détail ou
une réduction lors du rebudget final (avec budget_saturated pour cette dernière).
Les compteurs sont recalculés sur les unités effectivement assemblées et les
omissions/protections ; aucun compteur réduit ou avis omis pour gagner des tokens.

Trois chemins clairement séparés :

- server : avis de base + omissions/variantes effectives ;
- relay : les mêmes, plus unresolved_requests si unresolved/errors non vides ;
- prospective : ajoute toujours unresolved_requests, réservation P-A avant relais.

L'empty frame (0 unité) est mesuré dans tous ces formats. Il n'est pas soustrait
du texte estimé : chaque envelope complet est **resérialisé et recompté**.
Budget conjoint exact : UTF-8 ≤6000 octets et tokens ≤budget demandé.

Sortie `oracle.jsonl` : frames mesurées (tokens, bytes, marges, SHA, avis,
couverture, variantes), et witness intégral le moins coûteux parmi les frames
qui tiennent pour chaque chemin ; à défaut, witness minimal non faisable.
Les witnesses incluent le CallToolResult réel compact_v2, une seule copie JSON,
avec offsets/références/protected. Pas de normalisation ni compteur approximatif.
Les frames non witnesses gardent leurs mesures sans dupliquer le texte complet.

`individual` et `joint` sont distincts. Les booléens de faisabilité signifient
**existence d'une frame avec uniquement les exigences indiquées**, conditionnée
au scope/pool/protections/avis fournis. Ce n'est pas une garantie d'allocation
par le sélecteur, ni la preuve que toutes les autres contraintes sont couvertes.
Une frame individuelle peut omettre une autre unité protégée ; son avis reste
présent. Le contexte et ses limites sont conservés via SHA et spécification scellée.

## Claude — lancement hors sandbox, sans bascule ni score rétroactif

Après import du bundle nommé release/4.0.7 et arbre propre. Préparer et sceller
oracle-specs-v1.jsonl ; garder ce fichier avec son SHA, sans transmettre le gold
complet. Copier son SHA complet dans ORACLE_SHA ci-dessous, jamais une valeur
supposée. L'outil vérifie ce seal avant lancement. Exemple de chemin privé :
`.local-qualification/v5-oracle-specs/oracle-specs-v1.jsonl`.

```sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1 MEMORY_V4_MIN_FREE_BYTES=3000000000
ORACLE_SHA="<SHA-256 complet du fichier oracle-specs-v1.jsonl scellé>"
nice -n 15 "$PYTHON" -B scripts/brief_v5_feasibility_oracle.py \
  --specs .local-qualification/v5-oracle-specs/oracle-specs-v1.jsonl \
  --specs-sha256 "$ORACLE_SHA" \
  --table "$HOME/.config/memory-os/cl100k_base.tiktoken" \
  --output .local-qualification/407-pa-feasibility-oracle-claude-v1
```

Garde disque opérateur 3 Go (5 Go par défaut), swap 8,5 Go, un worker séquentiel,
timeout 900 s. Aucune garde contournée. Sortie fraîche, refus et sorties partielles
conservés. Le code ne réécrit ni collecte v1 ni score officiel. Les résultats
d'oracle éclairent le protocole/gold v2 avant la nouvelle validation scellée.

Preuves locales : **53 tests verts**, dont 17 nouveaux tests de l'oracle,
compteur cl100k réel, refus des ambiguïtés, scopes/protections explicites,
réserve/relais séparés, variantes extractives et faisabilité conjointe.
Worker testé sur les sources figées 324aa68 avec constructeur moteur interdit.
Ruff ciblé et diff --check verts. Aucun run des cas officiels ni gold lu.
