"""Job selection + preemption: the just-opened book jumps the queue."""


def _sup(db):
    from app.supervisor import Supervisor
    s = Supervisor(db)          # thread NOT started; we drive the logic directly
    s._queue_active.set()
    return s


def test_pick_job_is_fifo_without_preference(db):
    j1 = db.add_job("/a.pdf", 1, "/out")
    j2 = db.add_job("/b.pdf", 1, "/out")
    s = _sup(db)
    assert s._pick_job()["id"] == j1  # lowest id first


def test_preferred_job_is_picked_first(db):
    j1 = db.add_job("/a.pdf", 1, "/out")
    j2 = db.add_job("/b.pdf", 1, "/out")
    s = _sup(db)
    s.request_job(j2)
    assert s._pick_job()["id"] == j2  # preferred jumps ahead of the lower id


def test_request_sets_preempt_only_when_another_job_runs(db):
    j1 = db.add_job("/a.pdf", 1, "/out")
    j2 = db.add_job("/b.pdf", 1, "/out")
    s = _sup(db)

    # No job processing yet -> no preemption, just a preference.
    s.request_job(j2)
    assert not s._preempt.is_set()

    # A different job is processing -> opening j1 preempts it.
    s._current_job_id = j2
    s.request_job(j1)
    assert s._preempt.is_set()
    assert s._preferred_job_id == j1


def test_reopening_the_running_book_does_not_preempt(db):
    j1 = db.add_job("/a.pdf", 1, "/out")
    s = _sup(db)
    s._current_job_id = j1
    s.request_job(j1)  # same book already playing
    assert not s._preempt.is_set()


def test_preferred_falls_back_to_fifo_when_not_queued(db):
    j1 = db.add_job("/a.pdf", 1, "/out")
    j2 = db.add_job("/b.pdf", 1, "/out")
    db.update_job(j2, status="done")   # preferred finished
    s = _sup(db)
    s._preferred_job_id = j2
    assert s._pick_job()["id"] == j1   # resumes the other queued book
