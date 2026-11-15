# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task

from django_leads.settings import QUEUE_DEFAULT


@shared_task(name="django_leads.refresh_potential", queue=QUEUE_DEFAULT)
def refresh_potential() -> int:
    """Daily beat: silence, return days and manual colours whose return day has come; companies changed."""
    from django_leads.services import potential_service

    return potential_service.sweep()


@shared_task(name="django_leads.refresh_channel_potential", queue=QUEUE_DEFAULT)
def refresh_channel_potential(channel_id: int) -> int:
    """After a change of the channel's potential thresholds."""
    from django_leads.models import Channel
    from django_leads.services import potential_service

    channel = Channel.objects.filter(pk=channel_id).first()
    return potential_service.refresh_channel(channel) if channel else 0
