# Notes pour la v3 d'explain_retrieval.py

Ajouts à faire par-dessus la v2 :

1. **Section "EXCLUSION REASONS"** après le DETAIL existant.
   Pour chaque note qui n'est PAS dans le top-K affiché, classer la
   raison d'exclusion :
   - "below cutoff" si score < cutoff
   - "domain mismatch" si une politique de domaine serait appliquée
     (nécessite de simuler RetrievalPolicy.decide() en plus du brute-force)
   - "rank beyond top-K" sinon (juste pas assez bon comparé aux autres)

2. **Lexical match pondéré par champ**, comme suggéré :
   title=3pts, summary=2pts, tags=1pt, normalisé par le score max possible
   plutôt qu'une simple fraction matched/total. Remplace _lexical_score().

3. **Affichage détaillé par champ** dans le DETAIL :
   ```
   Title   : ✓ apply  ✓ skill_workshop
   Summary : ✓ fails
   Tags    : —
   ```
   au lieu de la simple liste "Found in fields: title, summary"

Pas encore implémenté — à faire dans une prochaine session, après avoir
testé le golden dataset retrieval/ sur plusieurs requêtes types pour
valider que ces métriques affinées changent réellement quelque chose
d'observable, plutôt que d'ajouter de la complexité non mesurée.
