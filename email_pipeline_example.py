"""
email_pipeline_example.py

Reference implementation of the email pipeline.
Copy and split into email_setup.py / sender.py / log_utils.py for production use.

Sections:
  1. Configuration  (email_setup.py)
  2. Sender         (sender.py)
  3. Logging        (log_utils.py)
"""

# ============================================================
# 1. CONFIGURATION  (email_setup.py)
# ============================================================
from pathlib import Path

DESKTOP_PATH  = Path.home() / 'Desktop'
DOWNLOAD_BASE = DESKTOP_PATH / 'YourFolder' / 'SubFolder' / 'Data'
LOG_BASE      = DESKTOP_PATH / 'YourFolder' / 'SubFolder' / 'Logs'
MAX_DATA_FOLDERS = 5

# .env file path, holds TMS/SMTP credentials
ENV_PATH = Path(r'\\your-server\path\to\env-folder\.env')

# TMS credentials (.env keys)
TMS_USER_ENV = 'TMSUser'
TMS_PASS_ENV = 'TMSPW'

# SMTP
SMTP_SERVER   = 'your.smtp.server'
SMTP_PORT     = 25
SMTP_USER_ENV = 'SMTPUser'
SMTP_PASS_ENV = 'SMTPPW'

NOTIFICATION_FROM = 'sender@example.com'

ATTACHMENT_MIME = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# Report email, Provider A
EMAIL_SUBJECT = 'Provider A_Report Name_{}_{}'  # .format(region, date)
EMAIL_TO      = ['recipient@example.com']
# CC is region specific, defined per-run in a RUNS dict (see main_example.py)
EMAIL_BCC     = ['bcc@example.com']

# Report email, Provider B
EMAIL_SUBJECT_PROVIDER_B = 'Provider B_Report Name_{}_{}'  # .format(region, date)
EMAIL_TO_PROVIDER_B      = ['partner-recipient@example.com']
# CC is region specific, same per-run cc list as Provider A

# Failure / no-data alert email
EMAIL_SUBJECT_FAILURE = 'FAILED - Provider A_Report Name_{}_{}'  # .format(region, date)
EMAIL_SUBJECT_NO_DATA = 'NO DATA - Provider A_Report Name_{}_{}'  # .format(region, date)
EMAIL_SUBJECT_FAILURE_PROVIDER_B = 'FAILED - Provider B_Report Name_{}_{}'  # .format(region, date)
EMAIL_SUBJECT_NO_DATA_PROVIDER_B = 'NO DATA - Provider B_Report Name_{}_{}'  # .format(region, date)
EMAIL_TO_FAILURE      = ['oncall@example.com']
EMAIL_CC_FAILURE      = []


# ============================================================
# 2. SENDER  (sender.py)
# ============================================================
import os
import smtplib
import logging

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.utils import formatdate
from email import encoders
from typing import List, Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self, user: str, password: str):
        self.user = user
        self.password = password

    def send(
        self,
        subject: str,
        body: str,
        to: List[str],
        cc: List[str] = None,
        bcc: List[str] = None,
        sender: str = None,
        attachment: Optional[Path] = None,
        attachment_mime: tuple = ATTACHMENT_MIME,
    ) -> bool:
        cc  = cc  or []
        bcc = bcc or []
        from_addr = sender or NOTIFICATION_FROM

        root = MIMEMultipart('related')
        root['From']    = from_addr
        root['To']      = ','.join(to)
        root['Cc']      = ','.join(cc)
        root['Date']    = formatdate(localtime=True)
        root['Subject'] = subject

        alt = MIMEMultipart('alternative')
        root.attach(alt)
        alt.attach(MIMEText(body, 'html', 'utf-8'))

        if attachment:
            attachment = Path(attachment)
            if attachment.exists():
                part = MIMEBase(*attachment_mime)
                part.set_payload(attachment.read_bytes())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment', filename=attachment.name)
                root.attach(part)
            else:
                logger.warning(f"Attachment not found, skipping: {attachment}")

        recipients = to + cc + bcc
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
                smtp.login(self.user, self.password)
                smtp.sendmail(from_addr, recipients, root.as_string())
            logger.info(f"Mail sent → {recipients}")
            return True
        except Exception as e:
            logger.error(f"Failed to send mail: {e}", exc_info=True)
            return False


def load_sender() -> Optional[EmailSender]:
    """Load SMTP credentials from .env and return an EmailSender instance."""
    load_dotenv(ENV_PATH)
    user = os.environ.get(SMTP_USER_ENV)
    pw   = os.environ.get(SMTP_PASS_ENV)
    if not user or not pw:
        logger.error("SMTP credentials missing")
        return None
    return EmailSender(user, pw)


# ============================================================
# 3. LOGGING  (log_utils.py)
# ============================================================
import logging.handlers
from datetime import date


def setup_logger(log_dir: Path, name: str = 'main', max_logs: int = 10):
    """Buffer all log records in memory and keep the last N daily log files on disk."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{name}_{date.today().strftime('%Y%m%d')}.log"

    logs = sorted(log_dir.glob(f"{name}_*.log"))
    for old in logs[:-max_logs]:
        old.unlink()

    fmt = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    memory_handler = logging.handlers.MemoryHandler(capacity=10000, flushLevel=logging.CRITICAL)
    memory_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(memory_handler)
    root.addHandler(console_handler)

    return logging.getLogger(name), log_file


def _flush_to_file(log_file: Path) -> None:
    """Write the buffered log records to disk. Call this once, right before exit."""
    root = logging.getLogger()
    memory_handler = next((h for h in root.handlers if isinstance(h, logging.handlers.MemoryHandler)), None)
    if memory_handler is None:
        return
    fmt = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(fmt)
    memory_handler.setTarget(file_handler)
    memory_handler.flush()
    file_handler.close()
    memory_handler.setTarget(None)


def get_log_snippet(log_file: Path) -> str:
    """Extract only WARNING/ERROR lines from the day's log file for the alert email body."""
    if not log_file.exists():
        return '(log file not found)'
    lines = [l.rstrip() for l in log_file.read_text(encoding='utf-8').splitlines()
             if '| ERROR   |' in l or '| WARNING |' in l]
    return '<br>'.join(lines) if lines else '(no warning/error lines found)'


def notify_failure(notifier, subject: str, to: list, cc: list, log_file: Path):
    """Flush the buffered log to disk and email the WARNING/ERROR lines as a failure alert."""
    _flush_to_file(log_file)
    snippet = get_log_snippet(log_file)
    body = (
        '<BODY style="font-size:11pt;font-family:Calibri">'
        'An error occurred. Please see the log below.<br><br>'
        '<b>Log (WARNING / ERROR):</b><br>'
        f'<pre style="font-size:10pt;font-family:Consolas">{snippet}</pre>'
        '</BODY>'
    )
    notifier.send(subject=subject, body=body, to=to, cc=cc)
