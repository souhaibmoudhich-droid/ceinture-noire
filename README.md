# Ceinture Noire

Planning journalier étude + entraînement (judo mardi/mercredi, tractions, haltères, nunchaku, vélo).
S'adapte à ce qui est fait ou pas, marche hors ligne, se synchronise entre appareils via un Gist privé.

Ouvrir dans Safari sur iPhone → Partager → « Sur l'écran d'accueil ».

## Agenda iPhone

`scripts/agenda.py` (lancé par la tâche GitHub « Agenda iPhone ») lit les agendas iCloud du secret `ICLOUD_URLS` et publie `busy.json` : uniquement les créneaux occupés, sans titres. Si la lecture échoue, les anciens créneaux sont gardés et l'app affiche l'erreur en rouge.

Tests : `pip install -r scripts/requirements.txt pytest && python -m pytest tests`
