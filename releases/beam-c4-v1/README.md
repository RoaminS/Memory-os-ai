# Livraison BEAM C4 v1 — 2026-10-10

Source exécutable figée : `e15859ece77f6527b09fefac6c409bb84a80cdd2`.
Plan SHA-256 : `05bf7063fa663c149f2101204e05c095eb222887d4c8183dbb2a2176f733c39e`.
Prérequis bundle : `45238a8526296bc49609ce6abd015aa2e1b91166`.

[Protocole complet](../../v4/docs/BEAM_PROTOCOL_v1.md), [plan](plan.json),
[reçu](receipt.json), [audit](audit.json), [preuves](proofs.json).
Ces fichiers contiennent uniquement des métadonnées : aucun corpus ni référence
de réponse, aucun secret, aucune sortie d'inférence BEAM. Le plan est préparé,
les campagnes neuronales ne sont **pas exécutées**.

Audit : 20 conversations, 400 questions, 5 732 messages ; 355 questions avec
preuves non vides, 40 abstentions, 5 annotations explicitement vides publiées.
Zéro recoupement exact normalisé question/message avec LongMemEval-S.
Le pilote porte sur les conversations **7 et 15**, une question par catégorie,
20 ids fixés dans `plan.json`, exclusion de ces deux conversations pour une
éventuelle confirmation d'exactitude des réponses.

Preflight `gpt-5.6-luna/minimal` : **bloqué, zéro appel modèle** ; le catalogue
Codex local annonce low/medium/high/xhigh/max. `reader-blocked.json` prouve
l'arrêt avant accès aux contextes et avant encodage/appel réseau. Pas de coût
mesuré ou de quota restant inventé. L'exécutable Codex 0.162.0-alpha.17.2 est
attesté par SHA-256 ; connexion ChatGPT imposée, clé API refusée.

Vérifications : 964 tests v4 légers verts sur `454d785`, puis 30 tests BEAM verts
sur `e15859e` après remplacement du pipe stdin par un fichier régulier pour
garder la surveillance de ressources réactive pendant le démarrage CLI.
Smoke MCP stdio réel avec encodeur hash : magasin isolé neuf, encodeur réutilisé,
1 fait ajouté/0 dédoublonné/1 résultat. Smoke du lecteur contre un faux CLI
local : stdin fichier, sortie structurée, 23 tokens **synthétiques**, deux appels
de garde ; aucun appel au fournisseur. Lint des fichiers touchés + politique de
qualification habituelle E9/F verts. Le lint E/F élargi à tous les anciens bancs
et tests v4 rencontre 60 erreurs de style préexistantes ; aucun nettoyage hors
périmètre. Dix tests TCP existants et un test neuronal lent exclus.

## Commandes exactes Claude, après import du bundle

Le clone principal Codex contient déjà les sources ; si un clone différent est
utilisé, les fichiers du plan devront être régénérés avant résultats, car les
chemins privés et le commit sont attestés. Ne pas lancer simultanément les
commandes ; conserver les dossiers et échecs, jamais écraser ou relancer une
mutation incertaine.

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
bash scripts/run_beam.sh prepare --source-commit e15859ece77f6527b09fefac6c409bb84a80cdd2 --plan-dir .local-qualification/beam-c4-v1-minimal
bash scripts/run_beam.sh preflight --plan-dir .local-qualification/beam-c4-v1-minimal
```

Preflight retourne **3** aujourd'hui : ce résultat bloque uniquement le lecteur,
pas la récupération sans LLM. La récupération peut être mesurée hors sandbox :

```bash
bash scripts/run_beam.sh pilot-retrieval --plan-dir .local-qualification/beam-c4-v1-minimal
bash scripts/run_beam.sh retrieve --plan-dir .local-qualification/beam-c4-v1-minimal
```

Le pilote lecteur avec `minimal` rendra un rapport bloqué avec zéro appel :

```bash
bash scripts/run_beam.sh pilot-reader --plan-dir .local-qualification/beam-c4-v1-minimal
```

**Si et seulement si Romain autorise low**, nouveau plan avant toute inférence :

```bash
bash scripts/run_beam.sh prepare --source-commit e15859ece77f6527b09fefac6c409bb84a80cdd2 --effort low --plan-dir .local-qualification/beam-c4-v1-low
bash scripts/run_beam.sh preflight --plan-dir .local-qualification/beam-c4-v1-low
bash scripts/run_beam.sh pilot-retrieval --plan-dir .local-qualification/beam-c4-v1-low
bash scripts/run_beam.sh pilot-reader --plan-dir .local-qualification/beam-c4-v1-low
```

L'option low n'autorise jamais une campagne complète de réponses. Réaliser la
revue aveugle `reader-pilot/human-audit.json` (dix cas, ≥90 % d'accord), vérifier
les quatre contrôles du juge et les plafonds (84 appels/45 min/500 000 tokens),
puis présenter à Romain utilité, projection 1 440 appels et limites. Un nouveau
GO de Romain reste nécessaire. Ne pas demander une clé API.

Claude doit relire le pare-feu d'annotations, les cinq annotations vides,
la récence désactivée/séquence512 (C4 de récupération, pas identité de toute la
configuration serveur), l'IC sur 20 conversations et l'isolation du CLI.
Les corpus et magasins de production n'ont pas été modifiés ; aucun GitHub/PyPI
contacté pour cette livraison. La publication 4.0.2 garde ses artefacts qualifiés.
