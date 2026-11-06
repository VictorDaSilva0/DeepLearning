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

## Partager l'outil avec tes amis (Docker)

L'outil et sa base tournent dans Docker sur ton PC. Un tunnel Cloudflare le publie en HTTPS **sans ouvrir de port** sur ta box. Chacun a son compte, et la bibliothèque est commune.

Prérequis : [Docker Desktop](https://www.docker.com/products/docker-desktop/) et Ollama (sur le PC, pour la carte graphique).

1. Copie `.env.example` en `.env` et choisis le mot de passe administrateur (`ADMIN_PASSWORD`). Si tu le laisses vide, un mot de passe provisoire est affiché par `docker compose logs app`.
2. Ferme la version locale (fenêtre de `lancer.bat`), puis lance :

   ```bash
   docker compose up -d --build
   ```

   Au premier démarrage, ta bibliothèque actuelle (`data/`) est **recopiée** dans le volume Docker `app-data`. Le dossier `data/` n'est plus modifié ensuite : c'est la version Docker qui fait foi.
3. Ouvre <http://127.0.0.1:8765> et connecte-toi. Dans **Mon compte** (en bas de la barre latérale) :
   - **Adresse à donner à tes amis** : l'adresse publique, avec un bouton pour la copier ;
   - **Comptes** : crée un compte par ami. Un mot de passe provisoire s'affiche une seule fois : transmets-le, il en choisira un autre à sa première connexion. Tu peux aussi désactiver un compte, réinitialiser un mot de passe ou donner les droits d'administrateur.

Seuls les administrateurs peuvent changer les réglages de l'IA (modèle, mémoire…), qui sont communs à tout le monde.

**Adresse.** Sans autre réglage, l'adresse est **temporaire** (`https://….trycloudflare.com`) : elle change à chaque redémarrage du PC ou de Docker, et la nouvelle s'affiche dans Mon compte.

Pour une **adresse fixe**, il faut un domaine ajouté à un compte Cloudflare gratuit. Deux possibilités :
- un **sous-domaine d'un domaine que tu as déjà sur Cloudflare**, par exemple `revision.stratbook.dpdns.org` : rien à acheter ni à attendre ;
- un **nouveau domaine**, par exemple un domaine gratuit [DigitalPlat](https://domain.digitalplat.org) en `.dpdns.org`, ajouté à Cloudflare. Compte quelques heures avant qu'il soit actif.

Ensuite :

1. Dans Cloudflare, ouvre **Zero Trust → Networks → Tunnels** et crée un tunnel « Cloudflared » nommé `deeplearning`. Copie son **jeton** : la longue chaîne après `--token` dans la commande d'installation proposée.
2. Dans ce tunnel, ajoute un **Public Hostname** :
   - sous-domaine : par exemple `revision` ;
   - domaine : le tien ;
   - service : `HTTP`, URL `app:8765`.
3. Dans `.env`, mets `CLOUDFLARE_TUNNEL_TOKEN=<le jeton>`, puis relance `docker compose up -d`.

L'adresse fixe apparaît dans Mon compte. Pour revenir à l'adresse temporaire, vide `CLOUDFLARE_TUNNEL_TOKEN` et relance.

Une adresse DuckDNS (`duckdns.org`) ne marche pas avec un tunnel : Cloudflare n'accepte que les domaines ajoutés à ton compte. DuckDNS demanderait d'ouvrir le port 443 de la box vers le PC.

**À savoir**
- Un domaine tout neuf met de quelques minutes à quelques heures à être visible partout, puis Cloudflare active son certificat HTTPS. Sur un réseau filtré (pare-feu d'entreprise ou d'école, ex. FortiGate), un domaine récent peut aussi être bloqué (« Newly Observed Domain ») pendant quelques jours. Depuis ce PC, utilise alors <http://127.0.0.1:8765>.
- L'outil n'est joignable que quand le PC est allumé et que Docker tourne. Active « Start Docker Desktop when you sign in » dans les réglages de Docker Desktop : les conteneurs redémarrent alors tout seuls.
- Les fichiers Word / PowerPoint sont convertis par **LibreOffice**, inclus dans l'image. Pas besoin de Microsoft Office.
- Les générations de l'IA passent une par une sur la carte graphique : si plusieurs personnes génèrent en même temps, elles attendent leur tour.
- Le jeton du tunnel donne le droit de publier sur ton domaine : garde `.env` pour toi.

| Besoin | Commande |
| --- | --- |
| Démarrer / mettre à jour après une modification du code | `docker compose up -d --build` |
| Voir les journaux (mot de passe provisoire, erreurs) | `docker compose logs -f app` |
| Arrêter | `docker compose down` (les données restent dans le volume) |
| Sauvegarder la base | `docker compose exec app python -c "import sqlite3; sqlite3.connect('/data/deeplearning.db').backup(sqlite3.connect('/data/sauvegarde.db'))"` puis `docker compose cp app:/data/sauvegarde.db .` |

`lancer.bat` reste utilisable pour un usage local sans Docker, sans comptes, avec le dossier `data/`. Si la version Docker tourne déjà, il ouvre simplement le navigateur dessus.

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
| **Téléphone** | Interface adaptée aux petits écrans : barre du haut, bibliothèque en tiroir (☰), assistant IA en plein écran, cours en lecture (✎ pour éditer), QCM, flashcards et calendrier compact. On peut l'ajouter à l'écran d'accueil (« Ajouter à l'écran d'accueil » dans le navigateur). |
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
  office.py      conversions Word / PowerPoint → PDF (aperçu), .doc / .ppt → .docx / .pptx
                 (LibreOffice sous Linux/Docker, Microsoft Office sous Windows)
  auth.py        comptes et sessions de l'outil partagé (DL_AUTH=1)
  db.py          SQLite (data/deeplearning.db)
  static/        interface web (HTML/CSS/JS sans build)
run.py           lance le serveur et ouvre le navigateur
lancer.bat       lanceur Windows
Dockerfile, docker-compose.yml, docker/entrypoint.py   version Docker partagée (outil + tunnel Cloudflare)
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
