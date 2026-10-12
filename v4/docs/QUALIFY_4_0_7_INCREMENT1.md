# 4.0.7 — incrément 1 : intégration, A1 et B7

12 octobre 2026. Branche **release/4.0.7**, base `211ed06` ; version interne
**4.0.6.dev0**, normalisation PEP 440 de « 4.0.6.dev ». Ce n'est pas une RC.
Production maintenue en **4.0.5**, publication 4.0.6 immuable, bascule annulée.
Le travail a été fait dans un clone temporaire, jamais dans l'arbre principal.

## Livré et limites

- Plan `c4f8739` puis correctif cache `8084ae7` intégrés sur une seule ligne
  (commits portés `8adac4c`, `8b176b0`). Contresignature Claude conservée,
  amendement opérateur reporté en tête et dans §7 ; retour de qualification
  prévu vers 4.0.5. Version, registry draft et notes dev cohérents : `95bd1cb`.
- A1 : `wire_format=legacy|compact_v2`, `budget_tokens=200..1500`, au même
  outil. Le défaut direct reste legacy. Le relais neuf négocie compact_v2
  uniquement depuis le schéma de l'outil préchargé, sans outputSchema
  obligatoire. `/ready` annonce les formats ; le catalogue demeure un snapshot
  de session. Serveur ancien : appels historiques conservés ; v2 explicitement
  demandé sans support = refus sans envoi. Relais ancien : défaut legacy
  conservé. Le budget est recompté sur le résultat final après avis locaux.
- Compact_v2 conserve un objet JSON dans content.text, sans structuredContent
  dupliqué. Les parseurs JSON et le SDK MCP 1.26.0 sont testés. Erreurs,
  corps amont malformés et panne restent bornés ; les opérations locales
  indéterminées restent visibles, y compris si le brief est indisponible.
- B7 : nouveaux templates setup/quickstart/activation sans COS ni --confined.
  Variables anciennes et option --confined acceptées avec avis de dépréciation,
  sans effet de protection ni refus dû à leur seule valeur. Ready/doctor/santé
  exposent la politique réelle : replaces/identité single explicites, dédup
  d'assertion normalisée avec compatibilité de portée/identité/validité.
  La santé n'infère plus le confinement depuis un seuil COS. Toute supersession
  non acquittée requiert revue ; premier rapport != acquittement silencieux.
- Revue B6 dans `REVIEW_B6_CONTRACT_v1.md` : client ops autorisé à projeter une
  décision explicite dans la baseline, sous R1–R6 ; aucune décision d'acquitter
  un ID n'a été prise ici. Un ACK ne prouve pas la légitimité d'un fait.

**Non livré dans cet incrément : A2→A4→A3→A7**, B2–B5/B9 et C5. Les anciennes
coupes/extraits et la priorité de sélection restent à corriger. Ne pas annoncer
des items entiers ni un gain d'utilité maintenant. Aucun nouveau jeu d'évaluation,
import natif, profil neuronal, outil MCP, build publié ou opération production.

## Preuves locales

| Vérification | Résultat |
|---|---|
| Intégration/cache/version | 21 tests réussis |
| Suite v4 finale, hors TCP et test neuronal marqué slow | **1 902 passed**, 1 deselected ; aucun échec |
| Suite v3 hash/fake | **667 passed**, aucun échec |
| Matrice ciblée finale brief/SDK/health + anciennes fixtures | **319 passed**, aucun échec |
| Lint projet + lint v4/bench, diff --check | Réussis |
| Exact cl100k offline, 2 formats × 3 budgets × direct/relais | **12/12**, runtime aux dépendances qualifiées |
| Budget 200 : tokens/bytes max sur ces cas | **173 / 745** |
| Budget 600 : tokens/bytes max | **505 / 1 405** |
| Budget 1 500 : tokens/bytes max | **1 301 / 3 559** |
| Catalogue MCP complet, cl100k, même sérialisation | 23 outils ; 9 835→9 950 tokens, **+1,17 %** |
| Rejeu cache sur une nouvelle copie du snapshot refusé | 1 827 vecteurs byte-identiques, zéro encodage, tables hors cache identiques, réouverture sans encodage |

Les mesures d'enveloppe portent sur des cas synthétiques, pas sur la pertinence
du brief ni le prompt entier. Les 100 scénarios L1a historiques ont été rejoués
comme régression ; aucune qualité de sélection nouvelle n'est revendiquée.
Le test d'ancien relais simule son comportement legacy ; la qualification d'un
binaire 4.0.6 installé contre la candidate pourra compléter A9.
Le rejeu cache emploie une attestation factice qui refuse tout encode : ce
n'est pas une mesure MPS réelle ni une qualification wheel/sdist de 4.0.7.

Un premier passage large dans l'ancien runtime de test 3c a refusé 26 tests
sur dépendances non qualifiées et un test synthétique de doctor lié à la version
dev. Preuves conservées ; runtime qualifié réutilisé sans installation/téléchargement,
fixture de deployment séparée de la version dev, puis suites vertes ci-dessus.
Les échecs de mise au point du budget minimal sont également conservés en
dossiers distincts. Ne pas reprendre une sortie refusée comme preuve verte.

## Qualification de Claude, séquentielle, dossiers neufs

Importer la **ref nommée release/4.0.7**, synchroniser l'arbre avec son HEAD
en préservant les configurations privées, puis vérifier qu'aucun autre run ne
modifie les sources. L'interpréteur doit avoir les 61 versions qualifiées et
les extras ops/brief, pytest et ruff ; ne pas télécharger pour corriger un drift.
L'interpréteur ci-dessous est celui vérifié et utilisé pour ce run, hors production.

```sh
PYTHON=/private/tmp/release406-installed-7qdipavk/wheel/bin/python
export PYTHONDONTWRITEBYTECODE=1
export MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"

"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-increment1-full-claude-v1

PYTHONPATH=src:v4/src:v4 "$PYTHON" -B scripts/qualify_407_a1.py \
  --table "$MEMORY_V4_CL100K_PATH" \
  --output .local-qualification/407-increment1-a1-exact-claude-v1

PYTHONPATH=v4/src "$PYTHON" -B scripts/diagnose_406_cache.py \
  --evidence .local-qualification/copy-4.0.6-sqlite-v1 \
  --output .local-qualification/407-increment1-cache-claude-v1 \
  --reproduce-on-copy
```

Si le clone utilise `.local-qualification/lexical-dev-git` au lieu du `.git`
standard, exporter GIT_DIR et GIT_WORK_TREE **absolus** correspondant à cet
arbre avant les commandes. Le nouveau harnais A1 refuse les sources modifiées.
Arrêter au premier échec ; préserver sa sortie et utiliser une nouvelle sortie
pour une nouvelle tentative justifiée. Le harnais full applique la garde
disque effective/override validé et swap 8,5 GB ; **réserve S4 inchangée**.
Les petits cas exacts A1/cache sont des vérifications synthétiques sans modèle,
pas des campagnes lourdes, et n'abaissent aucune garde des outils production.

TCP à vérifier : les **28 cases du manifeste existant**, sans ajout de case
physique ; le cas `test_L1a_real_tcp_compact_and_offline_counter_failure` vérifie
désormais aussi la négociation v2 par défaut, le legacy explicite et les deux
enveloppes à 600 tokens. Aucun test TCP annoncé comme exécuté par Codex.

## Attentes parallèles et incrément suivant

Claude : appliquer R1–R6 au contrat B6 avant déploiement ; garder les ACKs
individuels sous décision explicite de Romain. Préparer A5/A8 sur serveur privé
à capacités annoncées ; ne pas envoyer les nouvelles options à la production
4.0.5, ni modifier les plists live sur la base de cette livraison.

Avant A2, figer les cas de développement (FR/EN, mots Unicode, SHA/URLs/code,
longues phrases, référence seule, bloc dominant, tâches nombreuses et alertes)
dans `v4/tests/fixtures/brief_v5/`. Format des anciennes fixtures L1a conservé,
avec `request.wire_format/budget_tokens`, `expect.whole_unit_ids`,
`expect.priority_before` (paires d'IDs) et `expect.counts` par type/scoped pool ;
tasks, s'il y en a, sont seedées via leur API dans un magasin privé.
Manifest date/SHA avant implémentation. Le jeu de validation indépendant de
40 cas peut être préparé et scellé par Claude hors dépôt, gardé hors réglage
de Codex jusqu'à A9 ; aucune donnée privée réelle ni téléchargement nécessaire.

Après qualification/revue de cet incrément : **A2, puis A4, A3, A7** sur la
même ligne. La version ne passe à 4.0.7 qu'à la préparation RC prévue ;
publication et bascule demandent leurs GOs séparés, après qualification complète.
