"""
Pluggable SMS delivery for the mobile-OTP password reset flow.

No specific SMS provider (Twilio, MSG91, Fast2SMS, etc.) is wired up here,
since that requires picking one and paying for an account with them. Until
you do that, OTPs are relayed through the admin webhook instead, so the
whole reset-by-phone flow is still fully testable today.

To connect a real provider later: most offer a simple HTTP endpoint that
accepts a POST with a phone number and message. Set its URL in Settings as
"SMS Gateway URL", and adjust the payload shape in send_otp() below to
match that provider's exact API (the key names differ provider to
provider - this sends a generic {"to": ..., "message": ...} shape as a
starting point).
"""
import requests
import db
import monitor

SMS_TIMEOUT = 5


def send_otp(phone_number, otp):
    """Returns True if the OTP was handed off somewhere (gateway or webhook
    relay), False if there was nowhere to send it at all."""
    gateway_url = db.get_setting("sms_gateway_webhook_url", "").strip()
    if gateway_url:
        try:
            requests.post(
                gateway_url,
                json={"to": phone_number, "message": f"Your camera dashboard OTP is {otp}. Valid for 10 minutes."},
                timeout=SMS_TIMEOUT,
            )
            return True
        except Exception:
            pass  # fall through to the webhook relay below

    admin_webhook = db.get_setting("admin_webhook_url", "").strip()
    if admin_webhook:
        masked = phone_number[-4:] if len(phone_number) >= 4 else phone_number
        monitor.notify(
            f"📱 OTP for phone ending in {masked}: {otp} (valid 10 min)\n"
            f"(No SMS gateway configured yet - relayed here instead. "
            f"Set one in Settings to send this as a real text message.)",
            webhook_url=admin_webhook,
        )
        return True

    return False
