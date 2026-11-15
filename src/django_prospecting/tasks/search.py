# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from celery import shared_task

from django_prospecting import settings as prospecting_settings
from django_prospecting.models import PeopleSearch
from django_prospecting.services import search_service


@shared_task(name="django_prospecting.run_search", queue=prospecting_settings.QUEUE_DEFAULT)
def run_search(search_id: int) -> str:
    search = PeopleSearch.objects.filter(pk=search_id).first()
    if search is None:
        return "missing"
    return search_service.run(search).status
