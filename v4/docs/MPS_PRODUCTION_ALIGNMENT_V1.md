# Rectification : backend de production et compatibilité, 2026-10-10

## Décision corrigée

La recommandation précédente « CPU = statu quo » était erronée. Claude a
constaté `Config.device=auto`, aucun `MEMORY_V4_DEVICE` dans launchd et le driver
Apple GPU chargé dans le service. La lecture du paquet installé confirme que
`auto` choisit MPS si disponible. **Le statu quo actuel est MPS**, pas CPU.
La candidate `49610ae` aurait changé ce backend sans qualification de production.
Le nouveau bundle **rétablit `device=auto`** ; aucun service/plist/runtime de
production n'est modifié. Ce rétablissement annule seulement le changement de
défaut CPU, pas les autres changements de la candidate.

Recommandation : conserver le service actuel ; après GO de Romain, préférer
un **pin explicite `MEMORY_V4_DEVICE=mps`**, dans le même paquet/environnement,
snapshot et paramètres d'encodage. Sur ce Mac disponible, le device résolu
reste MPS. Le pin évite une sélection CPU silencieuse après indisponibilité
du GPU ; une indisponibilité doit provoquer un échec visible. Aucune variable
de précision/longueur/batch n'est changée par ce pin. Une modification vers
CPU est une nouvelle décision de performance et d'embeddings, exigeant GO
et qualification sur copie ; elle ne constitue pas un retour au statu quo.

Le facteur ≈22× concerne les paires BEAM 100K dans le protocole précédent,
CPU natif/batch4 contre MPS float32/batch4. Il ne mesure pas le ralentissement
de chaque `memory_chat_save`, ni un ratio apparié avec le service natif/batch32.
Le sens du risque d'une bascule CPU est réel ; son ampleur par requête dépend
des textes, du démarrage, de l'extraction et des autres coûts.

## Profil mesuré et profil actif : distinction obligatoire

Lecture Codex sans importer le moteur installé ni encoder de texte :

| Champ | Service installé 4.0.2 | BEAM CPU historique | Bras MPS évalué |
|---|---|---|---|
| Device déclaré/résolu | auto / MPS selon constat Claude | cpu / CPU | mps / MPS |
| Modèle | Granite-97m-multilingual-r2 | même snapshot déclaré | même snapshot déclaré |
| Longueur configurée | 512 | 512 | 512 |
| Batch configuré | **32**, sans override plist | **4** | **4** |
| Précision de calcul | chargement par défaut du SDK, **non attestée** | bfloat16 attesté dans le pont récent | float32 imposé et attesté |
| Watermarks launchd/essai | 0,4 / 0,3 | sans objet | 0,25 / 0,20 |
| Profil de vecteurs persisté en production | **absent** | protocole archivé | protocole archivé |

Le code installé ne fournit pas encore `embed_dtype` : une variable ajoutée
ultérieurement dans la candidate ne doit pas être supposée active dans ce
paquet. « Native » décrit une politique de chargement, pas une preuve de
bfloat16 ou float32. Le dtype des tableaux `.npy` n'atteste pas le dtype de
calcul. Le driver mappé ne démontre pas le backend historique de chaque
vecteur, ni la précision effective de tous les paramètres.

Le résultat 20-clusters reste **équivalence non démontrée** : ΔRecall +0,000619,
IC97,5 % [−0,008313 ; +0,013538] ; ΔMRR +0,000656 [−0,000315 ; +0,001917].
Il décrit ces deux bras spécifiques face à un CPU historique, pas une
équivalence avec le profil de production. Les résultats CPU antérieurs restent
publiés avec leurs configurations exactes ; LME-S utilisait en plus MiniLM/256.
Aucun score n'est recalculé ou renommé « non-infériorité » après observation.

## Vecteurs existants : constat et correctif de la candidate

Lecture seule de `~/.memory-os-v4/meta.json` : version4.0.2, modèle Granite,
dim384, **aucun `embed_compute_profile`**. La compatibilité complète des
vecteurs ne peut donc pas être certifiée à partir des métadonnées actuelles.
Le service actif n'est pas instancié dans un second processus pour cet audit.

Défaut trouvé dans la candidate : un ancien cache sans profil pouvait être
réutilisé en dtype `native`, puis recevoir le profil courant dans sa meta,
même si le backend avait changé. Le nouveau code ferme cette possibilité :

- JSONL : profil absent/incomplet ou différent ⇒ reconstruction complète des
  vecteurs dérivés avant recherches ; aucune adoption par simple estampillage.
- Le profil comprend device **résolu**, dtype configuré, longueur et **batch**.
  Modèle et dimension sont contrôlés séparément. Un ancien profil sans batch
  provoque donc également une reconstruction prudente, une fois.
- SQLite opt-in : l'empreinte de cache comprend maintenant aussi le batch ;
  device/dtype/longueur différents ne réutilisent pas les entrées précédentes.
- Pour un profil complet attesté, `auto→mps` avec device résolu MPS et autres
  paramètres identiques ne ré-encode pas. CPU, précision ou batch différents
  reconstruisent ou utilisent un cache distinct ; la réouverture identique
  réutilise les vecteurs et conserve les éléments métier.

**Conséquence opérationnelle : importer puis déployer cette candidate sur un
magasin ancien n'est pas neutre**, même après retour à `auto`. Le magasin de
production sans profil déclencherait une reconstruction au premier démarrage.
Qualification/reconstruction uniquement sur copie hors ligne dans le clone,
avec les gardes ; aucun nouveau moteur pointant vers la production. Conserver
la copie d'origine pour retour arrière. Un GO séparé et la qualification T05
du paquet réellement déployé sont requis avant toute installation/bascule.

Ces champs ne certifient pas une identité de poids ou de calcul à travers un
changement de snapshot, de versions torch/SDK ou d'implémentation d'indexation.
Ces changements demandent eux aussi une reconstruction/namespace distinct
et une qualification hors ligne ; ne pas utiliser un profil partiel comme
preuve générale de compatibilité. Une réindexation peut changer les floats
et rangs, notamment lorsque la forme des lots diffère ; aucune identité
numérique avec les vecteurs historiques inconnus n'est revendiquée.

## Suite d'évaluation recommandée : d'abord le contrat de production

Avant nouvelle campagne, figer source réellement installée, snapshot et
versions torch/transformers/sentence-transformers, paramètres résolus et
précision **effective** observée lors d'une exécution privée approuvée.
L'inspection du plist ci-dessous ne suffit pas à attester le dtype des poids.

Bras primaire futur : **profil encodeur fidèle à la production**, MPS,
chargement natif, longueur512, batch32, faits scalaires, autres réglages moteur
explicitement enregistrés. Le bras float32/batch4 reste une variante distincte.
Même extraction/packing/scorer pour les comparaisons ; publier les différences
inévitables avec l'application réelle (notamment citations sans LLM, dates
synthétiques BEAM et récence désactivée) plutôt que prétendre à une simulation
complète de tous les usages.

Les résultats CPU observés ne deviennent pas un témoin exact du nouveau
profil. Si une comparaison CPU/MPS est nécessaire, utiliser même source,
runtime, snapshot et batch32 dans les deux bras, en attestant les deux dtypes.
Le design de non-infériorité sur 100 nouvelles sources reste une proposition
non exécutable avant constitution des données et nouveau préenregistrement ;
les estimations de variance du lot float32/batch4 ne garantissent pas la
puissance du profil natif/batch32. Aucun modèle, corpus ou job n'est lancé ici.

Priorité produit : cohérence et traçabilité du profil actuel, puis exactitude
E2E sur jeu réservé. Maintenir MPS en service n'est pas déclarer un succès
scientifique ni « top1 ». Le prochain GO porte sur une procédure concrète de
pin/qualification ; aucune modification de launchd n'est fournie comme commande
à exécuter avant ce GO.

## Commandes Claude après import : tests et lecture seule

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
python3 -m pytest -q v4/tests
python3 -m ruff check --config pyproject.toml v4/src \
  v4/tests/test_encoding_precision.py scripts/verify_mps_reuse_result_v1.py
python3 scripts/verify_mps_reuse_result_v1.py .local-qualification/mps-beam-reuse-v1
python3 - <<'PY'
import json, plistlib
from pathlib import Path
root = Path('/Users/romain')
p = plistlib.loads((root / 'Library/LaunchAgents/com.romain.memory-os-v4.plist').read_bytes())
e = p.get('EnvironmentVariables', {})
print('explicit encoder env', {k: e.get(k) for k in (
    'MEMORY_V4_DEVICE', 'MEMORY_V4_EMBED_MODEL', 'MEMORY_V4_EMBED_DTYPE',
    'MEMORY_V4_EMBED_MAX_SEQ_LENGTH', 'MEMORY_V4_EMBED_BATCH_SIZE')})
m = json.loads((root / '.memory-os-v4/meta.json').read_text())
print('store metadata', {k: m.get(k) for k in (
    'version', 'embed_model', 'dim', 'embed_compute_profile')})
print('read-only; no model instantiated; effective model dtype not attested')
PY
```

Le vérificateur publie désormais `production_activation_performed=false` et
`production_state_inspected=false` : il n'effectue aucune bascule, mais ne
prétend plus décrire le backend réel avec `production_activated=false`.
Plan/rapport/prédictions historiques et empreintes restent inchangés.

Tests synthétiques uniquement : résolution auto GPU/CPU ; profil explicite ;
auto→MPS sans ré-encodage sur profils attestés JSONL/SQLite ; changement de
device ou batch sans réutilisation ; profil JSONL absent/incomplet reconstruit
une fois en natif, éléments métier/provenance conservés ; réouverture stable.
Aucun encodage neuronal, appel API, écriture magasin productif ou push.

Qualification locale : `pytest -q v4/tests --ignore=v4/tests/test_server_http_e2e.py`
donne **1027 passed, 2 skipped**, huit HTTP exclus, 3 warnings de dépendances,
36,21 s. Suite précision/cache ciblée : **16 passed**. Ruff, attestation du
rapport et diff check verts. Claude doit rejouer la suite entière, TCP inclus,
par la commande ci-dessus. Les fichiers utilisateur MCP restent inchangés.
