"""Journal before writing, preserve history, replay exactly the prepared bytes.

Caller holds the Brain lifecycle lock, not a model lease. External editors can
still race os.replace; pre/post checks detect some races but cannot provide a
cross-process filesystem transaction. Revisions remain in the SQLite journal.
"""
import json
from uuid import NAMESPACE_URL, uuid5
from datetime import datetime
from .contracts import Proposal, validate
from .packet import digest, section_marker


def apply_verified(job, result, packet, markdown, journal, reconcile, cancel):
    if job['mode'] != 'apply' or result.get('state') != 'approved':
        raise ValueError('Only approved apply jobs may change memory')
    proposal = Proposal.model_validate(result['proposal'])
    validate(proposal, packet)
    notes = {note['id']: note for note in packet['notes']}
    targets = [op.target_id for op in proposal.operations if op.target_id]
    if len(set(targets)) != len(targets):
        raise ValueError('One change per existing note per job; split remaining work')
    written = []
    for op in proposal.operations:
        if cancel.is_set():
            raise InterruptedError('Memory application paused')
        record = journal.operation(job['id'], op.id)
        if record is None:
            note_id = op.target_id or str(uuid5(NAMESPACE_URL, f'novi-memory:{job["id"]}:{op.id}'))
            path = markdown.find_for_id(note_id) if op.target_id else markdown.knowledge_dir / f'memory-{note_id}.md'
            if path is None:
                raise ValueError('Target was deleted or renamed outside the snapshot')
            if op.target_id:
                note = notes[op.target_id]
                if path.relative_to(markdown.knowledge_dir).as_posix() != note['path']:
                    raise ValueError('Target renamed; rebase required')
                before = path.read_bytes()
                if digest(before) != op.expected_revision:
                    raise ValueError('User changed target; preserve edit')
                meta, body = markdown.parse(path)
            else:
                if path.exists():
                    raise ValueError('New-note path already exists')
                before, meta, body = None, {}, ''
            section_id = uuid5(NAMESPACE_URL, f'{job["id"]}:{op.id}:section').hex
            text = op.markdown
            if op.action == 'link':
                linked = notes[op.link_id]
                linkpath = markdown.find_for_id(op.link_id)
                if linkpath is None or digest(linkpath.read_bytes()) != linked['revision']:
                    raise ValueError('Linked note changed')
                link = linkpath.relative_to(markdown.knowledge_dir).with_suffix('').as_posix()
                if any(c in link for c in '[]|#\n\r'):
                    raise ValueError('Unsupported wiki-link path')
                text += f'\n\n[[{link}]]'
            start, end = section_marker(section_id)
            section = f'{start}\n{text}\n{end}'
            manifest = meta.setdefault('memory_manifest', {'version': 1, 'sections': {}, 'history': []})
            if manifest.get('version') != 1:
                raise ValueError('Unsupported memory manifest')
            if op.action == 'update':
                old_start, old_end = section_marker(op.section_id)
                if body.count(old_start) != 1:
                    raise ValueError('Managed section changed')
                prefix, _, tail = body.partition(old_start)
                old_text, separator, suffix = tail.partition(old_end)
                old = manifest['sections'].get(op.section_id)
                if not separator or not old or digest(old_text.strip()) != old['content_sha256']:
                    raise ValueError('Managed section was edited')
                manifest.setdefault('history', []).append(dict(old, section_id=op.section_id, markdown=old_text.strip()))
                del manifest['sections'][op.section_id]
                body = prefix + section + suffix
            else:
                body = body.rstrip() + ('\n\n' if body.strip() else '') + section + '\n'
            manifest['sections'][section_id] = dict(op.model_dump(), content_sha256=digest(text),
                                                    job_id=job['id'], conversation_id=job['conversation_id'])
            now = datetime.now().isoformat()
            meta.update(id=note_id, memory_manifest=manifest, updated=now)
            meta.setdefault('timestamp', now)
            meta.setdefault('title', op.subject)
            meta.setdefault('type', 'composite')
            meta['status'] = 'candidate'
            meta.setdefault('source_kind', 'curation')
            # Record curated provenance so repetition-based reflection does not
            # turn model interpretations into verified user facts.
            meta.setdefault('evidence', {})['curation'] = {'version': 1, 'review': 'same-model consistency'}
            meta['sources'] = list(dict.fromkeys([*meta.get('sources', []), job['conversation_id']]))
            import yaml
            after = '---\n' + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + '---\n\n' + body
            record = {'path': path.relative_to(markdown.knowledge_dir).as_posix(), 'note_id': note_id,
                      'before': before.decode('utf-8') if before is not None else None,
                      'after': after, 'after_sha256': digest(after), 'done': False}
            journal.operation(job['id'], op.id, record)
        path = (markdown.knowledge_dir / record['path']).resolve()
        if not path.is_relative_to(markdown.knowledge_dir):
            raise ValueError('Journal path escapes vault')
        current = path.read_bytes() if path.exists() else None
        if current is None or digest(current) != record['after_sha256']:
            before = record['before'].encode('utf-8') if record['before'] is not None else None
            if current != before:
                raise ValueError('Note changed after preparation; rebase required')
            markdown.write_prepared(path, record['after'])
        if digest(path.read_bytes()) != record['after_sha256']:
            raise ValueError('Post-write conflict; inspect preserved journal revisions')
        reconcile()
        record['done'] = True
        journal.operation(job['id'], op.id, record)
        written.append(record['note_id'])
    return written
