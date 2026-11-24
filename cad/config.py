import configparser
import os

DEFAULTS = {
    "server": {
        "host": "127.0.0.1",
        "port": "8443",
        "tls_cert": "",
        "tls_key": "",
        "trust_proxy": "false",
        # Comma-separated networks allowed to connect, e.g. "10.20.30.0/24, 10.99.0.0/16". Empty = any.
        "allowed_networks": "",
        # Refuse to start without TLS unless explicitly allowed (testing only).
        "allow_insecure_http": "false",
    },
    "agency": {
        "name": "Police Department",
        "ori": "",
        "call_prefix": "",
        "case_prefix": "",
        # Placeholders: {yy} {yyyy} {seq} {call_prefix} {case_prefix}; e.g. {seq:04d} = zero-padded to 4 digits.
        "call_number_format": "{yy}MPD{seq:04d}",
        "case_number_format": "{yy}{case_prefix}-{seq:05d}",
    },
    "security": {
        "session_idle_minutes": "30",
        "session_max_hours": "12",
        "require_mfa": "true",
        # Disable accounts not used for this many days (0 = never).
        "inactive_disable_days": "90",
        "lockout_attempts": "5",
        "lockout_minutes": "15",
        "password_min_length": "12",
    },
    "archive": {
        "dir": "",
        "interval_seconds": "30",
        # Also write a PDF per call and a daily PDF call log.
        "pdf": "true",
    },
    "active911": {
        "enabled": "false",
        # Active911 alert e-mail address(es) per discipline (Agency tab in Active911). Comma-separate several.
        "fire_address": "",
        "ems_address": "",
        "police_address": "",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_security": "starttls",
        "smtp_username": "",
        "smtp_password": "",
        "smtp_password_file": "",
        "from_address": "",
        "include_caller": "false",
        "default_city": "",
    },
    "audit": {
        # Second, append-only copy of the audit log (JSON lines) on another machine/share.
        "mirror_dir": "",
    },
    "storage": {
        "db_path": "data/cad.sqlite3",
        "backup_dir": "data/backups",
        "log_file": "data/logs/server.log",
    },
}


class Config:
    def __init__(self, path=None, overrides=None):
        self.path = path
        self.cp = configparser.ConfigParser()
        self.cp.read_dict(DEFAULTS)
        if path and os.path.exists(path):
            self.cp.read(path, encoding="utf-8")
        for section, values in (overrides or {}).items():
            if not self.cp.has_section(section):
                self.cp.add_section(section)
            for k, v in values.items():
                self.cp.set(section, k, str(v))
        self.base_dir = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()

    def get(self, section, key):
        return self.cp.get(section, key)

    def getint(self, section, key):
        return self.cp.getint(section, key)

    def getbool(self, section, key):
        return self.cp.getboolean(section, key)

    def path_of(self, section, key):
        p = self.get(section, key)
        if not p:
            return ""
        return p if os.path.isabs(p) else os.path.join(self.base_dir, p)
