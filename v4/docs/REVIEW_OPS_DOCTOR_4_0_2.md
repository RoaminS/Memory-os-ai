# Revue adverse de feat/ops-doctor — 4.0.2 candidate

Codex, 2026-10-10. Sources Claude lues sans écriture : `139c6d1`, `228bbb1`,
`02af645`, `0eb80ba`. Importées par cherry-pick, au-dessus du moteur/eval/S3.

| Constat | Correction candidate | Preuve |
|---|---|---|
| Fusion textuelle Git réussie mais collision avec doctor S3 : deux parsers/handlers, toutes commandes admin refusées | Un seul doctor, diagnostic local conservé par `--store-only` | Tests ops, rétention SQLite, essais admin installés |
| Lecture JSON/TOML invalide ou permission refusée peut lever hors traitement | Forme des objets vérifiée, erreurs lecture/parsing rendues `unreadable` | Six cas malformed/permission, aucun traceback |
| `/ready` 200 sans identité ni `ready=true` déclaré PASS | Vérification ok/ready, version stable, commit complet, provenance build et arbre propre si checkout ; réponse arbitraire jamais affichée | Cas valide deployment_manifest et quatre faux 200 |
| Doctor suppose JSONL et ignore SQLite P2 | Quick-check/counters SQLite read-only via interface existante, expose octets/reçus ; aucune reconstruction/indexation | Test magasin SQLite hash et `--store-only` S3 |
| Claude Code user scope mal localisé dans `~/.mcp.json` | `~/.claude.json`, commande `claude mcp add --scope user`, note sur surcharge projet/local | [Documentation Anthropic](https://code.claude.com/docs/en/mcp), test registry/snippets |
| Snippet Grok omet les variables URL/jeton personnalisé | Bloc TOML avec command/args/env ; valeurs non secrètes propagées | Round-trip tomllib ; validation native Grok à faire par Claude |
| Config absente = PASS « non installé » sans preuve ; doctor prétend vérifier les connexions | WARN installation inconnue ; détection syntaxique, aucune preuve de handshake par client | Tests absence et limites explicites README |
| launchctl indisponible/hang : crash possible | WARN plateforme/timeout, sous-processus borné à 5 s | Contrôle local sans activation launchd |
| README attribue doctor/C4 à PyPI 4.0.1 et garanties atomiques JSONL ambiguës | Candidate 4.0.2 séparée de publiée 4.0.1 ; SQLite opt-in, backup SQLite refusé, recall/QA séparés | Notes et décision versionnées |

Aucun blocage source restant après ces corrections et la qualification locale.
Les chemins Antigravity/Grok observés chez Romain ne constituent pas une garantie
universelle du produit fournisseur ; les neuf entrées sont des recettes de
configuration, pas neuf certifications natives. `doctor --json` contient les
mêmes PASS/WARN/FAIL que le rendu texte ; WARN seul ne fait pas échouer le code.
Les configurations projet/local/managed peuvent supplanter les fichiers utilisateur.
L'identité deployment_manifest est une déclaration opérateur, pas une signature.

La publication reste bloquée jusqu'aux deux preuves TCP des nouveaux artefacts.
