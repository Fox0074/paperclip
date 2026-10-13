"""Validate the deployed team; optionally start the explicitly bounded integration fixture."""
import argparse
import json
from pathlib import Path
from configure import Api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://192.168.1.108:3100')
    parser.add_argument('--company', required=True)
    parser.add_argument('--auth-file', type=Path, required=True)
    parser.add_argument('--start-scenario', action='store_true')
    args = parser.parse_args()
    api = Api(args.base, json.loads(args.auth_file.read_text())['boardApiToken'])
    root = Path(__file__).parent
    manifest = json.loads((root / 'team.json').read_text(encoding='utf-8'))
    agents = api.request('GET', '/companies/' + args.company + '/agents')
    team = {a['metadata']['teamRole']: a for a in agents if (a.get('metadata') or {}).get('teamRole')}
    assert len(agents) == len(team) == 12, 'Missing/duplicate/unmapped agents'
    for definition in manifest['agents']:
        agent = team[definition['key']]
        expected = team[definition['manager']]['id'] if definition['manager'] else None
        assert agent['reportsTo'] == expected, 'Incorrect hierarchy: ' + definition['key']
        assert agent['adapterConfig']['model'] == 'codex_sale/gpt-6.1-sol'
        assert agent['defaultEnvironmentId'], 'Missing isolated execution environment'
        assert agent['runtimeConfig']['resourceLimits']['maxRetries'] == 2
        assert agent['adapterConfig']['timeoutSec'] == 900
        bundle = api.request('GET', '/agents/' + agent['id'] + '/instructions-bundle')
        entry = api.request('GET', '/agents/' + agent['id'] + '/instructions-bundle/file?path=' + bundle['entryFile'])
        assert definition['name'] in entry['content'] and len(entry['content']) > 3000
        assert entry.get('revision'), 'Unversioned instructions'
    print('PASS: 12 identities, hierarchy, configured model, environments, limits, versioned prompts')
    print('Monthly allocation (USD):', json.dumps({key: a['budgetMonthlyCents'] / 100 for key, a in team.items()}))
    if args.start_scenario:
        description = (root / 'integration_scenario.md').read_text(encoding='utf-8')
        description += '\n\n## Agent IDs for actual API assignments\n' + '\n'.join(
            f"- {key}: {agent['id']}" for key, agent in team.items())
        issue = api.request('POST', '/companies/' + args.company + '/issues', {
            'title': 'TEAM-VALIDATION: isolated commercial-to-code handoff',
            'description': description, 'status': 'todo', 'priority': 'medium',
            'assigneeAgentId': team['CEO']['id'], 'idempotencyKey': 'ai-hack-team-validation-v1'})
        print(json.dumps({'testIssueId': issue['id'], 'identifier': issue['identifier']}))


if __name__ == '__main__':
    main()
