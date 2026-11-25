import datetime as dt
import json
import os
import socket
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import agenda  # noqa: E402

TODAY = dt.date(2026, 11, 9)  # un lundi
NOW = dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.timezone.utc)


def ics(*events):
    body = "".join("BEGIN:VEVENT\r\nUID:%d@test\r\nDTSTAMP:20261101T000000Z\r\n%s\r\nEND:VEVENT\r\n" % (i, e.strip().replace("\n", "\r\n"))
                   for i, e in enumerate(events))
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//FR\r\n" + body + "END:VCALENDAR\r\n").encode()


def busy(*events, today=TODAY):
    return agenda.busy_days([ics(*events)], today)


# ---------- lecture des événements ----------

def test_simple_event_in_local_time():
    assert busy("DTSTART;TZID=Europe/Luxembourg:20261109T100000\nDTEND;TZID=Europe/Luxembourg:20261109T113000") == {
        "2026-11-09": [[600, 690]]}


def test_all_day_events_do_not_block_hours():
    assert busy("DTSTART;VALUE=DATE:20261109\nDTEND;VALUE=DATE:20261110",
                "DTSTART;VALUE=DATE:20261110\nDTEND;VALUE=DATE:20261115\nSUMMARY:Vacances") == {}


def test_all_day_recurring_birthday_ignored():
    assert busy("DTSTART;VALUE=DATE:20001110\nRRULE:FREQ=YEARLY") == {}


def test_weekly_recurring_event():
    days = busy("DTSTART;TZID=Europe/Luxembourg:20261103T180000\nDTEND;TZID=Europe/Luxembourg:20261103T193000\nRRULE:FREQ=WEEKLY;BYDAY=TU")
    assert days == {"2026-11-10": [[1080, 1170]], "2026-11-17": [[1080, 1170]], "2026-11-24": [[1080, 1170]]}


def test_recurring_exception_same_uid():
    blob = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//FR\r\n"
            b"BEGIN:VEVENT\r\nUID:r@t\r\nDTSTAMP:20261101T000000Z\r\nDTSTART;TZID=Europe/Luxembourg:20261109T080000\r\n"
            b"DTEND;TZID=Europe/Luxembourg:20261109T090000\r\nRRULE:FREQ=DAILY;COUNT=3\r\n"
            b"EXDATE;TZID=Europe/Luxembourg:20261110T080000\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:r@t\r\nDTSTAMP:20261101T000000Z\r\nRECURRENCE-ID;TZID=Europe/Luxembourg:20261111T080000\r\n"
            b"DTSTART;TZID=Europe/Luxembourg:20261111T140000\r\nDTEND;TZID=Europe/Luxembourg:20261111T150000\r\nEND:VEVENT\r\n"
            b"END:VCALENDAR\r\n")
    assert agenda.busy_days([blob], TODAY) == {"2026-11-09": [[480, 540]], "2026-11-11": [[840, 900]]}


def test_utc_event_converted_to_luxembourg_winter():
    assert busy("DTSTART:20261109T090000Z\nDTEND:20261109T100000Z") == {"2026-11-09": [[600, 660]]}


def test_utc_event_converted_to_luxembourg_summer():
    days = busy("DTSTART:20270330T090000Z\nDTEND:20270330T100000Z", today=dt.date(2027, 3, 29))
    assert days == {"2027-03-30": [[660, 720]]}  # UTC+2 en été


def test_event_in_other_timezone():
    # 9h à New York = 15h au Luxembourg en novembre
    assert busy("DTSTART;TZID=America/New_York:20261109T090000\nDTEND;TZID=America/New_York:20261109T100000") == {
        "2026-11-09": [[900, 960]]}


def test_floating_time_read_as_local():
    assert busy("DTSTART:20261109T100000\nDTEND:20261109T110000") == {"2026-11-09": [[600, 660]]}


def test_weekly_event_keeps_local_hour_across_dst_change():
    # cours à 18h le dimanche, de part et d'autre du passage à l'heure d'été (28 mars 2027)
    days = busy("DTSTART;TZID=Europe/Luxembourg:20270321T180000\nDTEND;TZID=Europe/Luxembourg:20270321T190000\nRRULE:FREQ=WEEKLY",
                today=dt.date(2027, 3, 22))
    assert days["2027-03-28"] == [[1080, 1140]]
    assert days["2027-04-04"] == [[1080, 1140]]


def test_dst_day_minutes_are_wall_clock():
    # le jour du passage à l'heure d'été, 10h reste 600 minutes « affichées »
    days = busy("DTSTART;TZID=Europe/Luxembourg:20270328T100000\nDTEND;TZID=Europe/Luxembourg:20270328T110000",
                today=dt.date(2027, 3, 27))
    assert days == {"2027-03-28": [[600, 660]]}


def test_event_across_midnight_is_split():
    assert busy("DTSTART;TZID=Europe/Luxembourg:20261109T220000\nDTEND;TZID=Europe/Luxembourg:20261110T013000") == {
        "2026-11-09": [[1320, 1440]], "2026-11-10": [[0, 90]]}


def test_duration_and_missing_end():
    # sans DTEND ni DURATION, la norme iCalendar dit « durée nulle » : rien n'est bloqué
    assert busy("DTSTART;TZID=Europe/Luxembourg:20261109T100000\nDURATION:PT45M",
                "DTSTART;TZID=Europe/Luxembourg:20261110T100000") == {
        "2026-11-09": [[600, 645]]}


def test_transparent_and_cancelled_ignored():
    assert busy("DTSTART;TZID=Europe/Luxembourg:20261109T100000\nDTEND;TZID=Europe/Luxembourg:20261109T110000\nTRANSP:TRANSPARENT",
                "DTSTART;TZID=Europe/Luxembourg:20261109T120000\nDTEND;TZID=Europe/Luxembourg:20261109T130000\nSTATUS:CANCELLED") == {}


def test_overlaps_merged_across_calendars():
    a = ics("DTSTART;TZID=Europe/Luxembourg:20261109T100000\nDTEND;TZID=Europe/Luxembourg:20261109T120000")
    b = ics("DTSTART;TZID=Europe/Luxembourg:20261109T113000\nDTEND;TZID=Europe/Luxembourg:20261109T130000",
            "DTSTART;TZID=Europe/Luxembourg:20261109T130000\nDTEND;TZID=Europe/Luxembourg:20261109T140000",
            "DTSTART;TZID=Europe/Luxembourg:20261109T160000\nDTEND;TZID=Europe/Luxembourg:20261109T170000")
    assert agenda.busy_days([a, b], TODAY) == {"2026-11-09": [[600, 840], [960, 1020]]}


def test_window_limits():
    days = busy("DTSTART;TZID=Europe/Luxembourg:20261101T100000\nDTEND;TZID=Europe/Luxembourg:20261101T110000",
                "DTSTART;TZID=Europe/Luxembourg:20261108T100000\nDTEND;TZID=Europe/Luxembourg:20261108T110000",
                "DTSTART;TZID=Europe/Luxembourg:20261212T100000\nDTEND;TZID=Europe/Luxembourg:20261212T110000")
    assert days == {"2026-11-08": [[600, 660]]}


def test_empty_calendar():
    assert agenda.busy_days([ics()], TODAY) == {}


def test_no_titles_in_output():
    days = busy("DTSTART;TZID=Europe/Luxembourg:20261109T100000\nDTEND;TZID=Europe/Luxembourg:20261109T110000\nSUMMARY:Dentiste secret\nLOCATION:Rue X")
    assert "Dentiste" not in json.dumps(days) and "Rue" not in json.dumps(days)


# ---------- agendas illisibles ----------

@pytest.mark.parametrize("blob", [b"", b"<html><body>Not found</body></html>", b"BEGIN:VEVENT\r\nEND:VEVENT\r\n"])
def test_not_a_calendar(blob):
    with pytest.raises(agenda.AgendaError, match="pas un agenda"):
        agenda.parse(blob)


def _raiser(exc):
    def f(*a, **k):
        raise exc
    return f


@pytest.mark.parametrize("exc,msg", [
    (urllib.error.HTTPError("https://secret", 404, "Not Found", {}, None), "HTTP 404"),
    (urllib.error.HTTPError("https://secret", 503, "Busy", {}, None), "HTTP 503"),
    (urllib.error.URLError(socket.gaierror("no host")), "réseau"),
    (urllib.error.URLError(socket.timeout("t")), "délai"),
    (TimeoutError(), "délai"),
    (ValueError("unknown url type"), "adresse valide"),
])
def test_fetch_errors_are_readable_and_hide_the_link(monkeypatch, exc, msg):
    monkeypatch.setattr(agenda.urllib.request, "urlopen", _raiser(exc))
    with pytest.raises(agenda.AgendaError) as info:
        agenda.fetch("webcal://p01-caldav.icloud.com/published/2/SECRET")
    assert msg in str(info.value)
    assert "SECRET" not in str(info.value) and "icloud.com" not in str(info.value)


def test_read_all_names_the_broken_calendar():
    blobs = {"u1": ics(), "u2": b"<html>"}
    with pytest.raises(agenda.AgendaError, match="agenda n°2"):
        agenda.read_all(["u1", "u2"], TODAY, fetcher=blobs.__getitem__)


# ---------- busy.json ----------

def iso(t):
    return t.isoformat(timespec="seconds")


def test_state_first_read():
    s = agenda.next_state({"updated": None, "days": {}}, {"2026-11-09": [[1, 2]]}, None, NOW)
    assert s == {"updated": iso(NOW), "checked": iso(NOW), "error": None, "days": {"2026-11-09": [[1, 2]]}}


def test_state_first_read_of_empty_calendar_is_written():
    # l'app doit voir que l'agenda est branché même sans rendez-vous
    s = agenda.next_state({"updated": None, "days": {}}, {}, None, NOW)
    assert s["checked"] == iso(NOW) and s["days"] == {}


def test_state_unchanged_recently_checked_not_written():
    old = {"updated": iso(NOW), "checked": iso(NOW), "error": None, "days": {"d": [[1, 2]]}}
    assert agenda.next_state(old, {"d": [[1, 2]]}, None, NOW + dt.timedelta(hours=1)) is None


def test_state_heartbeat():
    old = {"updated": iso(NOW), "checked": iso(NOW), "error": None, "days": {"d": [[1, 2]]}}
    later = NOW + dt.timedelta(hours=13)
    s = agenda.next_state(old, {"d": [[1, 2]]}, None, later)
    assert s["checked"] == iso(later) and s["updated"] == iso(NOW)


def test_state_error_keeps_old_days():
    old = {"updated": iso(NOW), "checked": iso(NOW), "error": None, "days": {"d": [[1, 2]]}}
    later = NOW + dt.timedelta(hours=1)
    s = agenda.next_state(old, None, "agenda : HTTP 404", later)
    assert s["days"] == {"d": [[1, 2]]} and s["checked"] == iso(NOW)
    assert s["error"] == {"since": iso(later), "msg": "agenda : HTTP 404"}
    # même erreur au passage suivant : rien à réécrire (pas de commit inutile)
    assert agenda.next_state(s, None, "agenda : HTTP 404", later + dt.timedelta(hours=1)) is None


def test_state_recovery_clears_error():
    old = {"updated": iso(NOW), "checked": iso(NOW), "error": {"since": iso(NOW), "msg": "x"}, "days": {"d": [[1, 2]]}}
    s = agenda.next_state(old, {"d": [[1, 2]]}, None, NOW + dt.timedelta(minutes=30))
    assert s["error"] is None


def test_state_tolerates_garbage():
    s = agenda.next_state("pas un objet", {}, None, NOW)
    assert s["checked"] == iso(NOW)


# ---------- main ----------

def run_main(tmp_path, urls, fetcher, old=None):
    out = tmp_path / "busy.json"
    if old is not None:
        out.write_text(json.dumps(old), encoding="utf-8")
    code = agenda.main(env={"ICLOUD_URLS": urls}, out=str(out), fetcher=fetcher, now=NOW)
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def test_main_without_secret_does_nothing(tmp_path):
    assert run_main(tmp_path, "", None) == (0, None)


def test_main_success(tmp_path):
    blob = ics("DTSTART;TZID=Europe/Luxembourg:20261109T100000\nDTEND;TZID=Europe/Luxembourg:20261109T110000")
    code, data = run_main(tmp_path, "webcal://a\nwebcal://b", lambda u: blob)
    assert code == 0 and data["days"] == {"2026-11-09": [[600, 660]]} and data["error"] is None


def test_main_unreachable_keeps_old_days_and_fails(tmp_path):
    old = {"updated": "2026-11-08T08:00:00+00:00", "checked": "2026-11-08T08:00:00+00:00", "error": None, "days": {"d": [[1, 2]]}}
    code, data = run_main(tmp_path, "webcal://a", _raiser(agenda.AgendaError("iCloud ne répond pas")), old)
    assert code == 1
    assert data["days"] == {"d": [[1, 2]]}
    assert data["error"]["msg"] == "agenda : iCloud ne répond pas"


def test_main_unexpected_crash_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(agenda, "busy_days", _raiser(KeyError("boom")))
    code, data = run_main(tmp_path, "webcal://a", lambda u: ics())
    assert code == 1 and "KeyError" in data["error"]["msg"]


def test_main_corrupt_busy_json(tmp_path):
    out = tmp_path / "busy.json"
    out.write_text("{oups", encoding="utf-8")
    code = agenda.main(env={"ICLOUD_URLS": "webcal://a"}, out=str(out), fetcher=lambda u: ics(), now=NOW)
    assert code == 0 and json.loads(out.read_text())["checked"] == iso(NOW)
