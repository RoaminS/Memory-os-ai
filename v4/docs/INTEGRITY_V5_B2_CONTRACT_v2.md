# B2 — contrat v2 et état de l'incrément, 13 octobre 2026

Remplace la proposition v1 pour cet incrément. Amendement A–D préenregistré
dans le commit a57d169, SHA e0b23faef244c1f26f092f2e62414ec905b76d6760bbfa80fb36708554f3cbeb.
Code ca5c970, tests de développement verts ; qualification complète/TCP par
Claude encore attendue. Version 4.0.6.dev0, production 4.0.5 inchangée.
B4/B5 sont des contrats proposés séparément, pas des outils déjà disponibles.

## A–D et réserve explicite

A/C/D acceptés. B accepté pour les appels legacy jusqu'à trois impacts :
notice correction_unverified, aucun CAS prétendu, impacts/acteur retournés.
**Contre-amendement à confirmer par Claude : conserver la garde >3 impacts du
plan v5**, exigeant alors un chemin contrôlé. Il ne s'agit pas d'un accord déjà
contresigné. La validation P-B de ce point attend l'accord avant le seal.

SQLite : vérification, sources/historique/audit et reçu final dans la même
transaction BEGIN IMMEDIATE. L'audit B2 est un champ correction sur le successeur,
persisté dans item_versions ; ce n'est pas encore le journal paginé B5.
JSONL : legacy conserve ses limites/effets partiels ; preview/apply contrôlés
refusés backend_unsupported. Zéro nouvelle garantie atomique JSONL.

## Lecture puis correction unique : un appel de mutation

memory_search.hits expose revision pour chaque fait. memory_get_context ajoute
references [{id,kind,revision,span}] : offsets en points de code Unicode du
champ context. Les références concernent seulement les faits effectivement
rendus. Le texte et son budget historique restent inchangés.

Révision opaque SHA-256 du dernier enregistrement logique autoritaire, incluant
les champs inconnus conservés, excluant hash/access_count/last_accessed.
L'accès et son flush ne changent pas le CAS.

Exemple après lecture de old :

```json
{
  "project":"P",
  "facts":[{"item_id":"new","text":"Service uses port 9000.","event_time":"2026-10-13T10:00:00Z","replaces":["old"]}],
  "reason":"Operator changed the port.",
  "expected_revisions":{"old":"<revision returned by read>"},
  "request_id":"operator-unique-id"
}
```

Un unique impact **explicit_replaces** peut être appliqué directement. Si
mode/expected_revisions/confirmed_impacts/preview_digest est présent, le chemin
est contrôlé : jamais de repli legacy sur une erreur. reason seule n'active pas
le contrôle. L'absence de revision dans un apply invalidant donne
revision_conflict ; obtenir un aperçu plutôt qu'un remplacement aveugle.
Les additions sans impact n'exigent pas une raison artificielle.

## Aperçu et application composée

mode=preview retourne ok, outcome=not_started, prepared_facts (IDs et dates
absolues fixes), expected_revisions, impacts, preview_digest, store_instance_id.
Pas d'épisode/résumé/fait, reçu terminé, encodage ni flush. Autorisé en read_only.
L'ouverture initiale SQLite peut initialiser sa structure ; sur magasin existant,
zéro changement logique. Une requête de preview n'est pas journalisée comme
mutation par le relais et ne consomme pas un request_id de mutation.

Plus d'un impact OU impact single_identity, même unique : aperçu obligatoire.
Apply réutilise prepared_facts, reason et expected_revisions, ajoute
preview_digest, confirmed_impacts (liste exacte des IDs de cibles, sans doublon)
et request_id. Le digest lie instance, projet, contenu, temps, cibles,
successeurs, mécanismes, raison ; pour chat_save, messages/résumé/date aussi.
Ce hash n'est ni une signature ni une autorisation humaine.

Tous les impacts sont recalculés sous transaction. L'insertion historique
inclut aussi une éventuelle fermeture d'un fait nouvellement ajouté dans le lot :
absence préalable de cet ID vérifiée, pas de revision existante inventée.
Dates invalides ou explicites non croissantes : invalid_correction_time.
Aucune proximité d'entités/texte/cosinus ne crée une correction.

## Résultat, provenance et refus

Chaque invalidation effective réussie retourne impacts et changed_by :
client_id/client_version déclaratifs, authenticated=false. Le relais injecte
clientInfo quand le schéma annonce client_provenance, et écrase ce champ fourni
par le modèle. Serveur direct : déclaration sans attestation. La provenance
n'accorde aucun droit et ne remplace pas le token/scope.

Audit durable : audit_id déterministe, reason, changed_by, verified, impacts.
Chaque impact contient target_id, successor_id, expected_revision,
before_valid_until, valid_until, mechanism. Legacy invalidant : notices contient
correction_unverified. Une erreur JSONL après invalidation connue expose
legacy_partial/uncertain avec ces impacts ; pas de succès de lot annoncé.

Codes : reason_required, request_id_required, revision_conflict,
impacts_confirmation_required, impact_mismatch, invalid_target,
invalid_correction_time, backend_unsupported. Refus contrôlé : not_started,
zéro écriture sémantique. Réponse perdue après envoi : statut avant reprise.
Rejeu explicite du même request_id/payload retourne le reçu sans double effet.
Read_only/maintenance restent bloquants pour apply.

Le catalogue contient toujours 23 outils. B2 n'implémente pas encore compensation,
journal de changements, rétention de ce journal ni matrice complète de crash B9.

