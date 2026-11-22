import smtplib
from datetime import UTC, date, datetime
from email.message import EmailMessage

from backend.app.domain.schedule import local_due, next_due
from backend.app.infrastructure.qq_smtp import QQSender


def test_dst_gap_and_fold_are_single_daily_slots():
    assert local_due(date(2026, 3, 8), "02:30", "America/New_York") == datetime(
        2026, 3, 8, 7, tzinfo=UTC
    )
    assert local_due(date(2026, 11, 1), "01:30", "America/New_York") == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )
    assert next_due(
        datetime(2026, 11, 1, 6, tzinfo=UTC), "01:30", "America/New_York"
    ).date() == date(2026, 11, 2)


def test_smtp_acceptance_ambiguity_and_authentication(settings, monkeypatch):
    settings.smtp_username = "sender@example.com"
    from pydantic import SecretStr

    settings.smtp_auth_code = SecretStr("offline-test-code")
    mode = ["accepted"]

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            return (250, b"ok")

        def login(self, *args):
            if mode[0] == "auth":
                raise smtplib.SMTPAuthenticationError(535, b"denied")

        def mail(self, *args):
            return (250, b"ok")

        def rcpt(self, *args):
            return (250, b"ok")

        def data(self, *args):
            if mode[0] == "unknown":
                raise TimeoutError("reply lost after DATA")
            return (250, b"accepted")

        def close(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    message = EmailMessage()
    message.set_content("offline fixture")
    sender = QQSender(settings)
    assert sender.send(message, "reader@example.com").status == "accepted"
    mode[0] = "unknown"
    assert sender.send(message, "reader@example.com").status == "unknown"
    mode[0] = "auth"
    assert sender.send(message, "reader@example.com").status == "failed"
