# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task

from django_leads.settings import QUEUE_DEFAULT


@shared_task(name="django_leads.rotate_thread", queue=QUEUE_DEFAULT, acks_late=True)
def rotate_thread(thread_id: int) -> bool:
    from django_communicator.models import Thread

    from django_leads.services import rotation_service

    thread = Thread.objects.filter(pk=thread_id).first()
    return thread is not None and rotation_service.rotate_thread(thread) is not None


@shared_task(name="django_leads.rotate_unresponsive", queue=QUEUE_DEFAULT)
def rotate_unresponsive() -> int:
    """Daily beat: catches finished sequences whose `sequence_finished` receiver did not rotate."""
    from django_leads.services import rotation_service

    return rotation_service.rotate_unresponsive()
