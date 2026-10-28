import copy

import pytest

from suite_a.specs import (
    SUBJECTS,
    Subject,
    check_committed,
    derive_facts,
    load_spec,
    plan_pauses,
    prompt_for_narration,
    slides_from_answer,
    slides_schema,
    spoken_bullet,
    validate_narration,
    validate_slides,
)

SUBJECT = Subject("t01", "testing", 7, 2, {2: "line"})


def slides():
    return [
        {
            "title": "Cell Energy Basics",
            "bullets": ["Mitochondria make ATP", "Glucose is the main fuel"],
            "chart": None,
        },
        {
            "title": "Output Over Time",
            "bullets": ["Oxygen raises the yield", "Enzymes speed reactions"],
            "chart": {
                "type": "line",
                "title": "ATP Output",
                "x_label": "Hour",
                "y_label": "Molecules",
                "points": [
                    {"label": "H1", "value": 11.0},
                    {"label": "H2", "value": 17.5},
                    {"label": "H3", "value": 23.0},
                ],
            },
        },
    ]


def content():
    return {
        "title": "Respiration",
        "slides": slides(),
        "absent_decoys": [
            {"kind": "slide_title", "text": "A slide titled Photosynthesis Steps appears."},
            {"kind": "chart_value", "text": "The chart shows Lactate at 8.3."},
            {"kind": "image", "text": "A photo of a microscope is shown."},
            {"kind": "bullet", "text": "A bullet says ribosomes build proteins."},
        ],
    }


def narration():
    return {
        "slides": [
            {
                "narration": [
                    "Inside every cell the mitochondria make ATP for the work ahead.",
                    "We will see why that matters for living tissue.",
                ],
                "pauses": [2.0, 3.0],
            },
            {
                "narration": [
                    "Remember that enzymes speed reactions throughout the whole process.",
                    "Keep that idea in mind for the next lecture.",
                ],
                "pauses": [1.5, 4.0],
            },
        ]
    }


def merged():
    return [{**s, **n} for s, n in zip(content()["slides"], narration()["slides"], strict=True)]


def test_valid_example_passes_both_validators():
    assert validate_slides(content(), SUBJECT) == []
    assert validate_narration(content(), narration()) == []


def test_derive_facts_by_rule():
    facts = derive_facts(merged())
    visual = {(f["slide"], f["kind"], f["anchor"]) for f in facts["visual_facts"]}
    spoken = {(f["slide"], f["kind"], f["anchor"]) for f in facts["spoken_facts"]}
    assert (1, "bullet", "Mitochondria make ATP") in spoken
    assert (2, "bullet", "Enzymes speed reactions") in spoken
    assert (2, "bullet", "Oxygen raises the yield") in visual
    assert (1, "title", "Cell Energy Basics") in visual
    assert (1, "bullet", "Glucose is the main fuel") in visual
    assert (2, "label", "ATP Output") in visual
    assert (2, "chart_value", "H3") in visual  # the highest value
    trend = [f for f in facts["visual_facts"] if f["kind"] == "chart_trend"]
    assert len(trend) == 1 and "rise at every step" in trend[0]["text"]


def test_a_bar_chart_has_no_trend_and_a_said_direction_suppresses_it():
    s = merged()
    s[1]["chart"]["type"] = "bar"
    assert not [f for f in derive_facts(s)["visual_facts"] if f["kind"] == "chart_trend"]
    s = merged()
    s[1]["narration"][1] = "Production climbs as the hours pass in this experiment."
    assert not [f for f in derive_facts(s)["visual_facts"] if f["kind"] == "chart_trend"]


def test_narration_that_says_a_chart_value_is_rejected():
    n = narration()
    n["slides"][1]["narration"][1] = "By the end it reaches twenty-three molecules per cell."
    errs = validate_narration(content(), n)
    assert any("chart value 23" in e for e in errs)


def test_narration_that_skips_the_spoken_bullet_is_rejected():
    n = narration()
    n["slides"][0]["narration"][0] = "Cells are busy places with many moving parts inside."
    errs = validate_narration(content(), n)
    assert any('must say "Mitochondria make ATP"' in e for e in errs)


def test_decoys_must_be_absent_and_of_each_kind():
    c = content()
    c["absent_decoys"][1]["text"] = "The chart shows Lactate at 17.5."  # a value on screen
    assert any("17.5" in e for e in validate_slides(c, SUBJECT))
    c = content()
    c["absent_decoys"][2]["kind"] = "bullet"
    assert any("one absent decoy of each kind" in e for e in validate_slides(c, SUBJECT))


def test_chart_placement_is_enforced():
    c = content()
    c["slides"][0]["chart"] = copy.deepcopy(c["slides"][1]["chart"])
    assert any("must have no chart" in e for e in validate_slides(c, SUBJECT))


def test_spoken_bullet_skips_numbers_and_direction_words():
    s = {"bullets": ["Yields 36 ATP", "Output rises fast", "Oxygen is the acceptor"]}
    assert spoken_bullet(1, s) == "Oxygen is the acceptor"
    assert spoken_bullet(1, {"bullets": ["2 ATP", "Up 3%"]}) is None


def test_pauses_are_seeded_and_in_range():
    a = plan_pauses(SUBJECT, [2, 3])
    assert a == plan_pauses(SUBJECT, [2, 3])
    assert [len(x) for x in a] == [2, 3]
    assert all(1.5 <= p <= 4.0 and round(p, 1) == p for row in a for p in row)


def test_schema_fixes_the_structure():
    schema = slides_schema(SUBJECT)
    props = schema["properties"]["slides"]["properties"]
    assert set(props) == {"slide_1", "slide_2"}
    assert "chart" not in props["slide_1"]["properties"]
    assert props["slide_2"]["properties"]["chart"]["properties"]["type"]["enum"] == ["line"]
    answer = {
        "title": "T",
        "slides": {"slide_1": {"title": "A", "bullets": ["x", "y"]}, "slide_2": slides()[1]},
        "absent_decoys": [],
    }
    out = slides_from_answer(answer, SUBJECT)
    assert out["slides"][0]["chart"] is None and out["slides"][1]["chart"]["type"] == "line"


def test_narration_prompt_names_the_bullet_and_forbidden_words():
    text = prompt_for_narration(content())
    assert '"Mitochondria make ATP"' in text
    assert "glucose" in text.split("Slide 1:")[1].split("Slide 2:")[0]
    assert "11, 17.5, 23" in text


@pytest.mark.parametrize("subject", SUBJECTS, ids=lambda s: s.id)
def test_committed_specs_validate_and_rederive(subject):
    assert check_committed(load_spec(subject.id)) == []
