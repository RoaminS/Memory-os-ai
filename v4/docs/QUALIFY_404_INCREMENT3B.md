# Qualification candidate 4.0.4 — incrément 3b

Base **f88a41d** (3a qualifié par Claude), branche `feat/404-increment-3b`.
Code L1a initial **5d6e555**, L5 **b164bc0**, gel préalable **cd4ad41**.
Pas de publication, bascule, inférence, API, téléchargement ou écriture directe
dans le magasin de production. Le numéro de package n'est pas encore promu 4.0.4.

## Livrables / état honnête

- L5 : capacités dans ready/status, attentes doctor et observations client
  expurgées (distinction configuration / contrôle récent / connexion vivante).
- L1a : compact MCP par défaut, enveloppe complète bornée, validité homogène,
  références et blocs développables, mode détaillé explicite, compteur local exact
  requis, rebudget après notices du relais, erreurs bornées.
- 100 cas amendés scellés avant code :
  `8d005cf0fa6de55ec75e1f023a4fa02f802a99dc73589fe5e38241914baa2209`.
  Original e93e9662… et amendements conservés. Le manifeste n'a pas été modifié
  en fonction des sorties ; les corrections portent sur le code/tests techniques.
- **1488 tests légers verts**, 0 échec/erreur/skip, 1 slow explicitement désélectionné,
  lint vert, baseline hash. Rapport `.local-qualification/404-increment3b-unit-final`.
- **PAS encore qualifié au compteur exact ni en TCP.** Les tests structurels
  utilisent un double identifié ; le vrai préflight cl100k a refusé avant mesure
  (table absente, 0 cas), `.local-qualification/404-increment3b-exact-preflight`.
  Ce refus est attendu et ne constitue ni un test de budget réussi ni un benchmark.
- Un premier passage ciblé a révélé qu'un ancien test de résolution pouvait tenter
  d'ouvrir le magasin par défaut : sandbox l'a refusé avant écriture. Correction :
  résolution froide sans moteur ; test avec `get_engine` interdit. Selfcheck teste
  désormais detailed explicitement ; la voie compact a sa qualification distincte.

## Commandes Claude après import du bundle nommé

Depuis le clone contenant ce bundle (Git privé si utilisé dans le clone Codex) :

```sh
git --git-dir=.local-qualification/lexical-dev-git bundle verify 404-increment3b.bundle
git --git-dir=.local-qualification/lexical-dev-git fetch ./404-increment3b.bundle refs/heads/feat/404-increment-3b
git --git-dir=.local-qualification/lexical-dev-git merge --ff-only FETCH_HEAD
```

Préparer un runtime **jetable**, sans pip/réseau ni modification des installations
existantes : Python 3.11 qualifié + dépendances existantes lues via .pth, tiktoken
0.12.0 déjà présent sous Homebrew. Le script refuse si ces prérequis manquent.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python \
  scripts/make_brief_test_runtime.py \
  --output .local-qualification/brief-test-runtime-claude-v1
```

**Seulement après GO explicite Romain pour cette acquisition** (question transmise,
aucun GO reçu lors de la livraison) : table officielle OpenAI, environ 1,7 Mo,
cap strict 2 Mo, SHA-256 vérifié, échec conservé en `.part`, pas de proxy/redirect.
Ce fichier n'est pas un modèle neuronal. Aucun téléchargement du package : celui-ci
est déjà présent localement. Si Claude possède déjà cette table exacte, copier
ses octets dans la destination au lieu de l'acquérir.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python \
  scripts/provision_cl100k.py --download-approved \
  --output .local-qualification/cl100k/cl100k_base.tiktoken
```

Ensuite, séquentiellement, hors sandbox (garde swap 8,5 GiB/disque 5 GB) :

```sh
export MEMORY_V4_CL100K_PATH="$PWD/.local-qualification/cl100k/cl100k_base.tiktoken"
export PYTHONDONTWRITEBYTECODE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
PATH=/opt/homebrew/bin:$PATH .local-qualification/brief-test-runtime-claude-v1/bin/python \
  scripts/qualify_404_increment3b.py --suite full \
  --output .local-qualification/404-increment3b-claude-v1
.local-qualification/brief-test-runtime-claude-v1/bin/python \
  scripts/qualify_brief_l1a.py \
  --output .local-qualification/404-increment3b-exact-claude-v1
.local-qualification/brief-test-runtime-claude-v1/bin/python \
  scripts/verify_404_increment3b.py \
  --source-report .local-qualification/404-increment3b-claude-v1/report.json \
  --brief-report .local-qualification/404-increment3b-exact-claude-v1/report.json
```

Dossiers frais, aucun rejeu dans une preuve échouée. Attendues : ≥1508 tests dont
20 TCP ; `l1a_transport_qualified=true` ; 400 mesures exactes, toutes conformes ;
verdict combiné `increment_qualified=true`, **release_qualified=false**.
Le runner structurel peut réussir sans table, mais son flag transport et le
vérificateur combiné empêchent de présenter ce résultat comme qualification L1a.

TCP : les 18 de 3a restent exigés (dont 30 cycles ≤5 s après ready), plus
`test_L5_capabilities_roundtrip_and_client_observation` et
`test_L1a_real_tcp_compact_and_offline_counter_failure`. Avec table, ce dernier
mesure réellement l'enveloppe reçue et développe une référence ; sans table il
prouve le refus seulement. La preuve distingue ces branches.

## À relire / décisions

Claude : `REVIEW_L1a_AND_REGISTRY_v1.md`, `COMPACT_BRIEF_L1a.md`,
`DEPLOYED_CAPABILITIES_L5.md`, manifeste scellé. Extensions de format acceptées
avec corrections documentées ; `sensitive` est un marquage explicite, pas du DLP.
Le texte de mémoire ne vaut ni instruction ni autorisation.

Romain : acquisition de la table (petite, explicite), validation du registre et
attribution des 9 éléments `implementation` avant fusion déclarée ; approbation
progressive des contextes manquants (54 projets sans contexte dans le draft).
Aucun mode permissif ni auto-fusion. L'activation du registre, la qualification
sur copie, la bascule et la publication restent des étapes distinctes après GOs.
L11 reste non gelé sans budget/cas, L14 reste en pause.
