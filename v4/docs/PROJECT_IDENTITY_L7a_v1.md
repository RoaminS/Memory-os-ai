# L7a — identité canonique, contrat serveur et hooks (candidate 4.0.4)

2026-10-11. Registre optionnel, activation après qualification et GO opérateur.
Sans `MEMORY_V4_PROJECT_REGISTRY`, les labels existants gardent leur comportement.
Avec ce paramètre, le moteur charge UNE photographie validée du fichier avant
l'ouverture du magasin. Aucun rechargement à chaud ; redémarrer après une modification approuvée.
Le registre n'est ni généré ni modifié par les hooks, les indices Git ou le serveur.

## Registre approuvé

Fichier local appartenant à l'utilisateur, régulier, sans lien symbolique, non
inscriptible par groupe/autres, ≤1 MiB. Schéma :
`schemas/project-registry-v1.schema.json` ; les collisions inter-projets de labels,
IDs de contexte ou racines sont également refusées par le validateur Python.
Le SHA-256 porte sur les octets exacts ; doublons de clés JSON refusés.

```json
{
  "schema_version": 1,
  "projects": [{
    "id": "prj_memory_os_ai",
    "aliases": ["memory-os-ai", "Memory-os-ai"],
    "contexts": [{
      "id": "codex-implementation",
      "root": "/Users/romain/Documents/Codex/2026-10-09/v/work/implementation",
      "git_root_hint": "/Users/romain/Documents/Codex/2026-10-09/v/work/implementation"
    }]
  }]
}
```

EXEMPLE seulement : aucun fichier de production créé. Choisir les IDs/alias
avec Romain. Un remote commun ne prouve pas un projet commun (forks) ; un
worktree ou clone supplémentaire demande un contexte explicitement approuvé.
Un déplacement ajoute/modifie une racine approuvée sans changer l'ID. Les alias
sont exacts, sensibles à la casse ; ni normalisation implicite ni fusion automatique.
Ne pas mettre de credentials dans `remote_hint`. Les hints sont descriptifs,
jamais utilisés par le résolveur pour étendre les accès.

## Ce que Claude doit envoyer

SessionStart résout un contexte **localement**, par racine exacte approuvée :

```sh
python -m memory_os_v4.projects --registry /chemin/registre.json --root /racine/approuvee
```

Sortie : `{"project_id":"prj_memory_os_ai","context_id":"codex-implementation","registry_sha256":"…"}`.
Le hook peut remonter les parents de son cwd, en s'arrêtant à la racine du
workspace déterminée localement ; une sous-racine explicitement approuvée prime.
En cas de contexte inconnu/ambigu, registre invalide/absent quand exigé : notice
seule, aucun appel avec projet omis, `default` ou `*`, aucune capture automatique.
Le registre et sa somme doivent être identiques côté hooks et serveur ; le
contrôle d'attestation dans `/ready` appartient à L5/3b.

Appels MCP **sans nouveau champ arbitraire** :

```json
{"project":"prj_memory_os_ai","focus_query":"travail courant"}
```

pour `memory_session_brief` ; `project` identique pour recherche, contexte,
timeline, occurrences, documents, faits et blocs. SessionStop/capture :

```json
{"project_label":"prj_memory_os_ai","request_id":"uuid-stable-du-lot",
 "messages":[{"role":"assistant","content":"Résumé explicite du travail."}],
 "summary":"Résumé bref", "facts":[]}
```

Le client garde `context_id`, `registry_sha256`, cwd et hints dans son journal
local d'intégration, pas dans les arguments MCP ni dans des faits automatiques.
Conserver projet+request_id+arguments exacts pour résoudre une incertitude L3 ;
ne pas recalculer le projet au milieu d'un lot. SessionStart injecte seulement
`text` (contrat Q0). `mode: compact/detailed` attend L1a/3b : ne pas l'envoyer encore.

## Sémantique et limites

- Écritures MCP sur ID ou alias : nouvelles lignes sous l'ID canonique.
  Reçu canonique sur l'ID ; empreinte brute sur les arguments réellement reçus.
  Le relais exige toujours la même empreinte brute pour un request_id connu.
- Lectures : ID + tous ses alias approuvés forment UN ensemble filtré avant
  calcul BM25, normalisation, candidats et top_k. Les réponses conservent le label
  historique de chaque ligne. Aucun renommage, réencodage ou migration implicite.
- `*` reste une lecture inter-projets explicite d'administration, et le défaut
  historique du serveur partagé reste inchangé. Ce registre est un mécanisme
  d'identité, PAS une autorisation multi-utilisateurs. Les hooks doivent toujours
  fournir un ID explicite ; label inconnu non vide = refus, jamais fallback global.
- Blocs : un bloc canonique prime, sinon contenu unique hérité des alias ; deux
  valeurs historiques différentes = `project_alias_block_conflict`, sans choix
  arbitraire. Un opérateur peut définir un nouveau bloc canonique après revue.
  Append modifie uniquement le bloc canonique propre, pas les contenus hérités.
- Corrections/supersession entre anciens labels restent conservatrices : pas de
  rapprochement automatique des identités de faits ni de déduplication inter-alias.
- Les API Python bas niveau, migration v3 et administration hors MCP conservent
  leurs labels littéraux. Pour une lecture Python groupée, passer
  `engine.projects.resolve(label, read=True)` ; pour une nouvelle écriture,
  `engine.projects.resolve(label)`. L'activation ne réécrit jamais les sources.
- Réaffecter un ancien alias à un autre ID peut exposer son histoire au mauvais
  projet : modification opérateur à revoir, pas une opération automatique permise.

## Preuves

`test_projects_l7a.py` : JSONL et SQLite, réouverture sans réécriture, scope BM25
avant ranking, tous chemins de lecture, reçus canoniques, forks/worktrees/clones,
non-Git, déplacement inconnu, collisions, conflits de blocs et refus inter-projets.
`test_L7a_registry_scopes_through_packaged_relay` : preuve TCP à exécuter par Claude.
