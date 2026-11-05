# DeepLearning — atelier de révision avec IA locale

Un outil qui tourne entièrement sur ton PC pour :

- **importer tes cours** (PDF, Word `.docx`, PowerPoint `.pptx`, `.txt`, `.md`), convertis en texte Markdown modifiable ;
- **les retravailler** dans un éditeur Markdown (aperçu en direct, barre d'outils, rechercher/remplacer, formules LaTeX, export) ;
- **demander à une IA locale (Ollama)** de générer fiches de révision, résumés, exercices corrigés, QCM interactifs, flashcards, glossaires, plans, questions d'examen… ou de **discuter avec ton cours**.

Aucune donnée ne quitte ta machine : la base de données et les fichiers importés sont stockés dans `data/` (ignoré par git).

## Lancer l'application

Prérequis : [Python 3.11+](https://www.python.org/downloads/) et [Ollama](https://ollama.com) avec au moins un modèle (`ollama pull gemma3:12b` par exemple).

**Windows** : double-clique sur `lancer.bat`. Au premier lancement il crée l'environnement Python et installe les dépendances, puis ouvre <http://127.0.0.1:8765> dans le navigateur.

**À la main** :

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

Options : `python run.py --no-browser`. Variables d'environnement : `DEEPLEARNING_PORT` (défaut 8765), `DEEPLEARNING_DATA` (dossier des données), `OLLAMA_URL` (défaut `http://127.0.0.1:11434`).

## Fonctionnalités

| Zone | Ce qu'on peut faire |
| --- | --- |
| **Bibliothèque** | Matières colorées, cours et documents générés rangés sous leur cours d'origine, recherche plein texte, glisser-déposer de fichiers (sur une matière pour l'y ranger). |
| **Import** | PDF (titres détectés d'après la taille de police, tableaux, en-têtes/pieds de page répétés supprimés), DOCX (titres, listes, gras/italique, liens, tableaux), PPTX (une section par diapositive, puces, tableaux, notes de l'orateur). Le fichier d'origine reste consultable. |
| **Pages scannées** | Les pages PDF sans texte sont repérées ; le bouton *Transcrire avec l'IA* les lit avec un modèle **vision** (ex. Gemma). |
| **Éditeur** | Markdown avec aperçu côte à côte, titres, listes (continuation auto), tableaux, surlignage `==texte==`, formules `$…$`, Ctrl+B/I/K/S/F, rechercher/remplacer, mode concentration, sauvegarde automatique, export `.md` / page imprimable (PDF). |
| **IA — Générer** | Fiche de révision, résumé, exercices corrigés (corrigés regroupés à la fin), QCM, flashcards, questions d'examen, explication simplifiée, glossaire, plan / carte mentale, demande libre. Sur tout le cours ou seulement la sélection, avec difficulté, nombre et consignes. |
| **IA — Discuter** | Chat avec le cours (les passages pertinents sont retrouvés automatiquement pour les cours longs), enregistrable comme note. |
| **IA — Outils texte** | Sur la sélection : corriger, reformuler, simplifier, développer, résumer, structurer, exemples, traduire, ou consigne libre ; puis *Remplacer* / *Insérer après* (annulable avec Ctrl+Z). |
| **QCM** | Quiz interactif noté (raccourcis 1–4 / Entrée), explications, historique des scores, conversion des erreurs en flashcards. |
| **Flashcards** | Répétition espacée (type Anki : À revoir / Difficile / Bien / Facile), révision du jour par matière, édition des cartes, entraînement libre. |

### Cours longs

Le modèle a une mémoire limitée (réglable dans ⚙ : 8k → 128k tokens). Si un cours ne tient pas, il est d'abord **condensé partie par partie** puis la génération se fait sur la version condensée (mise en cache pour les fois suivantes). Pour le chat, seuls les passages pertinents sont envoyés.

## Architecture

```
app/
  main.py        API FastAPI (documents, import, génération en streaming, flashcards…)
  importers.py   PDF / DOCX / PPTX / TXT → Markdown
  llm.py         client Ollama, budget de contexte, condensation, recherche de passages
  prompts.py     consignes envoyées au modèle
  render.py      Markdown → HTML assaini (aperçu, export)
  srs.py         planification des flashcards
  db.py          SQLite (data/deeplearning.db)
  static/        interface web (HTML/CSS/JS sans build)
run.py           lance le serveur et ouvre le navigateur
lancer.bat       lanceur Windows
```

## Idées pour la suite

- **Recherche sémantique multi-cours** avec des embeddings Ollama (`nomic-embed-text`) pour un chat sur toute une matière.
- **Examen blanc** : sujet chronométré généré sur plusieurs cours, puis correction de tes réponses rédigées par l'IA.
- **Correction de copie** : tu réponds aux exercices, l'IA note et commente.
- **Export Anki** (`.apkg`) des flashcards et export Word/PDF des fiches.
- **Images des cours** : extraction des schémas et description par le modèle vision.
- **Planning de révision** jusqu'à une date d'examen, avec statistiques de progression.
- **Lecture audio** des fiches (synthèse vocale) et mode Pomodoro.
