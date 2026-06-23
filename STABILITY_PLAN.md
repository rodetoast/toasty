# Aseprite Tag Master Stability Plan

## Current Stability Risks
- Backups can be overwritten and the app currently continues saving even if backup creation fails.
- Aseprite subprocess calls have no timeout, so a hung CLI can leave a worker thread stuck.
- Tag rename collisions can create duplicate tag names in a file, especially after spell correction or find/replace.
- `settings.json` and `custom_dictionary.json` fallback exists, but corrupt-file behavior and save failures need clearer user feedback.
- Lua string generation is safer than before, but should stay covered by pure logic tests.
- Batch operations can partially fail; the UI should keep reporting which file failed and continue safely.

## Priorities
1. Prevent source file damage: abort saving if backup/preflight fails.
2. Make failures explainable: include command, stdout, stderr, timeout, and target file in logs.
3. Avoid risky duplicate tag names: detect likely per-file collisions in the report and default them off.
4. Keep config/data fallback stable for missing or corrupt JSON.
5. Add low-friction pure logic tests without a large refactor.

## Files To Change
- `main.py`: backup policy, subprocess timeout, collision detection, settings/dictionary resilience.
- `test_stability.py`: pure logic tests for escaping, JSON fallback, and collision detection.
- `.gitignore`: keep local settings, logs, build outputs, and asset files out of source control.

## Test Method
- `cmd /c run_app.bat --check`
- `venv\Scripts\python.exe -m py_compile main.py extract_words.py test_bug.py test_fuzz.py test_stability.py`
- `venv\Scripts\python.exe test_fuzz.py`
- `venv\Scripts\python.exe test_bug.py`
- `venv\Scripts\python.exe test_stability.py`
- Copy a sample `.aseprite` file to a temporary folder, then verify read, save, loop repeat, backup creation, and original-file preservation.

## Original File Protection Policy
- Never run write tests against original project assets.
- Always copy `.ase` / `.aseprite` files to a temporary test folder before saving.
- Abort save when source file is missing, empty, read-only, or backup creation fails.
- Create timestamped backups in `.aseprite_backup` to avoid overwriting older backups.

## Release Checklist
- `settings.json` is not included in the default release.
- `custom_dictionary.json` is included next to the exe.
- `aseprite_tag_master_error.log`, `venv`, `build`, `dist`, `__pycache__`, and sample `.aseprite` files are excluded from source control/release packaging.
- Build with `AsepriteTagMaster.spec`.
- Launch the built exe and verify it reads `custom_dictionary.json` from the exe folder.
