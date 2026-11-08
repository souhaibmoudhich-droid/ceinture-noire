"""Lit les agendas iCloud partagés (liens dans le secret ICLOUD_URLS, un par ligne)
et écrit busy.json : seulement les créneaux occupés de chaque jour, sans aucun titre.

busy.json = {"updated": "...", "days": {"2026-11-09": [[480, 960], ...]}}
(minutes depuis minuit, heure du Luxembourg)
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

TZ = ZoneInfo("Europe/Luxembourg")
OUT = os.path.join(os.path.dirname(__file__), "..", "busy.json")


def fetch(url):
    url = url.strip().replace("webcal://", "https://", 1)
    req = urllib.request.Request(url, headers={"User-Agent": "ceinture-noire-agenda"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def as_local(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def busy_days(ics_blobs, today):
    start = today - dt.timedelta(days=2)
    end = today + dt.timedelta(days=22)
    days = {}
    for blob in ics_blobs:
        cal = icalendar.Calendar.from_ical(blob)
        for ev in recurring_ical_events.of(cal).between(start, end):
            s = ev.get("DTSTART").dt
            if not isinstance(s, dt.datetime):
                continue  # événement « journée entière » (vacances, anniversaire) : ne bloque pas d'heures
            if str(ev.get("TRANSP", "")).upper() == "TRANSPARENT":
                continue  # marqué « disponible » dans l'agenda
            if ev.get("DTEND") is not None:
                e = ev.get("DTEND").dt
            elif ev.get("DURATION") is not None:
                e = s + ev.get("DURATION").dt
            else:
                e = s + dt.timedelta(hours=1)
            s, e = as_local(s), as_local(e)
            cur = s
            while cur < e:  # découpe les événements qui passent minuit
                midnight = dt.datetime.combine(cur.date(), dt.time(0), TZ)
                seg_end = min(e, midnight + dt.timedelta(days=1))
                a = int((cur - midnight).total_seconds() // 60)
                b = int((seg_end - midnight).total_seconds() // 60)
                if b > a:
                    days.setdefault(cur.date().isoformat(), []).append([a, b])
                cur = seg_end
    merged = {}
    for k in sorted(days):
        out = []
        for a, b in sorted(days[k]):
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        merged[k] = out
    return merged


def main():
    urls = [u for u in os.environ.get("ICLOUD_URLS", "").split() if u.strip()]
    if not urls:
        print("Aucun lien d'agenda dans le secret ICLOUD_URLS : rien à faire.")
        return 0
    blobs = []
    for i, u in enumerate(urls, 1):
        try:
            blobs.append(fetch(u))
        except Exception as exc:  # on garde l'ancien busy.json si un agenda ne répond pas
            print(f"Agenda n°{i} illisible : {exc}", file=sys.stderr)
            return 1
    days = busy_days(blobs, dt.datetime.now(TZ).date())
    try:
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f).get("days")
    except (OSError, ValueError):
        old = None
    if old == days:
        print("Agenda inchangé.")
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "days": days}, f, separators=(",", ":"))
    print(f"busy.json écrit : {sum(len(v) for v in days.values())} créneaux sur {len(days)} jours.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
