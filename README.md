# Write Blocker

Software write blocker avec interface graphique PySide6 pour l'acquisition forensique sous Linux.

> **Description courte :** Write Blocker est un bloqueur d'ecriture logiciel sous Linux avec GUI PySide6. Il detecte automatiquement les disques USB connectes, les verrouille en lecture seule et permet de choisir quels disques autoriser en ecriture. Ideal pour preparer une acquisition forensique avec Guymager.

## Fonctionnalites

- **Detection automatique** des disques USB via udev (branchement a chaud)
- **Politique par defaut : lecture seule** — tout nouveau disque est immediatement demonte et verrouille
- **Bascule RO/RW** par disque avec confirmation obligatoire avant tout passage en ecriture
- **Demontage automatique** des partitions avant verrouillage (empeche les ecritures sur un mount existant)
- **Protection complete** : disque + toutes ses partitions (blockdev --setro)
- **Tableau de bord** : device, modele, vendor, taille, numero de serie, statut

## Prerequis

- Linux (teste sur Ubuntu)
- Python 3.10+
- Droits root (sudo)
- `libxcb-cursor0` — dependance systeme requise par Qt/PySide6 pour le rendu des curseurs sous X11/XWayland

## Lancement rapide

```bash
./run.sh
```

Le script `run.sh` gere tout automatiquement :

1. **Dependances systeme** — installe `libxcb-cursor0` si absent (via `apt-get`)
2. **Environnement virtuel** — cree le venv `env` et installe les dependances Python si le dossier n'existe pas
3. **Affichage** — force le mode X11 (`QT_QPA_PLATFORM=xcb`) pour que la fenetre ait des decorations (fermer, minimiser, maximiser) meme sous Wayland
4. **Execution** — lance le programme en `sudo` avec les variables d'affichage necessaires

## Installation manuelle

Si vous preferez ne pas utiliser `run.sh` :

```bash
sudo apt-get install -y libxcb-cursor0
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
sudo ./env/bin/python write_blocker.py
```

### Workflow forensique typique

1. Lancer le write blocker
2. Brancher le disque **source** (evidence) → le laisser en **READ-ONLY** (defaut)
3. Brancher le disque **cible** (destination) → choisir **READ-WRITE** dans la popup
4. Lancer **Guymager** pour l'acquisition
5. Debrancher les disques

## Fonctionnement technique

Le write-blocking repose sur `blockdev --setro` au niveau kernel :

1. Un disque USB est detecte via **pyudev**
2. Toutes ses partitions sont **demontees** (`umount`)
3. Le flag read-only est applique sur le disque et ses partitions (`blockdev --setro`)
4. Toute tentative de mount RW est refusee par le kernel

Ce n'est pas un bloqueur materiel — il protege contre les ecritures accidentelles logicielles, ce qui est suffisant pour une acquisition forensique standard.

## Licence

MIT
