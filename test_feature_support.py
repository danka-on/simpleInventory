"""Fixtures for exercising imported feature functions with isolated dependencies.

Unlike the old source-extraction fixtures, these call the actual imported code.
Use feature_overrides once around concurrent tests; isolated_functions is for
single-call fixtures whose request payload/dependencies change between calls.
"""
from contextlib import ExitStack, contextmanager
from functools import wraps
from importlib import import_module
import inspect
import sys
from types import FunctionType
from unittest.mock import patch

from sweetshelves.legacy import SYMBOL_MODULES, resolve


@contextmanager
def feature_overrides(scope):
    """Patch symbol owners and imported dependencies, restoring everything after use."""
    with ExitStack() as stack:
        modules = [module for name, module in list(sys.modules.items())
                   if name.startswith('sweetshelves.') and module is not None]
        for name, value in scope.items():
            if name.startswith('__'):
                continue
            if isinstance(value, FunctionType):
                value = getattr(value, '_test_function', value)
            owner = SYMBOL_MODULES.get(name)
            if owner:
                stack.enter_context(patch.object(import_module(f'sweetshelves.{owner}'), name, value))
            else:
                for module in modules:
                    if name in vars(module) or name in {'print', 'open'}:
                        stack.enter_context(patch.object(module, name, value, create=True))
        yield


def isolated_functions(names, scope):
    """Install callables backed by real features in a mutable fixture namespace."""
    for name in names:
        function = inspect.unwrap(resolve(name))

        def bind(function):
            @wraps(function)
            def call(*args, **kwargs):
                with feature_overrides(scope):
                    return function(*args, **kwargs)
            call._test_function = function
            return call

        scope[name] = bind(function)
    return scope
