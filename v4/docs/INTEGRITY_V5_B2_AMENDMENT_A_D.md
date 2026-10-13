# B2 — réponse A–D et amendement avant implémentation, 13 octobre 2026

A accepté : un impact explicite unique + revision lue + reason + request_id
s'applique en un appel sur SQLite. Révisions offertes dans search, get_context
et lectures détaillées. La lecture ne périme pas le CAS.

B accepté en transition 4.0.7, avec limite du plan v5 : appels legacy jusqu'à
trois invalidations = correction_unverified, aucun CAS prétendu, impacts retournés.
Plus de trois invalidations exige aperçu puis confirmation exacte, même legacy.
C'est le contre-amendement explicite à faire confirmer par Claude ; pas de
suppression silencieuse de la garde approuvée. Un appel ayant un champ de
contrôle est contrôlé et ne retombe JAMAIS en legacy sur erreur.

C accepté : JSONL legacy reste non atomique, et contrôlé mode=preview|apply
retourne backend_unsupported. Une opération JSONL interrompue peut laisser des
écritures ; aucun succès de lot ni garantie SQLite lui est attribué.

D accepté : chaque invalidation acquittée expose ses impacts réels et changed_by
avec authenticated=false, même legacy ; audit durable dans l'historique des faits
pour préparer B5. Déclaratif n'est pas une autorisation. Le relais injecte
client_provenance uniquement si le catalogue serveur annonce ce champ.

Contrôle : mode, expected_revisions, confirmed_impacts ou preview_digest présent
sélectionne le chemin contrôlé. reason seule ne prétend pas fournir un CAS.
Le chemin contrôlé refuse JSONL avant écriture ; aperçu autorisé en read_only
mais sans reçu/mutation/indexation. Apply exige read_write ; aucune exception
maintenance/read_only. Dédoublonnage/identité/projet/temps restent ceux du moteur.

Aperçu indispensable pour >1 impact ou mécanisme implicite : prepared_facts
avec IDs/dates fixes, expected_revisions, impacts, preview_digest liant instance,
project, payload, raison, révisions et impacts. Apply fournit les mêmes facts,
révisions, raison, digest et confirmed_impacts (IDs exacts) ; le digest lie aussi
successeur/temps/mécanisme, donc la liste d'IDs ne suffit jamais seule.
Un expected_revisions absent est un refus revision_conflict avec invite à l'aperçu,
pas un CAS facultatif. Zéro impact n'exige pas une raison artificielle.

La confirmation d'impacts n'est pas une preuve de GO humain. B4/B5 demandent
leurs contrats contresignés ; P-B 40 cas reste à sceller avant gel du candidat
complet. Aucun résultat de validation utilisé pour régler l'implémentation.
