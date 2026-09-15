import pytest
from novi.brain.curation.draft import Draft, expand
from tests.test_memory_curation import source


def draft():
    return {'outcome': 'propose', 'reason': 'Explicit preference', 'operations': [{
        'action': 'add', 'target_id': '', 'section_id': '', 'link_id': '',
        'relation': 'none', 'subject': 'Model preference', 'scope_quotes': ['For Novi'],
        'temporal': '', 'markdown': 'For Novi the user prefers local models.',
        'source_ids': ['chat:0:user']}]}


def test_expansion_derives_exact_evidence_without_changing_claim():
    proposal = expand(Draft.model_validate(draft()), source())
    op = proposal.operations[0]
    assert op.id == 'op1' and op.actor == 'user'
    assert op.evidence[0].quote == source()['turns'][0]['text']
    assert op.evidence[0].end == len(op.evidence[0].quote)
    assert op.markdown == draft()['operations'][0]['markdown']
    assert op.scopes[0].text == 'For Novi'


def test_expansion_cannot_launder_scope_or_invent_source():
    value = draft()
    value['operations'][0]['markdown'] = 'The user prefers local models.'
    with pytest.raises(ValueError, match='Scope missing'):
        expand(Draft.model_validate(value), source())
    value = draft()
    value['operations'][0]['source_ids'] = ['invented']
    with pytest.raises(ValueError, match='Unknown source'):
        expand(Draft.model_validate(value), source())


def test_expansion_rejects_mixed_actor_evidence():
    packet = source()
    packet['turns'].append({'id': 'a', 'actor': 'assistant', 'text': 'A guess'})
    value = draft()
    value['operations'][0]['source_ids'].append('a')
    with pytest.raises(ValueError, match='one source actor'):
        expand(Draft.model_validate(value), packet)


def test_managed_development_packet_uses_real_sections_without_rubric(tmp_path):
    import json
    from pathlib import Path
    from scripts.memory_qualification.packets import development_packet
    cases = json.loads(Path('tests/fixtures/memory_curation/managed6.json').read_text())
    packet = development_packet(cases[2], tmp_path)
    assert 'rubric' not in packet
    note = packet['notes'][0]
    section = next(iter(note['sections']))
    assert note['sections'][section] == 'For Novi the user prefers light mode and large text.'
    assert note['claims'][section]['subject'] == 'Display preference'
    value = draft()
    value['operations'][0].update(action='update', target_id='n1', section_id=section,
                                  relation='supersedes', subject='Display preference',
                                  markdown='For Novi the user now prefers dark mode and large text.',
                                  source_ids=['t1'])
    result = expand(Draft.model_validate(value), packet)
    assert result.operations[0].expected_revision == note['revision']
    assert result.operations[0].scopes[0].text == 'For Novi'


def test_link_expansion_preserves_only_selected_supplied_targets(tmp_path):
    import json
    from pathlib import Path
    from scripts.memory_qualification.packets import development_packet
    cases = json.loads(Path('tests/fixtures/memory_curation/managed6.json').read_text())
    packet = development_packet(cases[3], tmp_path)
    value = draft()
    value['operations'][0].update(action='link', target_id='n1', link_id='n2',
                                  relation='references', scope_quotes=['for Novi'],
                                  markdown='The offline requirement explains the choice of local models for Novi.',
                                  source_ids=['t1'])
    result = expand(Draft.model_validate(value), packet)
    assert result.operations[0].link_id == 'n2'
    assert result.operations[0].expected_revision == packet['notes'][0]['revision']
    value['operations'][0]['link_id'] = 'invented'
    with pytest.raises(ValueError, match='Unknown or invalid relationship'):
        expand(Draft.model_validate(value), packet)
