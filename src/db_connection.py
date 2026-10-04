"""SQL Server transport and principal checks. No imports trigger authentication."""
from contextlib import closing
import struct
from synthetic_demo.schema import TABLES


class TokenAuthenticationError(RuntimeError):
    pass


def odbc_value(value):
    if "\x00" in value:
        raise ValueError("Invalid connection setting")
    return "{" + value.replace("}", "}}") + "}"


def connection_string(settings):
    fields = {"DRIVER": settings.driver, "SERVER": "tcp:" + settings.server,
              "DATABASE": settings.database, "Encrypt": "yes",
              "TrustServerCertificate": "yes" if settings.trust_local_certificate else "no",
              "ApplicationIntent": "ReadOnly"}
    if settings.auth == "sql_password":
        fields.update(UID=settings.username, PWD=settings.password, Authentication="SqlPassword")
    return ";".join(f"{key}={odbc_value(value)}" for key, value in fields.items()) + ";"


def access_token(settings):
    from azure.identity import AzureCliCredential, ManagedIdentityCredential
    if settings.auth == "azure_cli":
        credential = AzureCliCredential(process_timeout=settings.connect_timeout)
    else:
        credential = ManagedIdentityCredential(
            client_id=settings.managed_identity_client_id or None,
            connection_timeout=settings.connect_timeout, read_timeout=settings.connect_timeout,
            retry_total=0)
    try:
        with closing(credential):
            token = credential.get_token("https://database.windows.net/.default").token
    except Exception:
        raise TokenAuthenticationError("Token authentication failed") from None
    encoded = token.encode("utf-16-le")
    return struct.pack("<I", len(encoded)) + encoded


def connect_sqlserver(settings):
    import pyodbc
    options = {"timeout": settings.connect_timeout, "autocommit": True}
    if settings.auth != "sql_password":
        options["attrs_before"] = {1256: access_token(settings)}
    # Token connections omit UID/PWD/Authentication/Trusted_Connection entirely.
    connection = pyodbc.connect(connection_string(settings), **options)
    try:
        connection.timeout = settings.query_timeout
    except Exception:
        connection.close()
        raise
    return connection


def _require_identity(cursor, schema="demo"):
    """Reject effective write/setup privileges, including custom role grants."""
    statement = ("SELECT CURRENT_USER, IS_MEMBER('db_owner'), IS_MEMBER('db_datawriter'), "
                   "IS_MEMBER('db_ddladmin'), CASE WHEN "
                   + " OR ".join(f"IS_SRVROLEMEMBER('{role}')=1" for role in (
                       "sysadmin", "securityadmin", "serveradmin", "setupadmin", "processadmin", "diskadmin", "dbcreator", "bulkadmin"))
                   + " THEN 1 ELSE 0 END, "
                   "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONTROL'), "
                   "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE'), "
                   "HAS_PERMS_BY_NAME('demo', 'SCHEMA', 'ALTER'), "
                   "HAS_PERMS_BY_NAME('demo', 'SCHEMA', 'CONTROL'), "
                   "IS_MEMBER('db_securityadmin'), IS_MEMBER('db_accessadmin'), "
                   "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER ANY USER'), "
                   "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER ANY ROLE'), "
                   "HAS_PERMS_BY_NAME('dbo', 'USER', 'IMPERSONATE'), "
                   "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'ALTER ANY SCHEMA'), "
                   "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE VIEW'), "
                   "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE PROCEDURE')")
    if schema == "demo":
        cursor.execute(statement)
    else:
        cursor.execute(statement.replace("HAS_PERMS_BY_NAME('demo',", "HAS_PERMS_BY_NAME(?,"), (schema, schema))
    row = cursor.fetchone()
    # Server-role visibility can be NULL in Azure; object permissions below are mandatory.
    if (not row or len(row) != 17 or str(row[0]).lower() == "dbo" or any(value != 0 for value in row[1:4])
            or row[4] == 1 or any(value != 0 for value in row[5:])):
        raise PermissionError("A restricted runtime identity is required")
    # IMPERSONATE is a USER permission, not a DATABASE permission. Check every
    # visible target, not just dbo or direct grants: HAS_PERMS_BY_NAME includes
    # inherited role/group grants and permissions implied by CONTROL on a user.
    # A permission on another user makes that user visible in this catalog;
    # no additional metadata permissions are required. Exclude the current user
    # (no identity change) and the two non-impersonatable system pseudo-users.
    cursor.execute("SELECT name, HAS_PERMS_BY_NAME(name, 'USER', 'IMPERSONATE') "
                   "FROM sys.database_principals "
                   "WHERE type IN ('S', 'U', 'G', 'E', 'X', 'C', 'K') "
                   "AND principal_id <> USER_ID() "
                   "AND name NOT IN ('dbo', 'sys', 'INFORMATION_SCHEMA')")
    for target in cursor.fetchall():
        if len(target) != 2 or target[1] != 0:
            raise PermissionError("Runtime identity must not impersonate other users")


def require_read_principal(cursor, require_all_tables=True):
    """Preserved synthetic principal and nine-table checks."""
    _require_identity(cursor)
    for table in TABLES:
        object_name = "demo." + table.name
        cursor.execute("SELECT HAS_PERMS_BY_NAME(?, 'OBJECT', 'SELECT'), "
                       "HAS_PERMS_BY_NAME(?, 'OBJECT', 'INSERT'), "
                       "HAS_PERMS_BY_NAME(?, 'OBJECT', 'UPDATE'), "
                       "HAS_PERMS_BY_NAME(?, 'OBJECT', 'DELETE'), "
                       "HAS_PERMS_BY_NAME(?, 'OBJECT', 'ALTER'), "
                       "HAS_PERMS_BY_NAME(?, 'OBJECT', 'CONTROL')", (object_name,) * 6)
        permissions = cursor.fetchone()
        if not permissions or len(permissions) != 6 or any(value not in (0, 1) for value in permissions):
            raise PermissionError("Runtime table permission results must be known")
        if not require_all_tables and permissions and permissions[0] != 1:
            if any(value == 1 for value in permissions[1:]):
                raise PermissionError("Runtime identity has write privileges")
            continue
        if not permissions or permissions[0] != 1 or any(value != 0 for value in permissions[1:]):
            raise PermissionError("Runtime table permissions must be SELECT-only")


def require_custom_read_principal(cursor, settings):
    """Effective permissions, not direct-grant lists; no unknown results accepted."""
    _require_identity(cursor, settings.schemas[0])
    cursor.execute("SELECT DB_NAME(), HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'EXECUTE')")
    row=cursor.fetchone()
    if not row or len(row)!=2 or row[0]!=settings.database or row[1]!=0:
        raise PermissionError('Custom database identity must match and have no database EXECUTE grant')
    for schema in settings.schemas:
        permissions=('ALTER','CONTROL','INSERT','UPDATE','DELETE','EXECUTE')
        cursor.execute('SELECT '+', '.join("HAS_PERMS_BY_NAME(?, 'SCHEMA', '"+p+"')" for p in permissions), (schema,)*len(permissions))
        row=cursor.fetchone()
        if not row or len(row)!=len(permissions) or any(v!=0 for v in row):
            raise PermissionError('Custom schema permissions must be known and read-only')
    # Any object permission makes its target visible. Evaluate table/view writes
    # across the database, including outside the configured query scope. Bulk SQL
    # keeps permission checks bounded in round trips as the catalog grows.
    qualified="QUOTENAME(s.name)+N'.'+QUOTENAME(o.name)"
    permissions=('SELECT','INSERT','UPDATE','DELETE','ALTER','CONTROL')
    fields=', '.join("HAS_PERMS_BY_NAME("+qualified+", 'OBJECT', '"+p+"')" for p in permissions)
    cursor.execute("SELECT s.name,o.name,o.type,"+fields+", CASE WHEN EXISTS (SELECT 1 FROM sys.columns c "
                   "WHERE c.object_id=o.object_id AND (HAS_PERMS_BY_NAME("+qualified+", 'OBJECT', 'UPDATE', c.name, 'COLUMN') IS NULL "
                   "OR HAS_PERMS_BY_NAME("+qualified+", 'OBJECT', 'UPDATE', c.name, 'COLUMN')<>0)) THEN 1 ELSE 0 END "
                   "FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id "
                   "WHERE o.is_ms_shipped=0 AND o.type IN ('U','V') ORDER BY s.name,o.name")
    objects=cursor.fetchmany(1001)
    if not objects or len(objects)>1000:
        raise PermissionError('Custom permission inventory missing or exceeds 1000 visible tables/views')
    found=set()
    scopes={s.casefold() for s in settings.schemas}
    allowed={n.casefold() for n in settings.table_scope}
    for obj in objects:
        if len(obj)!=10:raise PermissionError('Custom permission inventory is malformed')
        schema,name,kind,*values=obj
        if any(v not in (0,1) for v in values) or any(v!=0 for v in values[1:]):
            raise PermissionError('Custom object/column permissions must be known with no effective writes')
        full=schema+'.'+name
        if kind.strip()=='U' and schema.casefold() in scopes and (not allowed or full.casefold() in allowed):
            if values[0]!=1:raise PermissionError('Approved custom tables require SELECT on every column')
            found.add(full.casefold())
    if not found or (allowed and found!=allowed):
        raise PermissionError('Approved custom table visibility is incomplete')
    # Reject effective user-procedure execution and module alteration/control.
    cursor.execute("SELECT CASE WHEN o.type IN ('P','PC','FN','FS') THEN HAS_PERMS_BY_NAME("+qualified+", 'OBJECT', 'EXECUTE') ELSE 0 END, "
                   "HAS_PERMS_BY_NAME("+qualified+", 'OBJECT', 'ALTER'), HAS_PERMS_BY_NAME("+qualified+", 'OBJECT', 'CONTROL') "
                   "FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id "
                   "WHERE o.is_ms_shipped=0 AND o.type IN ('P','PC','FN','FS','IF','TF','FT','AF')")
    modules=cursor.fetchmany(1001)
    if len(modules)>1000 or any(len(row)!=3 or any(v!=0 for v in row) for row in modules):
        raise PermissionError('Custom runtime identity must not execute or control user modules')
