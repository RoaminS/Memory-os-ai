# Échec du lot CPU — diagnostic du 2026-10-10

Exécution Claude hors sandbox : suite v4 **998 passed**, puis deux jobs,
5 485 s ; le gate de rangs échoue, aucun `report.json`. Pas de redémarrage.
Les preuves originales de `.local-qualification/indexing-batch-v1` sont conservées
et ne sont pas modifiées par ce diagnostic ou par la campagne suivante.

| conversation | CPU scalaire | CPU faits par lots | vitesse avant/après | max Δ vecteur | sondes changées |
|---|---:|---:|---:|---:|---:|
| 2203b7a3 | 865 s | 1 016 s | 0,85× | 0,0078125 | 2/5 |
| 4ef06ff2 | 648 s | 828 s | 0,78× | 0,0078125 | 4/5 |
| fe61be4b | 957 s | 1 159 s | 0,83× | 0,0078125 | 4/5 |

Textes, éléments, provenance, parents et résultats de capture : identiques.
Tolérance flottante et rangs : échec. Les durées augmentent d'environ 17–28 %.
Les objectifs 300/450/600 s sont ratés. Ce lot ne constitue aucune accélération
qualifiée et ne doit pas être activé en production.

## Ce que le code et les artefacts établissent

`Store.save_embeddings` fait `astype(np.float32)` puis `np.save`, sans
quantification de stockage. Les trois tableaux avant sont float32, mais **100 %
des valeurs ont leurs 16 bits bas nuls**, donc sont exactement représentables en
bfloat16. Le snapshot Granite déclare `dtype: bfloat16`. La perte de précision est
en amont de la sauvegarde ; convertir les sorties en float32 ne restaure pas les
bits perdus. `2^-7` est compatible avec des pas bfloat16, sans prouver qu'un simple
franchissement de seuil explique toutes les différences internes de calcul.
Les normes avant vont approximativement de 0,997 à 1,004, cohérentes avec une
normalisation de précision réduite. Le nouveau journal relève le dtype et le
device réels des paramètres ; aucune charge de modèle n'a été faite par Codex.

L'encodeur représente 99,7–99,9 % du temps des trois paires. Le chiffre global
BEAM fourni, 14,79 millions **de millisecondes** sur 14 840 secondes, donne 99,66 %
(14,84 millions de millisecondes au dénominateur). MCP/disque ne sont pas ici le
levier principal.

Le padding est une hypothèse plausible, pas une cause isolée. La bibliothèque
SentenceTransformer installée **trie déjà les entrées par longueur dans chaque
appel**, puis restaure leur ordre. Les groupes chronologiques de quatre faits
limitent cette optimisation ; ajouter un tri identique ne suffit pas. La campagne
suivante journalise tokens utiles et tokens paddés du tokenizer, sans assimiler
ce compteur au nombre exact de FLOPs des kernels.

Le confondant thermique existe : avant a toujours précédé après. Sans mesures
thermiques/contrebalancement, aucun pourcentage du ralentissement ne peut lui être
attribué. L'alternance AB/BA ci-dessous réduit le confondant, sans supprimer les
effets de charge ambiante ou garantir une température constante.

1 883 textes pour 1 361 appels prouve seulement une moyenne de plusieurs textes
par appel, **pas des textes identiques**. SQLite possède déjà un cache de vecteurs
par empreinte. Avant d'ajouter un cache JSONL, mesurer les chaînes d'indexation
exactement identiques, avec modèle/précision/device/longueur dans l'empreinte.

## Choix suivant

La capture scalaire des faits redevient le défaut de la candidate ;
`MEMORY_V4_BATCH_FACTS=1` garde le lot expérimental accessible. Les épisodes
conservent leur batching antérieur. C4 reste inchangé.

Priorité : **Granite MPS float32**, même snapshot et longueur 512, aucun ONNX,
int8, autre modèle ou raccourcissement de texte dans cette expérience. C'est un
traitement composite device+précision ; un gain ne sera pas attribué au GPU seul.
PyTorch décrit [MPS](https://docs.pytorch.org/docs/stable/notes/mps.html) ; les
[options de précision Sentence Transformers](https://sbert.net/docs/sentence_transformer/usage/efficiency.html)
permettent d'imposer le dtype à la construction. La disponibilité et le support
effectif du modèle restent à vérifier par Claude ; échec = arrêt, pas de fallback
CPU ni nouveau bras choisi après résultats.

Suite proposée si ce bras échoue : CPU float32 isolé, puis batching par longueurs
réelles, puis ONNX/int8 avec même gate qualité. Longueur 256 ou nouveau modèle
changent la représentation et demandent une étude distincte ; aucune activation
sur une seule amélioration de débit.

Les nouveaux critères sont définis dans [INDEXING_MPS_V2.md](INDEXING_MPS_V2.md)
avant toute nouvelle exécution. Ils ne changent pas rétroactivement le verdict v1.
