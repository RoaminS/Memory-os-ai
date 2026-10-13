# B4/B5 — propositions à contresigner avant code

13 octobre 2026. Contrats de conception ; **aucune implementation B4/B5 dans
cet incrément**. B2 fournit CAS, impacts et audit, base de ces lots. Zéro nouvel
outil MCP. Le budget cumulé de catalogue ≤+10 % reste à contrôler : B2 a déjà
consommé ~6,4 % depuis la baseline d003e54.

## B4 — compensation sans réécrire le passé

Étendre memory_add_facts : champ optionnel par fait
compensates={audit_id,restore_id,restore_revision}. Le client envoie le texte et
les champs factuels restaurés, une NOUVELLE date actuelle, et replaces ciblant
la tête erronée ; preview obligatoire même avec un impact unique.
memory_chat_save n'accepte pas cette extension dans la première livraison B4.

- Le serveur retrouve audit_id dans l'historique B2 du même projet ; restore_id
  doit être une source effectivement affectée par cet audit. restore_revision
  identifie sa version logique antérieure, pas une révision tirée d'un texte.
- Le contenu proposé doit correspondre au contenu restaurable de cette version
  (texte/identité/provenance factuelle) ; aucune copie libre cross-project.
  Les parents de la nouvelle assertion relient source et correction.
- La source historique peut être close mais ne doit pas être oubliée. Le
  serveur contrôle aussi l'état ACTUEL de la source, empêchant de réintroduire
  un contenu oublié en lisant une vieille version.
- Source marquée sensitive/secret/credential ou détectée secret-like : refus
  source_not_restorable. Détection heuristique à définir et sceller par fixtures
  synthétiques avant code ; aucune promesse de détection de tous les secrets.
  Tout oubli prévaut sur compensation, même si une sauvegarde contient le texte.
- L'aperçu matérialise les prepared_facts, révisions des sources et de la tête,
  impacts/digest liés au marqueur compensates. Apply : raison, confirmation
  exacte, request_id et mêmes révisions ; une nouvelle correction concurrente
  refuse le vieux plan, sans retarget automatique.
- L'ancienne fenêtre reste intacte. Nouvelle assertion à maintenant ; aucune
  réouverture de valid_until, aucun effacement de la correction erronée.
- Historique/audit/événement B5/reçu final dans UNE transaction SQLite.
  JSONL : backend_unsupported pour compensation contrôlée, même en aperçu.
- Le client ne doit pas affirmer qu'il a « annulé » un historique : il annonce
  l'effet actuel et la trace de compensation. Les corrections avant B2 sans
  audit restent manuelles ; pas de lignée certaine reconstruite à partir du texte.

Critères : timeline avant/pendant/après correcte ; tête changée, source oubliée,
hors projet ou sensible refusées ; audit/reçu atomiques, replay sans doublon ;
exercice opérateur <5 min après serveur disponible. Tests hash et crash B9.
P-B inclut 10 cas compensation/traçabilité. Cet ajout et les noms ci-dessus
demandent la contre-signature Claude avant implementation.

## B5 / L1b — journal durable et curseur borné

Étendre memory_timeline(view=changes, project, since_cursor, limit).
Default view=history et son query actuel inchangés ; query devient facultatif
UNIQUEMENT pour view=changes. Dans changes : project explicite, query refusé
plutôt qu'un filtrage ambigu, limit 1..200 (défaut 30).
Chemin sans recherche/encodeur. JSONL : backend_unsupported pour changes.

Schéma SQLite suivant, migration transactionnelle sauvegardable sur copie :
change_events(sequence INTEGER PRIMARY KEY, event_id UNIQUE, operation,
project, record_json). Insertion en fin de mutation dans la même transaction
que l'autorité et le reçu ; aucune déduction depuis l'index de recherche.
Séquence monotone non réutilisée après purge. Un événement par appel logique
committé, pas un par écriture interne/flush/cache ; son lot contient les impacts.

Événements couverts prospectivement : ajout, correction vérifiée/legacy,
compensation, oubli, bloc et task CAS, doublon/no-op. Même request_id rejoué :
aucun nouvel événement. Refus/transaction annulée : aucun événement de succès.
La réponse mentionne reason/changed_by déclaratif, refs de versions, effectifs,
impacts ; pas de corps complet ni de secret copié dans le journal.
Oubli : références/effectifs expurgés selon politique de confidentialité,
aucun contenu historique ressuscité par le delta.

Retour :
events, next_cursor, has_more, coverage_start, coverage="prospective",
notices, store_instance_id. Pour les anciennes mutations : couverture
indisponible explicitement ; pas de faux événements « certains » reconstruits.
L'audit B2 peut servir à une lecture historique distincte, sans prétendre couvrir
toutes les anciennes opérations.

Curseur opaque liant store_instance_id, génération du journal, scope exact,
séquence et version du format, contrôlé par MAC avec clé locale privée de
l'instance. Ce n'est pas une frontière d'autorisation supplémentaire.
Mauvais scope/instance/cursor altéré : cursor_invalid ou identity_changed,
aucun élargissement. Restauration : conserver store_instance_id mais changer
génération du journal à la réouverture, anciens cursors explicitement périmés.
Une perte de clé/retour JSONL invalide aussi les cursors.

Rétention : pas de purge automatique dans la première livraison, taille
exposée dans health/doctor et alerte de seuil 10 MiB ; nettoyage explicite sous
maintenance, seulement entrées >30 jours. Ce choix évite de perdre une annonce
non lue silencieusement. Un cursor sous la borne conservée donne cursor_expired,
requiert une lecture complète ; jamais succès avec un delta incomplet.
La rétention des reçus existants reste indépendante. Limites finales de
croissance à réviser avant lancement si la mesure montre un volume excessif.

Le hook avance son cursor APRÈS injection réussie, jamais après fetch seul.
Pas de cursor enregistré par le serveur à la place du client ; pas d'ACK humain
déduit de cette injection. client_id filtre/présente déclarativement ; aucune
autorisation d'accès ajoutée par une préférence.

Critères : chaque catégorie ci-dessus observable après COMMIT seulement ;
pagination stable sans saut/doublon, cross-project refusé, alias explicitement
résolu ; restauration/rétention/concurrence/ACK interrompu testés ; 0 encodage ;
10k événements, page50 p95 ≤30 ms sur machine qualifiée (SQL index project,
sequence), seuil gelé avant mesure. Catalogue cumulé ≤+10 % ou arbitrage
documenté avant code supplémentaire, jamais coupure silencieuse des descriptions.

## Points à confirmer par Claude

1. B4 marqueur compensates et validation source historique/tête actuelle,
   preview toujours, refus des sources sensibles/oubliées.
2. B5 scope explicite, SQLite-only, événements prospectifs, schéma suivant,
   génération changée à la restauration, cursor MAC.
3. Rétention initiale explicite seulement, alerte 10 MiB et expiration annoncée.
4. P-B format et répartition 10/10/10/10 ; gold scellé avant gel COMPLET B4/B5/B9.
   Ajouter un event/cursor dans les références de steps après accord sur ces noms.

Ces choix ne demandent aucune activation actuelle ; publication, migration et
bascule production restent les GOs distincts de Romain.

