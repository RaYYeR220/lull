import random

import pytest

from lull.stages import schedule as sched
from lull.stages.schedule import (
    AHEAD_PREFIX,
    WEIGHTS,
    Placement,
    compose_cues,
    placement_report,
    schedule,
    schedule_greedy,
)
from lull.timeline import find_gaps, split_gaps
from lull.types import Gap, Variant, VisualEvent


def _ev(id_, t0, t1, *, sal=3, kind="action", text="Something happens."):
    return VisualEvent(
        id=id_, t_start=t0, t_end=t1, kind=kind, text=text,
        verbatim_text=None, salience=sal, said_in_dialogue=False,
    )  # fmt: skip


def _gap(id_, us, ue, cap):
    return Gap(id=id_, start=us - 0.2, end=ue + 0.2, usable_start=us, usable_end=ue,
               capacity_words=cap)  # fmt: skip


def _var(eid, length, words, text=None):
    text = text if text is not None else " ".join(["word"] * words) + "."
    return Variant(event_id=eid, length=length, text=text, words=words)


def _pl(eid, length, gid, ahead=False):
    return Placement(event_id=eid, variant=length, gap_id=gid, ahead=ahead)


def _objective(placements, priority):
    """The objective: sum of priority x weight, minus 1 per ahead placement."""
    return sum(priority[p.event_id] * WEIGHTS[p.variant] - p.ahead for p in placements)


def _check_invariants(placements, events, variants, gaps, *, lead=6.0, ahead_words=3, wps=2.36):
    ev = {e.id: e for e in events}
    gp = {g.id: g for g in gaps}
    words = {(v.event_id, v.length): v.words for v in variants}
    used = dict.fromkeys(gp, 0)
    seen: set[str] = set()
    for p in placements:
        assert p.event_id not in seen, "an event placed twice"
        seen.add(p.event_id)
        e, g = ev[p.event_id], gp[p.gap_id]
        assert g.usable_start < e.t_end, "described after the visual ended"
        assert g.usable_end > e.t_start - lead, "described more than `lead` seconds early"
        assert p.ahead == (g.usable_end <= e.t_start)
        used[g.id] += words[(p.event_id, p.variant)] + ahead_words * p.ahead
    for gid, n in used.items():
        assert n <= gp[gid].capacity_words, f"{gid} over capacity: {n} > {gp[gid].capacity_words}"
    # R2 per sentence: a gap voices its sentences in visual start order, so each one starts
    # after the words before it; it must start before its own visual has ended
    for gid in gp:
        here = sorted((p for p in placements if p.gap_id == gid),
                      key=lambda p: (ev[p.event_id].t_start, p.event_id))  # fmt: skip
        before = 0
        for p in here:
            start = gp[gid].usable_start + before / wps
            assert start < ev[p.event_id].t_end, f"{p.event_id} voiced at {start:.2f} s, too late"
            before += words[(p.event_id, p.variant)] + ahead_words * p.ahead


def _random_instance(rng: random.Random):
    """Gaps sorted and disjoint like listen's, events anywhere, some events without variants."""
    duration = rng.uniform(40.0, 180.0)
    gaps, t = [], rng.uniform(0.0, 5.0)
    for i in range(rng.randint(0, 8)):
        us = t + rng.uniform(0.5, 15.0)
        ue = us + rng.uniform(1.2, 9.0)
        gaps.append(_gap(f"g{i + 1:03d}", round(us, 2), round(ue, 2), int((ue - us) * 3.0)))
        t = ue + 0.4
    events, variants, priority = [], [], {}
    words = {"short": (2, 7), "medium": (7, 13), "long": (12, 22)}
    for i in range(rng.randint(0, 14)):
        eid = f"e{i + 1:03d}"
        t0 = rng.uniform(0.0, max(duration, t))
        events.append(_ev(eid, round(t0, 2), round(t0 + rng.uniform(0.3, 12.0), 2),
                          sal=rng.randint(1, 5)))  # fmt: skip
        lengths = [ln for ln in ("short", "medium", "long") if rng.random() < 0.75]
        if not lengths:
            continue
        priority[eid] = rng.randint(1, 5)
        variants += [_var(eid, ln, rng.randint(*words[ln])) for ln in lengths]
    return events, variants, priority, gaps


INSTANCES = [_random_instance(random.Random(1000 + k)) for k in range(30)]


# -- worked cases ----------------------------------------------------------------


def test_compose_joins_one_gap_in_time_order_and_prefixes_ahead():
    events = [_ev("e001", 12.0, 15.0), _ev("e002", 22.0, 25.0)]
    variants = [
        _var("e001", "short", 3, "A door opens."),
        _var("e002", "short", 3, "A car arrives"),
    ]
    gaps = [_gap("g003", 10.0, 20.0, 30)]
    placements = [_pl("e002", "short", "g003", ahead=True), _pl("e001", "short", "g003")]
    [cue] = compose_cues(placements, variants, events, gaps)
    assert cue.id == "c003"
    assert cue.gap_id == "g003"
    assert cue.text == "A door opens. In a moment, a car arrives."
    assert cue.event_ids == ["e001", "e002"]
    assert cue.start == 10.0 and cue.end is None
    assert cue.status == "verified" and cue.ahead is True
    assert cue.claims == [] and cue.audio_path is None and cue.audio_dur is None


def test_capacity_never_exceeded():
    events = [_ev(f"e{i:03d}", 1.0, 20.0) for i in range(1, 6)]
    variants = [_var(e.id, n, w) for e in events for n, w in
                (("short", 4), ("medium", 8), ("long", 14))]  # fmt: skip
    priority = {e.id: 5 for e in events}
    gaps = [_gap("g001", 2.0, 6.0, 12), _gap("g002", 8.0, 12.0, 9)]
    for run in (schedule, schedule_greedy):
        placements, _ = run(events, variants, priority, gaps)
        _check_invariants(placements, events, variants, gaps)
        assert placements, run.__name__


def test_event_ending_before_a_gap_is_never_placed_there():
    events = [_ev("e001", 2.0, 5.0, sal=4)]
    variants = [_var("e001", "short", 3)]
    gaps = [_gap("g001", 5.0, 15.0, 30), _gap("g002", 6.0, 15.0, 30)]
    for run in (schedule, schedule_greedy):
        assert run(events, variants, {"e001": 5}, gaps) == ([], ["e001"])


def test_long_too_big_for_the_gap_so_both_events_get_their_short():
    events = [_ev("e001", 1.0, 9.0), _ev("e002", 1.0, 9.0)]
    variants = [_var("e001", "short", 5), _var("e001", "long", 12), _var("e002", "short", 4)]
    priority = {"e001": 5, "e002": 2}
    gaps = [_gap("g001", 2.0, 6.0, 10)]
    expected = [_pl("e001", "short", "g001"), _pl("e002", "short", "g001")]
    for run in (schedule, schedule_greedy):
        placements, _ = run(events, variants, priority, gaps)
        assert sorted(placements, key=lambda p: p.event_id) == expected


def test_a_brief_visual_is_not_voiced_after_a_long_sentence_ends_it():
    """A (9-30 s, 20 words) sorts first in gap 10-20, so B (10.5-11 s) would
    start about 8.5 s in, long after it ended; only one of them may use the gap."""
    events = [_ev("e001", 9.0, 30.0), _ev("e002", 10.5, 11.0, sal=4)]
    variants = [_var("e001", "long", 20), _var("e002", "short", 4)]
    gaps = [_gap("g001", 10.0, 20.0, 30)]
    for run in (schedule, schedule_greedy):
        placements, extended = run(events, variants, {"e001": 5, "e002": 5}, gaps)
        assert placements == [_pl("e001", "long", "g001")], run.__name__
        assert extended == ["e002"]
        _check_invariants(placements, events, variants, gaps)


def test_a_brief_visual_that_starts_first_is_voiced_first():
    events = [_ev("e001", 10.5, 11.0), _ev("e002", 12.0, 30.0)]
    variants = [_var("e001", "short", 4, "A light blinks."), _var("e002", "long", 20)]
    gaps = [_gap("g001", 10.0, 20.0, 30)]
    for run in (schedule, schedule_greedy):
        placements, _ = run(events, variants, {"e001": 5, "e002": 5}, gaps)
        assert placements == [_pl("e001", "short", "g001"), _pl("e002", "long", "g001")]
        [cue] = compose_cues(placements, variants, events, gaps)
        assert cue.text.startswith("A light blinks. word")


def test_a_visual_ending_within_a_word_of_the_gap_start_is_not_placed():
    # floor((10.3 - 10.0) x 2.8) - 1 < 0: not even the first word would land in time
    events = [_ev("e001", 9.0, 10.3)]
    variants = [_var("e001", "short", 2)]
    gaps = [_gap("g001", 10.0, 20.0, 30)]
    for run in (schedule, schedule_greedy):
        # priority 5 and unplaced: offered for the extended track instead
        assert run(events, variants, {"e001": 5}, gaps) == ([], ["e001"])
    placements, _ = schedule(events, variants, {"e001": 5}, gaps, wps=6.0)
    assert placements == [_pl("e001", "short", "g001")]


def test_split_windows_keep_a_no_speech_video_near_its_visuals():
    """With the 30 s no-speech gap split into windows, a visual at 25 s is voiced near 25 s,
    not at 0.2 s together with the one at 3 s."""
    windows = split_gaps(find_gaps([], 30, wps=2.36), wps=2.36)
    events = [_ev("e001", 3.0, 8.0), _ev("e002", 25.0, 28.0)]
    variants = [_var("e001", "short", 6), _var("e002", "short", 6)]
    placements, _ = schedule(events, variants, {"e001": 3, "e002": 3}, windows)
    cues = compose_cues(placements, variants, events, windows)
    assert [c.event_ids for c in cues] == [["e001"], ["e002"]]
    assert cues[0].start < 3.0 + 0.5 and 24.0 < cues[1].start < 25.0 + 0.5


@pytest.mark.parametrize("cap, placed", [(8, False), (9, True)])
def test_ahead_placement_costs_three_more_words(cap, placed):
    events = [_ev("e001", 12.0, 14.0)]
    variants = [_var("e001", "short", 6)]
    gaps = [_gap("g001", 7.0, 10.0, cap)]  # ends 2 s before the event starts
    for run in (schedule, schedule_greedy):
        placements, _ = run(events, variants, {"e001": 3}, gaps)
        assert placements == ([_pl("e001", "short", "g001", ahead=True)] if placed else [])


def test_ahead_window_is_bounded_by_lead():
    events = [_ev("e001", 12.0, 14.0)]
    variants = [_var("e001", "short", 3)]
    gaps = [_gap("g001", 2.0, 6.0, 30)]  # ends 6 s before the event: not > t_start - lead
    assert schedule(events, variants, {"e001": 3}, gaps) == ([], [])
    placements, _ = schedule(events, variants, {"e001": 3}, gaps, lead=6.5)
    assert placements == [_pl("e001", "short", "g001", ahead=True)]


def test_no_speech_video_does_not_over_describe():
    """One gap over the whole 30 s video, one low-priority event."""
    events = [_ev("e001", 3.0, 8.0, sal=2)]
    variants = [_var("e001", "short", 5), _var("e001", "medium", 10), _var("e001", "long", 18)]
    gaps = [_gap("g001", 0.2, 29.8, 88)]
    for run in (schedule, schedule_greedy):
        # priority 1: the solver leaves it, the filling pass gives the empty pause its line
        assert run(events, variants, {"e001": 1}, gaps) == ([_pl("e001", "long", "g001")], [])
        placements, extended = run(events, variants, {"e001": 2}, gaps)
        assert placements == [_pl("e001", "long", "g001")]
        assert extended == []
        cues = compose_cues(placements, variants, events, gaps)
        assert [c.event_ids for c in cues] == [["e001"]]
        assert cues[0].text.count(".") == 1


def test_continuous_speech_leaves_only_extended_candidates():
    """No gaps -> no placements, every salience >= 4 event is a candidate."""
    events = [_ev("e001", 30.0, 32.0, sal=5), _ev("e002", 4.0, 6.0, sal=4),
              _ev("e003", 10.0, 12.0, sal=3)]  # fmt: skip
    variants = [_var(e.id, "short", 4) for e in events]
    priority = {"e001": 5, "e002": 4, "e003": 3}
    for run in (schedule, schedule_greedy):
        assert run(events, variants, priority, []) == ([], ["e002", "e001"])


def test_extended_candidates_follow_the_plan_priority_not_the_first_guess():
    """Live csc_intro: the inventory rated 42 one-second shots salience 4; the plan, seeing the
    whole video, rated most of them priority 1, yet each one stopped the video. Only what the
    plan rates 4-5 may stop it; an event the plan gave nothing keeps its salience (and stays
    undescribed, which the pipeline reports)."""
    events = [_ev("e001", 1.0, 2.0, sal=4), _ev("e002", 2.0, 3.0, sal=3),
              _ev("e003", 3.0, 4.0, sal=5), _ev("e004", 4.0, 5.0, sal=4)]  # fmt: skip
    variants = [_var(e, "short", 4) for e in ("e001", "e002", "e003")]
    priority = {"e001": 1, "e002": 5, "e003": 4}  # e004: no item
    for run in (schedule, schedule_greedy):
        assert run(events, variants, priority, []) == ([], ["e002", "e003", "e004"])


# -- solver vs greedy, randomised -------------------------------------------------------


@pytest.mark.parametrize("k", range(len(INSTANCES)))
def test_random_instances_keep_invariants_and_solver_beats_greedy(k):
    events, variants, priority, gaps = INSTANCES[k]
    solved, solved_ext = schedule(events, variants, priority, gaps)
    greedy, greedy_ext = schedule_greedy(events, variants, priority, gaps)
    _check_invariants(solved, events, variants, gaps)
    _check_invariants(greedy, events, variants, gaps)
    assert _objective(solved, priority) >= _objective(greedy, priority)
    for placements, ext in ((solved, solved_ext), (greedy, greedy_ext)):
        placed = {p.event_id for p in placements}
        ordered = sorted(events, key=lambda e: (e.t_start, e.id))
        rated = [e for e in ordered if priority.get(e.id, e.salience) >= 4]
        assert ext == [e.id for e in rated if e.id not in placed]


def test_tie_break_never_costs_objective(monkeypatch):
    with_tie = [_objective(schedule(*inst)[0], inst[2]) for inst in INSTANCES]
    monkeypatch.setattr(sched, "TIE_BREAK", False)
    without = [_objective(schedule(*inst)[0], inst[2]) for inst in INSTANCES]
    assert with_tie == without


def test_random_instances_exercise_the_solver():
    """Guards the randomised test against vacuity: most instances place something."""
    placed = sum(bool(schedule(*inst)[0]) for inst in INSTANCES)
    assert placed >= 15


def test_solver_beats_greedy_when_greedy_is_myopic():
    # greedy gives the p5 event its long variant and starves two p4 events
    events = [_ev("e001", 1.0, 9.0), _ev("e002", 1.0, 9.0), _ev("e003", 1.0, 9.0)]
    variants = [_var("e001", "short", 4), _var("e001", "long", 12),
                _var("e002", "short", 4), _var("e003", "short", 4)]  # fmt: skip
    priority = {"e001": 5, "e002": 4, "e003": 4}
    gaps = [_gap("g001", 2.0, 6.0, 12)]
    greedy, _ = schedule_greedy(events, variants, priority, gaps)
    solved, _ = schedule(events, variants, priority, gaps)
    assert greedy == [_pl("e001", "long", "g001")]
    assert _objective(solved, priority) == 5 * 60 + 4 * 60 + 4 * 60
    assert _objective(solved, priority) > _objective(greedy, priority)


# -- determinism and gap choice ------------------------------------------------------


def test_same_input_twice_gives_identical_placements():
    events, variants, priority, gaps = max(INSTANCES, key=lambda i: len(i[0]) * len(i[3]))
    first = schedule(events, variants, priority, gaps)
    for _ in range(3):
        assert schedule(events, variants, priority, gaps) == first


def test_input_order_does_not_change_the_answer():
    rng = random.Random(7)
    for events, variants, priority, gaps in INSTANCES[:10]:
        first = schedule(events, variants, priority, gaps)
        ev, va, ga = list(events), list(variants), list(gaps)
        for seq in (ev, va, ga):
            rng.shuffle(seq)
        assert schedule(ev, va, priority, ga) == first
        assert schedule_greedy(ev, va, priority, ga) == schedule_greedy(
            events, variants, priority, gaps
        )


def test_solver_prefers_a_gap_during_the_visual_over_ahead():
    events = [_ev("e001", 20.0, 40.0)]
    variants = [_var("e001", "short", 3)]
    gaps = [_gap("g001", 15.0, 19.0, 20), _gap("g002", 35.0, 38.0, 20),
            _gap("g003", 22.0, 25.0, 20)]  # fmt: skip
    placements, _ = schedule(events, variants, {"e001": 3}, gaps)
    assert placements == [_pl("e001", "short", "g003")]


def test_solver_picks_the_closest_ahead_gap():
    events = [_ev("e001", 20.0, 22.0)]
    variants = [_var("e001", "short", 3)]
    gaps = [_gap("g001", 14.5, 16.0, 20), _gap("g002", 17.0, 19.0, 20)]
    placements, _ = schedule(events, variants, {"e001": 3}, gaps)
    assert placements == [_pl("e001", "short", "g002", ahead=True)]


def test_equal_value_look_aheads_go_to_the_latest_gap_not_the_earliest():
    # every 20 s: two short gaps, then a visual 1.5 s after the second; both gaps are equal-value
    # look-aheads for it, and the one nearer the visual must win for every visual
    gaps, events = [], []
    for k in range(1, 9):
        t = 20.0 * k
        gaps += [
            _gap(f"g{2 * k - 1:03d}", t, t + 1.5, 20),
            _gap(f"g{2 * k:03d}", t + 2, t + 3.5, 20),
        ]
        events.append(_ev(f"e{k:03d}", t + 5.0, t + 7.0))
    variants = [_var(e.id, "short", 3) for e in events]
    placements, _ = schedule(events, variants, dict.fromkeys((e.id for e in events), 3), gaps)
    assert [(p.event_id, p.gap_id, p.ahead) for p in placements] == [
        (f"e{k:03d}", f"g{2 * k:03d}", True) for k in range(1, 9)
    ]


# -- greedy fallback -------------------------------------------------------------------


def test_greedy_orders_by_priority_then_length_then_closest_gap():
    events = [_ev("e001", 0.0, 12.0), _ev("e002", 0.0, 12.0)]
    variants = [_var("e001", "short", 5), _var("e001", "long", 12),
                _var("e002", "short", 4), _var("e002", "long", 10)]  # fmt: skip
    gaps = [_gap("g001", 0.5, 5.0, 6), _gap("g002", 6.0, 11.0, 20)]
    placements, _ = schedule_greedy(events, variants, {"e001": 5, "e002": 3}, gaps)
    assert placements == [_pl("e002", "short", "g001"), _pl("e001", "long", "g002")]


def test_greedy_prefers_a_gap_during_the_visual_to_an_earlier_look_ahead():
    events = [_ev("e001", 20.0, 40.0)]
    variants = [_var("e001", "short", 3)]
    gaps = [_gap("g001", 15.0, 19.0, 20), _gap("g002", 22.0, 25.0, 20)]
    placements, _ = schedule_greedy(events, variants, {"e001": 3}, gaps)
    assert placements == [_pl("e001", "short", "g002")]


def test_falls_back_to_greedy_without_ortools(monkeypatch):
    monkeypatch.setattr(sched, "cp_model", None)
    for inst in INSTANCES[:5]:
        assert schedule(*inst) == schedule_greedy(*inst)


def test_falls_back_to_greedy_when_the_solver_finds_nothing(monkeypatch):
    real = sched.cp_model.CpSolver

    class NoAnswer:
        """A solver that times out before any solution, as on a huge instance."""

        def __init__(self):
            self.parameters = real().parameters

        def solve(self, model):
            return sched.cp_model.UNKNOWN

    monkeypatch.setattr(sched.cp_model, "CpSolver", NoAnswer)
    events, variants, priority, gaps = INSTANCES[0]
    assert schedule(events, variants, priority, gaps) == schedule_greedy(
        events, variants, priority, gaps
    )


def test_a_timed_out_answer_worse_than_greedy_gives_way_to_greedy(monkeypatch):
    real = sched.cp_model.CpSolver
    seen = []

    class Feasible:
        """A solver stopped by its time limit holding a poor (empty) answer."""

        def __init__(self):
            self.parameters = real().parameters
            seen.append(self.parameters)

        def solve(self, model):
            return sched.cp_model.FEASIBLE

        def boolean_value(self, var):
            return False

    monkeypatch.setattr(sched.cp_model, "CpSolver", Feasible)
    events, variants, priority, gaps = next(i for i in INSTANCES if schedule_greedy(*i)[0])
    greedy = schedule_greedy(events, variants, priority, gaps)
    assert schedule(events, variants, priority, gaps, time_limit_s=2.5) == greedy
    [params] = seen
    assert params.max_time_in_seconds == 2.5 and params.max_deterministic_time == 2.5
    assert params.num_workers == 1 and params.random_seed == 0


# -- edge inputs -----------------------------------------------------------------------


def test_empty_inputs():
    assert schedule([], [], {}, []) == ([], [])
    assert schedule_greedy([], [], {}, []) == ([], [])
    assert compose_cues([], [], [], []) == []


def test_variants_for_unknown_events_and_full_gaps_are_ignored():
    events = [_ev("e001", 1.0, 9.0)]
    variants = [_var("e001", "short", 4), _var("e999", "short", 2)]
    gaps = [_gap("g001", 2.0, 6.0, 0), _gap("g002", 7.0, 8.5, 4)]
    placements, _ = schedule(events, variants, {"e001": 3, "e999": 5}, gaps)
    assert placements == [_pl("e001", "short", "g002")]


def test_missing_priority_falls_back_to_salience():
    events = [_ev("e001", 1.0, 9.0, sal=5), _ev("e002", 1.0, 9.0, sal=1)]
    variants = [_var("e001", "short", 4), _var("e002", "short", 4)]
    gaps = [_gap("g001", 2.0, 6.0, 5)]
    placements, _ = schedule(events, variants, {}, gaps)
    assert placements == [_pl("e001", "short", "g001")]


# -- cue text --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, proper, expected",
    [
        ("A car arrives.", None, "In a moment, a car arrives."),
        ("NASA's logo fills the screen.", None, "In a moment, NASA's logo fills the screen."),
        ("Proog's hand shakes.", {"Proog"}, "In a moment, Proog's hand shakes."),
        ("Proog's hand shakes.", None, "In a moment, proog's hand shakes."),
        ('"Exit" glows above the door.', None, 'In a moment, "Exit" glows above the door.'),
        ("A sign reads Exit", None, "In a moment, a sign reads Exit."),
        ("I am here, says a sign.", None, "In a moment, I am here, says a sign."),
        ("I'm open, reads a sign.", None, "In a moment, I'm open, reads a sign."),
        ("I'll, I've and I'd fill a slide.", None, "In a moment, I'll, I've and I'd fill a slide."),
        ("I've arrived, reads a banner.", None, "In a moment, I've arrived, reads a banner."),
        ("I'd stay, reads a card.", None, "In a moment, I'd stay, reads a card."),
        ("U.S. flags wave.", None, "In a moment, U.S. flags wave."),
        ("TV screens flicker.", None, "In a moment, TV screens flicker."),
        ("It rains.", None, "In a moment, it rains."),
        ("Ice covers the lake.", None, "In a moment, ice covers the lake."),
        ("A U.S. flag waves.", None, "In a moment, a U.S. flag waves."),
        # curly or straight apostrophes match either spelling of a proper noun
        ("O’Brien waves.", {"O’Brien"}, "In a moment, O’Brien waves."),
        ("O’Brien waves.", {"O'Brien"}, "In a moment, O’Brien waves."),
        ("O'Brien waves.", {"O’Brien"}, "In a moment, O'Brien waves."),
        ("O’Brien’s dog barks.", {"O’Brien"}, "In a moment, O’Brien’s dog barks."),
    ],
)
def test_ahead_prefix_and_casing(text, proper, expected):
    events = [_ev("e001", 12.0, 15.0)]
    variants = [_var("e001", "short", 4, text)]
    gaps = [_gap("g001", 7.0, 10.0, 20)]
    placements = [_pl("e001", "short", "g001", ahead=True)]
    [cue] = compose_cues(placements, variants, events, gaps, proper_nouns=proper)
    assert cue.text == expected
    assert cue.text.startswith(AHEAD_PREFIX)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("A door opens", "A door opens."),
        ("Is anyone there?", "Is anyone there?"),
        ('A slide reads: "Water use."', 'A slide reads: "Water use."'),
        ('A sign reads "Exit"', 'A sign reads "Exit".'),
        ("  A door   opens  ", "A door opens."),
    ],
)
def test_each_sentence_ends_with_terminal_punctuation(text, expected):
    events = [_ev("e001", 1.0, 9.0)]
    variants = [_var("e001", "short", 3, text)]
    gaps = [_gap("g001", 2.0, 6.0, 20)]
    [cue] = compose_cues([_pl("e001", "short", "g001")], variants, events, gaps)
    assert cue.text == expected


def test_one_cue_per_gap_in_time_order():
    events = [_ev("e001", 1.0, 3.0), _ev("e002", 20.0, 25.0)]
    variants = [_var("e001", "short", 2, "A."), _var("e002", "short", 2, "B.")]
    gaps = [_gap("g002", 21.0, 24.0, 20), _gap("g001", 1.5, 2.8, 20), _gap("g003", 30, 40, 9)]
    placements = [_pl("e002", "short", "g002"), _pl("e001", "short", "g001")]
    cues = compose_cues(placements, variants, events, gaps)
    assert [(c.id, c.gap_id, c.start, c.ahead) for c in cues] == [
        ("c001", "g001", 1.5, False),
        ("c002", "g002", 21.0, False),
    ]


def test_extended_cues_come_from_unplaced_salient_events():
    events = [
        _ev("e001", 1.0, 3.0, sal=5),  # placed
        _ev("e002", 40.0, 44.0, sal=5),  # unplaced, long variant
        _ev("e003", 30.0, 33.0, sal=4),  # unplaced, medium + short only
        _ev("e004", 50.0, 52.0, sal=3),  # unplaced but not salient
        _ev("e005", 60.0, 62.0, sal=4),  # unplaced, no variant at all
    ]
    variants = [
        _var("e001", "short", 3, "A door opens."),
        _var("e002", "short", 2, "Rain falls."),
        _var("e002", "long", 6, "Heavy rain falls on the roof"),
        _var("e003", "short", 2, "Lights dim."),
        _var("e003", "medium", 5, "The stage lights dim slowly."),
        _var("e004", "short", 2, "Birds fly."),
    ]
    gaps = [_gap("g001", 1.5, 2.8, 20)]
    placements = [_pl("e001", "short", "g001")]
    cues = compose_cues(placements, variants, events, gaps)
    assert [(c.id, c.gap_id, c.start, c.status, c.text, c.event_ids, c.ahead) for c in cues] == [
        ("c001", "g001", 1.5, "verified", "A door opens.", ["e001"], False),
        ("x003", None, 30.0, "extended", "The stage lights dim slowly.", ["e003"], False),
        ("x002", None, 40.0, "extended", "Heavy rain falls on the roof.", ["e002"], False),
    ]
    explicit = compose_cues(placements, variants, events, gaps, extended=["e002"])
    assert [c.id for c in explicit] == ["c001", "x002"]


def test_compose_rejects_placements_it_cannot_resolve():
    events = [_ev("e001", 1.0, 9.0)]
    variants = [_var("e001", "short", 3)]
    gaps = [_gap("g001", 2.0, 6.0, 20)]
    with pytest.raises(ValueError, match="e001"):
        compose_cues([_pl("e001", "long", "g001")], variants, events, gaps)
    with pytest.raises(ValueError, match="g404"):
        compose_cues([_pl("e001", "short", "g404")], variants, events, gaps)
    with pytest.raises(ValueError, match="twice"):
        compose_cues([_pl("e001", "short", "g001")] * 2, variants, events, gaps)


# -- report ----------------------------------------------------------------------------


def test_placement_report_summarises_use_of_the_gaps():
    events = [_ev("e001", 1.0, 9.0, sal=5), _ev("e002", 12.0, 14.0, sal=2),
              _ev("e003", 30.0, 31.0, sal=4)]  # fmt: skip
    variants = [_var("e001", "long", 6), _var("e002", "short", 4), _var("e003", "short", 4)]
    gaps = [_gap("g001", 2.0, 6.0, 14), _gap("g002", 8.0, 10.0, 6), _gap("g003", 20, 22, 6)]
    placements = [_pl("e002", "short", "g001", ahead=True), _pl("e001", "long", "g001")]
    report = placement_report(placements, variants, events, gaps)
    assert report == {
        "placed": [
            {"event_id": "e001", "variant": "long", "gap_id": "g001", "ahead": False, "words": 6},
            {"event_id": "e002", "variant": "short", "gap_id": "g001", "ahead": True, "words": 7},
        ],
        "unplaced_salient": ["e003"],
        "gaps": [
            {
                "gap_id": "g001",
                "capacity_words": 14,
                "words_used": 13,
                "event_ids": ["e001", "e002"],
            },
            {"gap_id": "g002", "capacity_words": 6, "words_used": 0, "event_ids": []},
            {"gap_id": "g003", "capacity_words": 6, "words_used": 0, "event_ids": []},
        ],
        "words_used": 13,
        "capacity_words": 26,
        "utilisation": 0.5,
    }
    empty = placement_report([], [], [], [])
    assert empty["utilisation"] == 0.0 and empty["placed"] == [] and empty["gaps"] == []


# -- what may be placed, what may stop the video ------------------------------------


def test_standard_placement_needs_priority_two():
    """The solver never places a priority-1 line; in a pause under 3 s the filling pass
    does not either."""
    events = [_ev("e001", 1.0, 9.0, sal=4), _ev("e002", 1.0, 9.0, sal=4)]
    variants = [_var("e001", "short", 3), _var("e002", "short", 3)]
    gaps = [_gap("g001", 2.0, 4.8, 8)]
    for run in (schedule, schedule_greedy):
        placements, extended = run(events, variants, {"e001": 1, "e002": 2}, gaps)
        assert placements == [_pl("e002", "short", "g001")]
        assert extended == []


def test_compose_and_report_take_extended_candidates_from_the_plan_priority():
    """No salience-only leftovers: compose_cues' default extended list and the report's
    unplaced_salient follow the plan priority like extended_candidates."""
    events = [_ev("e001", 1.0, 3.0, sal=5), _ev("e002", 10.0, 12.0, sal=3),
              _ev("e003", 20.0, 22.0, sal=4)]  # fmt: skip
    variants = [_var("e001", "short", 2, "Rain falls."), _var("e002", "short", 2, "Lights dim.")]
    priority = {"e001": 1, "e002": 4}  # e003: no item, so its salience counts
    cues = compose_cues([], variants, events, [], priority=priority)
    assert [c.id for c in cues] == ["x002"]  # e003 has no variant to voice
    report = placement_report([], variants, events, [], priority=priority)
    assert report["unplaced_salient"] == ["e002", "e003"]


def test_events_the_dialogue_conveys_never_stop_the_video():
    """R21: an event said in dialogue (salience under 5) is not planned, so it is no extended
    candidate either (and never reported undescribed)."""
    said = _ev("e001", 1.0, 3.0, sal=4).model_copy(update={"said_in_dialogue": True})
    loud = _ev("e002", 5.0, 6.0, sal=5).model_copy(update={"said_in_dialogue": True})
    for run in (schedule, schedule_greedy):
        assert run([said, loud], [], {}, []) == ([], ["e002"])
    assert placement_report([], [], [said, loud], [])["unplaced_salient"] == ["e002"]


# -- fuller long pauses -----------------------------------------------------------------------


def test_a_half_empty_long_pause_takes_a_lower_priority_visual_inside_it():
    """A 6 s window holds a 4-word line in 16 words of room: a priority-1 visual inside it
    is added, up to about 80 % of the room (12 words), still one cue for the window."""
    events = [_ev("e001", 10.0, 16.0, sal=4), _ev("e002", 12.0, 15.0, sal=2)]
    variants = [_var("e001", "short", 4), _var("e002", "short", 5), _var("e002", "long", 9)]
    gaps = [_gap("g001", 10.0, 16.0, 16)]
    for run in (schedule, schedule_greedy):
        placements, _ = run(events, variants, {"e001": 4, "e002": 1}, gaps)
        assert placements == [_pl("e001", "short", "g001"), _pl("e002", "short", "g001")]
    cues = compose_cues(placements, variants, events, gaps, extended=[])
    assert [c.gap_id for c in cues] == ["g001"] and cues[0].event_ids == ["e001", "e002"]


def test_the_filling_pass_respects_short_pauses_full_ones_and_look_aheads():
    events = [_ev("e001", 10.0, 16.0, sal=4), _ev("e002", 12.0, 15.0, sal=2),
              _ev("e003", 30.0, 33.0, sal=2), _ev("e004", 20.0, 22.0, sal=2)]  # fmt: skip
    variants = [_var("e001", "short", 9), _var("e002", "short", 3), _var("e003", "short", 3),
                _var("e004", "short", 3)]  # fmt: skip
    gaps = [
        _gap("g001", 10.0, 16.0, 16),  # 9 of 16 words used: more than half, left alone
        _gap("g002", 23.0, 28.0, 14),  # e003 starts after it: a look-ahead is never added
        _gap("g003", 20.5, 23.0, 7),  # under 3 s: never filled
    ]
    placements, _ = schedule(events, variants, {"e001": 4, "e002": 1, "e003": 1, "e004": 1},
                             gaps)  # fmt: skip
    assert placements == [_pl("e001", "short", "g001")]


def test_the_filling_pass_upgrades_a_short_line_to_a_longer_variant_first():
    from lull.stages import schedule as sch

    events = [_ev("e001", 10.0, 16.0, sal=4)]
    variants = [_var("e001", "short", 4), _var("e001", "long", 11)]
    gaps = [_gap("g001", 10.0, 16.0, 16)]
    pool = sch._options(events, variants, {"e001": 4}, gaps, sch.LEAD, sch.AHEAD_WORDS, 2.8,
                        min_priority=1)  # fmt: skip
    short = next(o for o in pool if o.length == "short")
    filled = sch._fill([short], pool)
    assert [(o.event.id, o.length) for o in filled] == [("e001", "long")]  # 11 <= 12
