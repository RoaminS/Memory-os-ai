# RC 4.0.6 v5 — référence HF binaire exacte, sans réparation implicite

2026-10-12. Candidate non publiée/non déployée ; correctif limité à provision
et ses tests/documents. Les trois gates v4 étaient verts ; le trial privé
`20261012-083306-sqlite` a échoué au chargement du modèle, avec arrêt rapide
effectif et production/configurations attestées inchangées par Claude.

## Cause et politique

Le SDK qualifié huggingface_hub 1.5.0 (`file_download.py:1500`, source installée)
lit la référence textuelle verbatim : `revision = f.read()`. Le LF introduit
par notre provision fait chercher un autre répertoire de snapshot. Nos deux
comparaisons `.strip()` acceptaient à tort cette représentation.

1. Ref créée par nous : bytes ASCII de la révision, exactement 40 octets,
   écriture binaire exclusive, fsync, installation sans écrasement.
2. Ref existante : fichier régulier, bytes strictement identiques ; LF, CRLF,
   espaces, NUL, symlink/format différent sont refusés. Jamais de nettoyage,
   normalisation ou réparation silencieuse du cache tiers.
3. Ref absente : le preview reste non ready ; application explicite peut créer
   la ref après vérification des ressources. Cache natif conforme réutilisable.
4. Ref changée pendant le téléchargement : refus à la publication, ref laissée
   intacte et staging/preuves conservés, sans annoncer ready.

La reprise se fait dans un **nouveau HOME/cache privé**, pas en modifiant les
fixtures des trials échoués. Une éventuelle réparation d'un cache appartenant
à l'opérateur serait une opération distincte, explicite, non livrée ici.

## Révision explicite au moteur : décision de compatibilité

API SentenceTransformer compatible avec `revision=...`, mais ce paramètre
n'existe pas dans notre wrapper actuel : l'ajouter change `embed.py`.
`Embedder.encoding_profile` inclut le SHA-256 de ce fichier ; le profil est
comparé au profil historique et incorporé aux clés des embeddings SQLite
(`memory.py:390–445`). Le chargeur ne peut pas changer tout en prétendant
conserver cette attestation. Utiliser un chemin de snapshot à la place du
model_id changerait aussi l'identité/cache du modèle.

**Différé pour RC v5**, avec signalement explicite à Claude ; ce point n'est
pas présenté comme réalisé. Le SHA du fichier réel est identique entre le
runtime 4.0.5 qualifié et la candidate :

`encoder_source_sha256` est consigné dans la preuve locale `source-proof.json`.

Le nouveau correctif ne change ni `embed.py`, ni config, ni poids/révision,
ni device/dtype/batch, ni namespace. Le quickstart vérifie déjà la révision
réellement chargée contre le catalogue dans `_ready` avant configuration des
clients : les nouvelles refs strictes renforcent cette défense. Un paramètre
de révision au wrapper nécessite sa propre qualification sur copie, décision
sur la réutilisation des embeddings et activation séparée.

## Preuves / commandes

Test modelfree : faux `modules.json` provisionné avec la ref produite ; vrai
HF cache reader appelé avec `revision=None` et `main`, `HF_HUB_OFFLINE=1` ;
chemin du snapshot attendu, absence d'import Torch/SentenceTransformer.
Test bytes exacts, refus de quatre suffixes sans modification du cache,
ref concurrente préservée. Aucun réseau/poids/inférence par Codex.

Bundle nommé `release/4.0.6-candidate-v5` au-dessus de `125c049`.
Claude rejoue full + artefacts + résolution réseau dans les dossiers **v5**,
puis trial v3 sur le **nouveau wheel exact** ; commandes à jour dans
`docs/releases/v4.0.6.md`. Préserver tous les rapports v4 et les fixtures
privées, sans publication/bascule. Le succès du prochain trial reste à prouver.
