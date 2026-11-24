import configparser
import os

DEFAULTS = {
    "server": {
        "host": "127.0.0.1",
        "port": "8443",
        "tls_cert": "",
        "tls_key": "",
        "trust_proxy": "false",
    },
    "agency": {
        "name": "Police Department",
        "ori": "",
        "call_prefix": "",
        "case_prefix": "",
    },
    "security": {
        "session_idle_minutes": "30",
        "session_max_hours": "12",
        "require_mfa": "false",
        "lockout_attempts": "5",
        "lockout_minutes": "15",
        "password_min_length": "12",
    },
    "storage": {
        "db_path": "data/cad.sqlite3",
        "backup_dir": "data/backups",
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
