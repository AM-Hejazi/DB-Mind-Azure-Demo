"""Security and startup contracts of the deployable Container Apps template."""
import json
from pathlib import Path
import unittest


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.template=json.loads(Path('infra/container-app.json').read_text())
        self.app=self.template['resources'][0]['properties']
        self.container=self.app['template']['containers'][0]

    def test_secrets_are_secure_parameters_and_env_uses_references(self):
        for name in ('appAuth','sqlPassword','deepseekKey'):
            self.assertEqual(self.template['parameters'][name]['type'],'securestring')
        env=self.template['variables']['publicEnv']
        by_name={v['name']:v for v in env}
        self.assertEqual(by_name['APP_AUTH']['secretRef'],'private-auth')
        self.assertEqual(by_name['DEEPSEEK_API_KEY']['secretRef'],'deepseek-key')
        self.assertNotIn('value',by_name['APP_AUTH'])
        self.assertNotIn('value',by_name['DEEPSEEK_API_KEY'])
        self.assertNotIn('DB_SQLITE_PATH',by_name)
        self.assertEqual(by_name['DB_PROFILE']['value'],"[parameters('dbProfile')]")
        self.assertEqual(self.template['parameters']['dbProfile']['defaultValue'],'azure_sql')
        self.assertEqual(self.template['parameters']['dbSchemaScope']['defaultValue'],'demo')
        self.assertEqual(self.template['parameters']['dbProfile']['allowedValues'],['azure_sql','azure_sql_custom'])
        self.assertEqual(by_name['DB_DIALECT']['value'],'sqlserver')

    def test_https_private_boundary_and_single_replica_contract(self):
        configuration=self.app['configuration']
        self.assertFalse(configuration['ingress']['allowInsecure'])
        self.assertEqual(configuration['ingress']['targetPort'],7860)
        self.assertEqual(configuration['activeRevisionsMode'],'Single')
        self.assertEqual(self.app['template']['scale'],{'minReplicas':1,'maxReplicas':1})
        self.assertIn('identity',configuration['registries'][0])
        self.assertNotIn('passwordSecretRef',configuration['registries'][0])

    def test_startup_probe_covers_three_discovery_attempts_and_retry_delays(self):
        env={v['name']:v.get('value') for v in self.template['variables']['publicEnv']}
        discovery_seconds=int(env['DB_DISCOVERY_TIMEOUT_SECONDS'])
        startup=next(p for p in self.container['probes'] if p['type']=='Startup')
        allowance=startup['periodSeconds']*startup['failureThreshold']
        self.assertGreater(allowance,3*discovery_seconds+2*10+60)
        self.assertTrue(all(1 <= p['failureThreshold'] <= 48 for p in self.container['probes']))
        self.assertEqual(startup['httpGet']['path'],'/health/ready')
        liveness=next(p for p in self.container['probes'] if p['type']=='Liveness')
        self.assertEqual(liveness['httpGet']['path'],'/health/live')


if __name__=='__main__':unittest.main()
