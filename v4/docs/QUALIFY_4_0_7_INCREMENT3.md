# Incrément 3 — A3+A7, développement 4.0.6.dev0

Base restaurée incrément 2 : **cff2958** (original perdu 1bbab24).
Même ligne release/4.0.7 ; protocole original 184b5e9 restauré à 4845974,
fichier JSON/SHA inchangé. Huit fichiers moteur/serveur/relais sont
byte-identiques aux empreintes de la preuve source c84c9f3 du 12 octobre. Aucun gain d'utilité annoncé,
aucun réglage sur le gold des 40 scénarios de Claude. Production reste 4.0.5.

## Contrat livré

- Priorité : expansion explicite, contrainte taguée constraint+critical,
  tâches blocked puis open non périmées, décisions/contraintes, procédures,
  changements d'autre client déclarés, mémoire ordinaire, tâches périmées.
  Focus lexical, importance, date et ID départagent ; aucune validité inférée
  depuis la date. La priorité précède le pool de 40 : une décision ancienne
  ne disparaît plus derrière 40 faits récents. Les blocs demeurent contextuels.
- Autre client seulement depuis source `client:<id>` ou un unique tag de même
  forme, sans déclaration contradictoire. Tags change/update obligatoires.
  Caller inconnu = aucun boost. Champ optionnel client_id ; relais le remplit
  depuis initialize.clientInfo, écrase l'override du modèle, uniquement si le
  schéma amont l'accepte. C'est déclaratif, jamais authentification. Pas de
  réécriture d'historique ou de provenance déduite du contenu. Les futurs
  captures de Claude doivent utiliser les IDs exacts des clients, via tags,
  et fournir budget_tokens/focus_query si adaptés au hook.
- `coverage.by_kind` et `coverage.totals` : eligible, pool, content,
  reference_only, omitted ; outside_pool au total. Éligibles = scope/validité/
  sensibilité déjà filtrés ; tâches open/blocked du projet + expansions
  explicites, comptées via SQL avant limite 10. Une phrase complète compte
  comme content, pas comme preuve de couverture sémantique complète.
  Référence seule = catégorie distincte. Omitted porte sur le pool ; hors
  pool est distinct. Pas de double compte par parent/focus/expansion.
- Relay rebudget : compteurs recomputés à partir des unités restantes.
  Sous petit budget : détail retiré, puis totaux positionnels exacts avec
  notice coverage_detail_omitted ; ordre documenté dans le protocole.
  Saturation/omission critique explicites, y compris hors pool.
- Aucun modèle sur le chemin brief ; variantes/parts 30 %/20 % et C4, profil
  d'encodage, cache, SQLite/reçus/ETags inchangés. 23 outils, pas d'outil ajouté.

## Preuves historiques et limites (12 octobre, non requalifiées par restauration)

278 tests ciblés verts ; 50 tests WorkState+A3/A7 avec table explicite verts.
670 tests v3 verts. La première suite globale sans table a refusé trois tests
L7b (tokenizer_unavailable) ; la reprise avec table explicite a été interrompue
par le garde disque à 2,98 Go, v4 incomplète : **pas de qualification globale**
sur la base de ces runs. Journaux et rapports sont conservés. Lint projet/v4
et diff --check verts. La preuve exacte finale est une étape distincte.

Preuve exacte **c84c9f3** : 12/12 envelopes direct/relais logiciel ×2 formats
×3 budgets verts, cl100k_base qualifié, compteurs finaux vérifiés ; cela ne
qualifie pas TCP. Dans le run v4 interrompu, un F correspond à la matrice
SIGKILL SQLite selon l'ordre JUnit précédent ; traceback final indisponible
après arrêt du groupe. Rejoué isolément sans changer son code : **1 passed,
43,6 s**. Cause non attribuée, pas de classement automatique « faux positif ».
Un troisième lancement global est refusé avant tout test à **2,22 Go** ;
ne pas insister sans espace disponible. Configs clients inchangées dans les
trois rapports. Les fixtures des quatre anciens runs terminés A1/B7 ont été
archivées sans perte (contenus/modes/liens/types/membership vérifiés), SHA/JUnit
préservés dans RETAINED_EVIDENCE_ARCHIVES.json ; aucun fichier utilisateur
ou magasin production n'a été touché par ce rangement privé.

P-A physique 10k fourni mais **non exécuté ici** : 2 sources séquentielles,
baseline fdd60cb puis candidat, même runtime/machine/seed/requêtes, 10 warmups
et 100 appels par chemin. Serveur et relais/TCP loopback privés, hash seulement,
encode interdit après startup, p95 ≤250/750 ms. Guard disque/swap mesuré,
groupe de processus arrêté au timeout/guard, magasins synthétiques nettoyés,
journaux/mesures intermédiaires conservés. Pas de latence neuronale ou gain
utilisateur déduit de ce banc. Les 40 golds restent séparés et secrets jusqu'au
gel du candidat ; critère ≥38/40 et sécurité zéro inchangé.

## Restauration du 13 octobre

Code restauré depuis les appels du journal persistant, pas recréé depuis les
résultats. Les anciens environnements/bundles sont perdus ; les anciens
rapports ne constituent pas une qualification des nouveaux commits. Arbre
persistant wt-407 ; bundles et qual-envs persistants dans memory-os-local.
Les paramètres et le gold caché restent inchangés. Les nouvelles preuves
seront datées séparément ; la qualification physique reste à Claude.

## Claude — commandes séquentielles, sorties fraîches

Importer d'abord le bundle incrément 2, puis celui-ci. Arbre au HEAD propre.
Ne pas qualifier pendant une modification par une autre session. Vérifier
espace libre ≥seuil opérateur avant lancement ; ne pas supprimer les runs refusés.

```sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1
export MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-recovered-increment3-full-claude-v1
PYTHONPATH=src:v4/src:v4 "$PYTHON" -B scripts/qualify_407_a1.py \
  --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-recovered-increment3-exact-claude-v1
nice -n 15 "$PYTHON" -B scripts/qualify_brief_v5_performance.py \
  --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-recovered-increment3-pa-10k-claude-v1
```

Les 28 TCP restent les mêmes IDs : le cas L1a est étendu à provenance/caller,
ordre other-client, compteurs et négociation finale. Gold : format JSONL dans
BRIEF_V5_P_A_PROTOCOL.md, expect caché ; Claude scelle les 40 cas+gold+manifest
avant ouverture au candidat, rapporte tous cas et impossibilités. Nouveau
paramétrage après observation = nouvel amendement/exploratoire, jamais masquer
un échec. A9/copie/T05/GOs restent nécessaires à RC, publication et bascule.
