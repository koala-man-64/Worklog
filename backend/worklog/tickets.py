"""Tickets: issues the person and agents file, work on and close, then keep as history.

Agents write tickets directly, as they post in discussions. Every change carries the
author's stable communication identity and lands in the ticket's history. Only the
person archives or recovers a ticket, and an archived ticket is read-only.
"""

from __future__ import annotations

# Added in schema v5. Every statement stands alone: execute_schema splits on semicolons.
SCHEMA = """
CREATE TABLE tickets(id TEXT PRIMARY KEY,number INTEGER NOT NULL UNIQUE CHECK(number>=1),title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL DEFAULT 'bug' CHECK(kind IN('bug','incident','task')),priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN('low','normal','high','urgent')),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','doing','blocked','done')),resolution TEXT NOT NULL DEFAULT '',item_id TEXT REFERENCES work_items(id),assignee_id TEXT,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,closed_at TEXT,last_activity_at TEXT NOT NULL);
CREATE INDEX tickets_list ON tickets(archived,status,last_activity_at,id);
CREATE INDEX tickets_item ON tickets(item_id,archived);
CREATE INDEX tickets_assignee ON tickets(assignee_id,archived,status);
CREATE TABLE ticket_repositories(ticket_id TEXT NOT NULL REFERENCES tickets(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(ticket_id,repository_id));
CREATE INDEX ticket_repositories_repository ON ticket_repositories(repository_id,ticket_id);
CREATE TABLE ticket_labels(ticket_id TEXT NOT NULL REFERENCES tickets(id),label TEXT NOT NULL,PRIMARY KEY(ticket_id,label));
CREATE INDEX ticket_labels_label ON ticket_labels(label,ticket_id);
CREATE TABLE ticket_comments(id TEXT PRIMARY KEY,ticket_id TEXT NOT NULL REFERENCES tickets(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),created_at TEXT NOT NULL);
CREATE INDEX ticket_comments_ticket ON ticket_comments(ticket_id,created_at,id);
"""
