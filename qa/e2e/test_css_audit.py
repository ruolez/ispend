"""Static CSS rules from qa/e2e/css_audit.py --check as a pytest so CI and the phase gates see them.
Rules: rem type scale, breakpoint ladder, single dark token block, radius/colour tokens."""
import css_audit
import pytest


def test_css_static_rules():
    failures = css_audit.static_checks()
    assert failures == [], "\n".join(failures)


@pytest.mark.xfail(strict=True, reason="dvh + hover gating land in mobile makeover Phase 1")
def test_css_touch_rules():
    failures = css_audit.touch_checks()
    assert failures == [], "\n".join(failures)
