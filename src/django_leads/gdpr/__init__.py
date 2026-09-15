# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""GDPR export and erasure across modules. `django_leads.gdpr` is itself a participating hooks module (a package,
because it also holds the protocol and the registry)."""

from django_leads.gdpr.hooks import gdpr_erase, gdpr_export
