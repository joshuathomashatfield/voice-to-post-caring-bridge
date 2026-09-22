from backend.privacy import has_high_risk_flags, scan_text


def test_detects_phone_number():
    flags = scan_text("Call me at 555-123-4567 anytime.")
    assert any(f.category == "phone_number" for f in flags)


def test_detects_ssn_like():
    flags = scan_text("SSN: 123-45-6789")
    assert any(f.category == "ssn_like" for f in flags)
    assert has_high_risk_flags(flags)


def test_detects_email():
    flags = scan_text("reach us at family@example.com")
    assert any(f.category == "email" for f in flags)


def test_no_false_positive_on_plain_text():
    flags = scan_text("She had surgery on Tuesday and is resting comfortably.")
    assert flags == []


def test_detects_street_address():
    flags = scan_text("We're staying at 4210 Maple Avenue while she recovers.")
    assert any(f.category == "street_address" for f in flags)
