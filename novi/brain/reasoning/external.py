"""Pure external-evidence admission and validity rules; no storage or network."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from ..types import KnowledgeForm, KnowledgeItem, KnowledgeStatus


def utc(value=None):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_item(statement, sources, volatility='changing', now=None, *, independent=False):
    """Admit source-attributed evidence. Verification also needs independent support.

    The caller validates excerpts and support against actual retrieved pages.
    Distinct domains alone are not proof of independence (syndication exists).
    """
    now = utc(now)
    records = []
    for source in sources[:5]:
        parsed = urlsplit(source.get('url', ''))
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username:
            continue
        if not source.get('excerpt', '').strip():
            continue
        records.append({k: str(source.get(k, ''))[:2000] for k in
                        ('url', 'title', 'excerpt', 'published_at')})
    if not records or not statement.strip():
        raise ValueError('A claim requires source evidence')
    domains = {urlsplit(s['url']).hostname.lower().removeprefix('www.') for s in records}
    verified = independent and len(domains) >= 2
    volatility = volatility if volatility in ('stable', 'changing', 'live') else 'changing'
    interval = {'changing': 1, 'live': 0}.get(volatility)
    evidence = dict(sources=records, volatility=volatility,
                    verified_at=now.isoformat() if verified else None,
                    retrieved_at=now.isoformat(),
                    recheck_after=(now + timedelta(days=interval)).isoformat() if interval is not None else None)
    # Source content versions, not observation count, establish identity.
    identity = json.dumps([re.sub(r'\s+', ' ', statement.strip()).casefold(), records], sort_keys=True)
    return KnowledgeItem(
        id='web-' + sha256(identity.encode()).hexdigest()[:24],
        form=KnowledgeForm.ATOMIC, content=statement.strip()[:1500],
        confidence=.85 if verified else .5,
        status=KnowledgeStatus.VERIFIED if verified else KnowledgeStatus.CANDIDATE,
        tags=('public_fact',), sources=tuple(s['url'] for s in records),
        created_at=now, evidence=evidence,
    )


def reusable(item, now=None, freshness='stable'):
    data = item.evidence
    if item.status != KnowledgeStatus.VERIFIED or not data.get('verified_at') or data.get('conflicted'):
        return False
    try:
        now = utc(now)
        if utc(data['verified_at']) > now:
            return False
        if freshness == 'live':
            return False
        expiry = data.get('recheck_after')
        if expiry and now >= utc(expiry):
            return False
        if freshness == 'changing' and now - utc(data['verified_at']) >= timedelta(days=1):
            return False
        return True
    except (TypeError, ValueError):
        return False
