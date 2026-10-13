"""Reconcile the AI-Hack team through authenticated, company-scoped Paperclip APIs.

No provider credentials, prompts or limits are changed implicitly on repeat runs.
"""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


class Api:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip('/'), token

    def request(self, method, path, data=None):
        req = urllib.request.Request(self.base + '/api' + path,
            data=None if data is None else json.dumps(data).encode(), method=method,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)


def matches(existing, role):
    return [agent for agent in existing if (agent.get('metadata') or {}).get('teamRole') == role['key']
        or agent['name'].casefold() in {role['name'].casefold(), role['key'].casefold()}]


def configure(api, company, root, task_cents=None, daily_cents=None):
    manifest = json.loads((root / 'team.json').read_text(encoding='utf-8'))
    agents = api.request('GET', f'/companies/{company}/agents')
    presets = api.request('GET', f'/companies/{company}/execution-presets')
    if isinstance(presets, dict):
        presets = presets.get('presets', [])
    preset = next(p for p in presets if p['id'] == 'codex-sale')
    model = preset['defaultModel']
    selected = {}
    for role in manifest['agents']:
        found = matches(agents, role)
        if len(found) > 1:
            raise RuntimeError('Ambiguous existing role: ' + role['key'])
        if found and found[0]['status'] == 'terminated':
            raise RuntimeError('Role is terminated; explicit owner decision required: ' + role['key'])
        selected[role['key']] = found[0] if found else None
    # Preflight all identities before the first mutation. Single operator only.
    for role in manifest['agents']:
        agent = selected[role['key']]
        configured = bool(agent and (agent.get('metadata') or {}).get('teamVersion') == 1)
        parent = selected.get(role['manager'])
        prompt = (root / role['promptFile']).read_text(encoding='utf-8')
        if agent is None:
            agent = api.request('POST', f'/companies/{company}/agents', {
                'name': role['name'], 'role': role['role'], 'title': role['key'],
                'reportsTo': parent['id'] if parent else None,
                'executionPresetId': 'codex-sale', 'adapterType': 'opencode_local',
                'adapterConfig': {'model': model, 'timeoutSec': 900},
                'runtimeConfig': {'timeoutSec': 900, 'heartbeat': {
                    'enabled': False, 'wakeOnDemand': True, 'maxConcurrentRuns': 1,
                    'skipTimerWhenNoActionableWork': True}},
                'permissions': {'canCreateAgents': False, 'canCreateSkills': False, 'canAssignTasks': True},
                'metadata': {'teamRole': role['key'], 'executionProfile': role['profile']}})
            print('Created', role['name'], agent['id'], flush=True)
        else:
            print('Reusing', agent['name'], agent['id'], flush=True)
        selected[role['key']] = agent
        metadata = {**(agent.get('metadata') or {}), 'teamRole': role['key'],
            'executionProfile': role['profile']}
        runtime = {**agent.get('runtimeConfig', {}), 'timeoutSec': 900,
            'heartbeat': {**agent.get('runtimeConfig', {}).get('heartbeat', {}),
                'enabled': False, 'wakeOnDemand': True, 'maxConcurrentRuns': 1,
                'skipTimerWhenNoActionableWork': True}}
        defaults = role['limits']
        limits = {**runtime.get('resourceLimits', {}), 'maxRetries': defaults['maxRetries'],
            'taskCents': defaults['taskCents'], 'dailyCents': defaults['dailyCents']}
        if task_cents is not None:
            limits['taskCents'] = task_cents
        if daily_cents is not None:
            limits['dailyCents'] = daily_cents
        runtime['resourceLimits'] = limits
        patch = {
            'name': role['name'], 'role': role['role'], 'title': role['key'],
            'reportsTo': parent['id'] if parent else None, 'metadata': metadata,
        }
        if not configured:
            patch.update(runtimeConfig=runtime,
                adapterConfig={**agent.get('adapterConfig', {}), 'timeoutSec': 900})
        agent = api.request('PATCH', '/agents/' + agent['id'], patch)
        api.request('PATCH', '/agents/' + agent['id'] + '/permissions', {
            'canCreateAgents': False, 'canCreateSkills': False, 'canAssignTasks': True})
        # The preset's historical remote-only path must never be interpreted as
        # the control server's entire /paperclip data directory during migration.
        if agent.get('adapterConfig', {}).get('instructionsFilePath') == '/paperclip/AGENT.md':
            config = dict(agent['adapterConfig'])
            for key in ['instructionsFilePath', 'instructionsRootPath', 'instructionsEntryFile', 'instructionsBundleMode']:
                config.pop(key, None)
            agent = api.request('PATCH', '/agents/' + agent['id'], {
                'adapterConfig': config, 'replaceAdapterConfig': True})
        bundle = api.request('GET', '/agents/' + agent['id'] + '/instructions-bundle')
        if bundle.get('mode') != 'managed':
            bundle = api.request('PATCH', '/agents/' + agent['id'] + '/instructions-bundle', {
                'mode': 'managed', 'rootPath': None, 'entryFile': 'AGENTS.md', 'clearLegacyPromptTemplate': True})
        path = bundle.get('entryFile', 'AGENTS.md')
        try:
            current = api.request('GET', '/agents/' + agent['id'] + '/instructions-bundle/file?path=' + urllib.parse.quote(path))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            current = {'content': '', 'revisionId': None}
        if not configured and current.get('content') != prompt:
            api.request('PUT', '/agents/' + agent['id'] + '/instructions-bundle/file', {
                'path': path, 'content': prompt, 'baseRevisionId': (current.get('revision') or {}).get('id'),
                'clearLegacyPromptTemplate': True})
        if not configured:
            api.request('PATCH', '/agents/' + agent['id'] + '/budgets', {
                'budgetMonthlyCents': defaults['monthlyCents']})
            agent = api.request('PATCH', '/agents/' + agent['id'], {
                'metadata': {**metadata, 'teamVersion': 1}})
        selected[role['key']] = agent
    return {key: value['id'] for key, value in selected.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://192.168.1.108:3100')
    parser.add_argument('--company', required=True)
    parser.add_argument('--auth-file', type=Path, required=True)
    parser.add_argument('--task-cents', type=int)
    parser.add_argument('--daily-cents', type=int)
    parser.add_argument('--deployment-root', type=Path,
        help='Optional Ubuntu deployment root; initialize managed memory using the existing broker profile')
    args = parser.parse_args()
    auth = json.loads(args.auth_file.read_text())
    api = Api(args.base, auth['boardApiToken'])
    api.request('GET', '/cli-auth/me')  # Real authenticated owner authorization required.
    result = configure(api, args.company, Path(__file__).parent, args.task_cents, args.daily_cents)
    if args.deployment_root:
        from bootstrap_memory import bootstrap_memory
        bootstrap_memory(args.deployment_root, args.company,
            api.request('GET', '/companies/' + args.company + '/agents'))
    print(json.dumps({'agents': result}, indent=2))


if __name__ == '__main__':
    main()
