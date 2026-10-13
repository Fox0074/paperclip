"""Initialize file-backed memory inside the existing broker-managed company paths."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid


def safe_child(root, name):
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Memory path escapes its managed root')
    return path


def seed(path, content):
    if path.is_symlink():
        raise ValueError('Refusing a symlink memory file')
    if path.exists():
        return
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(content)
    os.chown(path, 1000, 1000)
    path.chmod(0o600)


def bootstrap_memory(root, company, agents):
    if os.geteuid() != 0:
        raise ValueError('Memory initialization requires the host operator; use sudo')
    company = str(uuid.UUID(company))
    root = root.resolve(strict=True)
    profiles = json.loads((root / 'onboarding/broker.json').read_text())['profiles']
    profile = next(p for p in profiles if p['companyId'] == company and p['id'] == 'codex-sale')
    homes = Path(profile['homeRoot']).resolve(strict=True)
    workspace = Path(profile['projectRoot']).resolve(strict=True)
    if homes != root / 'agent-data/managed' / company or workspace != root / 'workspaces' / company:
        raise ValueError('Broker memory paths do not match this installation/company')
    date = datetime.now(timezone.utc).isoformat()
    for agent in agents:
        role = (agent.get('metadata') or {}).get('teamRole')
        if not role:
            continue
        if agent['companyId'] != company:
            raise ValueError('Agent belongs to another company')
        identifier = str(uuid.UUID(agent['id']))
        home = safe_child(homes, identifier)
        marker = json.loads(safe_child(home, '.paperclip-managed.json').read_text())
        if marker['agentId'] != identifier:
            raise ValueError('Unmanaged agent home')
        memory = safe_child(home, 'memory')
        memory.mkdir(mode=0o700, exist_ok=True)
        os.chown(memory, 1000, 1000)
        seed(safe_child(memory, 'team-profile.md'),
            f'# {role} personal profile\n\nDate: {date}. Status: verified setup.\n'
            f'Source: Paperclip agent {identifier}.\n'
            'Permanent specialization/instructions and their revisions are edited in Paperclip Instructions.\n'
            'Current model and limits are read from Paperclip Configuration; do not infer them from stale memory.\n'
            'Store dated, sourced decisions and lessons here, without credentials.\n')
        seed(safe_child(memory, 'INDEX.md'),
            '# Personal memory\n\n- team-profile.md: specialization and instruction source (verified setup).\n'
            'Select only relevant records. Use verified/hypothesis/outdated labels.\n')
    knowledge = safe_child(workspace, 'knowledge')
    knowledge.mkdir(exist_ok=True)
    os.chown(knowledge, 1000, 1000)
    for name in ['company', 'projects']:
        directory = safe_child(knowledge, name)
        directory.mkdir(exist_ok=True)
        os.chown(directory, 1000, 1000)
    seed(safe_child(knowledge / 'company', 'INDEX.md'),
        f'# Company memory\n\nDate: {date}. Source: owner-authorized team setup.\n'
        'Mission: evidence-backed commercial products and real revenue.\n'
        'Current financial limits and permissions are read from Paperclip, not inferred from memory.\n'
        'Team setup authorizes no commercial product launch.\n'
        'Records must include source/date and verified/hypothesis/outdated labels; no credentials.\n')
