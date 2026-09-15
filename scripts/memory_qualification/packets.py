"""Development packets built from temporary real Markdown, never rubric answers."""
from novi.brain.storage.markdown_store import MarkdownStore
from novi.brain.curation.packet import build_packet, digest, section_marker


def development_packet(case, root):
    store = MarkdownStore(root)
    for note in case['packet']['notes']:
        text = note.get('markdown', note.get('body', ''))
        meta = {'id': note['id'], 'type': 'composite', 'status': 'candidate'}
        body = text
        if 'managed' in note:
            section = digest(note['id'])[:32]
            start, end = section_marker(section)
            body = f'{start}\n{text}\n{end}\n'
            old = note['managed']
            meta['memory_manifest'] = {'version': 1, 'history': [], 'sections': {section: {
                'actor': 'user', 'subject': old['subject'],
                'scopes': [{'text': scope, 'source_id': 'previous'} for scope in old['scopes']],
                'content_sha256': digest(text), 'conversation_id': 'previous',
                'evidence': [{'source_id': 'previous', 'start': 0, 'end': len(text), 'quote': text}]}}}
        store._write_frontmatter(store.knowledge_dir / f'{note["id"]}.md', meta, body)
    base = {'conversation_id': case['id'], 'project_id': None, 'turns': case['packet']['turns']}
    return build_packet(base, store, [n['id'] for n in case['packet']['notes']])
