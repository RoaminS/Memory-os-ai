# P-B : champs gelés avant B4/B5, 13 octobre 2026

Base 385141d, B legacy <=3 accepté par Claude, contrats B4/B5 contresignés.
Ce document et secret_like_b4_v1.json sont committés AVANT code B4/B5.
Prototype de catalogue sans changement produit : 10652 -> 10814 tokens,
23 outils, +8,03 % depuis d003e54 (10010), sous +10 %. Résultat final à mesurer.
R1/R2/R3 acceptés : fixtures FR/EN positives et négatives scellées, alerte
journal_size dans health/doctor, descriptions de nouveaux champs courtes.

## API exacte

- memory_add_facts uniquement : facts[].compensates={audit_id,restore_id,
  restore_revision}. audit_id et restore_revision = SHA-256 minuscules 64 chars.
  FactIn de memory_chat_save inchangé : compensation non acceptée dans chat.
- Champs B2 inchangés : mode preview|apply, facts, reason,
  expected_revisions {target_id:revision}, confirmed_impacts [target_id],
  preview_digest, request_id, client_provenance {client_id,client_version?}.
- Aperçu : ok, outcome=not_started, prepared_facts, expected_revisions,
  impacts, preview_digest, store_instance_id. Le marqueur compensates est
  conservé dans prepared_facts et lié au digest. Aucun ID de reçu consommé.
- Impact : target_id,successor_id,expected_revision,before_valid_until,
  valid_until,mechanism (explicit_replaces|single_identity).
- Réponse invalidante : added,deduped,superseded,impacts,changed_by,notices,
  **audits** [{item_id,audit_id,verified,compensates?}]. audit_id est un identifiant
  opaque de contenu et non une autorisation. Pas de audit_id scalaire ambigu
  au sommet d'un lot. changed_by contient authenticated=false.
- Refus B4 : source_not_restorable, compensation_preview_required,
  invalid_correction_time ; autres codes B2 conservés. Aucun rollback du passé.
- Compensation : nouvelle validité actuelle ; dates explicites du prepared
  preview réutilisables <=300 s, jamais futures de >5 s. Le now des fixtures
  peut être gelé par le runner, jamais envoyé comme une autorisation à l'API.
- memory_timeline : view=history|changes (history défaut), query obligatoire
  seulement en history ; changes exige project, refuse query, accepte
  since_cursor optionnel et limit 1..200 (30 défaut). SQLite uniquement.
- Changes : ok,events,next_cursor,has_more,coverage_start,
  coverage=prospective,notices,store_instance_id. Sans since_cursor : depuis la
  borne retenue. next_cursor est retourné même pour une page vide.
- events[] : sequence,event_id,operation,project,committed_at,changed_by,
  reason,impacts,refs,audit_ids,no_op. Refs sans texte : {id,kind,revision?}.
- Mutation SQLite committée publique : change_event={event_id,sequence}
  en plus du résultat existant, lorsqu'un journal est créé. Aucun événement
  de succès pour refus, lecture, preview, flush/cache ou rejeu de reçu.
- Curseur : cursor_invalid,identity_changed,cursor_expired ; non-observabilité
  des anciennes opérations = coverage=prospective, jamais score comparatif.

## Binding preview -> apply, sans expression ni confirmation implicite

Step ordinaire : {id,tool,arguments}. $ref remplace sa valeur entière et vise
UNIQUEMENT un step antérieur : {"$ref":"read.hits.0.revision"}.
Le point et les index décimaux parcourent les données, jamais code ou shell.

Step de binding explicite (ne contacte pas le serveur) :

~~~json
{"id":"bound","bind_preview":{"step":"preview","confirmed_impacts":["old"],"request_id":"pb-unique-apply"}}
~~~

Le résultat bound.arguments copie les arguments originaux de preview,
remplace mode par apply, facts par preview.prepared_facts, ajoute
expected_revisions,preview_digest,confirmed_impacts et request_id ; reason,
project et parent_ids conservés. La liste confirmée vient EXPLICITEMENT du cas,
jamais déduite de tous les impacts. La correspondance exacte des IDs est vérifiée.
Le step suivant :

~~~json
{"id":"apply","tool":"memory_add_facts","arguments":{"$ref":"bound.arguments"}}
~~~

Altération pour test : utiliser un step ordinaire avec $ref par champ ; pas
d'overrides de binding qui contournent l'audit. Binding invalide = refus de
compatibilité AVANT collecte, pas résultat évalué caché.

## Runner et gel

40 inputs sans expect, schema de P-B v1, 20 FR/20 EN ; paths server|relay,
store neuf par cas/path, hash uniquement, clientInfo synthétique déclaratif.
Baseline B2 = 385141d optionnelle ; B4/B5 absents y sont not_observable, pas
des réussites/échecs comparatifs. Candidat lié à commit, archive SHA de v4/src,
inputs SHA et manifeste BEFORE run. Deux répétitions identiques en stores
séparés. Les champs non déterministes (IDs auto/receipts/timestamps) restent
dans les preuves, leur projection déterministe est explicitement décrite.
Fault exception avec point exact et occurrence ; process_exit B9 exécuté par
worker privé et reprise read-only, jamais rejeu automatique d'une mutation.
Les inputs incompatibles font refuser l'audit sans les réinterpréter.

## Correction factuelle de la restauration

S4 existant crée déjà un NOUVEAU store_instance_id lors d'une vraie restauration,
à distinguer d'une migration normale qui préserve l'identité. Ce comportement
est conservé : génération de journal renouvelée aussi ; anciens cursors donnent
identity_changed. Le contrat v1 évoquait une identité préservée en restauration :
cette phrase ne doit pas faire régresser S4. Copier pour drill sans rouvrir
n'est pas une activation ni une restauration automatiquement reconnue.
