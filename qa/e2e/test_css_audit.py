"""Static CSS rules from qa/e2e/css_audit.py --check as a pytest so CI and the phase gates see them.
Rules: rem type scale, breakpoint ladder, single dark token block, radius/colour tokens."""
import css_audit


def test_css_static_rules():
    failures = css_audit.static_checks()
    assert failures == [], "\n".join(failures)
