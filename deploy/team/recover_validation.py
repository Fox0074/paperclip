"""Resume the already completed research in the diagnostic team fixture.

Owner-authenticated, explicit issue IDs only; never used as an autonomous worker.
"""
import argparse
import json
from pathlib import Path
import urllib.request
from configure import Api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://192.168.1.108:3100')
    parser.add_argument('--company', required=True)
    parser.add_argument('--auth-file', type=Path, required=True)
    parser.add_argument('--ceo-issue', required=True)
    parser.add_argument('--cpo-issue', required=True)
    parser.add_argument('--research-issue', required=True)
    args = parser.parse_args()
    api = Api(args.base, json.loads(args.auth_file.read_text())['boardApiToken'])
    # The previous image has a broken date parameter once native policies exist.
    with urllib.request.urlopen(args.base + '/api/health', timeout=15) as response:
        health = json.load(response)
    if not health['commit'].startswith('d7f2422b0'):
        raise RuntimeError('Deploy the approved budget repair before resuming the fixture')
    api.request('GET', '/cli-auth/me')
    agents = api.request('GET', '/companies/' + args.company + '/agents')
    team = {(a.get('metadata') or {}).get('teamRole'): a for a in agents}
    issues = {role: api.request('GET', '/issues/' + identifier) for role, identifier in [
        ('CEO', args.ceo_issue), ('CPO', args.cpo_issue), ('Researcher', args.research_issue)]}
    for role, issue in issues.items():
        assert issue['companyId'] == args.company and issue['assigneeAgentId'] == team[role]['id']
        assert issue['title'].startswith('TEAM-PIPELINE-V2:'), 'Not the diagnostic fixture'
    assert issues['CPO']['parentId'] == issues['CEO']['id']
    assert issues['Researcher']['parentId'] == issues['CPO']['id']
    root_comments = api.request('GET', '/issues/' + issues['CEO']['id'] + '/comments')
    if issues['CEO']['status'] == 'done' and any('TEAM-STAGE-8: PASS' in c['body'] for c in root_comments):
        print('Validation already completed; no task reopened and no new runs requested')
        return
    if issues['CEO']['status'] != 'done' or issues['CPO']['status'] != 'done' or issues['Researcher']['status'] != 'blocked':
        raise RuntimeError('Fixture has already advanced or is partially repaired; review its current state instead of resetting it')
    for role, stage in [('CEO', 0), ('CPO', 1), ('Researcher', 2)]:
        comments = api.request('GET', '/issues/' + issues[role]['id'] + '/comments')
        assert any(f'TEAM-STAGE-{stage}: PASS' in c['body'] for c in comments), 'Missing actual stage result'
    description = (Path(__file__).parent / 'integration_scenario.md').read_text(encoding='utf-8')
    description += '\n\n## Actual agent IDs\n' + '\n'.join(f"- {role}: {agent['id']}" for role, agent in team.items())
    description += '\n\nСтадии 0, 1 и 2 уже выполнены и проверены в комментариях этих существующих задач. Не повторять их. Руководители должны продолжить свои задачи по результату дочерних зависимостей.\n'
    # Wire both waiting parents before closing the existing researcher result.
    # This creates native issue_blockers_resolved wakes, no manual agent wake.
    api.request('PATCH', '/issues/' + issues['CPO']['id'], {
        'description': description, 'status': 'blocked',
        'blockedByIssueIds': [issues['Researcher']['id']],
        'comment': 'Validation protocol repaired: retain this CPO parent and resume after the existing research result. No reverse delegation.'})
    api.request('PATCH', '/issues/' + issues['CEO']['id'], {
        'description': description, 'status': 'blocked',
        'blockedByIssueIds': [issues['CPO']['id']],
        'comment': 'Validation protocol repaired: retain this CEO parent until the complete CPO/CTO/QA/COO result is returned.'})
    api.request('PATCH', '/issues/' + issues['Researcher']['id'], {
        'status': 'done', 'comment': 'Owner installer verified the existing TEAM-STAGE-2 research deliverable. The failed reverse-delegation step is replaced by native blocker completion; no research or financial results fabricated.'})
    print('Existing verified research returned through native dependencies; waiting parents preserved')


if __name__ == '__main__':
    main()
