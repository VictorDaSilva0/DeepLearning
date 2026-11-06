# DeepLearning — atelier de révision avec IA locale

Un outil qui tourne entièrement sur ton PC pour :

- **importer tes cours** (PDF, Word `.docx` / `.doc`, PowerPoint `.pptx` / `.ppt`, `.txt`, `.md`), convertis en texte Markdown modifiable ;
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

Le serveur se **recharge tout seul** quand le code Python change (quelques secondes). Si une ancienne version tourne encore, relancer `lancer.bat` l'arrête et la remplace automatiquement ; l'interface affiche un bandeau rouge si le serveur n'est pas à jour.

Options : `python run.py --no-browser`, `--no-reload`. Variables d'environnement : `DEEPLEARNING_PORT` (défaut 8765), `DEEPLEARNING_DATA` (dossier des données), `OLLAMA_URL` (défaut `http://127.0.0.1:11434`).

## Fonctionnalités

| Zone | Ce qu'on peut faire |
| --- | --- |
| **Bibliothèque** | Matières (modules) rangées par **semestre** (le semestre en cours est déplié, réglable dans ⚙), cours et documents générés rangés sous leur cours d'origine, recherche plein texte, glisser-déposer de fichiers (sur une matière pour l'y ranger). |
| **Import** | PDF (titres d'après la taille et le gras, paragraphes reconstitués, puces Word, commandes en police à chasse fixe → blocs de code, tableaux, repères pour les captures d'écran, en-têtes/pieds de page supprimés), DOCX (titres, listes, gras/italique, liens, tableaux), PPTX (une section par diapositive, titres, encadrés de terminal → blocs de code, commandes en ligne, tableaux, notes de l'orateur). Les anciens **`.doc` / `.ppt`** passent par **Microsoft Office** (Word en lit le contenu, PowerPoint le réenregistre en `.pptx` ; macros désactivées), puis sont importés de la même façon — ça marche aussi avec un Word non activé. |
| **CM / TD / TP / Contrôles / CR** | Chaque document de cours est classé en cours magistral, TD, TP, **contrôle d'une année précédente** ou compte-rendu — deviné d'après le nom du fichier (« TD2_routage.pdf », « TP-Docker.pptx », « Partiel 2024.pdf », « DS1… », « Compte-rendu… »), modifiable à l'import et dans l'en-tête du document. Sur un ancien contrôle, l'IA propose le **corrigé détaillé** et un **sujet d'entraînement** du même type (même format et barème, nouvelles questions, corrigé à la fin). Un compte-rendu est rattaché à son sujet de TD / TP (automatiquement quand c'est possible) et apparaît sous lui dans la bibliothèque. |
| **Traduction** | Les cours en anglais sont repérés automatiquement ; un clic les traduit en français, section par section, avec les termes techniques sous la forme « noyau (kernel) ». Commandes et blocs de code restent intacts, et l'original anglais est conservé (« revenir à l'anglais »). |
| **Cours d'origine** | Onglet *Original* : le fichier importé s'affiche tel quel dans l'outil. Les PDF directement ; les Word / PowerPoint sont convertis en PDF par **Microsoft Office** (une fois, puis mis en cache). Un Word non activé (« Produit sans licence ») refuse cette conversion : l'aperçu des fichiers Word n'est alors pas disponible (le bouton de téléchargement reste là). |
| **Calendrier** | Vue mensuelle des examens, partiels, oraux, TP notés et rendus, liés à une matière ; compte à rebours sur l'accueil et à côté de chaque matière ; export `.ics` vers Google Agenda / Outlook / téléphone. |
| **Pages scannées** | Les pages PDF sans texte sont repérées ; le bouton *Transcrire avec l'IA* les lit avec un modèle **vision** (ex. Gemma). |
| **Éditeur** | Markdown avec aperçu côte à côte, titres, listes (continuation auto), tableaux, surlignage `==texte==`, formules `$…$`, Ctrl+B/I/K/S/F, rechercher/remplacer, mode concentration, sauvegarde automatique, export `.md` / page imprimable (PDF). |
| **IA — Générer** | Fiche de révision, résumé, exercices corrigés (corrigés regroupés à la fin), QCM, flashcards, questions d'examen, explication simplifiée, glossaire, plan / carte mentale, **corrigé détaillé d'un TD / TP**, **relecture d'un compte-rendu** (comparé au sujet : couverture des attendus, erreurs techniques, améliorations), demande libre. Sur tout le cours ou seulement la sélection, avec difficulté, nombre et consignes. **Sur tous les CM d'une matière** (choix « 📚 Tous les CM » sur un CM, ou menu ⋯ de la matière) : une seule fiche / un seul QCM… pour toute la matière, en choisissant éventuellement les CM inclus ; les CM trop longs sont remplacés par leur fiche de révision si elle existe, sinon condensés (mis en cache). |
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
  importers.py   PDF / DOCX / PPTX / DOC / PPT / TXT → Markdown
  llm.py         client Ollama, budget de contexte, condensation, recherche de passages
  prompts.py     consignes envoyées au modèle
  render.py      Markdown → HTML assaini (aperçu, export)
  srs.py         planification des flashcards
  office.py      conversions via Office : Word / PowerPoint → PDF (aperçu), .doc / .ppt → .docx / .pptx
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
- **Planning de révision** généré à partir du calendrier des examens (qui réviser quel jour), avec statistiques de progression.
- **Rappels** : flashcards de la matière proposées en priorité quand son examen approche.
- **Lecture audio** des fiches (synthèse vocale) et mode Pomodoro.
