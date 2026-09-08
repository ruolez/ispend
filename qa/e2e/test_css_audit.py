"""Static CSS rules from qa/e2e/css_audit.py --check as a pytest so CI and the phase gates see them.
Expected to fail until Phase 3 (rem type scale, breakpoint ladder, single dark token block, radius/colour tokens)."""
import pytest

import css_audit


@pytest.mark.xfail(strict=True, reason="design-system hygiene lands in Phase 3")
def test_css_static_rules():
    failures = css_audit.static_checks()
    assert failures == [], "\n".join(failures)
