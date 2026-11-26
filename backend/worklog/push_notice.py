"""Format the same reference-only notice for either provider."""

RECORD_NOTICES = {
    ("ticket.status", "ticket"): "Ticket {} changed status.",
    ("item.status", "item"): "Work item {} changed status.",
    ("run.update", "run"): "Run {} was updated.",
    ("run.end", "run"): "Run {} ended.",
}


def notice_text(fields: dict[str, str]) -> str | None:
    kind, subject = fields["kind"], fields["subject_type"]
    if subject == "discussion" and kind in {"discussion.ask", "discussion.reply"}:
        message_id = fields.get("message_id")
        if not message_id or fields.get("discussion_id") != fields["subject_id"]:
            return None
        action = "request" if kind == "discussion.ask" else "reply"
        notice = f"Worklog {action}. Read message {message_id} in discussion {fields['subject_id']}."
        if kind == "discussion.ask":
            notice += f" To answer, reply in Worklog with --reply-to {message_id}."
        return notice
    template = RECORD_NOTICES.get((kind, subject))
    return template.format(fields["subject_id"]) + " Read the record in Worklog." if template else None
