"""Which profile a request works in.

Finance data belongs to a profile, not to the person: every finance query filters on the active
profile. auth.refresh_session_user resolves it once per request (the session's choice, else the
profile used last, else the first one) and checks that it belongs to the signed-in user.
Background jobs have no request, so they are handed the profile id explicitly.
"""
from dataclasses import dataclass

from flask import g, has_request_context, session


class NoProfile(RuntimeError):
    pass


def current_profile_id():
    if has_request_context():
        pid = g.get("profile_id")
        if pid is None:
            pid = session.get("profile_id")
        if pid is not None:
            return pid
    raise NoProfile("no active profile: work outside a request must be handed the profile id")


@dataclass(frozen=True)
class Scope:
    """Who is working (per-user things: AI keys, usage, the audit actor) and in which books."""
    uid: int
    pid: int


def current_scope():
    return Scope(session["user_id"], current_profile_id())
