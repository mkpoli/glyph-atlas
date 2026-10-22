import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "atlas3"))
MIGRATIONS = ROOT / "apps" / "cloudflare" / "migrations"


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    """The script with ATLAS3_DIR in tmp_path, an old database built from the site's own migrations, and
    an empty target; every query runs on local sqlite, the remote ones through a fake wrangler."""
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    for name in ("accounts", "load_history", "publish", "build", "media_map", "pull"):
        sys.modules.pop(name, None)
    module = importlib.import_module("accounts")
    old = sqlite3.connect(tmp_path / "old.sqlite")
    old.execute("CREATE TABLE submissions(id TEXT PRIMARY KEY, actor TEXT, at TEXT)")
    for m in ("0039_accounts.sql", "0040_sign_in.sql", "0041_admin.sql", "0057_account_profiles.sql"):
        old.executescript((MIGRATIONS / m).read_text())
    old.executescript("""
        DROP TABLE actor_claims;
        CREATE TABLE favourites(user_id TEXT NOT NULL REFERENCES user(id), unit TEXT, at TEXT, PRIMARY KEY(user_id, unit));
        CREATE TABLE d1_migrations(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
        INSERT INTO favourites VALUES('u1', 'ex:a1', '2026-01-01');
        CREATE TABLE units(id TEXT PRIMARY KEY);
        INSERT INTO units VALUES('ex:a1');
        INSERT INTO user(id, name, email, emailVerified, createdAt, updatedAt, role)
          VALUES('u1', 'O''Brien', 'a@example.org', 1, '2026-01-01', '2026-01-02', 'admin'),
                ('u2', 'あ', 'b@example.org', 0, '2026-01-01', '2026-01-02', NULL);
        INSERT INTO session(id, expiresAt, token, createdAt, updatedAt, userId)
          VALUES('s1', '2026-12-01', 't1', '2026-01-01', '2026-01-01', 'u1');
        INSERT INTO account(id, accountId, providerId, userId, createdAt, updatedAt)
          VALUES('a1', '42', 'github', 'u1', '2026-01-01', '2026-01-01');
        INSERT INTO account_profiles VALUES('a1', 'github', 'someone', NULL, NULL, NULL, '2026-01-01');
        INSERT INTO passkey(id, publicKey, userId, credentialID, counter, deviceType, backedUp)
          VALUES('p1', 'pk', 'u2', 'c1', 3, 'singleDevice', 0);
        INSERT INTO rateLimit VALUES('r1', '/sign-in', 2, 1760000000000);
    """)
    old.commit()
    target = sqlite3.connect(tmp_path / "target.sqlite")
    target.execute("PRAGMA foreign_keys = ON")
    sent = []

    def fake_send(database, file, stamp, query):
        assert database == "glyph-atlas-accounts"
        sent.append(stamp)
        target.executescript("BEGIN;\n" + Path(file).read_text() + "COMMIT;\n")
        return stamp in module.loaded(query)[0]

    monkeypatch.setattr(module, "send", fake_send)
    databases = {"glyph-atlas": old, "glyph-atlas-accounts": target}
    monkeypatch.setattr(module.publish, "query", lambda database, sql: module.local(databases[database])(sql))
    return module, old, target, sent


def pull(module, fresh=False):
    module.pull(module.remote(module.OLD), fresh)


def test_pull_copies_only_the_auth_tables_with_their_exact_schema(accounts):
    module, old, _, _ = accounts
    pull(module)
    copy = sqlite3.connect(module.COPY)
    names = {r[0] for r in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Not units, submissions, actors or favourites, which move with the history.
    assert names == {"user", "session", "account", "passkey", "verification", "account_profiles", "rateLimit"}
    want = dict(old.execute("SELECT name, sql FROM sqlite_master WHERE tbl_name IN "
                            "('user','session','account','passkey','verification','account_profiles','rateLimit') "
                            "AND sql IS NOT NULL"))
    assert dict(copy.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL")) == want
    assert copy.execute("SELECT name, role FROM user ORDER BY id").fetchall() == [("O'Brien", "admin"), ("あ", None)]
    assert copy.execute("SELECT count(*) FROM session").fetchone() == (1,)
    with pytest.raises(SystemExit, match="--fresh"):
        pull(module)
    assert module.COPY.stat().st_mode & 0o777 == 0o600


def test_pull_finds_the_tables_that_lead_to_a_user(accounts):
    module, old, _, _ = accounts
    old.executescript("""
        CREATE TABLE twoFactor(id TEXT PRIMARY KEY, secret TEXT, userId TEXT NOT NULL REFERENCES user(id));
        CREATE TABLE twoFactorUse(id TEXT PRIMARY KEY, factor TEXT NOT NULL REFERENCES twoFactor(id));
        INSERT INTO twoFactor VALUES('f1', 'x', 'u1');
        INSERT INTO twoFactorUse VALUES('e1', 'f1');
    """)
    pull(module)
    copy = sqlite3.connect(module.COPY)
    names = {r[0] for r in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"twoFactor", "twoFactorUse"} <= names
    assert "favourites" not in names
    assert copy.execute("SELECT factor FROM twoFactorUse").fetchall() == [("f1",)]


def test_pull_stops_on_a_table_nothing_copies(accounts):
    module, old, _, _ = accounts
    old.execute("CREATE TABLE mystery(id TEXT PRIMARY KEY)")
    with pytest.raises(SystemExit, match="nothing copies: mystery"):
        pull(module)
    assert not module.COPY.exists()


def test_pull_keeps_no_copy_that_differs_from_the_source(accounts, monkeypatch):
    module, old, _, _ = accounts
    query = module.remote(module.OLD)

    def changing(sql):
        # A sign-in lands between the copy and its comparison.
        if "quote(" in sql and '"session"' in sql:
            old.execute("UPDATE session SET expiresAt = '2027-01-01'")
        return query(sql)
    with pytest.raises(SystemExit, match="differs from glyph-atlas"):
        module.pull(changing, False)
    assert not module.COPY.exists()


def test_check_compares_the_copy_with_the_old_database(accounts, capsys):
    module, old, target, _ = accounts
    pull(module)
    module.load(True, module.local(target))
    module.check()
    out = capsys.readouterr().out
    assert "glyph-atlas matches" in out and "glyph-atlas-accounts matches" in out
    old.execute("INSERT INTO rateLimit VALUES('r2', '/sign-up', 1, 1760000000001)")
    old.commit()
    with pytest.raises(SystemExit) as stop:
        module.check()
    assert stop.value.code == 1
    out = capsys.readouterr().out
    assert "glyph-atlas: rateLimit: 1 rows here, 2 there" in out
    assert "glyph-atlas-accounts matches" in out
    old.execute("CREATE TABLE twoFactor(id TEXT PRIMARY KEY, userId TEXT REFERENCES user(id))")
    with pytest.raises(SystemExit):
        module.check()
    assert "glyph-atlas: twoFactor: not in the copy" in capsys.readouterr().out


def test_pull_reads_in_key_ranges(accounts, monkeypatch):
    module, old, _, _ = accounts
    monkeypatch.setattr(module, "CHUNK", 1)
    reads = []
    query = module.local(old)
    module.pull(lambda sql: reads.append(sql) or query(sql), fresh=False)
    assert any("WHERE (\"id\") > ('u1')" in r for r in reads)
    assert sqlite3.connect(module.COPY).execute("SELECT count(*) FROM user").fetchone() == (2,)


def test_a_dry_run_writes_parts_that_rebuild_the_copy_and_sends_nothing(accounts):
    module, _old, target, sent = accounts
    pull(module)
    module.load(False, module.local(target))
    assert sent == []
    assert target.execute("SELECT count(*) FROM sqlite_master").fetchone() == (0,)
    (run,) = [p for p in module.LOADS.iterdir() if p.name.startswith("accounts-")]
    assert sorted(p.name for p in run.glob("*.sql")) == ["1-schema-0000.sql", "2-rows-0000.sql", "3-indexes-0000.sql"]
    assert {p.stat().st_mode & 0o777 for p in (module.LOADS, run)} == {0o700}


def test_load_sends_once_and_check_passes(accounts, capsys):
    module, _old, target, sent = accounts
    pull(module)
    module.load(True, module.local(target))
    assert len(sent) == 3
    module.check()
    assert "glyph-atlas-accounts matches" in capsys.readouterr().out
    assert target.execute("SELECT count(*) FROM imports").fetchone() == (3,)
    # A rerun finds every stamp and sends nothing.
    module.load(True, module.local(target))
    assert len(sent) == 3


def test_check_accepts_the_indexes_the_accounts_migrations_add_and_misses_none_of_the_copys(accounts, capsys):
    module, _old, target, _ = accounts
    pull(module)
    module.load(True, module.local(target))
    target.execute((ROOT / "apps" / "cloudflare" / "migrations-accounts" / "0002_people.sql").read_text().splitlines()[1])
    target.commit()
    module.check()
    assert "glyph-atlas-accounts matches" in capsys.readouterr().out
    copied = target.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL AND name != 'user_created'").fetchone()[0]
    target.execute(f'DROP INDEX "{copied}"')
    target.commit()
    with pytest.raises(SystemExit):
        module.check()
    assert f"glyph-atlas-accounts: index {copied}: schema differs" in capsys.readouterr().out


def test_check_reports_a_changed_row(accounts, capsys):
    module, _old, target, _ = accounts
    pull(module)
    module.load(True, module.local(target))
    target.execute("UPDATE session SET expiresAt = '2027-01-01' WHERE id = 's1'")
    target.commit()
    with pytest.raises(SystemExit) as stop:
        module.check()
    assert stop.value.code == 1
    assert "glyph-atlas-accounts: session: 1 rows here, 1 there" in capsys.readouterr().out


def test_a_fresh_copy_sends_only_the_difference(accounts, capsys):
    module, old, target, sent = accounts
    pull(module)
    module.load(True, module.local(target))
    assert len(sent) == 3
    old.executescript("""
        INSERT INTO verification VALUES('v1', 'a@example.org', '123456', '2026-01-01', '2026-01-01', '2026-01-01');
        UPDATE user SET name = 'Ó Briain' WHERE id = 'u1';
        DELETE FROM rateLimit WHERE id = 'r1';
    """)
    old.commit()
    pull(module, fresh=True)
    capsys.readouterr()
    module.load(True, module.local(target))
    out = capsys.readouterr().out
    # One part of rows: the new verification, the changed user and the rate limit gone; no schema again.
    assert len(sent) == 4 and ".2-rows." in sent[-1]
    assert "verification: 1 new, 0 changed, 0 gone" in out and "user: 0 new, 1 changed, 0 gone" in out
    assert "rateLimit: 0 new, 0 changed, 1 gone" in out and "session: 0 new, 0 changed, 0 gone" in out
    # An update in place keeps the rows that reference the user.
    assert target.execute("SELECT count(*) FROM session WHERE userId = 'u1'").fetchone() == (1,)
    module.check()
    assert "glyph-atlas-accounts matches" in capsys.readouterr().out
    # Nothing changed since: nothing is sent.
    module.load(True, module.local(target))
    assert len(sent) == 4


def test_load_refuses_a_target_holding_a_stamp_it_did_not_send(accounts):
    module, _old, target, sent = accounts
    pull(module)
    module.load(True, module.local(target))
    target.execute("INSERT INTO imports VALUES('accounts-elsewhere.2-rows.0000', 0)")
    target.commit()
    (module.LOADED).unlink()
    with pytest.raises(SystemExit, match="lacks"):
        module.load(True, module.local(target))
    assert len(sent) == 3


def test_load_refuses_a_target_with_tables_of_its_own(accounts):
    module, _old, target, sent = accounts
    pull(module)
    target.execute('CREATE TABLE "user"(id TEXT PRIMARY KEY)')
    with pytest.raises(SystemExit, match="must be empty"):
        module.load(True, module.local(target))
    assert sent == []


def test_a_row_past_d1s_statement_limit_arrives_whole(accounts):
    module, old, target, _ = accounts
    image = "data:image/png;base64," + "A" * 150_000
    old.execute("UPDATE user SET image = ? WHERE id = 'u2'", (image,))
    old.commit()
    pull(module)
    module.load(True, module.local(target))
    assert target.execute("SELECT image FROM user WHERE id = 'u2'").fetchone() == (image,)
    (run,) = [p for p in module.LOADS.iterdir() if p.name.startswith("accounts-")]
    for part in run.glob("*.sql"):
        assert max(len(line.encode()) for line in part.read_text().split("\n")) < 100_000


def test_load_refuses_rows_whose_parent_is_missing(accounts):
    module, old, target, sent = accounts
    old.execute("INSERT INTO session(id, expiresAt, token, createdAt, updatedAt, userId) "
                "VALUES('s2', '2026-12-01', 't2', '2026-01-01', '2026-01-01', 'gone')")
    old.commit()
    pull(module)
    with pytest.raises(SystemExit, match="missing parent"):
        module.load(True, module.local(target))
    assert sent == []
