# 4.0.7 — corrections de qualification et transition SQLite 7 → 8

13 octobre 2026. Base 72591df ; version maintenue à 4.0.6.dev0.
Aucune modification de production, aucune publication. Le run refusé
407-integrity-b4-b5-full-claude-v1 reste conservé ; nouvelles preuves dans des
sous-dossiers distincts. Les 40 inputs/gold P-B ne sont ni lus ni exécutés.

## Décisions sur les deux échecs

1. **B5 reste observable dans les statistiques détaillées** : `session_brief`
   détaillé contient déjà `status`, dont les reçus propres à SQLite. Le journal
   opérationnel ne fait pas partie de l'autorité mémorielle à comparer entre
   JSONL et SQLite. Le test ignore donc `change_journal`, comme `receipts` et
   `data_dir`, mais vérifie son seuil 10 MiB. Cela ajoute quelques champs au mode
   détaillé, explicitement non borné par le contrat compact. Aucun changement
   de production du brief : le nouveau test compare les deux enveloppes compact_v2
   complètes, exige leur égalité et ≤6000 octets / ≤1500 tokens cl100k exacts,
   et vérifie l'absence de `change_journal` dans ce format.
2. **Aucun assouplissement des checksums** : la fixture simulée schéma6 omettait
   d'enlever la migration8, sa table et son état de journal. Elle retire désormais
   tous les objets post-six et attend la version courante du schéma. Ce test reste
   une simulation clairement identifiée ; il ne constitue pas la preuve 7→8.

## Preuve d'une vraie migration

`test_schema7_genuine_upgrade.py` exporte réellement
`fdd60cb0c9fee95f2f32471e93d77a2873a3d946` (`v4/src` et `pyproject.toml`), dans
un répertoire privé, puis exécute cet ancien code en subprocess/encodeur hash.
Fait exact : ce commit annonce **4.0.6.dev0**, bien que son schéma7 soit celui
hérité de 4.0.5. Il n'est pas présenté comme le wheel installé 4.0.5 : la
qualification sur copie compare séparément à ce dernier.

Le seed produit des épisodes/faits avec provenance, correction explicite,
oubli, deux versions d'un bloc CAS, deux versions d'une tâche et des reçus
explicites. Aucun DROP ni falsification de `user_version` dans ce scénario.
Le cache hash est également non vide. Après fermeture, le magasin original est
copié ; seul ce double est ouvert avec le nouveau SQLiteStore.

`schema8_inventory.verify_upgrade` compare exhaustivement **toutes les lignes et
objets SQL existants**, cache et métadonnées inclus. Seules additions autorisées :
la migration8 exacte (checksum/horodatage), `change_events` vide et ses objets SQL,
la ligne `change_journal`. L'identité est préservée. `coverage_start` se situe
dans la fenêtre de migration ; génération/clé privées initialisées, floor=high=0.
Pas d'événements historiques reconstruits. Deuxième ouverture : inventaire
identique, mêmes génération/couverture. Original byte-identique ; son double de
retour arrière reste ouvrable avec fdd60cb et logiquement identique. Contrôles
négatifs distincts : reçus, tâches, blocs, historique, cache, table inattendue,
horodatage du journal ; toute modification supplémentaire refuse la preuve.
Les rapports exposent effectifs/empreintes, jamais le contenu des clés du journal.

## Claude — chaîne obligatoire, séquentielle, nouveaux dossiers

Depuis le clone principal après import du bundle nommé `release/4.0.7`, sans
édition concurrente :

~~~sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1 MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 MEMORY_V4_EMBED_MODEL=hash

"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-integrity-schema8-full-claude-v2

nice -n 15 "$PYTHON" -B scripts/qualify_integrity_v5_journal.py \
  --output .local-qualification/407-integrity-journal-10k-claude-v2
~~~

Arrêt au premier échec ; conserver chaque refus. Full inclut les **31 TCP**.
Le sandbox Codex interdit bind loopback : aucune qualification TCP locale n'est
revendiquée. Les tests synthétiques utilisent hash ; aucun modèle téléchargé.
P-B attend full/performance verts, audit de compatibilité, puis nouveau binding
au tip importé. Ne pas modifier le seal/gold pour contourner un refus.

La répétition **source** sur copie réelle est ensuite décrite dans
[SWITCH_4_0_7_SQLITE.md](SWITCH_4_0_7_SQLITE.md). Elle n'autorise aucune bascule et
ne remplace pas les preuves installées du futur RC.
