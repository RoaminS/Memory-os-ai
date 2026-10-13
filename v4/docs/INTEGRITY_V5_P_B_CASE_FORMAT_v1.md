# Format P-B v1 — inputs sans gold pour Claude

Les 40 cas P-B diffèrent de P-A : **séquences de mutations et lectures**, pas
simple seed→brief. Le gold `expect` reste chez Claude ; aucun champ expect dans
les inputs transmis. IDs `PB-001`…`PB-040`, 20 FR/20 EN ; families identity,
correction, compensation, journal/crash. Validation complète gelée après B4/B5,
pas à ce premier incrément B2. La limite legacy >3 fait l'objet du contre-amendement
explicite ; sceller les attentes de ce point seulement après accord.

```json
{
  "id":"PB-001","language":"fr","family":"correction",
  "now":"2026-10-13T10:00:00Z","backend":"sqlite_s1",
  "seed":{"items":[{"id":"old","kind":"fact","text":"Service uses port 8000.","project":"P","event_time":"2026-10-12T10:00:00Z","valid_from":"2026-10-12T10:00:00Z"}],"blocks":[],"tasks":[]},
  "steps":[
    {"id":"read","tool":"memory_search","arguments":{"project":"P","query":"Service port","top_k":1}},
    {"id":"correct","tool":"memory_add_facts","arguments":{"project":"P","request_id":"pb-001-correct","reason":"Operator changed the port.","expected_revisions":{"old":{"$ref":"read.hits.0.revision"}},"facts":[{"item_id":"new","text":"Service uses port 9000.","event_time":"2026-10-13T10:00:00Z","replaces":["old"]}]}}
  ],
  "client":{"name":"claude-code","version":"synthetic"},
  "paths":["server","relay"]
}
```

## Règles du futur runner

- now absolu fixé pendant le seed et les steps. Chaque bras/path/repetition a
  son magasin privé : jamais le store production, pas de source native ni auth.
- backend jsonl/sqlite_s1 explicite ; embed_model=hash, no neural/API/TCP réel
  pour le runner logique. Le relais logiciel initialise clientInfo déclaratif.
- seed.items = données historiques MemoryItem, dates ISO converties ; **pas une
  mutation API réinterprétant leur historique**. Blocs/tasks selon fixtures P-A
  avec audit de schéma ; la compatibilité de seed est vérifiée avant toute mesure.
- steps.tool = outil existant, arguments validés par son modèle réel après
  résolution de références. Un step est exécuté une fois ; pas de retry auto.
- `$ref` est seul dans son objet, cible un step PRÉCÉDENT et un chemin de données
  (clés/décimales d'index seulement), jamais du code/expression/commande. Résolution
  hors store, refus d'un résultat absent ou ambigu. `$ref` remplace la valeur entière.
- Pour preview/apply : copier `preview.prepared_facts`, expected_revisions,
  preview_digest et confirmer les IDs de `preview.impacts`, explicitement listés
  dans les inputs ou via une opération de binding fixe à préciser avant seal.
  Aucune règle « confirmer tout ce qui arrive » cachée dans le runner.
- Concurrence deterministe : lire la revision, exécuter un step correctif/forget
  concurrent déclaré, puis utiliser la vieille revision. Les vrais interleavings
  de threads/processus sont des tests B9 séparés, pas ordonnancement non reproductible.
- `fault` facultatif : {"step":"correct","point":"item_version","occurrence":1,
  "kind":"exception"}. Points SQL exposés par SQLiteStore._fault ; l'injection
  process_exit vient dans B9 avec preuve de reprise et reçus, sur copies jetables.
- Après chaque step : résultat complet sérialisé, acteur/impacts/notices, reçu,
  snapshot logique items/historique/blocs/tasks, profils/cache séparés. Garder
  exception/outcome et modifications partielles JSONL ; ne jamais les transformer
  en succès. Request status après interruption sans rejeu.
- Avant collecte : audit 40 IDs uniques, langues/familles, schema/refs et SHA.
  Les 40 cas restent au dénominateur ; toute exclusion doit être un refus affiché.
- Gold privé : expected writes/closures, source provenance, refus, état temporal
  et event/cursor après B5, zero surprise invalidation, zero partial success.

Envoyer à Codex **uniquement** inputs, manifest, SHA, liste de compatibilités et
format approuvé. Garder le gold et le scoreur séparés ; scorer après gel du candidat,
revue adverse et mutations du juge comme pour P-A.
