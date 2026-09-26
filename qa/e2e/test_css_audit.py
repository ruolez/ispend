"""Static CSS rules from qa/e2e/css_audit.py --check as a pytest so CI and the phase gates see them.
Rules: rem type scale, breakpoint ladder, single dark token block, radius/colour tokens,
no bare 100vh (pair with dvh) and :hover only inside @media (hover: hover)."""
import css_audit


def test_css_static_rules():
    failures = css_audit.static_checks()
    assert failures == [], "\n".join(failures)



def test_contrast_tokens():
    """WCAG 2.2 AA and APCA floors for every text/surface and control/surface pairing, both themes."""
    failures = css_audit.contrast_checks()
    assert failures == [], "\n".join(failures)
