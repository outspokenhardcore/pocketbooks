"""Recap text gathering (the network/TTS parts aren't unit-tested)."""
from app import recap


def test_gather_heard_text_stops_at_position(db):
    jid = db.add_job("/a.pdf", 1, "/out")
    db.write_chunk_plan(jid, [("Chapter one text.", 0, 0),
                              ("Second chunk.", 0, 0),
                              ("Third, not yet heard.", 0, 1)])
    for i, dur in enumerate([10.0, 10.0, 10.0]):
        db.update_chunk(jid, i, status="done", duration=dur)
    # Position inside the 2nd chunk -> first two included, third excluded.
    text, chapter = recap.gather_heard_text(db, jid, position_s=12.0)
    assert "Chapter one" in text and "Second chunk" in text
    assert "not yet heard" not in text
    assert chapter == 0


def test_gather_heard_text_includes_all_when_past_end(db):
    jid = db.add_job("/a.pdf", 1, "/out")
    db.write_chunk_plan(jid, [("One.", 0, 0), ("Two.", 0, 1)])
    db.update_chunk(jid, 0, status="done", duration=5.0)
    db.update_chunk(jid, 1, status="done", duration=5.0)
    text, chapter = recap.gather_heard_text(db, jid, position_s=9999)
    assert "One." in text and "Two." in text
    assert chapter == 1
