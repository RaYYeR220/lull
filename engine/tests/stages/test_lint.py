import pytest

from lull.stages.lint import Finding, autofix, lint


def rules(text, **kw):
    return {f.rule for f in lint(text, **kw)}


def sev(text, rule, **kw):
    return {f.severity for f in lint(text, **kw) if f.rule == rule}


def test_finding_is_model_with_span():
    f = lint("We walk in.")[0]
    assert isinstance(f, Finding)
    assert f.span == (0, 2)


@pytest.mark.parametrize(
    "text",
    [
        "We enter the room.",
        "Our hero waits.",
        "I watch him.",
        "You can see a door.",
        "He's near us.",
    ],
)
def test_r7_person_positive(text):
    assert "R7-person" in rules(text)


def test_r7_person_negative_and_error():
    assert "R7-person" not in rules("Maria enters the room.")
    assert "R7-person" not in rules("The user interface glows.")
    assert sev("We enter.", "R7-person") == {"error"}


def test_r7_tense():
    assert "R7-tense" in rules("Maria was standing. She opened the door.")
    assert "R7-tense" not in rules("Maria opens the door.")
    assert sev("Maria opened the door.", "R7-tense") == {"warning"}


def test_r12_hedge():
    for t in ["She appears to smile.", "It seems to rain.", "It looks like a cave."]:
        assert "R12-hedge" in rules(t)
    assert "R12-hedge" not in rules("Rain falls on a cave.")
    assert sev("It looks like a cave.", "R12-hedge") == {"error"}


def test_r12_emotion():
    assert "R12-emotion" in rules("Tom looks sad.")
    assert "R12-emotion" not in rules("Tom's lip trembles.")
    assert sev("Tom looks sad.", "R12-emotion") == {"warning"}


def test_r15_name():
    t = "Maria opens the door."
    assert "R15-name" in rules(t, named={"Maria"}, introduced=set())
    assert "R15-name" not in rules(t, named={"Maria"}, introduced={"Maria"})
    assert "R15-name" not in rules("A woman opens the door.", named={"Maria"})
    assert sev(t, "R15-name", named={"Maria"}) == {"error"}


def test_r18_label():
    for t in ["A flashback begins.", "The camera pans left.", "A close-up of a hand."]:
        assert "R18-label" in rules(t)
    assert "R18-label" not in rules("A hand grips a rail.")
    assert sev("A flashback begins.", "R18-label") == {"warning"}


def test_r19_verbatim():
    t = 'A sign reads: "Exit".'
    assert "R19-verbatim" not in rules(t, verbatim=["Exit"])
    assert "R19-verbatim" in rules(t, verbatim=["Entrance"])
    assert sev(t, "R19-verbatim", verbatim=["Entrance"]) == {"error"}


def test_r14_gender():
    assert "R14-gender" in rules("He opens the door.")
    assert "R14-gender" in rules("A woman waits.")
    assert "R14-gender" not in rules("A person waits.")
    f = [f for f in lint("He waits.") if f.rule == "R14-gender"][0]
    assert f.severity == "warning"
    assert f.message == "gendered word; confirm the programme establishes it"


def test_r2_ahead_format():
    assert "R2-ahead-format" in rules("Later, in a moment, a door opens.")
    assert "R2-ahead-format" not in rules("In a moment, a door opens.")
    assert "R2-ahead-format" not in rules("A door opens.")
    assert sev("Soon in a moment it opens.", "R2-ahead-format") == {"error"}


def test_quoted_exemption():
    t = 'A slide reads: "Our results were surprising."'
    assert not rules(t)
    assert not rules("Words appear: We are sad and he looks like a flashback.")
    assert not rules('A man says "I think she was sad".') - {"R14-gender"}
    assert "R14-gender" not in rules('A sign: "he said"')
    # text outside the lead-in sentence is still checked
    assert "R7-person" in rules("A sign reads: Hello. We enter.")


def test_autofix_strips_and_normalises():
    assert autofix("we see  a dog   run") == "A dog run."
    assert autofix("You can see that a man waits") == "A man waits."
    assert autofix("We can see the door.") == "The door."
    assert autofix("already fine.") == "Already fine."
    assert autofix('A sign reads: "We see you".') == 'A sign reads: "We see you".'
    assert autofix("  ") == ""


@pytest.mark.parametrize(
    "text",
    [
        "we see a dog",
        "  you see that  it  rains",
        "A sign.",
        'sign: "we see"',
        "we can see we see a cat",
        "Done!",
        "",
    ],
)
def test_autofix_idempotent(text):
    once = autofix(text)
    assert autofix(once) == once
