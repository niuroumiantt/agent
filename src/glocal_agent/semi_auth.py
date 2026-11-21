from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr


class SemiAuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    secure: bool
    user: str
    password: str
    sender: str


@dataclass(frozen=True)
class SemiAuthConfig:
    smtp: SmtpConfig | None
    code_pepper: str
    session_secret: str
    code_ttl_seconds: int = 600
    token_ttl_seconds: int = 2_592_000

    @property
    def configured(self) -> bool:
        return bool(self.smtp and self.code_pepper and self.session_secret)


@dataclass
class CodeRecord:
    email: str
    code_hash: str
    expires_at: float
    attempts: int = 0


def load_semi_auth_config() -> SemiAuthConfig:
    def env(name: str, default: str = "") -> str:
        return os.environ.get(name, default).strip()

    smtp = None
    if env("SEMI_AUTH_SMTP_HOST") or env("SEMI_AUTH_SMTP_USER") or env("SEMI_AUTH_SMTP_PASSWORD"):
        smtp = SmtpConfig(
            host=env("SEMI_AUTH_SMTP_HOST"),
            port=int(env("SEMI_AUTH_SMTP_PORT", "465")),
            secure=env("SEMI_AUTH_SMTP_SECURE", "1") not in {"0", "false", "False"},
            user=env("SEMI_AUTH_SMTP_USER"),
            password=env("SEMI_AUTH_SMTP_PASSWORD"),
            sender=env("SEMI_AUTH_SMTP_FROM"),
        )
    return SemiAuthConfig(
        smtp=smtp,
        code_pepper=env("SEMI_AUTH_CODE_PEPPER"),
        session_secret=env("SEMI_AUTH_SESSION_SECRET"),
        code_ttl_seconds=int(env("SEMI_AUTH_CODE_TTL_SECONDS", "600")),
        token_ttl_seconds=int(env("SEMI_AUTH_TOKEN_TTL_SECONDS", "2592000")),
    )


def send_smtp_code(config: SmtpConfig, email: str, code: str) -> None:
    missing = [
        name
        for name, value in {
            "SEMI_AUTH_SMTP_HOST": config.host,
            "SEMI_AUTH_SMTP_USER": config.user,
            "SEMI_AUTH_SMTP_PASSWORD": config.password,
            "SEMI_AUTH_SMTP_FROM": config.sender,
        }.items()
        if not value
    ]
    if missing:
        raise SemiAuthError(503, "email_not_configured", "Email sending is not configured.")

    message = EmailMessage()
    message["Subject"] = "Your Semi verification code"
    message["From"] = config.sender
    message["To"] = formataddr(("", email))
    message.set_content(
        "Your Semi verification code is:\n\n"
        f"{code}\n\n"
        "This code expires in 10 minutes. If you did not request it, you can ignore this email.\n"
    )
    if config.secure:
        with smtplib.SMTP_SSL(config.host, config.port, timeout=10) as smtp:
            smtp.login(config.user, config.password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config.host, config.port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(config.user, config.password)
            smtp.send_message(message)


class SemiAuthService:
    def __init__(
        self,
        config: SemiAuthConfig,
        sender: Callable[[SmtpConfig, str, str], None] = send_smtp_code,
        now: Callable[[], float] = time.time,
    ):
        self.config = config
        self._sender = sender
        self._now = now
        self._codes: dict[str, CodeRecord] = {}
        self._request_log: dict[str, list[float]] = {}

    def request_code(self, email: str, ip: str) -> dict:
        email = normalize_email(email)
        if not self.config.configured or self.config.smtp is None:
            raise SemiAuthError(503, "email_not_configured", "Email sending is not configured.")
        self._check_rate_limit(email, ip)
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = self._now() + self.config.code_ttl_seconds
        self._codes[email] = CodeRecord(
            email=email,
            code_hash=self._hash_code(email, code),
            expires_at=expires_at,
        )
        try:
            self._sender(self.config.smtp, email, code)
        except SemiAuthError:
            self._codes.pop(email, None)
            raise
        except Exception as exc:
            self._codes.pop(email, None)
            raise SemiAuthError(
                502, "email_send_failed", "Could not send verification email."
            ) from exc
        return {"ok": True, "expires_in": self.config.code_ttl_seconds}

    def verify_code(self, email: str, code: str) -> dict:
        email = normalize_email(email)
        if not code or not code.isdigit() or len(code) != 6:
            raise SemiAuthError(422, "invalid_code", "Verification code must be 6 digits.")
        record = self._codes.get(email)
        if record is None or record.expires_at < self._now():
            self._codes.pop(email, None)
            raise SemiAuthError(400, "code_expired", "Verification code expired.")
        if record.attempts >= 5:
            self._codes.pop(email, None)
            raise SemiAuthError(429, "too_many_attempts", "Too many verification attempts.")
        record.attempts += 1
        if not hmac.compare_digest(record.code_hash, self._hash_code(email, code)):
            raise SemiAuthError(400, "invalid_code", "Verification code is incorrect.")
        self._codes.pop(email, None)
        token = self._sign_session(email)
        return {"ok": True, "session": {"email": email, "token": token}}

    def session(self, token: str) -> dict:
        payload = self._verify_session(token)
        return {"ok": True, "session": {"email": payload["email"], "expires_at": payload["exp"]}}

    def _check_rate_limit(self, email: str, ip: str) -> None:
        now = self._now()
        window = now - 900
        keys = (f"email:{email}", f"ip:{ip}")
        for key in keys:
            recent = [stamp for stamp in self._request_log.get(key, []) if stamp >= window]
            if len(recent) >= 5:
                self._request_log[key] = recent
                raise SemiAuthError(429, "rate_limited", "Too many verification requests.")
            recent.append(now)
            self._request_log[key] = recent

    def _hash_code(self, email: str, code: str) -> str:
        return hmac.new(
            self.config.code_pepper.encode(),
            f"{email}:{code}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _sign_session(self, email: str) -> str:
        now = int(self._now())
        payload = {"email": email, "iat": now, "exp": now + self.config.token_ttl_seconds}
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        sig = _b64(
            hmac.new(
                self.config.session_secret.encode(), body.encode(), hashlib.sha256
            ).digest()
        )
        return f"{body}.{sig}"

    def _verify_session(self, token: str) -> dict:
        try:
            body, sig = token.split(".", 1)
            expected = _b64(
                hmac.new(
                    self.config.session_secret.encode(), body.encode(), hashlib.sha256
                ).digest()
            )
            if not hmac.compare_digest(sig, expected):
                raise ValueError
            payload = json.loads(_unb64(body))
            if payload["exp"] < int(self._now()):
                raise ValueError
            normalize_email(payload["email"])
            return payload
        except Exception as exc:
            raise SemiAuthError(401, "invalid_session", "Session is invalid or expired.") from exc


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if len(value) > 254 or "@" not in value or value.startswith("@") or value.endswith("@"):
        raise SemiAuthError(422, "invalid_email", "Email address is invalid.")
    local, domain = value.rsplit("@", 1)
    if not local or "." not in domain or any(char.isspace() for char in value):
        raise SemiAuthError(422, "invalid_email", "Email address is invalid.")
    return value


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
