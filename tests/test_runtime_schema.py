"""Long-running app cache renewal without a provider or network connection."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.access import AccessPolicy, load_access_settings, AccessError
from src.database import DatabaseError
from src.discovery import refresh
from src.service import SessionService
from src.settings import load_settings
from src.snapshots import atomic_write, digest, read_snapshot, SnapshotError
from synthetic_demo.seed import seed_sqlite
from synthetic_demo.questions import reference_cases


class RuntimeSchemaTests(unittest.TestCase):
    def setUp(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings=load_settings({'DB_SQLITE_PATH':str(root/'db.sqlite3'),
                                     'DB_SCHEMA_JSON':str(root/'schema.json')})
        seed_sqlite(self.settings.sqlite_path)
        refresh(self.settings)
        self.policy=AccessPolicy(load_access_settings({'APP_AUTH':'demo:offline-password'}))
        self.service=SessionService(self.policy,settings=self.settings,mode='mock')
        self.request=SimpleNamespace(username='demo',session_hash='owned')
        self.case=next(case for case in reference_cases() if case['id']=='september_labor')
        self.session=self.service.submit(self.case['question_en'],self.request)
        self.enterContext(patch('src.llm_client.LLMClient._factory',side_effect=AssertionError('No provider calls')))

    def expire(self):
        snapshot=json.loads(self.settings.schema_json.read_text())
        snapshot['extracted_at']=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        snapshot.pop('revision')
        snapshot['revision']=digest(snapshot)
        atomic_write(self.settings.schema_json,snapshot)
        with self.assertRaises(SnapshotError):read_snapshot(self.settings)

    def test_expired_cache_renews_then_real_gated_query_succeeds(self):
        self.expire()
        stages=[]
        result=self.service.advance(self.request,progress=stages.append)
        self.assertIn('Refreshing database context',stages)
        self.assertTrue(result.state['finished'])
        expected=json.loads(Path('data/synthetic/v1/reference-cases.json').read_text())['cases']
        expected=next(case for case in expected if case['id']=='september_labor')
        self.assertEqual([list(row) for row in result.state['rows']],expected['expected_rows'])
        self.assertEqual(len(read_snapshot(self.settings)['metadata']['tables']),9)
        self.assertEqual(result.state['llm_budget'].calls,0)

    def test_fresh_cache_does_not_start_discovery(self):
        with patch('src.discovery.refresh',side_effect=AssertionError('Unexpected refresh')):
            result=self.service.advance(self.request)
        self.assertTrue(result.state['finished'])

    def test_incomplete_refresh_stops_before_pipeline_and_preserves_old_cache(self):
        self.expire()
        previous=self.settings.schema_json.read_bytes()
        with patch('src.catalog.Reader.query',side_effect=DatabaseError('permissions','Restricted reader required')):
            with patch.object(self.service,'_mock_answer') as pipeline:
                result=self.service.advance(self.request)
        pipeline.assert_not_called()
        self.assertEqual(previous,self.settings.schema_json.read_bytes())
        self.assertNotIn('finished',result.state)
        with self.assertRaises(SnapshotError):read_snapshot(self.settings)

    def test_snapshot_error_has_actionable_message_without_sensitive_details(self):
        self.expire()
        with patch('src.discovery.refresh',side_effect=SnapshotError('secret-sensitive-driver-detail')):
            result=self.service.advance(self.request)
        reply=result.history[-1]['content']
        self.assertIn('contact the operator',reply)
        self.assertNotIn('secret-sensitive',reply)
        self.assertNotIn('Reset and try',reply)

    def test_cancelled_question_cannot_refresh_or_execute(self):
        self.expire()
        self.session.state['llm_budget'].cancel()
        with patch('src.discovery.refresh') as renewal,patch.object(self.service,'_mock_answer') as pipeline:
            self.service.advance(self.request)
        renewal.assert_not_called()
        pipeline.assert_not_called()

    def test_access_revocation_during_refresh_stops_next_database_read(self):
        self.expire()
        original=self.policy.allowance_key
        checks=0
        def access(*args):
            nonlocal checks
            checks+=1
            if checks>=3:raise AccessError('Demo visit expired')
            return original(*args)
        with patch.object(self.policy,'allowance_key',side_effect=access),patch.object(self.service,'_mock_answer') as pipeline:
            result=self.service.advance(self.request)
        pipeline.assert_not_called()
        self.assertIn('expired',result.history[-1]['content'])
        with self.assertRaises(SnapshotError):read_snapshot(self.settings)


if __name__=='__main__':unittest.main()
