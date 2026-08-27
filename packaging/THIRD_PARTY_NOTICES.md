# Third-Party Notices

DTUG IDE bundles the following third-party software at build time via
`requirements.txt` / `pyproject.toml`. This file is provided for distribution
compliance; it is not legal advice.

| Component | Version range | License | Notes |
| --- | --- | --- | --- |
| PyQt6 | >=6.7,<7 | GPL-3.0 or commercial | Qt 6 Python bindings. Dynamic linking only; no Qt source modified. |
| psutil | >=6,<7 | BSD-3-Clause | Process and resource utilities used for sandbox memory accounting. |

The application itself is distributed under the MIT License; see `LICENCE`.

When redistributing the frozen executable built with PyInstaller, the PyQt6
(GPL) licensing terms apply to the bundled Qt libraries. For closed-source
redistribution of the Qt components, a commercial Qt license is required; the
Python source form (`py -m dtug_ide`) is unaffected and remains MIT.
