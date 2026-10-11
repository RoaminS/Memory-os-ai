# L4 — reconnexion et diagnostics, incrément 3a

Base : 3450bce (2b), qualifiée par Claude : 1253 tests, dont 15 TCP.
Candidat seulement ; aucun runtime local, magasin ou jeton de production modifié.

Le propriétaire AnyIO reste unique. Le fichier 0600 est relu avant chaque requête ;
une rotation atomique ferme/recrée la connexion avant l'envoi. Les appels de
mutation déjà partis ne sont jamais rejoués. Un résultat absent reste incertain
et nécessite le statut de reçu, conformément à L3.

Ajouts :

- Annuler une attente réveille maintenant le propriétaire même si l'amont accepte
  TCP sans répondre. Une mutation annulée avant son départ est retirée de la file ;
  une mutation déjà partie reste soumise au journal d'incertitude.
- `memory_status` et la consultation amont d'un reçu ont un budget de contrôle
  de 4 secondes (file comprise). Le dépassement annule l'attente avec
  `control_timeout` ; il ne rejoue pas une mutation. Les recherches/ingestions
  longues ne reçoivent pas ce timeout. L'initialisation MCP est bornée à 3 s.
- Le préchargement du catalogue conserve sa limite de 5 s, et son abandon
  libère désormais la connexion ; le catalogue et le statut local sont toujours
  servis sans RPC amont pendant une panne.
- Diagnostics redigés sans exception brute/secret : `token_missing`,
  `token_invalid`, `authentication_rejected`, `upstream_unavailable`,
  `control_timeout`, `identity_changed`. `memory_request_status({})` expose
  `connection.last_error` (ou null), `live_probe:false`, deadline, version du
  relais et nature du catalogue. Ce n'est PAS une preuve de santé en temps réel.
- Une nouvelle identité de handshake (nom/version) bloque la session jusqu'à
  redémarrage du client après revue. L'identité du magasin est déjà vérifiée par
  le préflight L3 des mutations. Même version ≠ même magasin : UUID du reçu requis.

Critère physique inchangé : 30 cycles arrêt/rotation/redémarrage, contrôle réussi
≤5 s après `/ready`, sans redémarrage client. Chaque délai est conservé dans
`pytest-tmp/**/cycles.json`. Le runner refuse un scénario absent/échoué/skippé.
Ajouter la preuve TCP de changement d'identité et celle du registre via relais.

Limites : démarrage initial avec serveur absent = erreur explicite, pas un faux
serveur prêt. Le relais ne peut réparer un client tiers qui ferme sa propre
connexion stdio ; doctor/instructions doivent demander le redémarrage client.
Un contrôle arrivé derrière une longue mutation peut expirer : sa limite n'autorise
ni l'interruption automatique de cette mutation ni un succès fictif. Les délais
MCP/read sont des délais d'attente, pas une garantie de livraison de mutation.

Le catalogue est la photographie de la session ; nouvelles capacités ⇒ nouvelle
session. Une version serveur différente exige revue et redémarrage ; aucune
reconnexion automatique à un autre endpoint, aucun abaissement de la sécurité.
