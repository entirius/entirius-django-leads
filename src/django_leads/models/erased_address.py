# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class ErasedAddress(BaseModel):
    """The token (`utils/emails.anonymised_address`) of an address erased or anonymised by retention — never the
    address itself. Import and form paths refuse an incoming email whose token is listed."""

    token = models.CharField(max_length=254, unique=True)

    class Meta:
        verbose_name_plural = "erased addresses"

    def __str__(self) -> str:
        return self.token
