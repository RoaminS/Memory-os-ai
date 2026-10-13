# 4.0.7 — qualification sur copie SQLite, puis conditions de bascule

13 octobre 2026. **PRODUCTION RESTE EN 4.0.5.** Aucune commande de ce document ne
bascule un service. La candidate actuelle reste 4.0.6.dev0, schéma8 ; pas de RC
ni d'artefact 4.0.7 qualifié à ce stade. Les guards utilisent le seuil effectif
`min_free_bytes()` (5 Go, override opérateur ≥3 Go) et swap ≤8,5 Go. Travail
séquentiel, nice15, aucune acquisition de poids.

## 1. Critères gelés avant copie

| Contrôle | Critère / preuve |
|---|---|
| Entrée | runtime installé 4.0.5 ; schéma7 ; instance `286dfcb0357940468aedb9617a2ccd9c` ; chemin/backend/tasks conformes au plist lu |
| Source | connexion `mode=ro` et API SQLite backup du runtime4.0.5 ; aucun moteur ouvert sur le live ; WAL/SHM existants requis par S4 |
| Autorité | toutes les lignes et objets SQL du schéma7 inchangés ; seuls migration8, table/index/trigger du journal et son état sont ajoutés |
| Histoire | reçus explicites, versions d'items/blocs/tâches, métadonnées et identité préservés ; journal vide, couverture prospective datée, génération stable au reopen |
| Cache | copie intégrale ; 0 texte du corpus ré-encodé et 0 cache miss au démarrage ET au reopen ; profil attesté strictement identique à celui de4.0.5 |
| Recherche | mêmes 10 requêtes, même instant figé, C4/scoped_literal/guard3, rerank=false, top10 ordonné exact ; pas de touch sur les copies |
| Coût | migration ≤120 s, chaque démarrage chaud ≤120 s, campagne ≤900 s ; un dépassement refuse, jamais une dérogation automatique |
| Profil | production auto→MPS/native/batch32/max512, mêmes poids/runtime/empreinte d'encodeur ; registre INACTIF, tasks actives ; aucun download/réseau externe |
| Suite RC | wheel+sdist et reopen égaux ; HTTP privé --warm ready/doctor0FAIL ; relay installé et TCP ; preuve d'artefacts scellée |

Le benchmark ci-dessous est un **rehearsal de source** : il exporte le commit
entier déclaré (source_layout + pyproject + commit), enregistre l'empreinte de
l'archive et du plan, puis utilise le runtime qualifié existant pour ce code.
Ses résultats comportent toujours `release_qualified=false`. Ils ne permettent
ni publication ni bascule. L'inventaire du magasin ne sort pas en clair dans les
rapports publics ; garder les preuves privées.

## 2. Claude — source sur copie, avant le RC

Après full green, depuis le clone principal :

~~~sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1 MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset MEMORY_V4_PROJECT_REGISTRY
CANDIDATE_COMMIT=$(git rev-parse HEAD)

nice -n 15 "$PYTHON" -B scripts/qualify_schema8_copy.py \
  --candidate-commit "$CANDIDATE_COMMIT" \
  --baseline-python "$HOME/.memory-os-runtime/venv-4.0.5/bin/python" \
  --source-dir "$HOME/memory-os-local/stores/sqlite-20261011-v1" \
  --production-plist "$HOME/Library/LaunchAgents/com.romain.memory-os-v4.plist" \
  --expected-instance-id 286dfcb0357940468aedb9617a2ccd9c \
  --output .local-qualification/407-schema8-production-copy-source-claude-v1
~~~

Le lanceur remplace l'environnement du worker par le profil réel du plist ;
le `MEMORY_V4_EMBED_MODEL=hash` utilisé pour les suites ne doit pas remplacer ce
profil. Arrêt immédiat au premier échec, dossier neuf à chaque campagne.
`report.json` doit avoir ok=true, 0 corpus encoding/cache miss dans les trois
workers, rankings/profil identiques, migration exhaustive et journal stable.
La source live peut recevoir des écritures concurrentes : l'autorité de référence
est **la coupe transactionnelle scellée**, pas un SHA physique live instable.
L'empreinte de cette coupe et le plist sont contrôlés inchangés en fin de run.

## 3. Qualification RC installée — obligatoire, à livrer au gel 4.0.7

Le vieil outil `qualify_sqlite_release_copy.py` / manifest4.0.6 n'est **pas**
compatible avec cette migration : il attend schéma7 et une autorité strictement
identique sans whitelist7→8. Ne pas passer une autre version ni modifier son
rapport à la main. Au gel du RC, livrer un manifeste4.0.7 et un lanceur installé
utilisant la comparaison exhaustive7→8 ci-dessus, puis exécuter successivement :

1. nouvelle coupe via backup API4.0.5, baseline installée4.0.5 ;
2. copies wheel4.0.7 et sdist4.0.7, metadata+descriptor source complet vérifié,
   61 pins qualifiés ou nouveau ledger approuvé ;
3. migration exhaustive, --warm C4/MPS, les mêmes requêtes figées, reopen/cache0 ;
4. private HTTP/ready/doctor, relays installés, 31 TCP et suite complète ;
5. premier backup S4 schéma8 + restore-drill privé + réconciliation des oublis,
   y compris les anciennes données7 ;
6. manifest vérifié, revue Claude, GO distinct Romain, coupe finale sous fenêtre
   read_only avant toute modification du plist/pointer.

Les commandes installées exactes dépendent des artefacts RC qui **n'existent pas
encore** ; elles seront scellées avec ces empreintes, sans réutiliser les wheels
4.0.6. Ne pas qualifier « release_ready » sur la seule répétition source.

## 4. Retour arrière / restauration : frontière de schéma

- **Avant bascule / sans nouvelles écritures** : abandonner la copie8 ; conserver
  production4.0.5 et son magasin7. Pendant une vraie fenêtre, retour au runtime
  4.0.5 uniquement sur un **nouveau répertoire issu de la coupe7 qualifiée**,
  après vérification qu'aucune écriture acquittée n'est survenue depuis la coupe.
  Ce chemin est testé avec le vrai fdd60cb sur magasin synthétique.
- **Ne jamais ouvrir schéma8 avec4.0.5**, ni falsifier PRAGMA/checksums, ni supprimer
  la table du journal pour forcer le downgrade. Cela est explicitement refusé.
- **Après nouvelles écritures8** : revenir à une vieille coupe7 ferait perdre des
  écritures acquittées. Garder le runtime compatible8 ; privilégier correctif en
  avant ou restauration S4 d'une sauvegarde8 récente dans un nouveau répertoire,
  avec private reopening, identité/génération selon S4, anciens cursors périmés
  et forget reconciliation avant writes. Même cette restauration ne promet pas
  zéro perte d'écritures postérieures au backup : réconciliation requise.
- **S3 export sémantique** : possible sur un double8 hors ligne vers JSONL frais,
  source jamais modifiée. Il préserve l'autorité exportable selon S3, mais ne
  transporte pas le journal8/curseurs ni les garanties SQLite de tasks/CAS.
  Ce n'est donc **pas** un rollback4.0.5 SQLite7 qualifié. Un tel import descendant
  après nouvelles écritures exigerait outil + comparaison exhaustive + GO
  supplémentaires ; aucun convertisseur descendant n'est revendiqué ici.
- Ne rouvrir les écritures qu'après validation humaine des comptes/empreintes,
  private doctor/recherche et rapprochement des reçus incertains ; aucun replay
  automatique. Conserver backup final7, backups8, coupe/export et rapports refusés.

Ces limites doivent figurer dans le futur runbook de bascule ; elles évitent de
promettre un rollback immédiat incompatible avec le changement de schéma.
