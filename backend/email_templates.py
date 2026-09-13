"""Plain-text-first message bodies.

str.format over trusted constants rather than Jinja: nothing here can be tricked into rendering
user data as markup, and every value interpolated into the HTML alternative is escaped.
"""

import html as html_mod

APP = "iSpend"

TEMPLATES = {
    "welcome": {
        "subject": "Welcome to iSpend",
        "text": ("Hi {username},\n\n"
                 "Your account is ready to go, and your free trial runs until {trial_end}.\n\n"
                 "Confirm your email address so you can subscribe when the trial ends:\n{link}\n\n"
                 "If you did not create this account, ignore this message.\n"),
    },
    "verify_email": {
        "subject": "Confirm your email address",
        "text": ("Hi {username},\n\nConfirm your email address for iSpend:\n{link}\n\n"
                 "The link works for the next 7 days.\n"),
    },
    "password_reset": {
        "subject": "Reset your iSpend password",
        "text": ("Hi {username},\n\nUse this link to choose a new password:\n{link}\n\n"
                 "The link works once and expires in an hour. If you did not ask for it, "
                 "nothing has changed and you can safely ignore this message.\n"),
    },
    "password_changed": {
        "subject": "Your iSpend password was changed",
        "text": ("Hi {username},\n\nYour iSpend password has just been changed, and any other "
                 "sessions have been signed out.\n\n"
                 "If that was not you, reset your password straight away.\n"),
    },
    "trial_ending": {
        "subject": "Your iSpend trial ends soon",
        "text": ("Hi {username},\n\nYour free trial ends on {trial_end}. Subscribe to carry on adding "
                 "statements and making changes:\n{link}\n\n"
                 "If you do nothing, nothing is lost — you will still be able to look at "
                 "everything and download it, you just will not be able to make changes.\n"),
    },
    "trial_ended": {
        "subject": "Your iSpend trial has ended",
        "text": ("Hi {username},\n\nYour free trial has ended. You have {days} more days to "
                 "subscribe before iSpend becomes read-only:\n{link}\n"),
    },
    "payment_failed": {
        "subject": "We could not renew your iSpend subscription",
        "text": ("Hi {username},\n\nYour last payment did not go through. Update your payment "
                 "details to keep your subscription:\n{link}\n\n"
                 "Nothing gets deleted. If it is not sorted out, your account goes read-only.\n"),
    },
    "grace_ending": {
        "subject": "iSpend becomes read-only soon",
        "text": ("Hi {username},\n\nWithout a subscription, iSpend becomes read-only on {until}. "
                 "You will still be able to look at everything and download it.\n\n{link}\n"),
    },
    "read_only": {
        "subject": "iSpend is now read-only",
        "text": ("Hi {username},\n\nYour account is now read-only. Everything you have is still there, and you "
                 "can look at it and download it whenever you like.\n\n"
                 "Subscribe to start making changes again:\n{link}\n"),
    },
    "subscription_started": {
        "subject": "Your iSpend subscription is active",
        "text": ("Hi {username},\n\nThank you — you are all set. Stripe will email your receipts, and you can "
                 "manage the plan any time from the Billing page.\n"),
    },
    "subscription_canceled": {
        "subject": "Your iSpend subscription was cancelled",
        "text": ("Hi {username},\n\nYour subscription has been cancelled. Everything you have stays exactly as "
                 "it is — still readable, still downloadable.\n\n"
                 "You can subscribe again whenever you like:\n{link}\n"),
    },
    "test": {
        "subject": "iSpend test email",
        "text": "This is a test message from iSpend. If it arrived, email is working.\n",
    },
}

DEFAULTS = {"username": "there", "link": "", "trial_end": "", "days": "", "until": ""}


def render(template, **ctx):
    spec = TEMPLATES.get(template)
    if spec is None:
        raise KeyError(f"unknown email template {template!r}")
    values = {**DEFAULTS, **{k: ("" if v is None else v) for k, v in ctx.items()}}
    text = spec["text"].format(**values)
    body = "".join(
        f"<p>{html_mod.escape(p).replace(chr(10), '<br>')}</p>"
        for p in text.strip().split("\n\n"))
    html = (f"<html><body style=\"font-family:system-ui,sans-serif;line-height:1.5;color:#202124\">"
            f"{body}<p style=\"color:#5f6368;font-size:13px\">{APP}</p></body></html>")
    return spec["subject"], text, html
