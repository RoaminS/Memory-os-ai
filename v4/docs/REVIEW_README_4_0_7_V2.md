# Revue du README 4.0.7 v2 — 13 octobre 2026

Source lue en lecture seule :
`~/memory-os-local/distribution/README_4.0.7_DRAFT_v2.md`.
Destination à la préparation de la RC : README racine / long_description.
Ce run ne prépare pas la RC et ne remplace pas le README publié.

Accord sur le positionnement, le statut macOS ALPHA, l'attribution des essais
77/80 s à 4.0.6, les limites BEAM, l'absence de gain L11 ou de classement, et
l'explication du coût uvx et de la visibilité des extraits chez les fournisseurs.
Claude a vérifié la conservation des href HTTPS au nettoyage PyPI ; ce n'est
pas encore la preuve d'un clic GUI suivi d'un handshake et d'un brief privés.

Quatre amendements à intégrer lors de la RC :

1. Conserver « Current published release: 4.0.6. 4.0.7 is in preparation »
   jusqu'à la publication effective. Avant le bloc d'installation et les boutons
   4.0.7, ajouter : **« The commands and install buttons below target the upcoming
   4.0.7 release; they are usable only after it is published. For the currently
   published release, install memory-os-ai[ops,brief]==4.0.6. »**
   Retirer cet avertissement seulement à la publication et mettre à jour le statut.
2. Après la vérification SQLite : **« SQLite ≥ 3.51.3 is required for the SQLite
   backend; check the library linked to this Python interpreter, not the sqlite3
   command-line tool. »** Le minimum est celui de `SQLiteStore.MIN_SQLITE`.
3. Matrice clients : retirer Codex du groupe « writes its MCP entry » sans
   réserve ; grouper **Codex, Grok CLI** avec **« Adds an absent MCP entry to a valid
   TOML file; replacing an existing entry requires manual editing. An unchanged
   entry is kept. »** Pour Zed : **« Plain JSON supported; existing JSONC requires
   manual editing. »** `quickstart` refuse la réécriture TOML d'une entrée déjà
   présente ; il peut ajouter une section absente à un fichier TOML valide.
4. Remplacer « vector cache reused across releases (no re-encoding on upgrade) »
   par **« vector cache reused when the attested encoding profile is unchanged;
   zero-re-encoding upgrades require copy qualification. »** Une modification du
   modèle/profil ou un cache invalide ne doit jamais être présenté comme une
   garantie de réutilisation. La copie de la RC 4.0.7 reste une preuve à obtenir.

Avant publication : rendering/twine, clics réels Cursor/VS Code, handshake et
brief sur serveur privé ; aucune modification des configurations utilisateurs.
