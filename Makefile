# Auto-parse PEP 723 inline dependencies from main.py and convert to --with flags.
# \x23 is used instead of # because Make treats # as a comment character in variable assignments.
# \x22 is used instead of " to avoid shell quoting conflicts inside the single-quoted -c string.
# To add/remove runtime deps, edit the `dependencies = [...]` block in main.py.
DEPS_FLAGS = $(shell uv run python -c 'import re; s=open("main.py").read(); m=re.search(r"dependencies = \[\n(.*?)\n\x23 \]", s, re.DOTALL); print(" ".join("--with=\x27"+x+"\x27" for x in re.findall(r"\x22([^\x22]+)\x22", m.group(1)))) if m else print("")')

fmt:
	find . -name "*.py" -not -path "./.venv/*" -exec uvx autoflake --remove-all-unused-imports --in-place {} \;
	find . -name "*.py" -not -path "./.venv/*" -exec uvx isort {} \;
	find . -name "*.py" -not -path "./.venv/*" -exec uvx black {} \;

check:
	find . -name "*.py" -not -path "./.venv/*" -exec uvx flake8 --ignore "E203,W291,E501,W503,E402" {} \;

typecheck:
	uv run $(DEPS_FLAGS) --with mypy -m mypy main.py --disable-error-code=import-untyped

