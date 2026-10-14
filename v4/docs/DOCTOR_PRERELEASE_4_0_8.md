# Défaut doctor PEP440 — connu en4.0.7, correctif prévu pour4.0.8

14 octobre 2026. Référence : campagne privée `407rc-copy-claude-v1`.
Le doctor exige actuellement `re.fullmatch(r"\d+\.\d+\.\d+", version)` dans
ops/doctor.py. Ainsi ready=true/build valide/version4.0.7rc1 est refusé malgré
HTTP200. La chaîne finale4.0.7 est acceptée ; cela ne corrige pas le défaut.

Décision Codex : accord sur la proposition Claude, garder le code du doctor et
le critère de report A/B **inchangés** pour4.0.7. La chaîne finale est celle d'une
candidate locale non publiée ; reprise complète, nouveaux artefacts, descripteur
et preuves v2. Aucun renommage/recyclage du wheelRC1, aucun assouplissement du
critère0FAIL, aucune prétention que le runRC1 était qualifié. GOs distincts de
Romain après la qualification de4.0.7 pour publication et bascule.

Correctif futur à examiner en4.0.8 : validation PEP440 (packaging déjà dans le
profil épinglé), avec tests finals/rc/dev/a/b/post/local et versions invalides.
Préserver tous les autres contrôles ready/commit/source_dirty/descriptor,
authentification et runtime ; une syntaxe de version valide ne remplace jamais
l'attestation d'un build. Tester le doctor installé contre des réponses privées
valides/refusées et garder une erreur explicite pour toute version malformée.
Ce document est un suivi, aucun code de4.0.8 n'est livré ici.
