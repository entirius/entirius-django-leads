# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.validators import RegexValidator
from django.db import models
from django_utils.models.base_model import BaseModel

from django_leads.enums import AttributeEntity, AttributeKind

validate_attribute_key = RegexValidator(r"^[a-z][a-z0-9_]*$", "Lower-case slug: letters, digits, underscore.")


class AttributeDefinition(BaseModel):
    """A field a channel defines for its companies, contacts or deals; values live in the entity's `attributes` JSON.

    `options` (`[{value, label}]`) is required for the select kinds. The kind is fixed once a value is stored;
    a definition is deactivated rather than deleted. Shapes are checked by `attribute_service`, not here.
    """

    channel = models.ForeignKey("django_leads.Channel", on_delete=models.CASCADE, related_name="attribute_definitions")
    entity = models.CharField(max_length=16, choices=AttributeEntity.choices)
    key = models.CharField(max_length=48, validators=[validate_attribute_key])
    label = models.CharField(max_length=128)
    kind = models.CharField(max_length=16, choices=AttributeKind.choices)
    options = models.JSONField(default=list, blank=True)
    required = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)
    show_in_list = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["entity", "order", "key"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "entity", "key"], name="leads_attribute_channel_entity_key_uniq")
        ]

    def __str__(self) -> str:
        return f"{self.entity}.{self.key}"
