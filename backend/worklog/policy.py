"""Who may call each API route: the person alone, or an agent too, and in which scope (consolidation
step 3.1).

Every route under /api/v1 has an entry for each method it serves, and the middleware lets an agent's
call through to a route's handler only in an agent scope. The handler may check further: who owns a
proposal, who registered an identity, whether a credential was revoked while the call waited for the
write lock. A method a route has no entry for, HEAD among them, is the person's. A call no route serves
reaches no handler: the middleware lets an agent's read go on to the router's 404 or redirect, unless
the redirect leads to the person's page or to a path that serves no read, and refuses any other call
(passes_unserved).

Agents read what they read before this table (K2), apart from the person's own pages: settings, export
and the API document, the agent registry, the list of tracked chats, each chat's messages and
transcript, and the Claude Code sessions running on the person's machine. A transcript holds a whole
conversation, tool output included, and a chat's messages are the person's prompts to it: agents take
theirs from the inbox. The agent registry joins tracked chats into every agent's record, as a
roadmap's view of its agents does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import QueryParams
from starlette.routing import BaseRoute, Match
from starlette.types import Scope

PREFIX = "/api/v1"

HUMAN = "human"  # the person's alone
PUBLIC = "public"  # needs no credential: the session bootstrap, which checks its own caller
READ = "agent:read"  # what agents read (K2)
PROPOSE = "agent:propose"  # drafts and their evidence, which the person approves or rejects
DISCUSS = "agent:discuss"  # discussion threads, requests and roles
COORDINATE = "agent:coordinate"  # an agent's own runs and participations, and the repositories they name
TICKET = "agent:ticket"  # filing, changing and commenting on tickets
REGISTER = "agent:register"  # registering and retiring agent identities
INBOX = "agent:inbox"  # taking the messages the person writes into an agent's chats
TIE = "agent:tie"  # an agent's own ties to records, and its role on each


@dataclass(frozen=True)
class Only:
    """An agent scope that holds only while one path or query parameter has one of some values; with
    any other value the route is the person's. A repeated query parameter counts by its last value, as
    the route's handler reads it."""

    scope: str
    parameter: str
    values: frozenset[str]
    query: bool = False


# Each route's template under PREFIX, with the scope of each method it serves.
ROUTES: dict[str, dict[str, str | Only]] = {
    "/agent-links": {"GET": READ, "POST": TIE},
    "/agent-links/unlink": {"POST": TIE},

    "/agent-registrations": {"POST": REGISTER},
    "/agent-registrations/retire": {"POST": REGISTER},

    "/agent-runs": {"GET": READ, "POST": COORDINATE},
    "/agent-runs/{identity}": {"GET": READ},
    "/agent-runs/{identity}/end": {"POST": COORDINATE},

    "/agents": {"GET": HUMAN},
    "/agents/{identity}": {"GET": HUMAN},
    "/agents/{identity}/activity": {"GET": HUMAN},
    "/agents/{identity}/proposals": {"GET": HUMAN},

    "/attachments": {"GET": READ, "POST": Only(PROPOSE, "ownerType", frozenset({"proposal"}), query=True)},
    "/attachments/{identity}": {"GET": READ},
    "/attachments/{identity}/archive": {"POST": HUMAN},
    "/attachments/{identity}/recover": {"POST": HUMAN},

    "/backups": {"POST": HUMAN},

    "/chat-confirmations": {"POST": HUMAN},
    "/chat-confirmations/remove": {"POST": HUMAN},

    "/chats": {"GET": HUMAN, "POST": HUMAN},
    "/chats/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/chats/{identity}/archive": {"POST": HUMAN},
    "/chats/{identity}/confirmations": {"POST": HUMAN},
    "/chats/{identity}/confirmations/remove": {"POST": HUMAN},
    "/chats/{identity}/messages": {"GET": HUMAN, "POST": HUMAN},
    "/chats/{identity}/recover": {"POST": HUMAN},
    "/chats/{identity}/transcript": {"GET": HUMAN},

    "/claude-sessions": {"GET": HUMAN},

    "/context": {"GET": READ},

    "/coordination-updates": {"GET": READ},

    "/credentials": {"POST": HUMAN},
    "/credentials/{identity}/revoke": {"POST": HUMAN},
    "/credentials/{identity}/rotate": {"POST": HUMAN},

    "/days/{date}": {"GET": READ},
    "/days/{date}/entries/{item_id}": {"PUT": HUMAN},
    "/days/{date}/entries/{item_id}/remove": {"POST": HUMAN},
    "/days/{date}/reflection": {"PUT": HUMAN},
    "/days/{date}/reorder": {"POST": HUMAN},
    "/days/{date}/reschedule": {"POST": HUMAN},

    "/decisions": {"POST": HUMAN},
    "/decisions/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/decisions/{identity}/archive": {"POST": HUMAN},
    "/decisions/{identity}/recover": {"POST": HUMAN},

    "/discussions": {"GET": READ, "POST": DISCUSS},
    "/discussions/avatar": {"POST": DISCUSS},
    "/discussions/avatars": {"GET": READ},
    "/discussions/changes": {"GET": READ},
    "/discussions/messages/{identity}": {"GET": READ},
    "/discussions/messages/{identity}/acknowledge": {"POST": DISCUSS},
    "/discussions/messages/{identity}/cancel": {"POST": DISCUSS},
    "/discussions/messages/{identity}/release": {"POST": HUMAN},
    "/discussions/participants": {"GET": READ},
    "/discussions/resume": {"GET": READ},
    "/discussions/roles": {"GET": READ, "POST": DISCUSS},
    "/discussions/roles/{identity}/accept": {"POST": DISCUSS},
    "/discussions/roles/{identity}/claim": {"POST": DISCUSS},
    "/discussions/roles/{identity}/decline": {"POST": DISCUSS},
    "/discussions/roles/{identity}/offer": {"POST": DISCUSS},
    "/discussions/roles/{identity}/reassign": {"POST": HUMAN},
    "/discussions/roles/{identity}/release": {"POST": DISCUSS},
    "/discussions/roles/{identity}/withdraw": {"POST": DISCUSS},
    "/discussions/{identity}": {"GET": READ},
    "/discussions/{identity}/archive": {"POST": HUMAN},
    "/discussions/{identity}/messages": {"POST": DISCUSS},

    "/export": {"GET": HUMAN},

    "/github": {"POST": HUMAN},
    "/github/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/github/{identity}/archive": {"POST": HUMAN},
    "/github/{identity}/recover": {"POST": HUMAN},

    "/history": {"GET": READ},

    "/imports/preview": {"POST": HUMAN},
    "/imports/{identity}/commit": {"POST": HUMAN},
    "/imports/{identity}/download": {"GET": READ},

    "/inbox/receive": {"POST": INBOX},

    "/links": {"POST": HUMAN},
    "/links/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/links/{identity}/archive": {"POST": HUMAN},
    "/links/{identity}/recover": {"POST": HUMAN},

    "/notes": {"POST": HUMAN},
    "/notes/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/notes/{identity}/archive": {"POST": HUMAN},
    "/notes/{identity}/recover": {"POST": HUMAN},

    "/openapi.json": {"GET": HUMAN},

    "/participations": {"GET": READ, "POST": COORDINATE},
    "/participations/{identity}": {"GET": READ, "PATCH": COORDINATE},
    "/participations/{identity}/check-in": {"POST": COORDINATE},
    "/participations/{identity}/rejoin": {"POST": COORDINATE},
    "/participations/{identity}/release": {"POST": COORDINATE},
    "/participations/{identity}/updates": {"POST": COORDINATE},

    "/people": {"GET": READ, "POST": HUMAN},
    "/people/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/people/{identity}/{verb}": {"POST": HUMAN},

    "/person-links": {"POST": HUMAN},
    "/person-links/unlink": {"POST": HUMAN},

    "/proposals": {"GET": READ, "POST": PROPOSE},
    "/proposals/{identity}": {"GET": READ, "PATCH": PROPOSE},
    "/proposals/{identity}/{verb}": {"POST": Only(PROPOSE, "verb", frozenset({"submit", "withdraw", "reopen"}))},

    "/questions": {"POST": HUMAN},
    "/questions/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/questions/{identity}/archive": {"POST": HUMAN},
    "/questions/{identity}/recover": {"POST": HUMAN},

    "/references": {"POST": HUMAN},
    "/references/{identity}": {"GET": READ},
    "/references/{identity}/archive": {"POST": HUMAN},
    "/references/{identity}/recover": {"POST": HUMAN},

    "/repositories": {"GET": READ, "POST": HUMAN},
    "/repositories/ensure": {"POST": COORDINATE},
    "/repositories/resolve": {"GET": READ},
    "/repositories/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/repositories/{identity}/confirm": {"POST": HUMAN},
    "/repositories/{identity}/merge": {"POST": HUMAN},
    "/repositories/{identity}/{verb}": {"POST": HUMAN},

    "/resources": {"GET": READ, "POST": HUMAN},
    "/resources/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/resources/{identity}/{verb}": {"POST": HUMAN},

    "/roadmaps": {"GET": READ, "POST": HUMAN},
    "/roadmaps/{identity}": {"GET": READ},
    "/roadmaps/{identity}/agents": {"GET": HUMAN},
    "/roadmaps/{identity}/move": {"POST": HUMAN},
    "/roadmaps/{identity}/proposals": {"GET": READ},
    "/roadmaps/{identity}/restore": {"POST": HUMAN},
    "/roadmaps/{identity}/revisions": {"POST": HUMAN},
    "/roadmaps/{identity}/revisions/{number}": {"GET": READ},
    "/roadmaps/{identity}/{verb}": {"POST": HUMAN},

    "/search": {"GET": READ},

    "/session/bootstrap": {"POST": PUBLIC},

    "/settings": {"GET": HUMAN, "PATCH": HUMAN},

    "/steps/{identity}": {"GET": READ},
    "/steps/{identity}/history": {"GET": READ},
    "/steps/{identity}/progress": {"PATCH": HUMAN},

    "/tasks": {"POST": HUMAN},
    "/tasks/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/tasks/{identity}/archive": {"POST": HUMAN},
    "/tasks/{identity}/move": {"POST": HUMAN},
    "/tasks/{identity}/recover": {"POST": HUMAN},

    "/tickets": {"GET": READ, "POST": TICKET},
    "/tickets/{ref}": {"GET": READ, "PATCH": TICKET},
    "/tickets/{ref}/comments": {"POST": TICKET},
    "/tickets/{ref}/{verb}": {"POST": HUMAN},

    "/work-items": {"GET": READ, "POST": HUMAN},
    "/work-items/reorder": {"POST": HUMAN},
    "/work-items/{identity}": {"GET": READ, "PATCH": HUMAN},
    "/work-items/{identity}/move": {"POST": HUMAN},
    "/work-items/{identity}/proposals": {"GET": READ},
    "/work-items/{identity}/rank": {"POST": HUMAN},
    "/work-items/{identity}/{verb}": {"POST": HUMAN},
}


def served(routes: Iterable[BaseRoute], scope: Scope) -> tuple[str, dict[str, Any]] | None:
    """The template and path parameters of the route whose handler a request reaches, found as the
    router finds it: the first route that serves the request's path and method. None when no route
    does: the router then answers 404, 405 or a redirect, and no handler runs."""
    for route in routes:
        match, child = route.matches(scope)
        if match is Match.FULL:
            return getattr(route, "path", ""), dict(child.get("path_params", {}))
    return None


def scope_for(method: str, template: str, path_params: Mapping[str, Any], query_string: bytes = b"") -> str:
    """The scope of a call to a route: the route's entry for the method, or the person's without one."""
    entry = ROUTES.get(template.removeprefix(PREFIX), {}) if template.startswith(PREFIX + "/") else {}
    rule = entry.get(method, HUMAN)
    if isinstance(rule, Only):
        value = QueryParams(query_string).get(rule.parameter) if rule.query else path_params.get(rule.parameter)
        return rule.scope if value in rule.values else HUMAN
    return rule


def decide(routes: Iterable[BaseRoute], scope: Scope) -> str | None:
    """The scope of the call a request makes, or None when it reaches no handler (see served)."""
    found = served(routes, scope)
    if found is None:
        return None
    template, path_params = found
    return scope_for(scope["method"], template, path_params, scope.get("query_string", b""))


def agent_may(scope: str) -> bool:
    """Whether an agent's call in this scope gets past the middleware to the route's handler."""
    return scope != HUMAN


def passes_unserved(routes: Iterable[BaseRoute], scope: Scope) -> bool:
    """Whether an agent's call no route serves goes on to the router, which runs no handler for it.

    Only a read does. The router answers it with 404, or redirects it when some route serves the path
    with its trailing slash added or removed, and a read it would redirect is decided now as the read it
    becomes: a slash cannot turn the person's page into a redirect an agent follows. A path that serves
    no read leads to a 405, and a method a route has no entry for is the person's, so that read is
    refused too. Anything else no route serves is refused."""
    if scope["method"] != "GET":
        return False
    path = scope["path"]
    toggled = {**scope, "path": path.rstrip("/") if path.endswith("/") else path + "/"}
    elsewhere = False
    for route in routes:
        match, child = route.matches(toggled)
        if match is Match.FULL:
            return agent_may(scope_for("GET", getattr(route, "path", ""), dict(child.get("path_params", {})),
                                       scope.get("query_string", b"")))
        elsewhere = elsewhere or match is Match.PARTIAL
    return not elsewhere
