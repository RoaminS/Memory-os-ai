# Incrément 2b — correctif ciblé de la découverte MCP pendant une panne

Base474b09b ; ref `refs/heads/feat/404-increment-2b` ; code31ff798.
Aucune version publiée, bascule, modification de production, API ou inférence.

## Diagnostic et choix

Le rapport Claude `.local-qualification/404-increment2-claude-v1/report.json` est
**ok=false** :1246 tests passés,2 TCP échoués sur1248, un slow neural exclu.
Les deux échecs sont les scénarios partial-pending et committed-completed.
Ils ne prouvent pas une erreur dans les reçus : `_validate_tool_result` du SDK
appelle `list_tools`, dont le handler remontait au serveur déjà arrêté. Le statut
local devenait donc inutilisable lors de sa validation MCP.

Le correctif reprend le principe du patch de Claude, avec trois précisions :

1. Préchargement après initialize, attente bornée à5s, catalogue figé par session.
   Les listes suivantes ne tentent **aucun** RPC amont, même avant un fallback.
2. `memory_request_status` est toujours exposé avec le schéma local, sans reprendre
   un éventuel outputSchema serveur incompatible avec l'inventaire du relais.
3. En cas d'échec initial : seul le statut local est annoncé. Copies profondes
   des descripteurs ; changements amont adoptés à la session suivante.

Pas de rejeu de mutation ajouté. Les vérifications d'identité, de jeton et les
journaux restent inchangés. La borne5s concerne le préchargement ; elle ne vaut
pas qualification générale de L4 ni annulation garantie d'une lecture amont.

## Preuves et limites

`test_relay_tools_cache.py` couvre le cache, les copies, l'échec initial, le
préchargement silencieux borné et la propagation d'une annulation parent.
Deux cas utilisent le **vrai ClientSession et le vrai serveur MCP du relais**,
connectés par flux en mémoire : le client n'a jamais listé les outils, découvre
un orphan sending après panne, valide la réponse et reliste plusieurs fois.
Un seul appel amont est observé : le préchargement. Aucun TCP ni moteur/model.

Le résultat rapporté par Claude dans une copie isolée (3/3 TCP avec son patch)
n'est pas transposé en qualification de ce bundle. Les5 échecs liés à l'archive
sans historique ne sont pas supprimés de ses preuves. La suite locale finale du clone complet donne **1238 tests passés**, zéro
échec/skip, un slow neural exclu intentionnellement ; lint projet/v4 vert.
Les86 tests ciblés relay/ops passent aussi. Les empreintes sont enregistrées dans
`404_INCREMENT2B_EVIDENCE.json`. Les15 TCP restent à rejouer sur cette ref.
Mesure fsync rapportée par Claude :p50=0,089ms, p95=0,124ms, max=0,181ms,
100 échantillons ; ce correctif ne change aucune écriture de journal.

## Import et qualification par Claude

Dans le clone d'intégration disposant de474b09b (aucun reset ni push) :

```sh
git fetch /Users/romain/Documents/Codex/2026-10-09/v/work/implementation/404-increment2b.bundle refs/heads/feat/404-increment-2b
git merge --ff-only FETCH_HEAD
```

Pour le Git privé du clone Codex, remplacer `git` par
`git --git-dir=.local-qualification/lexical-dev-git`.
Le bundle possède un fichier SHA-256 adjacent ; sa base requise est474b09b.

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
PATH=/opt/homebrew/bin:$PATH PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python scripts/qualify_404_increment2.py --suite full --output .local-qualification/404-increment2b-claude-v1
```

Le runner est inchangé, conserve les gardes disque/swap et refuse les tests
échoués/ignorés/incomplets. Le répertoire de preuve est **nouveau** : ne pas
réutiliser, déplacer ou effacer `404-increment2-claude-v1`. Les serveurs TCP, jetons,
magasins hash et journaux restent jetables et séparés de la production. Aucun
besoin de modèle, téléchargement ou nouvelle mesure neural. Le venv historique
sert seulement d'interpréteur en lecture seule, sources candidates via PYTHONPATH.

## Accords et incrément suivant

- **A4 contresigné par Claude** le2026-10-11 : rotation automatique du journal
  actif, entretien séparé des journaux clos sous lock, adoption en lecture seule,
  aucune entrée incertaine purgée. Aucun job de production installé.
- **L11 v2 relu : R1–R8 intégrés.** Accord sur le texte amendé ; protocole encore
  non gelé. Restent le budget quota/temps approuvé par Romain, le choix du notateur,
  les plafonds confirmés, le fichier de cas/grilles et les deux empreintes de gel.
 94 reprises ne constituent pas une autorisation d'exécution anticipée.
- Prochain incrément dans l'ordre convenu : **L4 restant → L7a → L5 → L1a**.
  Il devra notamment distinguer disponibilité du catalogue et disponibilité
  effective du serveur ; aucun de ces lots supplémentaires n'est déclaré terminé
  par ce correctif ciblé. L14 reste en pause.

La candidate ne peut être publiée/basculée sans qualification de release sur
copie et les GO correspondants de Romain.
