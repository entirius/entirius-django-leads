# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Receiver plumbing: work runs after the sender commits and never raises into the sender."""

import logging
from collections.abc import Callable

from django.db import transaction

logger = logging.getLogger(__name__)


def after_commit(work: Callable[[], object], label: str) -> None:
    transaction.on_commit(lambda: _safely(work, label))


def _safely(work: Callable[[], object], label: str) -> None:
    try:
        work()
    except Exception:
        logger.exception("leads: receiver %s failed", label)
