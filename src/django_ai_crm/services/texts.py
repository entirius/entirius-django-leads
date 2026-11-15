# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The agent's own sentences in English and Polish (`language` of the chat request; anything else = English)."""

LANGUAGES = ("en", "pl")

TEXTS: dict[str, dict[str, str]] = {
    "plan_empty": {
        "en": "Nothing is waiting for you today.",
        "pl": "Na dziś nic na Ciebie nie czeka.",
    },
    "plan_head": {"en": "Your day, in order:", "pl": "Twój dzień, po kolei:"},
    "plan_hint": {
        "en": 'Tell me what you did, e.g. "done 1: called, interested" or "note 2: asked for a quote".',
        "pl": "Napisz, co zrobiłeś, np. „zrobione 1: dzwoniłem, zainteresowani” albo „notatka 2: prosi o ofertę”.",
    },
    "overdue": {"en": "overdue", "pl": "zaległe"},
    "today": {"en": "today", "pl": "dziś"},
    "drafts_line": {
        "en": "Review the drafts waiting for you: {count} (Approvals).",
        "pl": "Przejrzyj drafty do akceptu: {count} (Approvals).",
    },
    "replies_line": {
        "en": "Answer the replies waiting for a person: {count} (Inbox).",
        "pl": "Obsłuż odpowiedzi czekające na człowieka: {count} (Inbox).",
    },
    "return_line": {
        "en": "Come back to {company} (company #{id}).",
        "pl": "Wróć do firmy {company} (firma #{id}).",
    },
    "task_closed": {"en": "Done: task #{id} closed — {outcome}", "pl": "Gotowe: zadanie #{id} zamknięte — {outcome}"},
    "task_created": {
        "en": "New task #{id} for {company}: {title}",
        "pl": "Nowe zadanie #{id} dla {company}: {title}",
    },
    "note_added": {"en": "Note added to {company}.", "pl": "Notatka dodana do firmy {company}."},
    "deal_moved": {"en": "Deal #{id} moved to {stage}.", "pl": "Deal #{id} przeniesiony do etapu {stage}."},
    "do_not_contact": {
        "en": "{company} is now Do not contact.",
        "pl": "Firma {company} ma teraz Do not contact.",
    },
    "draft_approved": {
        "en": "Draft #{id} approved; it goes out in the next send slot.",
        "pl": "Draft #{id} zaakceptowany; wyjdzie w najbliższym oknie wysyłki.",
    },
    "proposal": {
        "en": "This cannot be undone — please confirm: {summary} (proposal #{id}; answer “yes” or “no”).",
        "pl": "Tego nie da się cofnąć — potwierdź: {summary} (propozycja #{id}; odpowiedz „tak” albo „nie”).",
    },
    "proposal_rejected": {"en": "Cancelled: {summary}", "pl": "Anulowano: {summary}"},
    "no_proposal": {
        "en": "There is nothing waiting for your confirmation.",
        "pl": "Nic nie czeka na Twoje potwierdzenie.",
    },
    "not_understood": {
        "en": "I did not understand that. "
        'Try: "plan", "done 1: <what happened>", "note 1: <text>", "task 1 tomorrow: <title>", '
        '"deal #12 -> <stage>".',
        "pl": "Nie zrozumiałem. "
        "Spróbuj: „plan”, „zrobione 1: <co się stało>”, „notatka 1: <tekst>”, „zadanie 1 jutro: <tytuł>”, "
        "„deal #12 -> <etap>”.",
    },
    "item_not_task": {
        "en": "Item {n} of the plan is not a task with a company.",
        "pl": "Pozycja {n} planu nie jest zadaniem przy firmie.",
    },
    "error": {"en": "I could not do that: {message}", "pl": "Nie udało się: {message}"},
}


def text(key: str, language: str = "en", **values: object) -> str:
    entry = TEXTS[key]
    return entry.get(language if language in LANGUAGES else "en", entry["en"]).format(**values)
