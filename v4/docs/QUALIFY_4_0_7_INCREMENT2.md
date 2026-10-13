# Incrément 2 — A2+A4, développement 4.0.6.dev0

Base : fdd60cb. Restauration du 13 octobre depuis le journal Codex persistant.
Scellés originaux : protocole 184b5e9, unités dev 74724a4 ; commits disparus,
paramètres restaurés byte-identiques au SHA attesté. Nouveaux commits de
restauration : 4845974 et 2cbbfe4. Gold des 40 validations gardé par Claude.
Production 4.0.5, aucune publication ou bascule. Pas de gain d'utilité mesuré.

## Livrable

Unités entières avec texte complet, première phrase conservatrice + référence,
ou référence seule si rien de complet ne tient. Tâches : champs complets ou
référence seule, pas résumé coupant next_action. Le texte source ne subit plus
les coupes [:480], [:len/2] ou le rognage du relais. Les unités portent des
spans relatifs au corps ; des marqueurs factices dans la mémoire ne deviennent
pas des unités. Métadonnées et références suivent les suppressions entières.
La lecture détaillée développe aussi les références de tâches, dans le scope.

Plafonds initiaux blocs 30 % / tâches 20 % du contenu restant, bytes ET tokens,
unités protégées traitées avant ces plafonds, mémoire puis capacité libre
réaffectée/upgrades. Aucun quota obligatoire ou sous-remplissage volontaire.
La place de l'avis unresolved_requests est testée dans une enveloppe prospective
exacte. Les impossibilités/refs critiques seules sont explicites.

Body legacy sans spans : préserver entier si possible ; sinon supprimer le
corps opaque avec avis, jamais inventer ses frontières. L'ancien relais peut
toujours fonctionner en legacy, mais seul le nouveau assure ce rebudget whole-unit.

## Preuves historiques (12 octobre, non transférées comme qualification nouvelle)

256 tests ciblés verts (A1, A2/A4, 100 anciens cas ×2 backends) ; lint ciblé et
diff --check. Counter structurel dans ces unités : exact cl100k via commande
distincte. Une erreur de gold dev DEV-11 (deux phrases attendues) est amendée
et tracée ; paramètres/moteur et gold caché inchangés. Aucun test physique TCP
ni mesure de latence 10k annoncé comme exécuté par Codex.

## Claude : qualification séquentielle, sorties neuves

Après import du bundle incrément 2, arbre au HEAD propre, mêmes dépendances
qualifiées/interpréteur que l'incrément 1 ; aucune qualification concomitante
aux modifications d'une autre session.

```sh
PYTHON=$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python
export PYTHONDONTWRITEBYTECODE=1
export MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-recovered-increment2-full-claude-v1
PYTHONPATH=src:v4/src:v4 "$PYTHON" -B scripts/qualify_407_a1.py \
  --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-recovered-increment2-exact-claude-v1
```

Les 28 cas TCP existants vérifient aussi le format/budget final réel. Les tests
spécifiques A2/A4 sont inclus dans la suite. Tous résultats refusés conservés,
reprise justifiée dans nouvelle sortie. Prochain incrément : A3+A7 ; mêmes
paramètres P-A, pas de gold tenu hors réglage consulté.
