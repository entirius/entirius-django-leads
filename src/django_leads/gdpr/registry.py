# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Discovery of GDPR hooks: `<app>.gdpr` of every installed app, imported at call time (never in `ready()`)."""

import importlib
import importlib.util

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

from django_leads.gdpr.protocol import GdprHooks

HOOK_NAMES = ("gdpr_export", "gdpr_erase")


def discover() -> dict[str, GdprHooks]:
    """App name → its `gdpr` module; an app without one is simply absent."""
    modules = {}
    for config in apps.get_app_configs():
        name = f"{config.name}.gdpr"
        if _has_module(name):
            modules[config.name] = _validated(importlib.import_module(name))
    return modules


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _validated(module) -> GdprHooks:
    missing = [hook for hook in HOOK_NAMES if not callable(getattr(module, hook, None))]
    if missing:
        raise ImproperlyConfigured(f"{module.__name__} must define {', '.join(missing)}")
    return module
