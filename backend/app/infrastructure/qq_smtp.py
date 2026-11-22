"""QQ SMTP transport: accepted, definite rejection, or ambiguous outcome."""

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from backend.app.conf.settings import Settings


@dataclass(frozen=True)
class MailResult:
    status: str
    code: str | None = None
    smtp_code: int | None = None


class QQSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: EmailMessage, recipient: str) -> MailResult:
        settings = self.settings
        if not settings.mail_configured:
            return MailResult("failed", "SMTP_NOT_CONFIGURED")
        smtp = None
        submitting = False
        try:
            smtp = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
                context=ssl.create_default_context(),
            )
            smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_auth_code.get_secret_value())
            for code, _reply in (
                smtp.mail(settings.smtp_username),
                smtp.rcpt(recipient),
            ):
                if code not in {250, 251}:
                    return MailResult(
                        "retryable_failure" if 400 <= code < 500 else "failed",
                        "SMTP_REJECTED",
                        code,
                    )
            submitting = True
            code, _reply = smtp.data(message.as_bytes())
            if code == 250:
                return MailResult("accepted", smtp_code=code)
            return MailResult(
                "retryable_failure" if 400 <= code < 500 else "failed", "SMTP_REJECTED", code
            )
        except smtplib.SMTPAuthenticationError as exc:
            return MailResult("failed", "SMTP_AUTH_FAILED", exc.smtp_code)
        except smtplib.SMTPResponseException as exc:
            return MailResult(
                "retryable_failure" if 400 <= exc.smtp_code < 500 else "failed",
                "SMTP_REJECTED",
                exc.smtp_code,
            )
        except (OSError, smtplib.SMTPException):
            return MailResult(
                "unknown" if submitting else "retryable_failure",
                "SMTP_RESULT_UNKNOWN" if submitting else "SMTP_CONNECTION_FAILED",
            )
        finally:
            if smtp is not None:
                # DATA acceptance is authoritative; a QUIT failure cannot undo it.
                try:
                    smtp.close()
                except OSError:
                    pass
