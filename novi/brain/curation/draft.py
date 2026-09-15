"""Small model-facing contract; code supplies bookkeeping, never claim meaning."""
from typing import Literal
from pydantic import Field
from .contracts import Strict, Proposal, validate


class DraftOperation(Strict):
    action: Literal['add', 'update', 'link']
    target_id: str
    section_id: str
    link_id: str
    relation: Literal['none', 'references', 'supersedes', 'conflicts_with']
    subject: str = Field(min_length=1, max_length=300)
    scope_quotes: list[str] = Field(max_length=8)
    temporal: str = Field(max_length=300)
    markdown: str = Field(min_length=1, max_length=6000)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class Draft(Strict):
    outcome: Literal['propose', 'abstain', 'defer']
    operations: list[DraftOperation] = Field(max_length=4)
    reason: str = Field(min_length=1, max_length=600)


def schema_for(packet):
    shape = Draft.model_json_schema()
    props = shape['$defs']['DraftOperation']['properties']
    notes = packet['notes']
    props['action']['enum'] = ['add'] + (['link'] if len(notes) > 1 else []) + (
        ['update'] if any(n.get('sections') for n in notes) else [])
    props['target_id']['enum'] = ['', *[n['id'] for n in notes]]
    props['link_id']['enum'] = ['', *[n['id'] for n in notes]]
    props['section_id']['enum'] = ['', *[s for n in notes for s in n.get('sections', {})]]
    props['source_ids']['items']['enum'] = [s['id'] for s in packet['turns']]
    if not notes:
        props['relation']['enum'] = ['none']
    return shape


def expand(draft, packet):
    sources = {s['id']: s for s in packet['turns']}
    notes = {n['id']: n for n in packet['notes']}
    operations = []
    for index, op in enumerate(draft.operations, 1):
        if any(s not in sources for s in op.source_ids):
            raise ValueError('Unknown source identity')
        cited = [sources[s] for s in dict.fromkeys(op.source_ids)]
        actors = {s['actor'] for s in cited}
        if len(actors) != 1:
            raise ValueError('Each operation must use one source actor')
        scopes = []
        for quote in op.scope_quotes:
            matches = [s for s in cited if quote and quote in s['text']]
            if not matches:
                raise ValueError('Scope must quote a cited source')
            scopes.append({'text': quote, 'source_id': matches[0]['id']})
        data = op.model_dump(exclude={'source_ids', 'scope_quotes'})
        data.update(id=f'op{index}', actor=cited[0]['actor'], scopes=scopes,
                    expected_revision=notes.get(op.target_id, {}).get('revision', ''),
                    evidence=[{'source_id': s['id'], 'start': 0, 'end': len(s['text']),
                               'quote': s['text']} for s in cited])
        operations.append(data)
    proposal = Proposal.model_validate({'outcome': draft.outcome, 'reason': draft.reason,
                                        'operations': operations})
    validate(proposal, packet)
    return proposal
