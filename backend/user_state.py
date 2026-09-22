"""The user lifecycle, kept out of a blueprint so auth.py can import the login gate.

active  — normal.
locked  — an admin froze the account: cannot sign in, data intact, reversible.
deleted — soft: hidden from the default list, cannot sign in, restorable by an admin.
purged  — no row at all; rows and uploaded files are gone. Only reachable from deleted.
"""

ACTIVE = "active"
LOCKED = "locked"
DELETED = "deleted"
STATUSES = (ACTIVE, LOCKED, DELETED)

# Locked and deleted share one message on purpose: someone holding the password must not be able
# to tell which of the two happened.
BLOCKED_MESSAGE = "This account is locked. Contact your administrator."
UNVERIFIED_MESSAGE = "Confirm your email address first. Check your inbox for the link we sent."
UNVERIFIED_CODE = "email_unverified"


def can_sign_in(row):
    """The single definition of 'this account may hold a session'. Used by auth.login,
    auth.me and auth.refresh_session_user — if these ever disagree, the app logs everyone out."""
    return bool(row) and row.get("status") == ACTIVE


def needs_verification(row, enforced):
    """Self-signups made while verification was required may not sign in until their link is used.
    `enforced` is the live switch (on, and email configured), so turning it off releases every
    pending account — the escape hatch when SMTP is broken. Only auth.login checks this: a pending
    account never holds a session (signup issues none, verify.html does not sign in), and a reset
    link proves the address itself, so refresh_session_user and me deliberately do not re-check."""
    return bool(enforced and row and row.get("verification_required") and not row.get("email_verified_at"))


def restore_target(row):
    """Restoring returns a user to locked when they were locked before being deleted; someone
    locked for cause must not come back able to sign in just because they were also deleted."""
    return LOCKED if row.get("locked_at") else ACTIVE
