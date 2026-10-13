# P-A — correction du lanceur, 13 octobre 2026

Base : 9bd4f73 (incréments 2–3 récupérés). Production inchangée : 4.0.5.

## Cause et périmètre

Le premier P-A 10k a refusé avant toute mesure complète : le lanceur écrivait
`MEMORY_V4_PROJECT_REGISTRY=''`. Sur fdd60cb, `ProjectRegistry.load` traite
cette chaîne comme un chemin vers le répertoire courant. Ce n'est pas un
registre absent ; l'ouverture échoue avec `IsADirectoryError`.

Le lanceur enlève désormais la variable de son environnement, de celui de
chaque worker et des enfants serveur/relais. Les deux bras utilisent les mêmes
labels synthétiques littéraux. Aucun registre utilisateur n'est chargé.
Le moteur n'est pas modifié : ce correctif ne décide pas du contrat produit
d'une variable de registre vide.

Le protocole reste scellé : SHA-256
`0a1df0d2b6832b8cedf2642616e27eed46b641cfdac06d8eb6c1872466066b4f`.
Baseline fdd60cb, 10 000 éléments, 10 warmups puis 100 appels par chemin,
ordre baseline/candidat et seuils p95 serveur 250 ms / transport 750 ms inchangés.
Le candidat sera le HEAD propre importé ; le rapport en attestera le commit.
Les sources produit sont inchangées depuis 9bd4f73. Les 40 scénarios/golds
de Claude restent séparés. Aucun résultat d'utilité n'est annoncé.

## Preuve locale et limites

`tests/test_brief_v5_measurement.py` : **10 passed en 1,28 s**.
Cas variable absente/vide/renseignée, environnement parent conservé, entrée
worker des deux bras, et vrais chargeurs Config/ProjectRegistry exportés de
fdd60cb et 9bd4f73. Aucun encodeur, modèle ou serveur lancé.
Ruff ciblé et `git diff --check` verts.

Les preuves Claude précédentes (675 v3, 1979 v4, 28 TCP et 12 exactes) restent
attribuées à 9bd4f73. Pas de nouvelle mesure P-A physique dans ce run.
Le rapport refusé v1 et ses journaux sont conservés ; ne pas reprendre dans
son dossier et ne pas recycler ses mesures partielles.

## Claude — nouveau run séquentiel

Après import du bundle nommé `release/4.0.7`, exécuter depuis le clone principal
au HEAD propre, sans autre session qui modifie l'arbre. Réutiliser le runtime
persistant qualifié et la table explicite, sans téléchargement.

```sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1
export MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
nice -n 15 "$PYTHON" -B scripts/qualify_brief_v5_performance.py \
  --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-recovered-increment3-pa-10k-claude-v2
```

Une seule campagne, bras séquentiels, modèles hash seulement. Le garde conserve
le seuil disque opérateur borné à 3 Go (5 Go sans override), swap 8,5 Go, et
l'arrêt des processus privés au refus/timeout. Critère : rapport `ok=true`,
deux workers complets, zéro appel neuronal, enveloppes ≤6000 octets/1500 tokens
et seuils p95 respectés. Échec : conserver le dossier, pas de reprise dedans.
B3/B2 → B4/B5 → B9 attendent la qualification P-A ; ce bundle ne les commence pas.
