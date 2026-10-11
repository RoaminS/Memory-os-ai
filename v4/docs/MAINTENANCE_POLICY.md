# L2 — politique de maintenance, incrément candidat 4.0.4

`MEMORY_V4_MAINTENANCE_MODE` est un réglage **opérateur**, lu avant ouverture du
magasin et figé pour la durée de vie du moteur. Valeur invalide : refus de
démarrage, sans magasin créé. Modifier une mémoire, un bloc, un argument MCP ou
l'environnement d'un moteur déjà ouvert n'accorde aucune permission.

| Mode | `dry_run=true` (défaut) | `dry_run=false` |
| --- | --- | --- |
| `disabled` (défaut) | refus | refus |
| `preview` | simulation sans écriture ni reçu | refus |
| `apply` | simulation sans écriture ni reçu | mutation autorisée |

Les deux noms MCP `memory_consolidate` et `memory_compact`, le handler direct et
la façade Python appliquent la même politique. Les refus précèdent toute
réservation de reçu et transaction SQLite ; un reçu ancien terminé ne contourne
pas une révocation. Une simulation ne possède donc pas de reçu rejouable, même
si le client fournit un ID. `dry_run` exige un booléen, sans coercition de texte.

Réponse de refus : `ok=false`, `code=maintenance_denied`,
`outcome=not_started`. Ce dernier désigne **cet appel refusé**, pas une éventuelle
ancienne opération portant le même ID. Les reçus existants restent conservés.
`memory_status.maintenance` affiche le mode effectivement capturé par le moteur.

L'opérateur doit approuver séparément toute maintenance réelle, son périmètre et
sa fenêtre ; `apply` n'est pas une autorisation à programmer une consolidation
hebdomadaire. Aucun endpoint MCP ne permet de modifier ce réglage. Les modules
internes de balayage ne sont pas une frontière de sécurité face à du code Python
arbitraire exécuté dans le processus : seules les interfaces publiques sont
protégées. Le selfcheck sur magasin jetable autorise uniquement `preview`.

## Migration des clients et tests

Claude doit retirer les recommandations « hebdomadaire » ou « >2000 éléments »
des AGENTS.md/configurations/copies qu'il maintient, et garder l'interdiction
INC-001/INC-002 tant que Romain ne la lève pas. Les descriptions et instructions
MCP du candidat sont alignées. Les anciennes suites de consolidation autorisent
explicitement `apply` sur leurs magasins jetables ; les nouveaux tests vérifient
le défaut fermé et les fichiers/reçus/transactions inchangés après refus.

Ce lot ne change ni la production ni sa configuration. Il ne remplace pas la
qualification sur copie ni le GO de bascule. JSONL conserve ses limites de
transaction ; SQLite conserve son activation opt-in.
