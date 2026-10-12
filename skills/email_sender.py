"""
DODO — skills/email_sender.py
Advanced, scalable email engine with document & file attachments, multi-mode sending,
smart attachment resolution, clipboard synchronization, and audit history.

Features:
- Document & File Attachments: Attach documents, PDFs, images, code files, archives.
- Smart Attachment Resolver: Automatically resolves relative paths, files on Desktop/Documents/Downloads,
  and special keywords like 'latest_screenshot' / 'screenshot'.
- Multi-Mode Delivery:
    1. Silent SMTP Delivery: Fully automated sending via Gmail, Outlook, or custom SMTP servers (with App Password).
    2. Interactive Browser Compose: Pre-filled Gmail web compose with clipboard image copying & file highlighting.
    3. Desktop Client Draft (.eml): Standard RFC 822 draft with embedded attachments opened directly in Outlook/Windows Mail.
- Safety & Validation: Email address format validation, total attachment size capping (25 MB), UTF-8 encoding.
- Audit & Outbox History: Persistent, thread-safe history in memory/email_history.json.
"""

import os
import io
import re
import json
import time
import logging
import smtplib
import tempfile
import threading
import mimetypes
import subprocess
import webbrowser
import urllib.parse
from datetime import datetime
from typing import Any, Optional, Union

from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid

from core.memory import load_config

logger = logging.getLogger("dodo.email")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_FILE = os.path.join(BASE_DIR, "memory", "email_history.json")
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB limit for standard email providers

_lock = threading.RLock()


# ─────────────────────────────────────────────────────────────────────────────
# Storage & Outbox History
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_dirs():
    """Ensure history directory exists."""
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)


def _load_history() -> list[dict[str, Any]]:
    """Thread-safe load of email history."""
    _ensure_dirs()
    with _lock:
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []


def _save_history_record(record: dict[str, Any]) -> None:
    """Thread-safe save of email history entry."""
    _ensure_dirs()
    with _lock:
        try:
            history = _load_history()
            history.append(record)
            tmp_path = HISTORY_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(history[-100:], f, indent=2)  # retain last 100 entries
            os.replace(tmp_path, HISTORY_FILE)
        except Exception as e:
            logger.warning("Failed to save email history: %s", e)


def get_email_history(limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent email activity history."""
    records = _load_history()
    return records[-limit:]


# ─────────────────────────────────────────────────────────────────────────────
# SMTP & Provider Configuration
# ─────────────────────────────────────────────────────────────────────────────

def _get_smtp_settings() -> dict[str, Any]:
    """Load email configuration from config.json."""
    cfg = load_config()
    email_cfg = cfg.get("email", {}) if isinstance(cfg.get("email"), dict) else {}

    addr = cfg.get("gmail_address") or email_cfg.get("address", "")
    pwd = cfg.get("gmail_app_password") or email_cfg.get("app_password", "")
    host = email_cfg.get("smtp_host") or cfg.get("smtp_host", "smtp.gmail.com")
    port = int(email_cfg.get("smtp_port") or cfg.get("smtp_port", 465))
    use_ssl = email_cfg.get("smtp_ssl", True if port == 465 else False)
    sender_name = email_cfg.get("sender_name") or cfg.get("user_name", "DODO User")
    preferred_mode = email_cfg.get("preferred_mode", "auto")

    return {
        "address": addr.strip() if addr else "",
        "password": pwd.strip() if pwd else "",
        "host": host.strip(),
        "port": port,
        "use_ssl": bool(use_ssl),
        "sender_name": sender_name,
        "preferred_mode": preferred_mode,
        "configured": bool(addr and pwd)
    }


def is_configured() -> bool:
    """Check if SMTP credentials are fully configured for direct sending."""
    return _get_smtp_settings()["configured"]


# ─────────────────────────────────────────────────────────────────────────────
# Smart Attachment Resolution & Validation
# ─────────────────────────────────────────────────────────────────────────────

def _make_attachment_info(file_path: str) -> dict[str, Any]:
    """Build metadata dictionary for an existing file."""
    abs_path = os.path.abspath(file_path)
    filename = os.path.basename(abs_path)
    size_bytes = os.path.getsize(abs_path)
    ctype, _ = mimetypes.guess_type(abs_path)
    return {
        "path": abs_path,
        "filename": filename,
        "size_bytes": size_bytes,
        "size_kb": round(size_bytes / 1024, 1),
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "mime_type": ctype or "application/octet-stream",
        "is_image": (ctype and ctype.startswith("image/")) or filename.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp"))
    }


def resolve_attachment(spec: str) -> Optional[dict[str, Any]]:
    """
    Intelligently resolve a file path, filename, or keyword into an existing file path and metadata.
    Searches common directories (Desktop, Documents, Downloads, Pictures, Screenshots) if path is relative.
    """
    if not spec or not isinstance(spec, str):
        return None
    spec = spec.strip().strip('"').strip("'")
    if not spec:
        return None

    # 1. Special keyword for latest screenshot
    if spec.lower() in ("latest_screenshot", "latest screenshot", "last screenshot", "screenshot", "current screenshot", "latest_ss"):
        try:
            import skills.screenshot_engine as se
            history = se.get_screenshot_history(limit=5)
            if history:
                latest_path = history[-1].get("path")
                if latest_path and os.path.isfile(latest_path):
                    return _make_attachment_info(latest_path)
            ss_dir = se.get_screenshots_dir()
            if os.path.isdir(ss_dir):
                pngs = [os.path.join(ss_dir, f) for f in os.listdir(ss_dir) if f.lower().endswith(".png")]
                if pngs:
                    pngs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    return _make_attachment_info(pngs[0])
        except Exception as e:
            logger.debug("Failed to resolve latest screenshot: %s", e)

    # 2. Direct absolute or relative path
    candidate = os.path.abspath(os.path.expanduser(spec))
    if os.path.isfile(candidate):
        return _make_attachment_info(candidate)

    # 3. Gather common user directories dynamically
    home = os.path.expanduser("~")
    search_dirs = [
        BASE_DIR,
        os.getcwd(),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Pictures"),
        os.path.join(home, "Pictures", "DODO_Screenshots"),
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "OneDrive", "Documents"),
        os.path.join(home, "OneDrive", "Pictures"),
        os.path.join(home, "OneDrive", "รูปภาพ", "dodo screenshot"),
        os.path.join(home, "OneDrive", "รูปภาพ"),
    ]

    try:
        import skills.screenshot_engine as se
        configured_ss = se.get_screenshots_dir()
        if configured_ss and configured_ss not in search_dirs:
            search_dirs.insert(0, configured_ss)
    except Exception:
        pass

    # Check any screenshot directories under OneDrive
    onedrive_dir = os.path.join(home, "OneDrive")
    if os.path.isdir(onedrive_dir):
        try:
            for item in os.listdir(onedrive_dir):
                full_item = os.path.join(onedrive_dir, item)
                if os.path.isdir(full_item):
                    sub = os.path.join(full_item, "dodo screenshot")
                    if os.path.isdir(sub) and sub not in search_dirs:
                        search_dirs.insert(0, sub)
        except OSError:
            pass

    filename = os.path.basename(spec)

    # Direct filename match
    for d in search_dirs:
        if os.path.isdir(d):
            p = os.path.join(d, filename)
            if os.path.isfile(p):
                return _make_attachment_info(p)

    # Case-insensitive search
    for d in search_dirs:
        if os.path.isdir(d):
            try:
                for entry in os.listdir(d):
                    if entry.lower() == filename.lower():
                        p = os.path.join(d, entry)
                        if os.path.isfile(p):
                            return _make_attachment_info(p)
            except OSError:
                pass

    # Fuzzy typo tolerance: find best matching candidate across all directories
    import difflib
    best_candidate = None
    best_score = 0.0
    best_mtime = 0.0

    for d in search_dirs:
        if os.path.isdir(d):
            try:
                for entry in os.listdir(d):
                    full_p = os.path.join(d, entry)
                    if os.path.isfile(full_p):
                        score = difflib.SequenceMatcher(None, filename.lower(), entry.lower()).ratio()
                        if score >= 0.78:
                            mtime = os.path.getmtime(full_p)
                            if score > best_score or (abs(score - best_score) < 0.02 and mtime > best_mtime):
                                best_score = score
                                best_mtime = mtime
                                best_candidate = full_p
            except OSError:
                pass

    if best_candidate and os.path.isfile(best_candidate):
        return _make_attachment_info(best_candidate)

    return None


def parse_and_resolve_attachments(attachments: Optional[Union[str, list[str]]]) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Parse a list or string of attachments, resolve each one, and return (resolved_list, missing_list).
    """
    if not attachments:
        return [], []

    raw_specs: list[str] = []
    if isinstance(attachments, str):
        # Support comma-separated strings or single file path
        if "," in attachments and not os.path.exists(attachments):
            raw_specs = [s.strip() for s in attachments.split(",") if s.strip()]
        else:
            raw_specs = [attachments.strip()]
    elif isinstance(attachments, (list, tuple)):
        raw_specs = [str(s).strip() for s in attachments if str(s).strip()]

    resolved = []
    missing = []
    for spec in raw_specs:
        info = resolve_attachment(spec)
        if info:
            resolved.append(info)
        else:
            missing.append(spec)

    return resolved, missing


# ─────────────────────────────────────────────────────────────────────────────
# MIME Construction & Attachment Encoding
# ─────────────────────────────────────────────────────────────────────────────

def _attach_file_to_mime(msg: MIMEMultipart, file_info: dict[str, Any]) -> None:
    """Attach a single file to a MIMEMultipart message with RFC-compliant headers."""
    file_path = file_info["path"]
    filename = file_info["filename"]
    ctype = file_info["mime_type"]
    maintype, subtype = ctype.split("/", 1) if "/" in ctype else ("application", "octet-stream")

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    part = MIMEBase(maintype, subtype)
    part.set_payload(file_bytes)
    encoders.encode_base64(part)

    # RFC 2231 / RFC 2047 utf-8 attachment header
    part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", filename))
    msg.attach(part)


def build_mime_message(
    sender: str,
    to: str,
    subject: str,
    body: str,
    attachments: Optional[list[dict[str, Any]]] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    html_body: Optional[str] = None,
    sender_name: Optional[str] = None
) -> MIMEMultipart:
    """Build a complete RFC 822 compliant MIME message."""
    msg = MIMEMultipart("mixed")
    from_header = f"{sender_name} <{sender}>" if sender_name else sender
    msg["From"] = from_header
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="dodo.ai")

    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc

    # Content body (plain text + optional HTML)
    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText(body, "plain", "utf-8"))
    if html_body:
        alt_part.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt_part)

    # File attachments
    if attachments:
        for att in attachments:
            _attach_file_to_mime(msg, att)

    return msg


# ─────────────────────────────────────────────────────────────────────────────
# Windows Native Clipboard File Integration (CF_HDROP & CF_DIB)
# ─────────────────────────────────────────────────────────────────────────────

def copy_files_to_windows_clipboard(file_paths: list[str]) -> bool:
    """
    Copy physical files/documents into the Windows Clipboard as real file objects (CF_HDROP).
    When pasted with Ctrl+V into web apps (like Gmail web compose, Slack, Discord, Explorer),
    the browser receives actual file drop events and uploads them as true attachments.
    Also injects CF_DIB if the first file is an image for instant rich inline paste.
    """
    if os.name != "nt" or not file_paths:
        return False

    valid_paths = [os.path.abspath(p) for p in file_paths if os.path.exists(p)]
    if not valid_paths:
        return False

    try:
        import struct
        import win32clipboard
        import win32con

        # Construct DROPFILES struct (20 bytes header followed by null-terminated utf-16 paths)
        files_str = "\0".join(valid_paths) + "\0\0"
        files_bytes = files_str.encode("utf-16le")
        header = struct.pack("IIIIi", 20, 0, 0, 0, 1)
        hdrop_data = header + files_bytes

        # Check if first file is an image to provide dual-format bitmap pasting
        dib_data = None
        first_path = valid_paths[0]
        if first_path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp")):
            try:
                from PIL import Image
                img = Image.open(first_path)
                out = io.BytesIO()
                img.convert("RGB").save(out, "BMP")
                dib_data = out.getvalue()[14:]  # Strip 14-byte BMP file header for DIB
                out.close()
            except Exception as e:
                logger.debug("DIB image prep error: %s", e)

        # Retry clipboard access in case another app has temporary lock
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, hdrop_data)
                if dib_data:
                    win32clipboard.SetClipboardData(win32con.CF_DIB, dib_data)
                win32clipboard.CloseClipboard()
                return True
            except Exception:
                time.sleep(0.05)
        return False
    except Exception as e:
        logger.warning("Failed to copy files to Windows clipboard: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Browser & Desktop Draft Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _auto_paste_attachment_into_browser(file_name: str):
    """
    Background worker that waits for the newly opened browser compose window to render,
    brings it to the foreground, and automatically simulates Ctrl+V so the file is attached
    hands-free without requiring the user to press Ctrl+V manually.
    """
    time.sleep(2.5)  # Allow browser tab to open and gain initial focus
    try:
        import pyautogui
        # Small click or hotkey in the active browser window
        pyautogui.hotkey("ctrl", "v")
        logger.info("Auto-pasted attachment '%s' into compose window", file_name)
    except Exception as e:
        logger.debug("Auto-paste into browser skipped: %s", e)


def _sync_attachments_to_clipboard_or_explorer(resolved_attachments: list[dict[str, Any]]) -> str:
    """
    Prepare attachments for 1-click web compose:
    1. Loads physical files directly into Windows Clipboard as CF_HDROP (+ CF_DIB for images)
       so the file can be attached directly.
    2. Spawns an automated background injector that sends Ctrl+V directly to the browser
       so DODO puts the file there automatically without the user needing to press Ctrl+V!
    3. Opens Windows Explorer with the file highlighted as an instant backup.
    Returns status note for user feedback.
    """
    notes = []
    file_paths = [a["path"] for a in resolved_attachments if os.path.exists(a["path"])]
    file_names = [a["filename"] for a in resolved_attachments]

    # 1. Native Windows Clipboard file injection
    if file_paths:
        copied = copy_files_to_windows_clipboard(file_paths)
        if copied:
            names_str = ", ".join(f"'{name}'" for name in file_names[:3])
            notes.append(f"📎 {names_str} attached to email.")
            # 2. Automatically trigger hands-free paste into browser compose
            threading.Thread(
                target=_auto_paste_attachment_into_browser,
                args=(file_names[0],),
                daemon=True
            ).start()

    # 3. File highlighting in Explorer for backup drag-and-drop
    if file_paths:
        try:
            first_path = os.path.normpath(file_paths[0])
            subprocess.Popen(["explorer.exe", f"/select,{first_path}"])
        except Exception:
            pass

    return "\n".join(notes)


def create_eml_draft(
    to: str,
    subject: str,
    body: str,
    attachments: Optional[list[dict[str, Any]]] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    sender: str = "dodo@localhost",
    open_client: bool = True
) -> tuple[str, str]:
    """
    Generate an RFC 822 .eml draft file with all attachments embedded.
    Optionally opens it in the default desktop email client (Outlook/Windows Mail).
    Returns (eml_path, user_message).
    """
    msg = build_mime_message(
        sender=sender,
        to=to,
        subject=subject,
        body=body,
        attachments=attachments,
        cc=cc,
        bcc=bcc,
        sender_name="DODO"
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_dir = os.path.join(tempfile.gettempdir(), "DODO_Drafts")
    os.makedirs(temp_dir, exist_ok=True)
    eml_path = os.path.join(temp_dir, f"DODO_Email_{ts}.eml")

    with open(eml_path, "wb") as f:
        f.write(msg.as_bytes())

    opened = False
    if open_client:
        try:
            os.startfile(eml_path)
            opened = True
        except Exception as e:
            logger.warning("Could not open .eml in default mail client: %s", e)

    att_summary = f" with {len(attachments)} attachment(s)" if attachments else ""
    client_status = "Opened in desktop mail client." if opened else f"Saved draft to: {eml_path}"
    msg_text = f"📨 Email draft created{att_summary}. {client_status}"
    return eml_path, msg_text


# ─────────────────────────────────────────────────────────────────────────────
# Main Email Sending Engine
# ─────────────────────────────────────────────────────────────────────────────

def send_email(
    to: Union[str, list[str]],
    subject: str,
    body: str,
    attachments: Optional[Union[str, list[str]]] = None,
    cc: Optional[Union[str, list[str]]] = None,
    bcc: Optional[Union[str, list[str]]] = None,
    html_body: Optional[str] = None,
    mode: str = "auto"
) -> str:
    """
    Send or draft an email with document and file attachments.

    Args:
        to: Recipient email address or list of addresses.
        subject: Email subject line.
        body: Main email body text.
        attachments: Single file path, list of file paths, or keyword ('latest_screenshot').
        cc: Optional CC recipient(s).
        bcc: Optional BCC recipient(s).
        html_body: Optional rich HTML body.
        mode: 'auto' (SMTP if configured, else browser), 'smtp', 'browser', or 'desktop_eml'.

    Returns:
        Human-friendly status string detailing sending or drafting outcome.
    """
    # 1. Normalize recipient fields
    to_str = ", ".join(to) if isinstance(to, (list, tuple)) else str(to).strip()
    cc_str = ", ".join(cc) if isinstance(cc, (list, tuple)) else (str(cc).strip() if cc else "")
    bcc_str = ", ".join(bcc) if isinstance(bcc, (list, tuple)) else (str(bcc).strip() if bcc else "")

    if not to_str:
        return "⚠️ Error: Recipient email address ('to') is required."

    # Basic email format check
    first_to = to_str.split(",")[0].strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", first_to):
        return f"⚠️ Warning: '{first_to}' does not look like a valid email address."

    subject = subject.strip() if subject else "(No Subject)"

    # 2. Resolve attachments
    resolved_attachments, missing_attachments = parse_and_resolve_attachments(attachments)

    if missing_attachments:
        missing_list = ", ".join(f"'{m}'" for m in missing_attachments)
        logger.warning("Could not find attachment(s): %s", missing_list)

    total_bytes = sum(a["size_bytes"] for a in resolved_attachments)
    if total_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
        total_mb = round(total_bytes / (1024 * 1024), 1)
        return (
            f"⚠️ Total attachment size ({total_mb} MB) exceeds the 25 MB provider limit.\n"
            f"Please attach smaller files or fewer documents."
        )

    # 3. Determine delivery mode
    smtp_cfg = _get_smtp_settings()
    chosen_mode = mode.lower().strip()
    if chosen_mode == "auto":
        chosen_mode = "smtp" if smtp_cfg["configured"] else "browser"

    # ── Delivery Mode 1: Desktop EML ───────────────────────────────────────────
    if chosen_mode == "desktop_eml":
        eml_path, msg_text = create_eml_draft(
            to=to_str,
            subject=subject,
            body=body,
            attachments=resolved_attachments,
            cc=cc_str,
            bcc=bcc_str,
            sender=smtp_cfg["address"] or "dodo@localhost",
            open_client=True
        )
        _save_history_record({
            "timestamp": datetime.now().isoformat(),
            "to": to_str,
            "subject": subject,
            "mode": "desktop_eml",
            "attachments": [a["filename"] for a in resolved_attachments],
            "status": "drafted"
        })
        return msg_text

    # ── Delivery Mode 2: Direct SMTP (silent & automated) ─────────────────────
    if chosen_mode == "smtp" and smtp_cfg["configured"]:
        sender = smtp_cfg["address"]
        password = smtp_cfg["password"]
        host = smtp_cfg["host"]
        port = smtp_cfg["port"]
        use_ssl = smtp_cfg["use_ssl"]
        sender_name = smtp_cfg["sender_name"]

        try:
            msg = build_mime_message(
                sender=sender,
                to=to_str,
                subject=subject,
                body=body,
                attachments=resolved_attachments,
                cc=cc_str,
                bcc=bcc_str,
                html_body=html_body,
                sender_name=sender_name
            )

            # Build list of all envelope recipients
            all_recipients = [r.strip() for r in to_str.split(",") if r.strip()]
            if cc_str:
                all_recipients.extend([r.strip() for r in cc_str.split(",") if r.strip()])
            if bcc_str:
                all_recipients.extend([r.strip() for r in bcc_str.split(",") if r.strip()])

            if use_ssl or port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=20) as server:
                    server.login(sender, password)
                    server.sendmail(sender, all_recipients, msg.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=20) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(sender, password)
                    server.sendmail(sender, all_recipients, msg.as_string())

            logger.info("Email successfully sent via SMTP to %s", to_str)

            _save_history_record({
                "timestamp": datetime.now().isoformat(),
                "to": to_str,
                "cc": cc_str,
                "bcc": bcc_str,
                "subject": subject,
                "mode": "smtp",
                "attachments": [a["filename"] for a in resolved_attachments],
                "status": "sent"
            })

            att_info = ""
            if resolved_attachments:
                att_info = f"\n📎 Attached {len(resolved_attachments)} file(s): " + ", ".join(
                    f"{a['filename']} ({a['size_kb']} KB)" for a in resolved_attachments
                )

            missing_info = ""
            if missing_attachments:
                missing_info = f"\n⚠️ Note: Could not find: " + ", ".join(f"'{m}'" for m in missing_attachments)

            return f"✅ Email sent to {to_str}!\nSubject: '{subject}'{att_info}{missing_info}"

        except smtplib.SMTPAuthenticationError:
            err_msg = (
                "⚠️ Gmail SMTP Authentication failed.\n"
                "Tip: Gmail requires an App Password (16 characters) created in Google Account -> Security -> 2-Step Verification -> App passwords.\n"
                "Falling back to Gmail web compose draft..."
            )
            logger.warning(err_msg)
            # Fall through to browser mode
        except Exception as e:
            logger.warning("SMTP failed (%s). Falling back to browser draft.", e)
            # Fall through to browser mode

    # ── Delivery Mode 3: Gmail Web Compose (Interactive) ──────────────────────
    # Clean email body: Never append local file path strings to user's body
    draft_body = body

    params: dict[str, str] = {
        "fs": "1",  # Forces compose window
        "to": to_str,
        "su": subject,
        "body": draft_body,
    }
    if cc_str:
        params["cc"] = cc_str
    if bcc_str:
        params["bcc"] = bcc_str

    gmail_url = f"https://mail.google.com/mail/u/0/?view=cm&{urllib.parse.urlencode(params)}"

    try:
        webbrowser.open(gmail_url)
        logger.info("Gmail web compose opened for %s", to_str)

        # Sync attachments to clipboard / open folder in explorer
        helper_note = ""
        if resolved_attachments:
            helper_note = "\n" + _sync_attachments_to_clipboard_or_explorer(resolved_attachments)

        att_summary = ""
        if resolved_attachments:
            att_summary = "\n📎 Files to attach: " + ", ".join(f"{a['filename']}" for a in resolved_attachments)

        missing_info = ""
        if missing_attachments:
            missing_info = f"\n⚠️ Could not locate: " + ", ".join(f"'{m}'" for m in missing_attachments)

        _save_history_record({
            "timestamp": datetime.now().isoformat(),
            "to": to_str,
            "cc": cc_str,
            "bcc": bcc_str,
            "subject": subject,
            "mode": "browser_draft",
            "attachments": [a["filename"] for a in resolved_attachments],
            "status": "drafted"
        })

        instructions = ""
        if resolved_attachments:
            instructions = (
                f"\n\n📎 Attachment:{helper_note}\n"
                f"✨ DODO is attaching the file directly to your Gmail draft.\n"
                f"(For 100% silent sending without opening the browser, add your Gmail App Password to config.json)."
            )

        return (
            f"📧 Gmail opened with your draft and attachment ready!\n"
            f"To: {to_str}\nSubject: {subject}{att_summary}{instructions}{missing_info}\n"
            f"Review and send in your browser when ready."
        )
    except Exception as e:
        return f"Couldn't open Gmail compose: {str(e)[:100]}"
