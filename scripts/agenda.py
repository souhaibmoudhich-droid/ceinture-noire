"""Lit les agendas iCloud partagés (liens dans le secret ICLOUD_URLS, un par ligne)
et écrit busy.json : seulement les créneaux occupés de chaque jour, sans aucun titre.

busy.json = {
  "updated": "...",   # dernier changement des créneaux
  "checked": "...",   # dernière lecture réussie de tous les agendas
  "error": null | {"since": "...", "msg": "..."},  # lecture en échec (on garde les anciens créneaux)
  "days": {"2026-11-09": [[480, 960], ...]}       # minutes depuis minuit, heure du Luxembourg
}

busy.json est public (GitHub Pages) : il ne contient jamais de titre ni de lien d'agenda.
"""
import datetime as dt
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

TZ = ZoneInfo("Europe/Luxembourg")
OUT = os.path.join(os.path.dirname(__file__), "..", "busy.json")
# même sans changement, on réécrit busy.json de temps en temps : l'app voit que la
# lecture tourne encore, et le dépôt reste « actif » (GitHub coupe les tâches
# planifiées après 60 jours sans activité)
HEARTBEAT = dt.timedelta(hours=12)


class AgendaError(Exception):
    """Erreur lisible par un humain, sans le lien secret de l'agenda."""


def fetch(url, timeout=30):
    url = url.strip().replace("webcal://", "https://", 1)
    req = urllib.request.Request(url, headers={"User-Agent": "ceinture-noire-agenda"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404, 410):
            raise AgendaError(f"lien refusé par iCloud (HTTP {exc.code}) : l'agenda n'est peut-être plus partagé publiquement") from None
        raise AgendaError(f"iCloud a répondu HTTP {exc.code}") from None
    except (socket.timeout, TimeoutError):
        raise AgendaError("iCloud ne répond pas (délai dépassé)") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise AgendaError("iCloud ne répond pas (délai dépassé)") from None
        raise AgendaError("impossible de joindre iCloud (réseau)") from None
    except ValueError:
        raise AgendaError("le lien n'est pas une adresse valide (il doit commencer par webcal:// ou https://)") from None


def parse(blob):
    try:
        cal = icalendar.Calendar.from_ical(blob)
    except ValueError:
        raise AgendaError("la réponse n'est pas un agenda (.ics)") from None
    if cal.name != "VCALENDAR":
        raise AgendaError("la réponse n'est pas un agenda (.ics)")
    return cal


def as_local(value):
    if value.tzinfo is None:  # heure « flottante » : on la lit comme heure du Luxembourg
        value = value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def busy_days(calendars, today):
    start = today - dt.timedelta(days=2)
    end = today + dt.timedelta(days=22)
    days = {}
    for cal in calendars:
        if not isinstance(cal, icalendar.Calendar):
            cal = parse(cal)
        for ev in recurring_ical_events.of(cal).between(start, end):
            s = ev.get("DTSTART").dt
            if not isinstance(s, dt.datetime):
                continue  # événement « journée entière » (vacances, anniversaire) : ne bloque pas d'heures
            if str(ev.get("TRANSP", "")).upper() == "TRANSPARENT":
                continue  # marqué « disponible » dans l'agenda
            if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                continue  # rendez-vous annulé
            if ev.get("DTEND") is not None:
                e = ev.get("DTEND").dt
            elif ev.get("DURATION") is not None:
                e = s + ev.get("DURATION").dt
            else:
                e = s  # norme iCalendar : sans fin ni durée, l'événement dure 0 minute
            if not isinstance(e, dt.datetime):
                e = dt.datetime.combine(e, dt.time(0))
            s, e = as_local(s), as_local(e)
            cur = s
            while cur < e:  # découpe les événements qui passent minuit
                midnight = dt.datetime.combine(cur.date(), dt.time(0), TZ)
                seg_end = min(e, midnight + dt.timedelta(days=1))
                # même tzinfo : la soustraction donne l'heure affichée (correct aussi les jours de changement d'heure)
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


def _iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def next_state(old, days, error, now):
    """Nouveau contenu de busy.json, ou None s'il n'y a rien à écrire.

    days : créneaux lus (None si la lecture a échoué) ; error : message ou None.
    """
    old = old if isinstance(old, dict) else {}
    old_days = old.get("days") if isinstance(old.get("days"), dict) else {}
    old_err = old.get("error") if isinstance(old.get("error"), dict) else None
    state = {
        "updated": old.get("updated"),
        "checked": old.get("checked"),
        "error": None,
        "days": old_days,
    }
    if error:
        # on garde les anciens créneaux : mieux vaut un agenda un peu vieux qu'un agenda vide
        same = old_err and old_err.get("msg") == error
        state["error"] = {"since": old_err["since"] if same else _iso(now), "msg": error}
        return None if same else state
    state["checked"] = _iso(now)
    if days != old_days:
        state["days"] = days
        state["updated"] = _iso(now)
        return state
    if old_err:
        return state
    try:
        last = dt.datetime.fromisoformat(old.get("checked"))
    except (TypeError, ValueError):
        return state
    return state if now - last >= HEARTBEAT else None


def read_all(urls, today, fetcher=fetch):
    """Créneaux de tous les agendas, ou AgendaError si un seul est illisible."""
    cals = []
    for i, u in enumerate(urls, 1):
        where = f"agenda n°{i}" if len(urls) > 1 else "agenda"
        try:
            cals.append(parse(fetcher(u)))
        except AgendaError as exc:
            raise AgendaError(f"{where} : {exc}") from None
    return busy_days(cals, today)


def main(env=os.environ, out=OUT, fetcher=fetch, now=None):
    urls = [u for u in env.get("ICLOUD_URLS", "").split() if u.strip()]
    if not urls:
        print("Aucun lien d'agenda dans le secret ICLOUD_URLS : rien à faire.")
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        with open(out, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    days, error = None, None
    try:
        days = read_all(urls, now.astimezone(TZ).date(), fetcher)
    except AgendaError as exc:
        error = str(exc)
    except Exception as exc:  # bug ou agenda bizarre : on le signale sans tout casser
        error = f"agenda illisible ({type(exc).__name__})"
    state = next_state(old, days, error, now)
    if state is not None:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    if error:
        print(f"Échec de lecture : {error}. Les anciens créneaux sont gardés.", file=sys.stderr)
        return 1  # la tâche GitHub passe en rouge (et GitHub prévient par e-mail)
    n = sum(len(v) for v in days.values())
    print(f"{'busy.json écrit' if state else 'Agenda inchangé'} : {n} créneaux sur {len(days)} jours.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
