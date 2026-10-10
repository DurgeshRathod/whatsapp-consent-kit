import os
import secrets

os.environ["CONSENT_ENVIRONMENT"] = "test"
os.environ["CONSENT_PUBLIC_BASE_URL"] = "http://testserver"
os.environ["CONSENT_FINGERPRINT_KEY"] = secrets.token_hex(32)
os.environ["CONSENT_SESSION_SECRET"] = secrets.token_hex(32)
os.environ["CONSENT_VERIFICATION_METHOD"] = "fake"
os.environ["CONSENT_WEB_SUBMISSIONS_PER_IP_HOUR"] = "100000"
