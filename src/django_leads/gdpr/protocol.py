# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The contract a module's top-level `gdpr` module fulfils to take part in GDPR export and erasure."""

from typing import Any, Protocol


class GdprHooks(Protocol):
    def gdpr_export(self, email: str) -> dict[str, Any]:
        """Everything held about the address: JSON-serialisable (DjangoJSONEncoder), keyed by model name."""
        ...

    def gdpr_erase(self, email: str) -> dict[str, int]:
        """Pseudonymise everything held about the address; counts of rows touched."""
        ...
