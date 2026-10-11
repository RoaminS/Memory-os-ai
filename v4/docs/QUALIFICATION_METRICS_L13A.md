# L13a — compteurs de qualification locaux, incrément candidat 4.0.4

Activation opérateur au démarrage seulement :
`MEMORY_V4_QUALIFICATION_METRICS=1` (`0` par défaut, autre valeur refusée).
Compteurs **en RAM du processus**, réinitialisés à sa fin ; aucun fichier ajouté
au magasin, aucune télémétrie distante ni activation par les outils.

`memory_status.qualification` fournit un snapshot, hors appel de status en cours.
Chaque nom d'outil connu possède une ligne ; tout nom inconnu est regroupé dans
`_unknown`, pour borner la cardinalité. Aucun label projet/client/query/ID
requête/chemin, aucune charge, réponse ou message d'erreur n'est conservé.
Une panne de mesure ne change pas la réponse d'une mutation déjà acquittée ;
`observation_failures` l'indique et invalide une qualification quantitative.

| Mesure | Portée exacte |
| --- | --- |
| appels/succès/erreurs/refus maintenance | Tentatives de dispatch, pas intentions utilisateur uniques ni hits utilisés. |
| durée somme/max/histogramme | Horloge monotone, dispatch synchrone incluant attente/verrou moteur ; hors queue du pool, sérialisation finale, transport et temps du modèle client. |
| octets somme/max | UTF-8 exact de `TextContent.text` produit par le serveur, même sérialiseur ; **pas l'enveloppe MCP réseau complète**. |
| budget brief demandé | Nombre demandé ou défaut du schéma, pas consommation mesurée. |
| taille du texte rendu | UTF-8 du seul `text`, pour comparer avec l'ensemble des fiches retournées. |
| tokens / verdict tokens | `null` / `not_measured`. Aucun raccourci caractères÷4 présenté comme mesure. |

Les histogrammes ont des bornes fixes 1/5/10/25/50/100/250/500/1000/5000/30000 ms
puis une classe sans borne supérieure. Ils donnent des classes de quantiles,
pas un p95 exact interpolé. Le coût de la mesure n'est pas imputé au dispatch
mesuré ; comparer instrumentation activée/désactivée avant toute promesse de SLA.
L13b, les IDs de clients pseudonymisés, les métriques d'utilité et la rétention
sur disque de 30 jours ne sont **pas** implémentés dans cet incrément.

## Reproduction sans modèle

Depuis le clone avec le Python de tests déjà installé :

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python \
  scripts/qualify_404_increment1.py --suite unit \
  --output .local-qualification/404-increment1-local-v1
```

Le dossier doit être neuf. Le script conserve les erreurs, les journaux, le XML
pytest et les empreintes des sources, puis exécute une petite baseline synthétique
sur vingt épisodes avec encodeur **hash**. Elle mesure cinq briefs, cinq recherches
et un refus de maintenance. Ni benchmark neuronal, ni assertion de pertinence
L1a, ni qualification de la release complète. Les tokens restent non mesurés.

Le mode `unit` exclut explicitement les deux fichiers TCP ; `tcp` ou `full`
doivent être exécutés par Claude hors sandbox. Tout test sélectionné ignoré,
échec ou manque de preuve refuse la qualification correspondante. Le script
refuse <5 Go de disque libre et les sorties hors du clone ; aucune charge lourde
ni encodage de modèle n'est planifié. Les gardes physiques du plan s'appliqueront
aux futures mesures lourdes, séparées de ces tests unitaires séquentiels.

Les preuves exportées à la demande restent dans `.local-qualification`, jamais
dans le magasin. Leur archivage ou suppression est explicite ; elles ne sont
pas des journaux continus de conversations. Les fichiers de preuve ne sont pas
inclus dans les packages destinés à la production.
