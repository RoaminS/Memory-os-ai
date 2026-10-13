# B2 — contrat de correction contrôlée, proposition v1

**À relire par Claude avant intégration B2.** Ce contrat n'est pas une API déjà
implémentée. B3 seul est livré dans cet incrément. Protocole P-B scellé
`a8a037ead1d1dd9f065aa77f7d286182de85e4d72a3327640b0ea38b165ed8fc`.
Aucune activation de production, aucun nouveau tool MCP, aucun changement du
profil d'encodage ou du schéma de stockage livré ici.

## Surface proposée

Étendre les outils existants de mutation de faits, principalement
memory_add_facts ; memory_chat_save suit exactement les mêmes contrôles pour
ses faits correctifs, sans répliquer la canonicalisation dans le relais.
Les additions ordinaires sans invalidation restent compatibles. Prévisualiser
une correction ne sauvegarde ni épisode, ni résumé, ni fait ni reçu terminé.

Champs communs proposés (noms à contresigner) :

```json
{
  "mode": "preview",
  "facts": [{"item_id":"new-stable-id","text":"New current assertion.","event_time":"2026-10-13T10:00:00Z","replaces":["old-id"]}],
  "project": "P",
  "reason": "Explicit rationale for correcting the current assertion."
}
```

L'aperçu retourne `outcome=not_started`, prepared_facts avec IDs et dates absolues
réutilisables, store_instance_id, targets `{id,expected_revision}`, impacts
`{target_id,successor_id,valid_until,mechanism}`, payload_sha256 et digest de plan.
Le plan ne contient aucun texte sensible ; ancienne assertion consultable par
lecture explicite scoped. Ces hashes sont de l'intégrité de contenu, ni signature,
ni preuve d'accord humain. L'aperçu ne crée aucun handle persistant dans le store.

Pour appliquer : mêmes prepared_facts, `mode=apply`, `reason` non vide,
`expected_revisions` couvrant exactement chaque cible affectée, `request_id`
explicite. Si plus de trois invalidations sont prévues, exiger aussi
`confirmed_impacts` (liste exacte) et digest liant payload/instance/révisions.
Ne pas accepter un simple confirm=true. Aucun champ client ne dispense des
contrôles de scope, maintenance, write_mode ou confidentialité.

## Découverte des impacts et CAS

Le moteur calcule les mêmes impacts avant et au début de l'application sous
verrou/transaction ; cibles explicites ET single identity précises. Il inclut
les ajustements temporels d'insertion historique, y compris ceux touchant une
nouvelle assertion et une chaîne déjà corrigée. Égalité de date ne ferme pas
une fenêtre ; homonymes/identités multi ne sont pas regroupés implicitement.
Il n'infère ni identité ni remplacement à partir d'entités/texte/cosinus.

Révision opaque proposée : SHA-256 du dernier enregistrement logique autoritaire,
JSON trié stable, excluant seulement hash dérivé/access_count/last_accessed.
Inclure identité, texte, provenance, validité, suppression, tags et champs
logiques inconnus conservés. Ne pas utiliser le SHA1 de texte comme CAS.
Relire la révision sous verrou, refuser toute cible oubliée/changée/hors scope.
Une lecture ne doit pas périmer le CAS par un compteur d'accès seulement.

Événement métier et valid_from absolus, conservés entre preview et apply ;
date invalide = refus, jamais fallback silencieux à maintenant pour une correction.
Le moteur détecte la vraie liste d'impacts, pas seulement len(replaces).
La raison est exigée seulement lorsqu'une invalidation effective est prévue ;
une assertion sans impact ne requiert pas une justification artificielle.
Entité commune éventuelle = avertissement informatif, jamais interdiction.

## Exécution et erreurs

SQLite : sources, ajustements, journal d'impacts et reçu final dans UNE
transaction BEGIN IMMEDIATE, acquittement après COMMIT. Sur refus : zéro écriture
sémantique, aucun succès/événement de changement annoncé. Index dérivés réparables.
JSONL conserve ses limites : aucun contrat atomique nouveau. Choix à acter dans
la revue B2 : laisser une correction unique avec garanties explicitement réduites,
ou refuser l'application contrôlée d'un lot invalidant non atomique avec
backend_unsupported. Les additions ordinaires et lectures ne sont pas affectées.
B4 compensation composée et C5 revision documentaire restent SQLite-only selon v5.

Codes proposés : reason_required, revision_conflict, impacts_confirmation_required,
impact_mismatch, invalid_target, invalid_correction_time, backend_unsupported.
Refus avant envoi = not_started ; erreur après envoi ambigu = uncertain, statut
à résoudre avant tout nouvel essai. Request ID identique après COMMIT retourne
le reçu précédent sans réappliquer, même si la tête a changé depuis.
Aucun retry automatique du relais. Le clientInfo reste déclaratif.

## Matrice de revue B2

| Groupe | Contrôles de développement et validation séparés |
|---|---|
| Identité | single précise, multi, générique refusée, homonymes, prédicats extensibles ; aucun regroupement par facette |
| Cible | même projet/kind/date valide ; cible inconnue/oubliée/autoproclamation et cross-project refusées |
| Raison | vraie invalidation sans raison refusée ; addition sans impact compatible ; raison sans entité commune acceptée |
| Impacts | 0/1/3/4 cibles, identité implicite avec >3 têtes ; confirmation exacte, altérée ou périmée ; double remplacement dédoublé |
| CAS | aperçu puis forget/correction/concurrence : stale refusé ; lecture sans périmer la révision ; request id replay sans double effet |
| Temporalité | date égale/avant/après, périodes, insertion historique et nouveau successeur dans le même lot |
| Atomicité | interruption à chaque écriture ; aucun reçu final d'un lot partiel, reprise sans aveuglement ; sources et provenance conservées |
| Compatibilité | JSONL/SQLite, read_only/maintenance ; vocabulaire d'erreur client stable ; catalogue ≤+10 %, zéro nouveau tool |

Claude prépare/scelle les 40 validations P-B indépendantes du jeu dev Codex,
révise les noms/champs et la décision JSONL. Les contrats B4/B5 suivants restent
à contresigner avant code ; pas de publication sous-entendue par cette revue.
