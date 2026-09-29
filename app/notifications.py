import logging
from urllib.parse import quote

import requests

from app.config import settings

logger = logging.getLogger("app.notifications")


def send_sms(phone: str, message: str) -> None:
    """
    Fire the configured SMS webhook. Best-effort: logs and swallows any failure so a
    down/misconfigured SMS provider never breaks appointment creation. Runs as a FastAPI
    BackgroundTask, so it never delays the API response either.

    Configure in .env:
        SMS_ENABLED=true
        SMS_API_URL=https://your-provider.com/send?api_key=KEY&to={phone}&msg={message}
        SMS_METHOD=GET   # or POST
    """
    if not settings.sms_enabled:
        return
    if not settings.sms_api_url:
        logger.warning("SMS_ENABLED is true but SMS_API_URL is not set — skipping SMS.")
        return

    url = settings.sms_api_url.format(phone=quote(phone), message=quote(message))
    try:
        if settings.sms_method.upper() == "POST":
            response = requests.post(url, timeout=settings.sms_timeout_seconds)
        else:
            response = requests.get(url, timeout=settings.sms_timeout_seconds)
        response.raise_for_status()
        logger.info("SMS sent to %s (status %s)", phone, response.status_code)
    except requests.RequestException as exc:
        logger.warning("SMS to %s failed: %s", phone, exc)


def build_appointment_message(patient_name, doctor_name, appt_date, appt_time,
                              serial=None, room=None) -> str:
    when = appt_time.strftime("%H:%M") if hasattr(appt_time, "strftime") else appt_time
    msg = (
        f"Dear {patient_name}, your appointment with {doctor_name} is confirmed "
        f"for {appt_date} at {when}."
    )
    if serial:
        msg += f" Serial no. {serial}."
    if room:
        msg += f" Room {room}."
    return msg + " Thank you."
