"""Time-synced transcript ("lyrics") generation."""
from app import server


def _job_with_chunks(db, chunks):
    """chunks = [(text, para_end, duration)]; all marked done."""
    jid = db.add_job("/a.pdf", 1, "/out")
    db.write_chunk_plan(jid, [(t, p, 0) for t, p, _ in chunks])
    for i, (_, _, dur) in enumerate(chunks):
        db.update_chunk(jid, i, status="done", duration=dur)
    return jid


def test_transcript_timings_and_sentence_split(db):
    jid = _job_with_chunks(db, [
        ("Hello there. General Kenobi.", False, 10.0),
        ("You are a bold one.", False, 4.0),
    ])
    lines = server._transcript(db, jid)
    assert len(lines) == 3                      # 2 + 1 sentences
    assert lines[0]["start_s"] == 0.0
    # sentences within a chunk are contiguous and ordered
    assert lines[0]["end_s"] == lines[1]["start_s"]
    # second chunk starts after first chunk's duration + the sentence gap
    assert lines[2]["start_s"] > 10.0
    assert lines[0]["text"] == "Hello there."
    assert lines[-1]["text"] == "You are a bold one."


def test_transcript_only_includes_done_chunks(db):
    jid = db.add_job("/a.pdf", 1, "/out")
    db.write_chunk_plan(jid, [("Done one.", 0, 0), ("Pending two.", 0, 0)])
    db.update_chunk(jid, 0, status="done", duration=5.0)
    # chunk 1 stays pending -> excluded from the transcript
    lines = server._transcript(db, jid)
    assert [l["text"] for l in lines] == ["Done one."]


def test_transcript_empty_for_unknown_job(db):
    assert server._transcript(db, 9999) == []
