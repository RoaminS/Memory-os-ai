# Template v4 explicite, pas une configuration automatiquement chargée

`mcp.template.json` utilise le relais installé. Aucun jeton, chemin personnel,
`PYTHONPATH` ou serveur v3 n'est embarqué. Le relais lit le jeton depuis son fichier
0600 configuré, par défaut `~/.config/memory-os/http.token`.

Le dépôt ne suit plus `.mcp.json` ni `.vscode/mcp.json` : les fichiers privés
existants sont conservés. Ne pas écraser ces fichiers en appliquant le template.
Vérifier le scope utilisateur/projet et fusionner explicitement les seules entrées
voulues. Pour un client dont PATH est limité, remplacer `command` par le chemin
absolu de **son runtime qualifié** `bin/memory-os-relay`, puis exécuter doctor et
un handshake. Adapter `MEMORY_V4_CLIENT_ID` au client (codex, cursor, etc.).
Le format VS Code utilise `servers`, avec `type: "stdio"`, pas `mcpServers`.
