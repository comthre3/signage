from main import generate_otp, hash_otp, verify_otp


def test_generate_otp_is_six_digits_numeric():
    otp = generate_otp()
    assert len(otp) == 6
    assert otp.isdigit()


def test_otp_hash_roundtrip():
    otp = "123456"
    stored = hash_otp(otp)
    assert stored != otp
    assert verify_otp(otp, stored) is True
    assert verify_otp("000000", stored) is False


def test_verify_otp_none_stored_returns_false():
    assert verify_otp("123456", None) is False
