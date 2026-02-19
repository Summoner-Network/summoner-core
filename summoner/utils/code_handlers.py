"""
Using inspection for Python code
"""

from typing import Optional, Set, Any
import inspect
import ast
import textwrap
import types
from pathlib import Path
from importlib import import_module


def get_callable_source(fn: Any, override: Optional[str] = None) -> str:
    """
    Return the best available source-code string for a callable.

    This is a best-effort helper for situations where you want a textual
    representation of a Python function (for display, logging, code export,
    reproducibility tools, etc.), but `inspect.getsource()` may fail.

    Resolution order:
      1) If `override` is a non-empty string, return it.
      2) Try `inspect.getsource(fn)`.
      3) If the object exposes a string attribute named `__dna_source__`,
         return it when non-empty. (This attribute name is conventional and can
         be set by tooling that caches source text externally.)
      4) Fallback to a small syntactically valid stub that raises at runtime.

    Notes
    -----
    - The fallback stub is intentionally minimal and always syntactically valid.
    - This function does not guarantee that the returned source is complete or
      executable in the current environment.

    Parameters
    ----------
    fn:
        A callable-like object (typically a function).
    override:
        Optional explicit source text provided by the caller.

    Returns
    -------
    str:
        A source string for `fn` (or a fallback stub).
    """
    if isinstance(override, str) and override.strip():
        return override

    try:
        return inspect.getsource(fn)
    except Exception: # pylint:disable=broad-exception-caught
        src = getattr(fn, "__dna_source__", None)
        if isinstance(src, str) and src.strip():
            return src

        name = getattr(fn, "__name__", "handler")

        # Prefer matching the coroutine-ness of the original callable.
        if inspect.iscoroutinefunction(fn):
            return (
                f"async def {name}(*args, **kwargs):\n"
                f"    raise RuntimeError('source unavailable')\n"
            )
        return (
            f"def {name}(*args, **kwargs):\n"
            f"    raise RuntimeError('source unavailable')\n"
        )


# pylint:disable=too-many-branches
def extract_annotation_identifiers(src: str) -> Set[str]:
    """
    Extract simple identifier names used inside function annotations from source text.

    This helper parses Python source and returns the set of `ast.Name` identifiers
    that appear in parameter annotations and/or the return annotation of the first
    function definition found in the source.

    Typical use cases:
    - Detecting which names must exist in a namespace to compile/evaluate annotated code.
    - Light static analysis of a function snippet without importing its module.

    Limitations
    -----------
    - Only `ast.Name` nodes are returned (e.g., `Event`), not dotted names
      (e.g., `typing.Any`) or attribute chains (e.g., `module.Event`).
    - Only the first function definition in the parsed source is inspected.
    - If `src` does not parse, an empty set is returned.

    Parameters
    ----------
    src:
        Python source code containing a function definition.

    Returns
    -------
    Set[str]:
        Identifier names referenced in annotations.
    """
    out: Set[str] = set()
    try:
        tree = ast.parse(textwrap.dedent(src))
    except Exception:# pylint:disable=broad-exception-caught
        return out

    fn_node = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_node = node
            break
    if fn_node is None:
        return out

    ann_nodes = []

    # Positional and keyword-only arguments.
    for a in getattr(fn_node.args, "args", []):
        if a.annotation is not None:
            ann_nodes.append(a.annotation)
    for a in getattr(fn_node.args, "kwonlyargs", []):
        if a.annotation is not None:
            ann_nodes.append(a.annotation)

    # *args and **kwargs annotations.
    vararg = getattr(fn_node.args, "vararg", None)
    if vararg is not None and vararg.annotation is not None:
        ann_nodes.append(vararg.annotation)

    kwarg = getattr(fn_node.args, "kwarg", None)
    if kwarg is not None and kwarg.annotation is not None:
        ann_nodes.append(kwarg.annotation)

    # Return annotation.
    if fn_node.returns is not None:
        ann_nodes.append(fn_node.returns)

    # Collect simple identifiers referenced by the annotation AST.
    for ann in ann_nodes:
        for n in ast.walk(ann):
            if isinstance(n, ast.Name):
                out.add(n.id)

    return out


def rebuild_expression_for(value: object, node_type: Optional[type] = None) -> Optional[str]:
    """
    Produce a deterministic reconstruction expression for a small set of values.

    The goal of a "recipe" is to emit a compact Python expression that can
    recreate `value` in another interpreter, assuming required symbols are
    available in scope.

    Current supported recipes:
    - pathlib.Path instances -> `Path("...")`
    - Collections (set/frozenset/list/tuple) of Node-like objects ->
      `set(Node(x) for x in [...])` (only enabled when `node_type` is provided)

    Parameters
    ----------
    value:
        The value to encode.
    node_type:
        Optional class used to recognize "Node-like" elements. If provided, the
        collection recipe is enabled only when all elements are instances of
        `node_type`. If not provided, the Node-collection recipe is disabled.

    Returns
    -------
    Optional[str]:
        A Python expression string, or None if no safe recipe is known.
    """
    if isinstance(value, Path):
        return f'Path({value.as_posix()!r})'

    if node_type is not None and isinstance(value, (set, frozenset, list, tuple)):
        elems = list(value)
        if elems and all(isinstance(e, node_type) for e in elems):
            labels = sorted({str(e) for e in elems})
            # Deterministic: `labels` is sorted and strings are repr'd.
            return f"set({node_type.__name__}(x) for x in {labels!r})"

    return None

# pylint:disable=too-many-return-statements, too-many-branches
def resolve_import_statement(name: str, value: object, known_modules: Set[str]) -> Optional[str]:
    """
    Try to produce a stable Python import statement that binds `name` to `value`.

    This helper is useful when you want to reconstruct an execution environment
    by replaying imports, without serializing complex objects.

    Behavior
    --------
    - If `value` is a module, returns either:
        * `import pkg.mod`                 (if `name` matches the module leaf), or
        * `import pkg.mod as alias`
    - If `value` has a non-`__main__` `__module__`, tries:
        1) `from <module> import <name>` if that attribute equals `value`
        2) `from <module> import <value.__name__>` with optional aliasing
    - As a final fallback, searches already-seen modules in `known_modules` for
      an exported attribute matching `name`.

    Parameters
    ----------
    name:
        The intended binding name in the target namespace (e.g. "Event").
    value:
        The object to import (module, class, function, constant, etc.).
    known_modules:
        A mutable set of module names already considered/seen. This function
        adds to it when it successfully imports a module by name.

    Returns
    -------
    Optional[str]:
        An import statement (single line) or None if no stable import could be found.

    Notes
    -----
    - Imports from `__main__` are deliberately rejected because they are not
      stable across runs and are usually not re-importable by name.
    - The returned string is meant to be executed via `exec` in a target globals
      dict, but this function does not execute anything itself.
    """
    # Modules are always importable by their full name.
    if isinstance(value, types.ModuleType):
        mod = value.__name__
        known_modules.add(mod)
        leaf = mod.split(".")[-1]
        if name == leaf:
            return f"import {mod}"
        return f"import {mod} as {name}"

    mod = getattr(value, "__module__", None)
    if mod == "__main__":
        return None

    # Try importing from the defining module when it looks importable.
    if isinstance(mod, str) and mod and mod != "builtins":
        try:
            m = import_module(mod)
            known_modules.add(mod)
            if getattr(m, name, None) is value:
                return f"from {mod} import {name}"
        except Exception:# pylint:disable=broad-exception-caught
            pass

        obj_name = getattr(value, "__name__", None)
        if isinstance(obj_name, str) and obj_name:
            try:
                m = import_module(mod)
                if getattr(m, obj_name, None) is value:
                    if name == obj_name:
                        return f"from {mod} import {obj_name}"
                    return f"from {mod} import {obj_name} as {name}"
            except Exception:# pylint:disable=broad-exception-caught
                pass

    # Fallback: search modules we've already seen.
    for km in tuple(known_modules):
        try:
            m = import_module(km)
            if getattr(m, name, None) is value:
                return f"from {km} import {name}"
        except Exception:# pylint:disable=broad-exception-caught
            continue

    return None
