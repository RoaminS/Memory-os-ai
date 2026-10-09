# Après 4.0.2 : réponse exacte, coût et classement — proposition à Romain

Métadonnées publiques vérifiées le 2026-10-10, **aucune donnée téléchargée, aucun
appel API ni soumission**. Le résultat S n'est pas un classement ni un test neuf.

1. **Banc neuf recommandé : BEAM 100K** (appelé 128K dans le papier), 20
   conversations. Fichier parquet affiché **5,43 Mo**, expansion logique du split
   14 120 723 octets. Hub BEAM complet hors 10M : download 105 542 405 octets,
   expansion 272 777 931 octets. Autorisation de Romain avant téléchargement ;
   figer révision, SHA, licence et tous les ids, puis garder les questions/answers
   inaccessibles à l'ingestion et au réglage. Réserver des conversations entières
   jamais inspectées, dédupliquer contre les jeux précédents, bootstrap par
   conversation, comparer BM25 + dense + fichiers et plein contexte si budget.
   20 conversations limitent la puissance : déclarer les IC larges, ne pas
   recycler automatiquement le seuil S de 30 clusters ou exclure pour gagner.
   [Fichiers BEAM](https://huggingface.co/datasets/Mohammadta/BEAM/tree/main/data),
   [carte et tailles](https://huggingface.co/datasets/Mohammadta/BEAM/blob/main/README.md).
2. **Alternative MemoryAgentBench** : 74 805 902 octets téléchargés,
   131 992 200 octets logiques, 146 exemples (pas 146 questions). Accurate_Retrieval
   parquet ≈20 Mo. Le jeu incorpore LongMemEval : ne pas le déclarer indépendant
   sans exclusion des sources/questions déjà vues et audit du recoupement.
   [Carte officielle](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench/blob/main/README.md),
   [fichiers](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench/tree/main/data).
3. **Lecteur figé proposé** : `gpt-4.1-mini-2025-04-14`, température 0, une réponse
   bornée à 256 jetons, même prompt/context budget/abstention et lecteur pour C4
   et BM25. Enregistrer modèle, prompt SHA, requêtes, réponses, usage réel, refus,
   retries facturés et latences ; les answers ne sont jamais envoyées au lecteur.
   EM normalisé/F1/containment comme diagnostics, scoreur officiel du banc si
   applicable et audit humain à l'aveugle pour erreurs/abstention. Pas de recherche
   des prompts sur test. Le lecteur reste un composant de banc, pas du serveur.

   Tarif standard affiché : $0,40/M jetons entrée, $1,60/M sortie, hors remise
   cache/batch. Hypothèse 8 000 entrée +256 sortie : $0,0036096/réponse, soit
   **$3,61 pour 500 questions ×2 systèmes**. Si juge LLM séparé figé, 2 000 entrée
   +128 sortie par réponse : +$1,00 pour 1 000 jugements ; avec marge 25 %,
   **GO budget $6** (pilote obligatoire, arrêt dur à ce montant). Pour plein
   contexte 115k entrée/question : environ $46,41 pour 1 000 réponses, sans juge.
   Formule : appels ×(entrée×0,40 +sortie×1,60)/1e6. Nombre BEAM de questions,
   tokens observés et scoreur effectif à établir après GO, ce n'est pas un devis
   pour tout BEAM. [Prix/snapshot officiels](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
4. **Coût d'indexation d'abord** : instrumenter tokenizer/encode/batch, doublons
   texte, index/persistance/MCP ; ingestion groupée avec transaction+reçu unique,
   cache par empreinte du modèle+texte (ne jamais réutiliser des corpus différents),
   puis démarrage/recherche chaude, rollback et mémoire RSS/swap séparés.
   Hypothèse cible, non mesurée : réduire le p50 ingestion de moitié à qualité
   constante et zéro acquittement perdu. Toutes exécutions séquentielles, nice15,
   arrêt swap>8,5 Go ou disque<5 Go, aucun modèle>300 Mo téléchargé.
5. **Classement seulement après** : relecture des règles officielles, adaptateur
   Add/Search contrôlé, coût total extraction+lecture+indexation rapporté, reproduction
   indépendante, GO Romain avant upload/données/API. Ne promettre aucun top1.
