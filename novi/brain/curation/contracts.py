"""Model proposals carry evidence, never write authority or truth status."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Evidence(Strict):
    source_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=8000)


class Scope(Strict):
    text: str = Field(min_length=1, max_length=300)
    source_id: str


class Operation(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    action: Literal['add', 'update', 'link']
    target_id: str
    expected_revision: str
    section_id: str
    actor: Literal['user', 'assistant', 'tool']
    subject: str = Field(min_length=1, max_length=300)
    scopes: list[Scope] = Field(max_length=8)
    temporal: str = Field(max_length=300)
    markdown: str = Field(min_length=1, max_length=6000)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)
    link_id: str
    relation: Literal['none', 'references', 'supersedes', 'conflicts_with']


class Proposal(Strict):
    outcome: Literal['propose', 'abstain', 'defer']
    operations: list[Operation] = Field(max_length=4)
    reason: str = Field(min_length=1, max_length=600)


class Review(Strict):
    verdict: Literal['approve', 'revise', 'reject', 'insufficient']
    reason: str = Field(min_length=1, max_length=600)


def grounded_schema(schema, packet):
    """Constrain identities and available operations, never the semantic answer."""
    shape = schema.model_json_schema()
    if schema is not Proposal:
        return shape
    notes, turns = packet['notes'], packet['turns']
    operation = shape['$defs']['Operation']['properties']
    operation['action']['enum'] = ['add'] + (['link'] if len(notes) > 1 else []) + (
        ['update'] if any(n.get('sections') for n in notes) else [])
    operation['target_id']['enum'] = [''] + [n['id'] for n in notes]
    operation['expected_revision']['enum'] = [''] + [n['revision'] for n in notes]
    operation['section_id']['enum'] = [''] + [key for n in notes for key in n.get('sections', {})]
    operation['link_id']['enum'] = [''] + [n['id'] for n in notes]
    actors = sorted({t['actor'] for t in turns})
    if actors:
        operation['actor']['enum'] = actors
    identities = [t['id'] for t in turns]
    if identities:
        shape['$defs']['Evidence']['properties']['source_id']['enum'] = identities
        shape['$defs']['Scope']['properties']['source_id']['enum'] = identities
        evidence = shape['$defs']['Evidence']['properties']
        evidence['start']['enum'] = [0]
        evidence['end']['enum'] = sorted({len(t['text']) for t in turns})
        evidence['quote']['enum'] = [t['text'] for t in turns]
    return shape


def validate(proposal, packet):
    if bool(proposal.operations) != (proposal.outcome == 'propose'):
        raise ValueError('Outcome and operations disagree')
    sources = {s['id']: s for s in packet['turns']}
    notes = {n['id']: n for n in packet['notes']}
    seen = set()
    for op in proposal.operations:
        if op.id in seen:
            raise ValueError('Duplicate operation identity')
        seen.add(op.id)
        if '<!--' in op.markdown or '[[ ' in op.markdown or '[[' in op.markdown:
            raise ValueError('Links and section markers are constructed by code')
        if op.action == 'add':
            if op.target_id or op.expected_revision or op.section_id or op.link_id or op.relation != 'none':
                raise ValueError('Add cannot modify an existing note')
        else:
            note = notes.get(op.target_id)
            if not note or op.expected_revision != note['revision']:
                raise ValueError('Unknown target or stale revision')
            if op.action == 'update' and op.section_id not in note.get('sections', {}):
                raise ValueError('Only intact managed sections may be updated')
            if op.action == 'update':
                previous = note.get('claims', {}).get(op.section_id)
                if not previous or previous.get('actor') != op.actor or previous.get('subject', '').casefold() != op.subject.casefold():
                    raise ValueError('Correction subject or actor does not match')
                if {s['text'].casefold() for s in previous.get('scopes', [])} != {s.text.casefold() for s in op.scopes}:
                    raise ValueError('Correction scope does not match; defer ambiguity')
        if op.action == 'link':
            if op.link_id not in notes or op.link_id == op.target_id or op.relation not in ('references', 'conflicts_with'):
                raise ValueError('Unknown or invalid relationship')
            if op.section_id:
                raise ValueError('A link adds its own managed section')
        elif op.link_id or op.relation != ('supersedes' if op.action == 'update' else 'none'):
            raise ValueError('Invalid operation relation')
        for evidence in op.evidence:
            source = sources.get(evidence.source_id)
            if not source or source['actor'] != op.actor:
                raise ValueError('Unknown source or actor mismatch')
            if evidence.start >= evidence.end or evidence.end > len(source['text']):
                raise ValueError('Invalid source offsets')
            if source['text'][evidence.start:evidence.end] != evidence.quote:
                raise ValueError('Source quote changed')
        evidence_ids = {e.source_id for e in op.evidence}
        for scope in op.scopes:
            if scope.source_id not in evidence_ids or scope.text not in sources[scope.source_id]['text']:
                raise ValueError('Scope must quote the cited source')
            if scope.text not in op.markdown:
                raise ValueError('Scope missing from visible memory')
        # Missing/incorrectly interpreted scopes still require semantic review.
