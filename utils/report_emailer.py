"""Send shipping intelligence PDF reports and alert emails via SMTP.

Supports Gmail, SendGrid SMTP relay, or any standard SMTP provider.
No external dependencies — uses Python's built-in smtplib and email modules.
"""
from __future__ import annotations

import smtplib
import traceback
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from engine.alert_engine_v2 import ShippingAlert


# ─────────────────────────────────────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────────────────────────────────────

def _get_smtp_config() -> dict:
    """Get SMTP config from st.secrets or env vars."""
    import os
    try:
        import streamlit as st
        return {
            "host":      st.secrets.get("SMTP_HOST",         "smtp.gmail.com"),
            "port":      int(st.secrets.get("SMTP_PORT",     "587")),
            "user":      st.secrets.get("SMTP_USER",         ""),
            "password":  st.secrets.get("SMTP_PASSWORD",     ""),
            "from_addr": st.secrets.get("SMTP_FROM",         ""),
            "to_addr":   st.secrets.get("REPORT_TO_EMAIL",   ""),
        }
    except Exception:
        return {
            "host":      os.getenv("SMTP_HOST",        "smtp.gmail.com"),
            "port":      int(os.getenv("SMTP_PORT",    "587")),
            "user":      os.getenv("SMTP_USER",        ""),
            "password":  os.getenv("SMTP_PASSWORD",    ""),
            "from_addr": os.getenv("SMTP_FROM",        ""),
            "to_addr":   os.getenv("REPORT_TO_EMAIL",  ""),
        }


def email_configured() -> bool:
    """Return True if SMTP credentials and recipient are set."""
    cfg = _get_smtp_config()
    return bool(cfg.get("user") and cfg.get("password") and cfg.get("to_addr"))


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send(cfg: dict, msg: MIMEMultipart) -> bool:
    """Open SMTP connection and send *msg*. Returns True on success."""
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg["user"], cfg["password"])
            from_addr = cfg["from_addr"] or cfg["user"]
            server.sendmail(from_addr, cfg["to_addr"], msg.as_string())
        logger.info(f"Email sent to {cfg['to_addr']}: {msg['Subject']}")
        return True
    except Exception as exc:
        logger.error(f"SMTP send failed: {exc}\n{traceback.format_exc()}")
        return False


_SEVERITY_COLORS = {
    "CRITICAL": "#b91c1c",
    "HIGH":     "#ea580c",
    "MEDIUM":   "#d97706",
    "LOW":      "#64748b",
}

_SEVERITY_ICONS = {
    "CRITICAL": "🚨",
    "HIGH":     "🔴",
    "MEDIUM":   "🟡",
    "LOW":      "⚪",
}

_BASE_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body {{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
           background:#0a0f1a;color:#f1f5f9;margin:0;padding:0}}
    .wrapper {{max-width:640px;margin:0 auto;padding:24px 16px}}
    .brand {{font-size:1.25rem;font-weight:800;color:#f1f5f9;
             border-bottom:2px solid #3b82f6;padding-bottom:12px;margin-bottom:20px}}
    .brand span {{color:#3b82f6}}
    .metric-row {{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}}
    .metric {{background:#1a2235;border:1px solid rgba(255,255,255,0.08);
              border-radius:8px;padding:12px 16px;flex:1;min-width:120px}}
    .metric-val {{font-size:1.4rem;font-weight:700;line-height:1}}
    .metric-lbl {{font-size:0.72rem;color:#64748b;margin-top:4px;text-transform:uppercase;
                  letter-spacing:0.06em}}
    .section {{background:#111827;border-radius:10px;padding:18px;margin-bottom:16px;
               border:1px solid rgba(255,255,255,0.07)}}
    .section-title {{font-size:0.8rem;font-weight:700;color:#94a3b8;
                     text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px}}
    .pill {{display:inline-block;padding:3px 10px;border-radius:999px;
            font-size:0.72rem;font-weight:700}}
    .footer {{font-size:0.68rem;color:#334155;margin-top:24px;
              border-top:1px solid rgba(255,255,255,0.06);padding-top:14px}}
  </style>
</head>
<body>
<div class="wrapper">
  <div class="brand">🚢 Ship<span>Tracker</span> Intelligence</div>
  {body_content}
  <div class="footer">
    Ship Tracker · Global Shipping Intelligence Platform ·
    Data: UN Comtrade · FRED · World Bank · yfinance · Freightos FBX<br>
    Not financial advice. Free public data sources only.
  </div>
</div>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def send_report_email(
    pdf_bytes: bytes,
    report_date: str,
    sentiment_label: str,
    signal_count: int,
    risk_level: str,
    top_recommendation: str = "",
) -> bool:
    """Send PDF report via SMTP. Returns True on success.

    Email format:
    - Subject: "Ship Tracker Intelligence Report — {date} — {sentiment_label}"
    - Body: HTML email with key metrics (sentiment, signals, risk, top recommendation)
    - Attachment: shipping_intelligence_{date}.pdf
    """
    cfg = _get_smtp_config()
    if not email_configured():
        logger.warning("SMTP not configured — skipping report email")
        return False

    from_addr = cfg["from_addr"] or cfg["user"]
    subject = f"Ship Tracker Intelligence Report — {report_date} — {sentiment_label}"

    risk_color = {
        "LOW": "#10b981",
        "MODERATE": "#f59e0b",
        "HIGH": "#ef4444",
        "CRITICAL": "#b91c1c",
    }.get(risk_level.upper(), "#94a3b8")

    top_rec_html = ""
    if top_recommendation:
        top_rec_html = f"""
        <div class="section">
          <div class="section-title">Top Recommendation</div>
          <div style="font-size:0.88rem;color:#f1f5f9">{top_recommendation}</div>
        </div>"""

    body_content = f"""
    <div class="metric-row">
      <div class="metric">
        <div class="metric-val" style="color:#3b82f6">{sentiment_label}</div>
        <div class="metric-lbl">Market Sentiment</div>
      </div>
      <div class="metric">
        <div class="metric-val" style="color:#10b981">{signal_count}</div>
        <div class="metric-lbl">Active Signals</div>
      </div>
      <div class="metric">
        <div class="metric-val" style="color:{risk_color}">{risk_level}</div>
        <div class="metric-lbl">Risk Level</div>
      </div>
    </div>
    <div class="section">
      <div class="section-title">Report Summary — {report_date}</div>
      <p style="font-size:0.85rem;color:#94a3b8;margin:0">
        The full intelligence report is attached as a PDF. It covers port demand analysis,
        freight rate trends, alpha signals, macro indicators, and route optimization for all
        tracked lanes.
      </p>
    </div>
    {top_rec_html}
    """

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = cfg["to_addr"]

    html_part = MIMEText(_BASE_HTML.format(body_content=body_content), "html")
    msg.attach(html_part)

    # PDF attachment
    pdf_part = MIMEBase("application", "octet-stream")
    pdf_part.set_payload(pdf_bytes)
    encoders.encode_base64(pdf_part)
    safe_date = report_date.replace("/", "-").replace(" ", "_")
    pdf_part.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"shipping_intelligence_{safe_date}.pdf",
    )
    msg.attach(pdf_part)

    return _send(cfg, msg)


def send_alert_email(alert: "ShippingAlert") -> bool:
    """Send a single alert notification email."""
    cfg = _get_smtp_config()
    if not email_configured():
        logger.warning("SMTP not configured — skipping alert email")
        return False

    from_addr = cfg["from_addr"] or cfg["user"]
    sev_icon = _SEVERITY_ICONS.get(alert.severity, "⚠️")
    sev_color = _SEVERITY_COLORS.get(alert.severity, "#64748b")
    subject = f"{sev_icon} Ship Tracker Alert [{alert.severity}]: {alert.title}"

    ts = alert.created_at[:19].replace("T", " ") + " UTC"
    entity_rows = ""
    if alert.ticker:
        entity_rows += f"<tr><td style='color:#64748b'>Ticker</td><td style='font-weight:600'>{alert.ticker}</td></tr>"
    if alert.route_id:
        entity_rows += f"<tr><td style='color:#64748b'>Route</td><td style='font-weight:600'>{alert.route_id}</td></tr>"
    if alert.port_locode:
        entity_rows += f"<tr><td style='color:#64748b'>Port</td><td style='font-weight:600'>{alert.port_locode}</td></tr>"
    if alert.value:
        entity_rows += f"<tr><td style='color:#64748b'>Value</td><td style='font-weight:600'>{alert.value:,.2f}</td></tr>"
    if alert.change_pct:
        entity_rows += f"<tr><td style='color:#64748b'>Change</td><td style='font-weight:600;color:{sev_color}'>{alert.change_pct:+.2f}%</td></tr>"

    body_content = f"""
    <div class="section" style="border-left:4px solid {sev_color}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
        <span style="font-size:1.5rem">{sev_icon}</span>
        <div>
          <div style="font-size:1rem;font-weight:700;color:{sev_color}">{alert.title}</div>
          <div style="font-size:0.72rem;color:#64748b">{ts} &nbsp;·&nbsp; {alert.alert_type}</div>
        </div>
        <span class="pill" style="background:{sev_color}20;color:{sev_color};
              border:1px solid {sev_color}50;margin-left:auto">{alert.severity}</span>
      </div>
      <p style="font-size:0.85rem;color:#f1f5f9;margin:0 0 12px 0">{alert.body}</p>
      <table style="width:100%;font-size:0.8rem;border-collapse:collapse">
        {entity_rows}
      </table>
    </div>
    """

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = cfg["to_addr"]
    msg.attach(MIMEText(_BASE_HTML.format(body_content=body_content), "html"))

    return _send(cfg, msg)


def send_test_email() -> bool:
    """Send a test email to verify SMTP configuration."""
    cfg = _get_smtp_config()
    if not email_configured():
        return False

    from_addr = cfg["from_addr"] or cfg["user"]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    body_content = f"""
    <div class="section" style="border-left:4px solid #10b981">
      <div class="section-title">Test Email</div>
      <p style="font-size:0.88rem;color:#f1f5f9;margin:0">
        SMTP configuration is working correctly. Sent at {ts}.
      </p>
      <p style="font-size:0.8rem;color:#64748b;margin:10px 0 0 0">
        Host: {cfg['host']} &nbsp;·&nbsp; Port: {cfg['port']} &nbsp;·&nbsp;
        From: {from_addr} &nbsp;·&nbsp; To: {cfg['to_addr']}
      </p>
    </div>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Ship Tracker — SMTP Test Successful"
    msg["From"] = from_addr
    msg["To"] = cfg["to_addr"]
    msg.attach(MIMEText(_BASE_HTML.format(body_content=body_content), "html"))

    return _send(cfg, msg)
