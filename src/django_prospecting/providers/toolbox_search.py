# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Public search through the AI toolbox: public profiles found by a search engine, the company's own pages and public
registers — never a logged-in session of any site. The toolbox does the searching; this asks and validates."""

import json

from django_utils.toolbox import CompletionRequest, Message, ToolboxClient, ToolboxError
from pydantic import ValidationError

from django_prospecting import settings as prospecting_settings
from django_prospecting.providers.base import JSON_SCHEMA, ProviderError, SearchAnswer

TAG = "prospecting.find_people"
PROMPT = (
    "Find up to {limit} people who work today at the company below and could buy B2B e-commerce services "
    "(owners, managers of e-commerce, marketing or sales). Use only public sources without logging in: public "
    "profiles found through a search engine, the company's website (team, contact, footer) and public registers "
    "(board members). For every person give the job title, whether the role fits the offer, whether they still "
    "work there (null when not sure), the public profile URL and the source with its date. Also list e-mail "
    "addresses of the company published anywhere, with the person they belong to when known. Never guess an "
    "address. Answer only with the JSON object of the schema."
)


class ToolboxSearch:
    def find_people(self, *, company_name: str, domain: str, channel_idx: str) -> SearchAnswer:
        if not prospecting_settings.PROSPECTING_SEARCH_MODEL:
            raise ProviderError("PROSPECTING_SEARCH_MODEL is not set")
        company = json.dumps({"company": company_name, "domain": domain}, ensure_ascii=False)
        request = CompletionRequest(
            model=prospecting_settings.PROSPECTING_SEARCH_MODEL,
            messages=[
                Message(role="system", content=PROMPT.format(limit=prospecting_settings.PROSPECTING_MAX_CANDIDATES)),
                Message(role="user", content=company),
            ],
            json_schema=JSON_SCHEMA,
            tags=[TAG, f"channel:{channel_idx}"[:64]],
        )
        try:
            with ToolboxClient() as client:
                parsed = client.complete(request).parsed
        except ToolboxError as error:
            raise ProviderError(f"{type(error).__name__} {error.code or '-'}") from None
        if parsed is None:
            raise ProviderError("the toolbox answer has no JSON object")
        try:
            return SearchAnswer.model_validate(parsed)
        except ValidationError:
            raise ProviderError("the toolbox answer does not match the schema") from None
