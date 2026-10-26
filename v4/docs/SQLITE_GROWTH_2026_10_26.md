# Croissance SQLite — diagnostic sur copie privée, 26 octobre 2026

Source autorisée : snapshot d'urgence Claude `emergency-20261026-220400`,
73248768octets, SHA256
`dea0f178f8d0ba86e5ed95b3bfdbfbfd237ef75e2847cc9fc275d36dde1ac32b`.
Copie locale sous `.local-qualification/408rc2-growth-private-v1`, SHA identique
avant/après, SQLite `mode=ro&immutable=1`, intégrité OK. Aucun accès au magasin
de production. Aucun modèle chargé/encodage ; aucune donnée textuelle publiée.
Script reproductible : `scripts/analyze_sqlite_growth.py --private-copy … --output …`.

| Poste | Volume de pages SQLite | Observation |
|---|---:|---|
| item_versions |35,14Mo|28281 versions pour7027 items|
| embedding_cache |21,60Mo|10520 lignes,16,16Mo de vecteurs bruts|
| legacy_files |10,74Mo|11 fichiers d'origine conservés|
| index item_versions |1,31Mo|index unique des révisions|
| request_receipts |1,27Mo|1170 reçus, aucun nettoyage effectué|
| change_events |0,93Mo|585 événements (566chat_save,19add_facts)|

## Pourquoi quatre versions par item ?

7027 versions initiales ; **20452 mises à jour statistiques seulement**
(access_count/last_accessed, hash non sémantique exclu) ;802 autres mises à jour.
Les seules stats représentent72,3% des versions et19,78Mo de JSON, sans index/
fragmentation associés. Les802 autres changements touchent notamment replaces755
fois, valid_until32, invalidated_by32, deleted9, correction6 ; champs peuvent se
recouper. Ce n'est pas le compte de802 corrections utilisateur distinctes.

Cause de code : Store.touch modifie les compteurs ; SQLiteStore.flush_access
appelle Store.flush_access → append_many ; chaque flush écrit une version
complète. Les requêtes/briefs peuvent donc augmenter l'historique sans nouveau
contenu métier. B5 filtre ces changements de compteur et n'en fait pas de fausses
corrections dans change_events. La persistance est intentionnelle, son coût est
une dette de conception mesurée. Pas de preuve de corruption ni d'invalidation
massive, pas d'attribution d'un appel à un hook sur le seul nom d'opération.

**Aucun changement de sémantique de ces stats dans cette RC.** Les supprimer,
les oublier au redémarrage ou les déplacer exigerait un contrat de persistance,
un inventaire de migration/retour et une requalification des reçus/lectures/
consolidation. Proposition ultérieure : stats dérivées agrégées séparées de
l'autorité immutable, sans réécrire les28281 versions existantes. Aucune purge,
VACUUM, compact/consolidate ou réécriture de production ici.

## Pourquoi plus de vecteurs que d'items ?

Reconstruction cryptographique des clés, avec le profil attesté stocké et le
texte d'index réel (pas d'inférence) : **7016 clés actuelles +3504 clés legacy
4.0.5 =10520** ; aucune ligne inexpliquée par ces deux namespaces. Le codec
sqlite_embeddings conserve volontairement les lignes legacy et adopte des alias
pour la compatibilité de retour4.0.5/4.0.6. Ce n'est pas10520 réencodages de7027items.

7018 items vivants (9 supprimés),7016 textes d'index distincts ; **7018/7018**
trouvent leur clé actuelle,0clé manquante. Deux partages de clés entre items
expliquent la différence texte/item. Les vecteurs legacy ne sont pas purgés ;
leur nécessité de retour est une décision de rétention future, pas une anomalie
à corriger pendant un incident de sauvegarde.

## Projection, non prévision garantie

Deux observations approximatives : ~46Mo au16/10,73,25Mo au26/10. Hypothèse
linéaire sur10jours : **~82Mo supplémentaires par30jours**, soit~0,98Go/an.
Le plafond64MiB était déjà dépassé ;4GiB laisserait environ51mois à ce rythme.
Ce n'est pas un intervalle statistique ni une garantie : imports, sessions,
lecture/accès et héritages changent le débit. Les révisions quotidiennes varient
par exemple de261à6795 (17/10 : pic6795), ce qui interdit de prendre une journée
comme fréquence stable. Relever les compteurs/tailles chaque semaine, prévoir
une nouvelle qualification bien avant80% du plafond et vérifier les durées S4.
Les économies futures d'une meilleure persistance des stats ne sont pas comptées
comme déjà acquises ; les chiffres décrivent uniquement la copie observée.
