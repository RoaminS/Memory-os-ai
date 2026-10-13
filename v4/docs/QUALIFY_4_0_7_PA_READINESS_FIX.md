# P-A — sources figées et readiness, 13 octobre 2026

Base : b555c96. Correctif du banc seulement ; moteur, paramètres et protocole
inchangés. Production reste 4.0.5.

## Cause et correction

Le run `407-recovered-increment3-pa-10k-claude-v2` a conservé 100 mesures
serveur baseline mais aucun résultat complet transport. L'export baseline
contenait seulement `v4/src` : sans `pyproject.toml`, le chargeur d'identité
ne reconnaît pas un arbre source et exige un descripteur installé absent.
L'environnement déclarant le commit est donc ignoré ; `/ready` refuse avec
`build_unqualified`. Le timeout n'était pas un problème de performance.

Les **deux bras** sont désormais exportés via `git archive <commit> v4/src
pyproject.toml` : baseline fdd60cb et candidat au HEAD propre, avec chacun son
SHA Git complet dans `MEMORY_V4_BUILD_COMMIT`. Aucun descripteur fabriqué,
aucun contournement du serveur : `identity_source=env_declared` est le mode
source opérateur existant, déclaratif et non signé.

Chaque worker vérifie le vrai handler authentifié `/ready` en ASGI sur son
moteur privé chargé, avant toute mesure chronométrée. La phase transport
vérifie de nouveau HTTP 200, `ready=true`, le commit exact et `env_declared`
avant ses warmups/mesures. Une réponse 200 incohérente refuse immédiatement.
Les preuves `readiness.json` sont conservées avec les mesures et les logs,
y compris en cas d'échec ultérieur. Les mesures serveur et transport restent
deux séries distinctes ; les vérifications ne sont pas dans leurs chronomètres.

## Protocole et preuves

Protocole inchangé : SHA-256
`0a1df0d2b6832b8cedf2642616e27eed46b641cfdac06d8eb6c1872466066b4f`.
10k éléments, 10 warmups/100 appels par chemin, ordre baseline puis candidat,
p95 serveur ≤250 ms / transport ≤750 ms, enveloppe ≤6000 octets/1500 tokens.
La variable de registre est toujours absente dans les deux bras. Aucune
reprise/import des mesures partielles v2 dans le nouveau résultat.

Tests légers : 16 verts, dont les vrais `/ready` des exports fdd60cb et HEAD
sans Git ni descripteur, le contrôle négatif sans pyproject pour chaque bras,
et les refus HTTP/ready/commit/source d'identité incorrects. Hash seulement,
aucun modèle neuronal, socket TCP ou banc 10k lancé dans ce run.
Ruff ciblé et `git diff --check` verts. La mesure physique reste à Claude.

Les sorties v1/v2 sont conservées, notamment les 100 mesures serveur v2.
La v2 est un run refusé, pas une qualification globale de performance.

## Claude — après import, sortie v3 neuve

Depuis le clone principal propre, aucune qualification pendant une modification
de son arbre. Même environnement persistant qualifié, pas de téléchargement.

```sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1
export MEMORY_V4_MIN_FREE_BYTES=3000000000
nice -n 15 "$PYTHON" -B scripts/qualify_brief_v5_performance.py \
  --table "$HOME/.config/memory-os/cl100k_base.tiktoken" \
  --output .local-qualification/407-recovered-increment3-pa-10k-claude-v3
```

Garde : seuil disque opérateur 3 Go (5 Go sans override), swap 8,5 Go,
bras séquentiels et arrêt du groupe privé sur garde/timeout. Qualification :
`ok=true`, workers complets, identités attendues dans les deux readiness par
bras, zéro appel neuronal et tous les seuils respectés. Échec : conserver,
pas de relance dans le même dossier. B3/B2 → B4/B5 → B9 attendent ce résultat.

README v2 : Claude a accepté les quatre amendements de
`REVIEW_README_4_0_7_V2.md` et prépare le draft v3. Intégration en
long_description seulement à la préparation de la RC ; conserver le statut
« 4.0.6 publiée / 4.0.7 en préparation » jusque publication effective.
