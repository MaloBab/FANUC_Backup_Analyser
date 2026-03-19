# FANUC Variable Extractor

Application Python/Tkinter d'extraction de variables robots FANUC depuis des fichiers `.VA`.

## Lancement

```bash
python main.py
```

## Architecture

```
fanuc_extractor/
│
├── main.py                         # Point d'entrée
│
├── config/
│   └── settings.py                 # Configuration centralisée (dataclass + JSON)
│
├── models/
│   └── fanuc_models.py             # Structures de données pures (dataclasses + enums)
│                                     FanucVariable, ConversionResult, ExtractionResult
│
├── services/                       # Logique métier — sans dépendance à Tkinter
│   ├── converter.py                # Appels Roboguide (subprocess) — Pattern Strategy
│   ├── parser.py                   # Parsing fichiers .VA — Pattern Template Method
│   ├── exporter.py                 # Export CSV/JSON — Pattern Strategy
│   └── orchestrator.py             # Façade : coordonne converter + parser + exporter
│                                     Pattern Facade + Observer (callbacks progression)
│
├── utils/
│   ├── logger.py                   # Configuration logging (console + fichier rotatif)
│   └── worker.py                   # Thread worker générique (queue thread-safe)
│
└── ui/                             # Couche présentation — Pattern MVVM
    ├── theme.py                    # Palette, polices, styles ttk centralisés
    ├── viewmodel.py                # AppViewModel : état + commandes exposés à l'UI
    ├── app.py                      # App : fenêtre racine + injection du ViewModel
    └── components/
        ├── header.py               # Barre de titre
        ├── sidebar.py              # Sélection dossier, filtres, actions
        ├── main_panel.py           # Tableau résultats (Treeview) + journal
        ├── statusbar.py            # Barre de statut + barre de progression
        └── settings_dialog.py      # Fenêtre modale paramètres
```

## Patrons de conception utilisés

| Pattern                   | Où                                         | Pourquoi                                               |
| ------------------------- | ------------------------------------------- | ------------------------------------------------------ |
| **MVVM**            | `ui/`↔`viewmodel.py`                   | Découple l'UI de la logique                           |
| **Facade**          | `orchestrator.py`                         | L'UI n'appelle qu'un seul point d'entrée              |
| **Strategy**        | `converter._build_command()`,`exporter` | Variantes interchangeables sans modifier les appelants |
| **Template Method** | `parser._parse_line()`                    | Flux fixe, étape variable                             |
| **Observer**        | callbacks `on_*`du ViewModel              | Notification non-couplée entre couches                |
| **Command**         | `BackgroundWorker`                        | Encapsule une tâche longue + résultat asynchrone     |

## TODO — points à compléter

* [ ] `services/converter.py` → `_build_command()` : adapter les arguments CLI Roboguide réels
* [ ] `services/converter.py` → `_find_convertible_files()` : ajuster les extensions source
* [ ] `services/parser.py` → `_VAR_PATTERN` : affiner le regex selon le format .VA exact
* [ ] `ui/viewmodel.py` → `_poll()` : remplacer `tk._default_root` par une référence propre
