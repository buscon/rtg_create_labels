"""End-to-end tests of the PHP API, run against PHP's built-in web server.

    pip install -r scripts/requirements.txt
    python scripts/make_placeholder_audio.py && python scripts/make_headphone_stimuli.py
    pytest tests/

Each run uses a temporary copy of public/ and private/ with its own database. The tests run
with SQLite, and additionally with MariaDB/MySQL if the environment variable
RGT_TEST_MYSQL is set to  host:port:user:password:dbname  (the database is
emptied before the tests; use a test database only).
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def set_ini(path: Path, section: str, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = rf"(\[{section}\][^\[]*?^{key}\s*=\s*)[^;\n]*"
    new, n = re.subn(pattern, rf"\g<1>{value} ", text, flags=re.M | re.S)
    assert n == 1, f"{section}.{key} not found"
    path.write_text(new, encoding="utf-8")


DRIVERS = ["sqlite"] + (["mysql"] if os.environ.get("RGT_TEST_MYSQL") else [])


@pytest.fixture(scope="module", params=DRIVERS)
def server(request, tmp_path_factory):
    driver = request.param
    site = tmp_path_factory.mktemp("site")
    ignore = shutil.ignore_patterns("db", "dumps", "models", "vendor", "interviewer_token.hash",
                                    "db_credentials.ini", "private_path.php")
    shutil.copytree(REPO / "public", site / "public", ignore=ignore)
    shutil.copytree(REPO / "private", site / "private", ignore=ignore)
    root = site                                       # contains public/ and private/
    ini = root / "private" / "config.ini"
    set_ini(ini, "similarity", "backend", '"embedding"')
    set_ini(ini, "server", "db_driver", f'"{driver}"')
    # access protection (design file 14): one group code for all tests; the timing check and the
    # IP limit are switched off here and tested separately
    set_ini(ini, "headphone_check", "enabled", "true")   # off in the study config, but still tested
    set_ini(ini, "triads", "practice_triad", "true")     # off in the study config; the tests expect it
    set_ini(ini, "access", "require_code", "true")
    set_ini(ini, "access", "min_play_fraction", "0")
    set_ini(ini, "access", "max_sessions_per_ip_hour", "0")
    (root / "private" / "access_codes.csv").write_text(
        "code,max_uses,valid_until,note\nTEST-CODE,100000,,tests\nFULL-CODE,1,,one use\n"
        "OLD-CODE,10,2020-01-01,expired\n", encoding="utf-8")
    mysql = None
    if driver == "mysql":
        import pymysql
        host, port, user, pw, dbname = os.environ["RGT_TEST_MYSQL"].split(":")
        mysql = dict(host=host, port=int(port), user=user, password=pw, database=dbname)
        con = pymysql.connect(host=host, port=int(port), user=user, password=pw)
        with con.cursor() as c:
            c.execute(f"DROP DATABASE IF EXISTS `{dbname}`")
            c.execute(f"CREATE DATABASE `{dbname}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        con.close()
        (root / "private" / "db_credentials.ini").write_text(
            f'host = "{host}"\nport = {port}\ndbname = "{dbname}"\nuser = "{user}"\npassword = "{pw}"\n')
    out = subprocess.run(["php", str(root / "private" / "setup.php"), "--set-token"],
                         capture_output=True, text=True, check=True).stdout
    token = re.search(r"new interviewer password: (\S+)", out).group(1)
    port = free_port()
    proc = subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", "-t", str(root / "public")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/"
    for _ in range(50):
        try:
            requests.get(base + "api.php?r=health", timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    with open(root / "private" / "headphone_manifest.csv", encoding="utf-8") as f:
        answers = {r["filename"]: int(r["target"]) for r in csv.DictReader(f)}
    yield {"base": base, "token": token, "root": root, "hp_answers": answers, "driver": driver, "mysql": mysql}
    proc.terminate()


def query(server, sql, params=()):
    """Read from the test database, whichever driver is used."""
    if server["driver"] == "sqlite":
        import sqlite3
        con = sqlite3.connect(server["root"] / "private" / "db" / "rgt.sqlite")
        rows = con.execute(sql, params).fetchall()
    else:
        import pymysql
        con = pymysql.connect(**server["mysql"])
        with con.cursor() as c:
            c.execute(sql.replace("?", "%s"), params)
            rows = c.fetchall()
    con.close()
    return [tuple(r) for r in rows]


def call(server, route, method="GET", body=None, token=None, **query):
    headers = {"X-Interviewer-Token": token} if token else {}
    r = requests.request(method, server["base"] + "api.php", params={"r": route, **query},
                         json=body, headers=headers, timeout=10)
    return r.status_code, r.json()


def new_session(server, token=None, pass_headphones=True):
    code, s = call(server, "session", "POST", {"language": "de", "access_code": None if token else "test-code"}, token=token)
    assert code == 200, s
    pid = s["participant_id"]
    answers = [server["hp_answers"][t["url"].split("/")[-1]] for t in s["headphone_trials"]]
    if not pass_headphones:
        answers = [a % 3 + 1 for a in answers]   # all wrong
    code, h = call(server, "headphone", "POST", {"answers": answers}, pid=pid)
    assert code == 200
    assert h["passed"] is pass_headphones
    return pid


def play_all(server, pid, triad):
    for c in triad["clips"]:
        for ev in ("play", "ended"):
            code, _ = call(server, "play", "POST", {"participant_id": pid, "triad_id": triad["triad_id"],
                                                     "position": c["position"], "event": ev, "at_ms": 1})
            assert code == 200


def vec(i: int, dim: int = 8) -> list[float]:
    v = [0.0] * dim
    v[i % dim] = 1.0
    return v


def answer(server, pid, sim, con, emb=None, nodiff=False, token=None):
    code, n = call(server, "next", pid=pid)
    assert code == 200 and not n["done"], n
    triad = n["triad"]
    play_all(server, pid, triad)
    body = {"participant_id": pid, "triad_id": triad["triad_id"], "rt_ms": 1000}
    if nodiff:
        body["no_difference"] = True
    else:
        body.update({"pair": ["A", "C"], "similarity_pole": sim, "contrast_pole": con})
        if emb is not None:
            body["embedding"] = emb
    code, r = call(server, "response", "POST", body, token=token)
    assert code == 200, r
    return triad, r


# ---------------------------------------------------------------- tests

def test_config_hides_categories(server):
    code, c = call(server, "config")
    assert code == 200
    assert "category" not in str(c) and "natural" not in str(c)


def test_private_files_not_in_api(server):
    code, s = call(server, "session", "POST", {"language": "de", "access_code": "TESTCODE"})
    assert "target" not in str(s)
    assert all(re.fullmatch(r"audio/headphone/hp_[0-9a-f]{8}\.wav", t["url"]) for t in s["headphone_trials"])


def test_headphone_fail_ends_session(server):
    pid = new_session(server, pass_headphones=False)
    code, st = call(server, "state", pid=pid)
    assert st["finished"] and st["stop_reason"] == "headphone_check"
    code, _ = call(server, "next", pid=pid)
    assert code == 200  # returns done


def test_must_play_all_clips(server):
    pid = new_session(server)
    code, n = call(server, "next", pid=pid)
    triad = n["triad"]
    assert triad["is_practice"]
    code, r = call(server, "response", "POST", {"participant_id": pid, "triad_id": triad["triad_id"],
                                                "pair": ["A", "B"], "similarity_pole": "ruhig", "contrast_pole": "laut"})
    assert code == 400


def test_label_validation(server):
    pid = new_session(server)
    code, n = call(server, "next", pid=pid)
    triad = n["triad"]
    play_all(server, pid, triad)
    base = {"participant_id": pid, "triad_id": triad["triad_id"]}
    assert call(server, "response", "POST", {**base, "pair": ["A", "A"], "similarity_pole": "x", "contrast_pole": "y"})[0] == 400
    assert call(server, "response", "POST", {**base, "pair": ["A", "B"], "similarity_pole": "", "contrast_pole": "y"})[0] == 400
    assert call(server, "response", "POST", {**base, "pair": ["A", "B"], "similarity_pole": "x" * 81, "contrast_pole": "y"})[0] == 400
    assert call(server, "response", "POST", {**base, "pair": ["A", "B"], "similarity_pole": "x", "contrast_pole": "y"})[0] == 200
    # same triad again is refused
    assert call(server, "response", "POST", {**base, "pair": ["A", "B"], "similarity_pole": "x", "contrast_pole": "y"})[0] == 409


def test_saturation_stop_with_embeddings(server):
    """6 new constructs, then 3 repetitions -> stop after triad 9 (min 6, 3 in a row)."""
    pid = new_session(server)
    answer(server, pid, "Übung", "Übung2")                                   # practice
    for i in range(6):
        _, r = answer(server, pid, f"a{i}", f"b{i}", {"similarity": vec(i), "contrast": vec(i + 1)})
        assert not r["done"]
    # repetitions: same vectors as construct 0, different words
    for k in range(3):
        _, r = answer(server, pid, f"anders{k}", f"wort{k}", {"similarity": vec(0), "contrast": vec(1)})
    assert r["done"] and r["stop_reason"] == "saturation"
    code, n = call(server, "next", pid=pid)
    assert n["done"]


def test_orientation_swap_counts_as_repetition(server):
    pid = new_session(server)
    answer(server, pid, "p", "q")                                             # practice
    answer(server, pid, "natürlich", "technisch", {"similarity": vec(0), "contrast": vec(5)})
    _, r = answer(server, pid, "Technisch", "natürlich!", {"similarity": vec(5), "contrast": vec(0)})
    row = query(server, "SELECT auto_is_new, similarity_backend, similarity_score FROM constructs "
                        "WHERE construct_id = ?", (r["construct_id"],))[0]
    assert row[0] == 0 and row[1] == "identical_text"


def test_text_fallback_without_embedding(server):
    pid = new_session(server)
    answer(server, pid, "p", "q")
    answer(server, pid, "Vogelgezwitscher", "Verkehrslärm")
    _, r = answer(server, pid, "Vogelgezwitscher hell", "Verkehrslärm")
    row = query(server, "SELECT similarity_backend, similarity_score, auto_is_new FROM constructs "
                        "WHERE construct_id = ?", (r["construct_id"],))[0]
    assert row[0] == "text" and row[1] > 0.85 and row[2] == 0


def n_triads_and_clips() -> tuple[int, int]:
    with open(REPO / "private" / "data" / "triad_set.csv", newline="") as f:
        triads = list(csv.DictReader(f))
    clips = {int(r[k]) for r in triads for k in ("clip_a", "clip_b", "clip_c")}
    return len(triads), len(clips)


def test_no_difference_counts_and_max_triads(server):
    pid = new_session(server)
    answer(server, pid, "p", "q")
    done = False
    n = 0
    while not done:
        n += 1
        # alternate new constructs and "no difference" so saturation is never reached
        if n % 2:
            _, r = answer(server, pid, f"wort{n}", f"gegen{n}", {"similarity": vec(n, 64), "contrast": vec(n + 30, 64)})
        else:
            _, r = answer(server, pid, "", "", nodiff=True)
        done = r["done"]
    assert n == n_triads_and_clips()[0] and r["stop_reason"] == "max_triads"


def test_each_session_covers_all_pairs(server):
    import itertools
    pid = new_session(server)
    rows = query(server, "SELECT pos_a, pos_b, pos_c FROM triads WHERE participant_id = ? AND order_index >= 1",
                 (pid,))
    n_triads, n_clips = n_triads_and_clips()
    assert len(rows) == n_triads
    pairs = {frozenset(p) for r in rows for p in itertools.combinations(r, 2)}
    assert len(pairs) == n_clips * (n_clips - 1) // 2


def test_interviewer_mode(server):
    assert call(server, "session", "POST", {"language": "de"}, token="wrong")[0] == 403
    pid = new_session(server, token=server["token"])
    _, st = call(server, "state", pid=pid)
    assert st["mode"] == "interviewer"
    answer(server, pid, "p", "q", token=server["token"])
    _, r1 = answer(server, pid, "ruhig", "hektisch", {"similarity": vec(0), "contrast": vec(1)}, token=server["token"])
    _, r2 = answer(server, pid, "still", "unruhig", {"similarity": vec(2), "contrast": vec(3)}, token=server["token"])
    assert [c["construct_id"] for c in r2["earlier_constructs"]] == [r1["construct_id"]]
    assert "auto_is_new" not in str(r2)                       # automatic judgement not shown
    body = {"participant_id": pid, "construct_id": r2["construct_id"], "judged_new": False,
            "same_as": r1["construct_id"], "note": "gleiche Dimension"}
    assert call(server, "judgement", "POST", body)[0] == 403
    assert call(server, "judgement", "POST", body, token=server["token"])[0] == 200


def test_unknown_participant(server):
    assert call(server, "next", pid="P-doesnotexist")[0] == 404


def test_backup_dump_and_import(server, tmp_path):
    out = subprocess.run(["php", str(server["root"] / "private" / "backup.php")],
                         capture_output=True, text=True, check=True).stdout
    dump = Path(re.search(r"dump written: (\S+)", out).group(1))
    assert (dump / "constructs.csv").exists()
    db = tmp_path / "local.sqlite"
    subprocess.run(["python3", str(REPO / "scripts" / "import_dump.py"), str(dump), "--out", str(db)],
                   check=True, capture_output=True)
    import sqlite3
    n_local = sqlite3.connect(db).execute("SELECT COUNT(*) FROM constructs").fetchone()[0]
    assert n_local == query(server, "SELECT COUNT(*) FROM constructs")[0][0] > 0
    res = subprocess.run(["python3", str(REPO / "scripts" / "simulate_stopping.py"), "--db", str(db)],
                         capture_output=True, text=True, check=True)
    assert "participant" in res.stdout


def test_answer_language_is_stored(server):
    pid = new_session(server)
    answer(server, pid, "p", "q")
    code, n = call(server, "next", pid=pid)
    triad = n["triad"]
    play_all(server, pid, triad)
    code, r = call(server, "response", "POST", {
        "participant_id": pid, "triad_id": triad["triad_id"], "pair": ["A", "B"],
        "similarity_pole": "calm", "contrast_pole": "busy", "language": "en"})
    assert code == 200
    assert query(server, "SELECT language FROM responses WHERE construct_id = ?", (r["construct_id"],))[0][0] == "en"
    # unknown languages are not stored
    code, n = call(server, "next", pid=pid)
    play_all(server, pid, n["triad"])
    code, r = call(server, "response", "POST", {
        "participant_id": pid, "triad_id": n["triad"]["triad_id"], "no_difference": True, "language": "xx"})
    assert code == 200
    assert query(server, "SELECT language FROM responses WHERE triad_id = ?", (n["triad"]["triad_id"],))[0][0] is None


def test_setup_adds_missing_column_to_old_database(server):
    """Databases created before the language column existed get it added by setup.php."""
    if server["driver"] == "sqlite":
        pytest.skip("SQLite cannot drop columns in old versions; MySQL covers the migration path")
    import pymysql
    con = pymysql.connect(**server["mysql"])
    with con.cursor() as c:
        c.execute("ALTER TABLE responses DROP COLUMN language")
    con.commit()
    con.close()
    subprocess.run(["php", str(server["root"] / "private" / "setup.php"), "--public-dir", str(server["root"] / "public")],
                   check=True, capture_output=True)
    cols = [r[0] for r in query(server, "SHOW COLUMNS FROM responses")]
    assert "language" in cols


# ---------------------------------------------------------------- access protection (design file 14)

def test_access_code_required(server):
    assert call(server, "access", c="")[1]["status"] == "missing"
    assert call(server, "access", c="nope")[1]["status"] == "invalid"
    assert call(server, "access", c="old-code")[1]["status"] == "expired"
    assert call(server, "access", c="test code")[1]["status"] == "ok"        # case, spaces and dashes ignored
    code, r = call(server, "session", "POST", {"language": "de"})
    assert code == 403 and r["detail"] == "access_code_missing"
    code, r = call(server, "session", "POST", {"language": "de", "access_code": "OLD-CODE"})
    assert code == 403 and r["detail"] == "access_code_expired"


def test_access_code_max_uses(server):
    code, r = call(server, "session", "POST", {"language": "de", "access_code": "FULL-CODE"})
    assert code == 200
    assert query(server, "SELECT access_code FROM participants WHERE participant_id = ?", (r["participant_id"],))[0][0] == "FULLCODE"
    code, r = call(server, "session", "POST", {"language": "de", "access_code": "full-code"})
    assert code == 403 and r["detail"] == "access_code_used_up"
    assert call(server, "access", c="FULL-CODE")[1]["status"] == "used_up"


def test_interviewer_needs_no_code(server):
    code, r = call(server, "session", "POST", {"language": "de"}, token=server["token"])
    assert code == 200


def test_ip_rate_limit(server):
    ini = server["root"] / "private" / "config.ini"
    set_ini(ini, "access", "max_sessions_per_ip_hour", "2")
    try:
        query_exec(server, "DELETE FROM session_starts")
        for _ in range(2):
            assert call(server, "session", "POST", {"language": "de", "access_code": "TEST-CODE"})[0] == 200
        code, r = call(server, "session", "POST", {"language": "de", "access_code": "TEST-CODE"})
        assert code == 429 and r["detail"] == "too_many_sessions"
        hashes = query(server, "SELECT ip_hash FROM session_starts")
        assert hashes and all("127.0.0.1" not in h[0] and len(h[0]) == 64 for h in hashes)
    finally:
        set_ini(ini, "access", "max_sessions_per_ip_hour", "0")


def test_clips_must_really_be_played(server):
    ini = server["root"] / "private" / "config.ini"
    set_ini(ini, "access", "min_play_fraction", "0.9")
    try:
        pid = new_session(server)
        code, n = call(server, "next", pid=pid)
        triad = n["triad"]
        play_all(server, pid, triad)                      # play and ended sent at once: too fast
        body = {"participant_id": pid, "triad_id": triad["triad_id"], "rt_ms": 1000,
                "pair": ["A", "B"], "similarity_pole": "x", "contrast_pole": "y"}
        code, r = call(server, "response", "POST", body)
        assert code == 400
        # backdate the play events: now the clips count as heard
        query_exec(server, "UPDATE play_events SET logged_at = ? WHERE triad_id = ? AND event = 'play'",
                   ("2020-01-01T00:00:00.000+00:00", triad["triad_id"]))
        code, r = call(server, "response", "POST", body)
        assert code == 200, r
    finally:
        set_ini(ini, "access", "min_play_fraction", "0")


def query_exec(server, sql, params=()):
    if server["driver"] == "sqlite":
        import sqlite3
        con = sqlite3.connect(server["root"] / "private" / "db" / "rgt.sqlite")
        con.execute(sql, params)
        con.commit()
    else:
        import pymysql
        con = pymysql.connect(**server["mysql"], autocommit=True)
        with con.cursor() as c:
            c.execute(sql.replace("?", "%s"), params)
    con.close()


def test_listening_situation_is_stored(server):
    code, r = call(server, "session", "POST", {"language": "en", "access_code": "TEST-CODE",
                                                "listening_device": "speakers", "listening_environment": "some_noise",
                                                "headphones_model": "Genelec 8020"})
    assert code == 200
    row = query(server, "SELECT listening_device, listening_environment, headphones_model FROM participants "
                        "WHERE participant_id = ?", (r["participant_id"],))[0]
    assert row == ("speakers", "some_noise", "Genelec 8020")
    code, r = call(server, "session", "POST", {"language": "en", "access_code": "TEST-CODE",
                                                "listening_device": "<script>", "listening_environment": "x"})
    row = query(server, "SELECT listening_device, listening_environment FROM participants WHERE participant_id = ?",
                (r["participant_id"],))[0]
    assert row == (None, None)


def test_group_settings(server):
    ini = server["root"] / "private" / "config.ini"
    original = ini.read_text(encoding="utf-8")
    try:
        ini.write_text(original + "\n[group.tests]\ninterface.no_difference_delay_s = 7\n"
                       "server.db_driver = \"sqlite\"\n", encoding="utf-8")        # not allowed per group: ignored
        _, base = call(server, "config")
        _, grp = call(server, "config", c="test-code")
        assert base["interface"]["no_difference_delay_s"] == 120 and base["group"] is None
        assert grp["interface"]["no_difference_delay_s"] == 7 and grp["group"] == "tests"
        # an invalid group value is ignored as a whole
        ini.write_text(original + "\n[group.tests]\ninterface.no_difference_delay_s = -5\n", encoding="utf-8")
        _, grp = call(server, "config", c="test-code")
        assert grp["interface"]["no_difference_delay_s"] == 120 and grp["group"] is None
        # the participant's group also applies to later requests (here: the IP limit)
        ini.write_text(original.replace("max_sessions_per_ip_hour = 0", "max_sessions_per_ip_hour = 1", 1)
                       + "\n[group.tests]\naccess.max_sessions_per_ip_hour = 0\n", encoding="utf-8")
        query_exec(server, "DELETE FROM session_starts")
        for _ in range(3):
            assert call(server, "session", "POST", {"language": "de", "access_code": "TEST-CODE"})[0] == 200
        assert call(server, "session", "POST", {"language": "de", "access_code": "FULL-CODE"})[0] in (403,)  # used up
    finally:
        ini.write_text(original, encoding="utf-8")


def test_thinking_prompts_are_stored(server):
    pid = new_session(server)
    code, n = call(server, "next", pid=pid)
    triad = n["triad"]
    play_all(server, pid, triad)
    code, r = call(server, "response", "POST", {"participant_id": pid, "triad_id": triad["triad_id"], "rt_ms": 1,
                                               "pair": ["A", "B"], "similarity_pole": "x", "contrast_pole": "y",
                                               "hints_shown": [1, 2, "bad", 99]})
    assert code == 200
    assert query(server, "SELECT hints_shown FROM responses WHERE triad_id = ?", (triad["triad_id"],))[0][0] == "[1,2]"


def test_demographics_are_stored(server):
    code, r = call(server, "session", "POST", {"language": "de", "access_code": "TEST-CODE", "age_group": "25-34",
                                                "gender": "na", "first_language": "other", "first_language_other": "Italiano",
                                                "sound_expertise": "professional", "residence": "x"})
    assert code == 200
    row = query(server, "SELECT age_group, gender, first_language, first_language_other, sound_expertise, residence "
                        "FROM participants WHERE participant_id = ?", (r["participant_id"],))[0]
    assert row == ("25-34", "na", "other", "Italiano", "professional", None)


def test_practice_clips_do_not_take_clip_ids(server):
    """Practice clips get ids from 1001 on, so clips added later (e.g. 8 -> 11) keep ids 9, 10, ..."""
    rows = query(server, "SELECT element_id, is_practice FROM elements ORDER BY element_id")
    clips = [int(r[0]) for r in rows if int(r[1]) == 0]
    practice = [int(r[0]) for r in rows if int(r[1]) == 1]
    assert clips == list(range(1, len(clips) + 1))
    assert practice and min(practice) >= 1001
