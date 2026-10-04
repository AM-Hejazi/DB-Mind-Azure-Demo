"""Explicit read-only installation step for an operator-selected Azure SQL schema.

Never seeds SQL, grants permissions, changes users, serves HTTP or calls a model.
"""
import argparse
import json
from pathlib import Path

from .settings import load_settings, SettingsError
from .discovery import refresh
from .snapshots import SnapshotError
from .database import DatabaseError


def private_environment(path):
    """Docker KEY=value syntax; never execute shell or expand credential text."""
    try:
        lines=Path(path).read_text().splitlines()
    except OSError:
        raise SettingsError('Private env file is unavailable') from None
    values={}
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):continue
        key,separator,value=line.partition('=')
        if not separator or not key.isidentifier() or key in values:
            raise SettingsError('Private env file requires distinct KEY=value entries')
        values[key]=value
    return values


def setup(settings):
    if settings.profile!='azure_sql_custom':
        raise SettingsError('Database setup requires explicit DB_PROFILE=azure_sql_custom')
    snapshot=refresh(settings)
    return {'profile':settings.profile,'tables':len(snapshot['metadata']['tables']),
            'complete':snapshot['complete'],'sampling_complete':snapshot['sampling_complete'],
            'sample_status_counts':{status:sum(s['status']==status for s in snapshot['samples'].values())
                                    for status in sorted({s['status'] for s in snapshot['samples'].values()})},
            'schema_fingerprint':snapshot['schema_fingerprint']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,help='Private Docker-format configuration; otherwise use process environment')
    args=parser.parse_args()
    try:
        settings=load_settings(private_environment(args.env_file) if args.env_file else None)
        print(json.dumps(setup(settings),indent=2))
    except (SettingsError,SnapshotError,DatabaseError) as error:
        parser.exit(1,f'Database setup stopped: {error}\n')
    except Exception:
        parser.exit(1,'Database setup stopped; inspect configuration, permissions and local cache access privately.\n')


if __name__=='__main__':main()
