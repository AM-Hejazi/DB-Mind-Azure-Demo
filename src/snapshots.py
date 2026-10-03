"""Versioned metadata/sample cache with identity, expiry, integrity, atomic writes."""
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from uuid import UUID

from synthetic_demo.schema import APPLICATION_ID, VERSION

FORMAT_VERSION = 1
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


class SnapshotError(ValueError):
    pass


def scalar(value, limit=160):
    """Typed JSON values; truncation is explicit, not silently substituted data."""
    def tagged(key, text):
        result = {key: text[:limit]}
        if len(text) > limit:
            result['truncated'] = True
        return result
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if len(str(value)) <= limit else tagged('$integer',str(value))
    if isinstance(value, float):
        return value if math.isfinite(value) and len(str(value)) <= limit else tagged('$float',str(value))
    if isinstance(value, Decimal):
        return tagged('$decimal',str(value))
    if isinstance(value, datetime):
        return tagged('$datetime',value.isoformat())
    if isinstance(value, date):
        return tagged('$date',value.isoformat())
    if isinstance(value, time):
        return tagged('$time',value.isoformat())
    if isinstance(value, UUID):
        return tagged('$uuid',str(value))
    if isinstance(value, (bytes, bytearray, memoryview)):
        binary = bytes(value)
        bound = limit // 2
        return {'$binary_hex': binary[:bound].hex(), 'truncated': len(binary) > bound}
    if not isinstance(value, str):
        raise SnapshotError('Unsupported sample value type')
    return value if len(value) <= limit else {'$text': value[:limit], 'truncated': True}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      default=scalar, allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def identity(settings):
    result = {'dialect': settings.dialect, 'scope': list(settings.schemas)}
    if settings.dialect == 'sqlite':
        result.update(path_id=digest(str(settings.sqlite_path.resolve())), application_id=APPLICATION_ID,
                      fixture_version=VERSION)
    else:
        result.update(server=settings.server.lower(), database=settings.database)
    return result


def policy(settings):
    return {'rows_per_table': settings.sample_rows, 'value_chars': settings.sample_value_chars,
            'total_bytes': settings.sample_total_bytes, 'scan_row_limit': settings.sample_scan_rows,
            'runtime_seconds': settings.discovery_timeout_seconds, 'ordering': 'random_small_tables'}


def metadata_fingerprint(tables, views):
    return digest({'tables': tables, 'views': views})


def atomic_write(path, value):
    payload = encoded(value)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError('Snapshot exceeds the 2 MiB storage limit')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.dbmind-refresh-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_snapshot(settings, now=None):
    """Pure local cache validation; no DB/network operations or implicit seeding."""
    try:
        with settings.schema_json.open('rb') as source:
            payload = source.read(MAX_SNAPSHOT_BYTES + 1)
        if len(payload) > MAX_SNAPSHOT_BYTES:
            raise SnapshotError('Snapshot exceeds the storage limit')
        snapshot = json.loads(payload)
        if (snapshot.get('format') != 'dbmind.snapshot' or snapshot.get('version') != FORMAT_VERSION
                or snapshot.get('synthetic_only') is not True):
            raise SnapshotError('Unsupported snapshot; legacy exports must not be reused')
        if snapshot['identity'] != identity(settings) or snapshot['sample_policy'] != policy(settings):
            raise SnapshotError('Snapshot database/scope/sample policy mismatch; refresh explicitly')
        if snapshot['complete'] is not True:
            raise SnapshotError('Incomplete discovery cannot be consumed by agents')
        metadata = snapshot['metadata']
        if snapshot['schema_fingerprint'] != metadata_fingerprint(metadata['tables'], metadata['views']):
            raise SnapshotError('Snapshot metadata integrity mismatch')
        revision = dict(snapshot)
        revision.pop('revision', None)
        if snapshot['revision'] != digest(revision):
            raise SnapshotError('Snapshot revision integrity mismatch')
        instant = now or datetime.now(timezone.utc)
        extracted = datetime.fromisoformat(snapshot['extracted_at'])
        if extracted.tzinfo is None or not -5 <= (instant - extracted).total_seconds() < settings.snapshot_ttl_seconds:
            raise SnapshotError('Snapshot expired or extraction time invalid; run preflight/refresh')
        if any(t['schema'] not in settings.schemas or name != t['schema']+'.'+t['name']
               for name, t in metadata['tables'].items()):
            raise SnapshotError('Snapshot contains objects outside its configured scope')
        return snapshot
    except SnapshotError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SnapshotError('Snapshot unavailable or malformed; run python -m src.discovery refresh') from None
