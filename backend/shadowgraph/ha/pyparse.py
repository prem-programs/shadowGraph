"""Parse Python source that may use syntax newer than the running interpreter.

Home Assistant requires Python 3.14 and already uses PEP 758 (``except A, B:`` without parentheses).
We run on 3.12+, so when ``ast.parse`` fails we rewrite that one construct and try again. Anything that
still fails is reported, never guessed.
"""
from __future__ import annotations

import ast
import re
import warnings

# `except A, B:` / `except* A, B:` (PEP 758 only allows this form without `as`)
_PEP758 = re.compile(r"^(?P<lead>[ \t]*except\*?[ \t]+)(?P<types>[^\n():#]+,[^\n():#]+?)[ \t]*:", re.M)


def _rewrite(source: str) -> str:
    return _PEP758.sub(lambda m: f"{m.group('lead')}({m.group('types').strip()}):", source)


def parse(source: str, filename: str = "<src>") -> ast.Module | None:
    """Return the module AST, or None when the file cannot be parsed even after the rewrite."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # third-party code: invalid escape sequences are not our business
        try:
            return ast.parse(source, filename=filename)
        except SyntaxError:
            pass
        except ValueError:  # null bytes
            return None
        try:
            return ast.parse(_rewrite(source), filename=filename)
        except (SyntaxError, ValueError):
            return None
