"""#20: every file of the terminal client says what belongs in it, and every public
function and method has a docstring - as the core's tests/test_docstrings.py. A new
public one without fails here; private ones (``_name``) are exempt."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "unseenet_tui"
HINT = " - say what it does, what it returns and when it is used"


def files():
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def test_every_file_says_what_belongs_in_it():
    for p in files():
        if p.name in ("__init__.py", "__main__.py"):
            continue
        doc = ast.get_docstring(ast.parse(p.read_text())) or ""
        assert len(doc.strip()) > 40, f"{p.relative_to(PKG)}: say what belongs in this file"


def test_every_public_function_has_a_docstring():
    missing = []
    for p in files():
        def scan(body, where):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    scan(node.body, f"{where}{node.name}.")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and not node.name.startswith("_") and not (ast.get_docstring(node) or "").strip():
                    missing.append(f"{p.relative_to(PKG)}: {where}{node.name}")
        scan(ast.parse(p.read_text()).body, "")
    assert not missing, "public functions without a docstring: " + ", ".join(missing) + HINT
