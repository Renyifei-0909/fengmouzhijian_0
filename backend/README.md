# Fengmou backend

FastAPI backend for the Fengmou construction-evidence verification and trusted-delivery MVP.

This is an application package, not a published model or compliance claim. The default stub and
demo analyzers do not establish visual-model accuracy, construction facts, or evidence-grade
results.

For a source checkout or source distribution, use the committed universal lock and policy verifier:

```bash
python -m pip install --require-hashes -r uv-bootstrap.txt
uv lock --check --no-python-downloads
python scripts/verify_dependency_lock.py
uv sync --extra dev --locked --no-python-downloads
```

The project requires uv 0.11.32 and an existing Python 3.11 or newer interpreter. The lock fixes
Python package artifacts; it does not install operating-system dependencies such as ffprobe or
PostgreSQL.
