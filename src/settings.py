"""Validated immutable settings. Environment parsing never opens a database."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent


class SettingsError(ValueError):
    """Configuration failure whose message contains names, never supplied values."""


@dataclass(frozen=True)
class Settings:
    profile: str
    dialect: str
    sqlite_path: Path
    schema_json: Path
    schema_text: str
    server: str
    database: str
    auth: str
    schemas: tuple[str, ...]
    driver: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    managed_identity_client_id: str = field(repr=False)
    trust_local_certificate: bool
    connect_timeout: int
    query_timeout: int
    connect_retries: int
    max_rows: int
    max_bytes: int
    snapshot_ttl_seconds: int = 3600
    sample_rows: int = 5
    sample_value_chars: int = 160
    sample_total_bytes: int = 65536
    sample_scan_rows: int = 10000
    discovery_timeout_seconds: int = 30
    context_chars: int = 16000

    def public_database_config(self):
        """Legacy dictionary bridge deliberately excludes credentials and hosts."""
        return {"PROFILE": self.profile, "DIALECT": self.dialect,
                "SQLITE_PATH": str(self.sqlite_path), "SCHEMA_JSON": str(self.schema_json),
                "SCHEMA_TEXT": self.schema_text, "SCHEMA_SCOPE": list(self.schemas),
                "AUTH": self.auth}


from contextvars import ContextVar
from contextlib import contextmanager
_RUNTIME = ContextVar('dbmind_database_settings', default=None)

@contextmanager
def configured(settings):
    token = _RUNTIME.set(settings)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


def load_settings(environ=None):
    if environ is None and _RUNTIME.get() is not None:
        return _RUNTIME.get()
    env = os.environ if environ is None else environ

    def value(name, default=""):
        return env.get(name, default).strip()

    def number(name, default, low, high):
        try:
            result = int(value(name, str(default)))
        except ValueError:
            raise SettingsError(f"{name} must be an integer") from None
        if not low <= result <= high:
            raise SettingsError(f"{name} must be between {low} and {high}")
        return result

    def path(name, default):
        raw = value(name, default)
        if not raw or "\x00" in raw:
            raise SettingsError(f"{name} must be a nonempty file path")
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_absolute() else ROOT / candidate

    profile = value("DB_PROFILE", "local_synthetic").lower()
    if profile not in {"local_synthetic", "azure_sql", "local_sqlserver"}:
        raise SettingsError("DB_PROFILE must be local_synthetic, azure_sql, or local_sqlserver")
    dialect = "sqlite" if profile == "local_synthetic" else "sqlserver"
    if value("DB_DIALECT", dialect).lower() != dialect:
        raise SettingsError("DB_DIALECT conflicts with DB_PROFILE; select the profile explicitly")
    scope = tuple(value("DB_SCHEMA_SCOPE", "demo" if dialect == "sqlserver" else "main").split(","))
    if scope != (("main",) if dialect == "sqlite" else ("demo",)):
        raise SettingsError("DB_SCHEMA_SCOPE must be main for local_synthetic or demo for SQL Server")
    server, database, auth = value("DB_SERVER"), value("DB_NAME"), value("DB_AUTH").lower()
    username, password = value("DB_USER"), env.get("DB_PASSWORD", "")
    if "\x00" in username or "\x00" in password:
        raise SettingsError("DB_USER and DB_PASSWORD must not contain NUL characters")
    driver = value("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    trust = value("DB_TRUST_LOCAL_CERTIFICATE", "false").lower()
    if trust not in {"true", "false"}:
        raise SettingsError("DB_TRUST_LOCAL_CERTIFICATE must be true or false")
    if profile != "local_sqlserver" and trust == "true":
        raise SettingsError("Certificate verification can only be bypassed for local_sqlserver")
    if value("DB_TRUSTED_CONNECTION"):
        raise SettingsError("DB_TRUSTED_CONNECTION is retired; select DB_AUTH explicitly")
    if dialect == "sqlserver":
        for name in ("DB_SERVER", "DB_NAME", "DB_AUTH", "DB_SCHEMA_SCOPE", "DB_SCHEMA_JSON"):
            if not value(name):
                raise SettingsError(f"{name} is required for SQL Server profiles")
        if not re.fullmatch(r"dbmind_synthetic_[A-Za-z0-9_]+", database):
            raise SettingsError("DB_NAME must identify a dedicated dbmind_synthetic_ database")
        if driver != "ODBC Driver 18 for SQL Server":
            raise SettingsError("DB_DRIVER must be ODBC Driver 18 for SQL Server")
        if profile == "azure_sql":
            if not re.fullmatch(r"[A-Za-z0-9-]+\.database\.windows\.net", server):
                raise SettingsError("DB_SERVER must be an Azure public-cloud SQL server FQDN")
            if auth not in {"managed_identity", "azure_cli", "sql_password"}:
                raise SettingsError("DB_AUTH must be managed_identity, azure_cli, or sql_password")
        elif server not in {"127.0.0.1,14333", "localhost,14333"} or auth != "sql_password":
            raise SettingsError("local_sqlserver requires loopback port 14333 and DB_AUTH=sql_password")
        if auth == "sql_password":
            if not username or not password:
                raise SettingsError("DB_USER and DB_PASSWORD are required for sql_password")
            if username.lower() in {"sa", "dbo", "db_owner"}:
                raise SettingsError("DB_USER must be a restricted runtime user, never a setup/admin user")
        elif username or password:
            raise SettingsError("DB_USER/DB_PASSWORD must be unset for token authentication")
    elif auth or server or database or username or password:
        raise SettingsError("SQL Server settings require an explicit SQL Server DB_PROFILE")
    client_id = value("DB_MANAGED_IDENTITY_CLIENT_ID")
    if client_id:
        try:
            if auth != "managed_identity" or str(UUID(client_id)) != client_id.lower():
                raise ValueError
        except ValueError:
            raise SettingsError("DB_MANAGED_IDENTITY_CLIENT_ID requires managed_identity and a UUID client ID") from None
    return Settings(profile, dialect, path("DB_SQLITE_PATH", "var/synthetic/maintenance_v1.sqlite3"),
                    path("DB_SCHEMA_JSON", "var/synthetic/schema-v1.json"), value("DB_SCHEMA_TEXT"),
                    server, database, auth or "sqlite_readonly", scope, driver, username, password,
                    client_id, trust == "true", number("DB_CONNECT_TIMEOUT_SECONDS", 10, 1, 60),
                    number("DB_QUERY_TIMEOUT_SECONDS", 15, 1, 120), number("DB_CONNECT_RETRIES", 2, 0, 3),
                    number("DB_MAX_RESULT_ROWS", 1000, 1, 10000),
                    number("DB_MAX_RESULT_BYTES", 1048576, 1024, 10485760),
                    number("DB_SNAPSHOT_TTL_SECONDS", 3600, 1, 86400),
                    number("DB_SAMPLE_ROWS", 5, 0, 20), number("DB_SAMPLE_VALUE_CHARS", 160, 16, 1024),
                    number("DB_SAMPLE_TOTAL_BYTES", 65536, 1024, 262144),
                    number("DB_SAMPLE_SCAN_ROWS", 10000, 1, 100000),
                    number("DB_DISCOVERY_TIMEOUT_SECONDS", 30, 1, 300),
                    number("LLM_SCHEMA_CONTEXT_CHARS", 16000, 2000, 32000))
