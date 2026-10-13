import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('configure_team', ROOT / 'configure.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeApi:
    def __init__(self):
        self.agents, self.files, self.budgets = [], {}, {}
        self.creates = 0

    def request(self, method, path, data=None):
        if path.endswith('/execution-presets'):
            return [{'id': 'codex-sale', 'defaultModel': 'codex_sale/gpt-6.1-sol'}]
        if path.endswith('/agents'):
            if method == 'GET':
                return copy.deepcopy(self.agents)
            self.creates += 1
            agent = {**copy.deepcopy(data), 'id': 'agent-' + str(self.creates), 'status': 'idle'}
            self.agents.append(agent)
            return copy.deepcopy(agent)
        identifier = path.split('/')[2]
        agent = next(a for a in self.agents if a['id'] == identifier)
        if '/instructions-bundle/file' in path:
            if method == 'GET':
                return {'content': self.files.get(identifier, ''), 'revision': {'id': 'revision-1'}}
            assert data['baseRevisionId'] == 'revision-1'
            self.files[identifier] = data['content']
            return {}
        if path.endswith('/instructions-bundle'):
            return {'mode': 'managed', 'entryFile': 'AGENTS.md'}
        if path.endswith('/permissions'):
            agent['permissions'] = copy.deepcopy(data)
        elif path.endswith('/budgets'):
            self.budgets[identifier] = data['budgetMonthlyCents']
        else:
            agent.update(copy.deepcopy(data))
        return copy.deepcopy(agent)


class TeamTests(unittest.TestCase):
    def test_repeat_preserves_owner_prompts_limits_and_identity(self):
        api = FakeApi()
        first = module.configure(api, 'company', ROOT)
        api.files[first['COO']] = 'Owner edited instructions'
        coo = next(a for a in api.agents if a['id'] == first['COO'])
        coo['runtimeConfig']['resourceLimits']['dailyCents'] = 123
        second = module.configure(api, 'company', ROOT)
        self.assertEqual(first, second)
        self.assertEqual(api.creates, 12)
        self.assertEqual(api.files[first['COO']], 'Owner edited instructions')
        self.assertEqual(coo['runtimeConfig']['resourceLimits']['dailyCents'], 123)
        self.assertEqual(sum(api.budgets.values()), 5000)
        parents = {a['metadata']['teamRole']: a['reportsTo'] for a in api.agents}
        self.assertEqual(parents['Developer'], first['CTO'])
        self.assertEqual(parents['Researcher'], first['CPO'])
        self.assertEqual(parents['COO'], first['CEO'])

    def test_ambiguous_roles_fail_before_mutation(self):
        api = FakeApi()
        api.agents = [{'id': 'a', 'name': 'COO', 'status': 'idle'}, {'id': 'b', 'name': 'AI-COO', 'status': 'idle'}]
        with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
            module.configure(api, 'company', ROOT)
        self.assertEqual(api.creates, 0)

    def test_all_prompts_and_hierarchy_exist(self):
        manifest = json.loads((ROOT / 'team.json').read_text())
        self.assertEqual(len({a['key'] for a in manifest['agents']}), 12)
        for agent in manifest['agents']:
            prompt = (ROOT / agent['promptFile']).read_text(encoding='utf-8')
            self.assertIn('Главная цель', prompt)
            self.assertIn('Память и контекст', prompt)
            self.assertIn('критерии готовности', prompt)


if __name__ == '__main__':
    unittest.main()
