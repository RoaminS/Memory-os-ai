# P-A v1 — préenregistrement A2/A4/A3/A7, 12 octobre 2026

Scellé **avant toute implémentation de ces quatre lots**, baseline fdd60cb.
Paramètres dans `v4/tests/fixtures/brief_v5/protocol_v1.json` et SHA adjacent.
Claude garde le gold des 40 validations ; Codex n'y règle aucun seuil.
Aucun changement de paramètres après résultat sans amendement daté et nouveau
statut exploratoire. Incrément 2 = A2+A4 ; incrément 3 = A3+A7.

## Unités et sélection figées

Une unité = un item, un bloc ou une tâche ; aucune duplication via focus,
parents ou expansion. Ordre déterministe. Représentations, essayées dans cet
ordre : texte complet, première phrase complète extractive + référence,
référence seule. Tâches : texte complet ou référence seule, jamais couper
title/next_action/blocked_reason ni inventer un résumé. Les références restent
développables dans le même scope, sans révéler du contenu interdit.

La première phrase se termine à une ponctuation forte suivie d'une frontière
de mots ; abréviations usuelles, segments de code ouverts et ponctuation interne
aux tokens ne sont pas des frontières. Aucun découpage à longueur arbitraire,
aucune ellipse qui prétend compléter un mot/SHA/URL/code. Si aucune phrase sûre
ne tient, référence seule. Offsets de chaque unité relatifs au corps hors
préfixe d'avis, jamais parsing par `[id]` : une mémoire peut elle-même contenir
de tels marqueurs. Le relais retire des unités complètes et actualise les
références, pas des caractères. Ancien envelope sans offsets : texte opaque
complet ou retrait du corps avec avis, pas pseudo-reconstruction.

Pools bornés : 40 items mémoire, 16 blocs, 10 tâches ; expansions exactes avant
les autres candidats. Les éligibles hors pool sont comptés séparément. Tous
les comptages sont uniques et dans le scope demandé, filtres de validité et
sensibilité appliqués avant ranking. Le profil neural et les écritures sont
hors du chemin du brief. Un client absent/inconnu ne prouve pas « autre client ».

A4 : budgets initiaux de blocs ≤30 % et tâches ≤20 % du contenu disponible
après overhead réel, simultanément en bytes et tokens. Ce sont des plafonds
initiaux ; le reliquat est remis à disposition, jamais laissé vide par principe.
Premier passage par section puis passage partagé selon rang. Les alertes
indéterminées et contraintes critiques faisables ne sont pas des victimes
silencieuses des plafonds ; omission critique ou référence seule signalée.
La faisabilité se mesure avec overhead/avis exacts, pas avec les pourcentages.

A3 : expansions demandées puis contraintes explicitement critiques
(`tags=[constraint,critical]`), tâches blocked/open non périmées, décisions et
contraintes, procédures, changements d'un autre client attesté déclarativement,
faits/résumés/épisodes/documents, tâches périmées. Le rang explicite et les
contraintes critiques sont des exceptions réservées à l'ordre nominal
tâches > décisions/contraintes > procédures > autres changements > faits.
Focus lexical, importance, date puis ID départagent ; la date ne définit jamais
la validité. Provenance utilisable uniquement `source=client:<id>` ou unique
tag `client:<id>`, comparée au client_id déclaré/relay ; aucune inference depuis
le texte, un chemin, un rôle ou « Claude a dit ». Aucune authentification promise.

A7 : par type, eligible/pool/content/reference_only/omitted ; omitted concerne
le pool, outside_pool = eligible-pool. Body présent en entier ou première
phrase = content ; référence seule n'est pas couverture sémantique. Totaux
exacts et notice de saturation. Si le détail par type ne tient pas au budget
minimal, totaux seuls + `coverage_detail_omitted`, pas une valeur inventée.
Les statistiques finales sont recalculées après rebudget du relais.

Précisions de représentation, sans changement des paramètres scellés :
`coverage.totals` est normalement un dictionnaire nommé ; au budget minimal,
il peut devenir le tableau `[eligible,pool,content,reference_only,omitted,outside_pool]`,
ordre fixé par `scoped_candidates_v1`, accompagné de coverage_detail_omitted.
Les erreurs de saturation conservent ces totaux, contenu=références=0.
Blocs de contexte : ordre stable au niveau décisions/contraintes, après ces
items à score/importance positifs ; une expansion explicite reste prioritaire.
Les tâches exigent un projet explicite : l'absence de tâche dans le scope `*`
ne mesure pas la population globale de tâches. Aucune tâche fermée ajoutée
implicitement ; seule son expansion explicite la rend éligible.

## Mesures et gates gelés

- CallToolResult sérialisé UTF-8 excluant None, final après avis locaux :
  ≤6 000 bytes et ≤budget demandé (200, 600, 1 500 tokens cl100k offline).
  La sélection simule l'avis local unresolved_requests pour garantir sa place,
  plutôt que réserver un nombre arbitraire de caractères/tokens.
- Anciennes 100 fixtures = régression, pas preuve de progrès. Gold 40 nouveaux
  cas, 20 FR/20 EN, cinq familles : unités/extraits ; partage ; priorités ;
  portée/validité/confidentialité ; saturation/erreurs/relais. ≥38/40 couvrent
  toutes exigences critiques faisables, 0 contenu interdit, 0 fragment coupé,
  100 % références exactes. Tous cas, échecs et infeasibilités conservés.
- Faisabilité déterminée **avant candidat** : assembler les unités/variantes
  obligatoires admises par le gold avec overhead + avis + compteurs réels et
  mesurer les deux budgets. Ref-only ne remplit pas un besoin de texte.
  Les exclusions ne permettent pas de réduire le dénominateur 40 ; un besoin
  impossible doit être signalé par une omission critique explicite.
- Performance synthétique, pas qualité neuronale : 10 000 items, 10 warmups,
  100 appels séquentiels, nice 15, concurrence 1. p95 serveur ≤250 ms,
  transport ≤750 ms ; bytes/tokens/p50/p95/max, nombre d'encodages =0 et
  source/configuration/runtime/machine enregistrés. Jobs physiques chez Claude,
  gardes disque/swap conservées ; aucun job lourd en qualification de bascule.
- Baseline/candidat et ordre de requêtes gelés, aucun modèle ni corpus téléchargé.
  Les mesures locales structurelles/hash ne prouvent ni gain utilisateur ni
  top-1. A9/T05/copie et GOs restent nécessaires à publication/bascule.

## Format des 40 cas de Claude

JSONL, 1 objet par cas ; IDs stables `V5-VAL-001`…`040`, fixture now absolu.
Le format reprend L1a, étendu ainsi (champs expect tenus secrets par Claude) :

```json
{
  "id": "V5-VAL-001", "language": "fr", "family": "units",
  "now": "2026-10-12T10:00:00Z",
  "items": [{"id": "fact-1", "kind": "fact", "text": "Texte entier.",
             "project": "P", "tags": ["constraint", "critical"]}],
  "blocks": [], "tasks": [],
  "request": {"project": "P", "wire_format": "compact_v2", "budget_tokens": 600},
  "relay": {"unresolved": [], "errors": []},
  "expect": {
    "required_ids": ["fact-1"], "forbidden_ids": [],
    "required_block_names": [], "forbidden_block_names": [],
    "critical_requirements": [{"id": "fact-1", "allowed": ["full"],
                               "required_text": "Texte entier."}],
    "whole_unit_ids": ["fact-1"], "priority_before": [],
    "notice_codes": [], "forbidden_texts": [],
    "counts": {"fact": {"eligible": 1, "pool": 1}},
    "expand_ids": ["fact-1"], "deterministic": true
  }
}
```

Registry facultatif : omission = labels littéraux, jamais créer un registry
vide qui refuse P. Si fourni, utiliser le schéma réel des fixtures L1a.
Items = champs MemoryItem ; dates ISO converties ; sensibilité via tag sensitive.
Blocs = name/text/project/scope. Tâches = entrée TaskCreateIn (UUID, project,
title, next_action…), puis optional update pour blocked/done et dated_fixture
pour stale dans le seed privé ; ETags obtenus via API, jamais inventés.
Préciser dated_fixture comme date ISO absolue de création/modification du seed,
et obtenir une nouvelle ETag par l'API si update est fourni ; pas d'ETag écrite
par le gold. Les cas avec tâches utilisent sqlite_s1+work_state dans leur magasin
privé. L'exemple sans registry utilise les labels littéraux ; un registry actif
vide serait un autre scénario, de refus, et ne permettrait pas de lire P.
`client_id` optionnel est un identifiant déclaratif. References tasks = task:UUID,
blocs = block:name. required_ids peut exiger une référence seulement ; les
critical_requirements exigent le contenu/phrase exacts admis, séparément.
`counts` n'impose pas le résultat candidat observé ; il fixe les populations
et les attentes comptables connues. Une priorité ne se teste que pour les
unités faisables, et chaque omission doit rester justifiée/bornée.

Claude scelle cas + gold + manifest SHA/date/langues/familles hors dépôt ; le
gold reste hors réglage jusqu'au gel du candidat d'incrément 3. Le petit jeu
dev de Codex est indépendant et synthétique, manifeste daté avant moteur.
