# P-A — correction minimale de l'admission protégée, 13 octobre 2026

## Candidat figé

**ceedb5cfdfd0d61929276dbcd1417ea077044412**, parent 177eedb.
Un seul fichier moteur modifié : `brief_selection.py` (24 lignes ajoutées,
6 retirées). Version toujours 4.0.6.dev0, production 4.0.5 inchangée.
Commit suivant : runner/liaison de seal et documentation, pas de changement
du moteur figé. Le nouveau runner ne choisit jamais HEAD implicitement.

La représentation de couverture était réduite seulement si la frame vide
ne tenait pas. Une frame vide pouvait donc tenir tandis que la même frame
avec un contenu protégé faisable à compteurs réduits était refusée.

Pour chaque variante de contenu protégée ou expansion explicite, le sélecteur
essaie maintenant les formes existantes de couverture : détail par kind,
totaux nommés, puis totaux positionnels. Même serializer/counter, mêmes avis,
mêmes populations. Il garde une réduction **uniquement quand la proposition
complète tient**, avec sa réserve prospective de relais. La réduction reste
monotone ; un essai infaisable ne dégrade pas globalement la représentation.

Ordre full → phrase complète → référence inchangé, fallback référence seule
inchangé. Aucun texte coupé, rang A3, limite 40/16/10, partage 30 %/20 %, seuil
200/600/1500, budget 6000 octets, filtre ou paramètre P-A changé. Les contraintes
dont le texte ne tient pas conservent leurs avis d'omission/référence propres
au contrat existant ; une référence seule ne satisfait pas un besoin de texte.

## Preuves locales, développement seulement

- V5-VAL-038 est désormais un cas **dev révélé** : 31 faits, `z-crit`,
  « Never replay an uncertain write. », tags constraint+critical, budget 200.
  Ancien sélecteur 177eedb : zéro unité malgré la faisabilité mesurée par oracle.
  Nouveau : texte entier présent, compteurs `[31,31,1,0,30,0]`, avis
  coverage_detail_omitted conservé, enveloppes serveur/relais ≤200 tokens.
- Oracle indépendant + tests positif full, positif phrase complète synthétique,
  négatif sans contenu faisable/omission explicite, non-protégé inchangé,
  vrai handler/relais logiciel hash sans encodage, répétition déterministe.
- **303 tests brief/oracle verts** (16,75 s), puis **63 tests runners/oracle
  verts** (5,76 s), Ruff ciblé/diff --check verts. Pas de modèle ni TCP local.
- **12/12 enveloppes exactes** qualifiées localement au runner 9e0df58,
  moteur byte-identique à ceedb5c : max 1498 tokens / 4119 octets dans ce jeu
  synthétique (distinct du P-A 10k). Lints de qualification verts : scopes
  src/tests/scripts avec pyproject racine, puis E9,F sur src/tests/v4/scripts.
- Les inputs v2, leur gold et leurs résultats n'ont pas été lus/exécutés.
  V1 reste FAIL 24/40 ; attribution exploratoire 15/16 au gold/infaisabilité
  rapportée par Claude, jamais substituée au verdict officiel.

A3 accepté par Claude : change/update ET provenance autre client. Aucun
curseur temporel ajouté. B3/B2 → B4/B5 → B9 et RC attendent la gate v2.

## Isolation du test d’entrée worker — reprise du 13 octobre 2026

Le run `407-protected-fallback-full-claude-v1` reste **REFUSÉ** : quatre
échecs v3, provoqués par une fuite du chemin cl100k depuis un test précédent.
Le test appelait le CLI `main()` dans le processus pytest : ses mutations
directes de `os.environ` échappaient au monkeypatch du registre seulement.

Correctif exclusivement dans les tests : invocation sous une copie restaurable
complète de l’environnement (`patch.dict`) et un contexte monkeypatch local
pour argv/worker. La restauration s’applique aussi aux exceptions. Quatre
régressions couvrent worker→consommateur et consommateur→worker, avec succès
ou exception ; elles vérifient les valeurs héritées, les clés initialement
absentes, le registre et le chemin du tokenizer. Aucun changement du CLI,
du moteur, des paramètres P-A, des inputs, des seals ou du candidat ceedb5c.

Preuves locales, séquentielles, sans modèle/réseau/TCP :

- `pytest tests/test_brief_v5_measurement.py tests/test_brief_v5_validation_runner.py` :
  **50 passed**, 6,17 s ;
- mêmes fichiers dans l’ordre inverse : **50 passed**, 6,53 s ;
- les quatre cas réels exact/negotiate × fdd60cb/324aa68 passent dans chaque
  ordre avec la table cl100k provisionnée ;
- les deux scopes Ruff de qualification et `git diff --check` passent.

Les logs/JUnit locaux sont conservés dans le worktree persistant
`.local-qualification/worktrees/407-test-env-fix-v1/.local-qualification/`
(`order-worker-first.*`, `order-consumer-first.*`). La qualification complète
avec ses 28 TCP reste à Claude. Ne jamais remplacer les rapports refusés.

## Claude — qualification séquentielle, sorties fraîches

Importer le bundle nommé release/4.0.7, arbre propre, sans édition concurrente.
Les paramètres de campagne/gold v2 doivent rester scellés avant réception du
correctif. Ne pas ouvrir le gold à Codex. Les sorties de v1 sont conservées.

```sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1 MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-protected-fallback-full-claude-v2
PYTHONPATH=src:v4/src:v4 "$PYTHON" -B scripts/qualify_407_a1.py \
  --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-protected-fallback-exact-claude-v2
nice -n 15 "$PYTHON" -B scripts/qualify_brief_v5_performance.py \
  --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-protected-fallback-pa-10k-claude-v2
```

Le run full garde ses 28 TCP attendus, aucun ajouté par ce correctif. Le
performance runner atteste le HEAD du bundle : son moteur est identique au
candidat ceedb5c (le vérifier avec git diff avant lancement), baseline fdd60cb
inchangée. Nice 15, concurrence 1, disque opérateur 3 Go/5 Go défaut, swap
8,5 Go. Aucune bascule, publication, réseau externe, modèle ou garde contournée.

## Liaison explicite à V5-VAL v2, sans changer les seals

Le runner conserve son défaut v1 324aa68. Pour le nouveau candidat, fournir
`--candidate-commit` avec SHA complet ET `--candidate-binding` lié aux empreintes
des inputs et du manifeste. Il rapporte honnêtement une différence avec
l'ancrage du manifeste, sans le réécrire ni prétendre que les moteurs sont
identiques. La liaison est une déclaration opérateur, pas une signature.
Les deux schémas sont toujours audités avant tout seed. 40 cas obligatoires,
IDs ordonnés V5-VAL-001…040 ou V5-VAL2-001…040 via un choix explicite.

Les chemins/seals v2 ne sont pas connus de Codex : définir les variables
ci-dessous depuis les fichiers scellés de Claude, sans transmettre leur contenu.
L'amendement negotiate/unavailable doit être lié au nouveau SHA des inputs.

```sh
INPUTS="<chemin du JSONL v2 sans expect>"
MANIFEST="<chemin du manifeste v2 scellé>"
AMENDMENT="<chemin de compatibility_v2 lié aux inputs>"
CASE_PREFIX="V5-VAL2-" # ou V5-VAL- selon les IDs scellés ; aucun ID réécrit
BINDING=".local-qualification/v5-candidate-binding-v2-env-fix.json"
export INPUTS MANIFEST BINDING
"$PYTHON" -B - <<'PY'
import datetime, hashlib, json, os
from pathlib import Path
manifest_path = Path(os.environ['MANIFEST'])
manifest = json.loads(manifest_path.read_text())
inputs_sha = hashlib.sha256(Path(os.environ['INPUTS']).read_bytes()).hexdigest()
assert inputs_sha == manifest['inputs_sha256'], 'v2 input seal mismatch'
binding = {
    'inputs_sha256': inputs_sha,
    'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    'candidate_commit': 'ceedb5cfdfd0d61929276dbcd1417ea077044412',
    'baseline_commit': 'fdd60cb0c9fee95f2f32471e93d77a2873a3d946',
    'bound_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'bound_by': 'Claude',
    'reason': 'Inputs v2 sealed before delivery; protected-content coverage fallback agreed.'
}
with Path(os.environ['BINDING']).open('x') as out:
    json.dump(binding, out, indent=2)
PY
INPUTS_SHA=$("$PYTHON" -B -c 'import json,os; print(json.load(open(os.environ["BINDING"]))["inputs_sha256"])')
nice -n 15 "$PYTHON" -B scripts/run_brief_v5_validation.py \
  --inputs "$INPUTS" --manifest "$MANIFEST" --inputs-sha256 "$INPUTS_SHA" \
  --amendment "$AMENDMENT" --case-id-prefix "$CASE_PREFIX" \
  --candidate-commit ceedb5cfdfd0d61929276dbcd1417ea077044412 \
  --candidate-binding "$BINDING" --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-pa-validation-v2-audit-claude-v2
```

Après audit complet et vérification des identités/arguments, même commande
avec `--run` et sortie neuve :

```sh
nice -n 15 "$PYTHON" -B scripts/run_brief_v5_validation.py --run \
  --inputs "$INPUTS" --manifest "$MANIFEST" --inputs-sha256 "$INPUTS_SHA" \
  --amendment "$AMENDMENT" --case-id-prefix "$CASE_PREFIX" \
  --candidate-commit ceedb5cfdfd0d61929276dbcd1417ea077044412 \
  --candidate-binding "$BINDING" --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-pa-validation-v2-collect-claude-v2
```

Claude score avec son gold privé et la gate originale, garde les 40 cas et les
échecs. Le runner ne score rien. Pas d'exclusion ou de réglage après résultats.
