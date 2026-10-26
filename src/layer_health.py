"""Per-source freshness, separate from publication time and advisory validity."""
from datetime import datetime,timezone


def status(stamp,now,ttl,available=True):
    try:
        date=datetime.fromisoformat(stamp.replace('Z','+00:00'));age=(now-date).total_seconds()
    except (ValueError,TypeError,AttributeError):return dict(state='UNAVAILABLE',source_at_utc=None,age_minutes=None,stale_after_minutes=ttl)
    state='RECENT' if available and -300<=age<=ttl*60 else 'STALE' if available and age>ttl*60 else 'UNAVAILABLE'
    return dict(state=state,source_at_utc=stamp,age_minutes=round(age/60,1),stale_after_minutes=ttl)


def build(now,rain,temperature,fire,official,station):
    sources=dict(rain=status(rain,now,60),temperature=status(temperature,now,120),fire=status(fire,now,60),advisories=status(official,now,360),stations=status(station,now,120))
    return dict(version=1,checked_at_utc=now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),layers=sources,context='Mac sleep stops the local collector. GitHub schedule continues independently but may be delayed. Station freshness refers to retrieval, not to the unverified observation hour.')
