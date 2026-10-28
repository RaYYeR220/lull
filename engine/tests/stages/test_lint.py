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


def test_autofix_strips_only_at_sentence_start():
    assert autofix("we see a dog run") == "A dog run."
    assert autofix("You can see that a man waits") == "A man waits."
    assert autofix("Door opens. We see a dog.") == "Door opens. A dog."
    assert autofix("In a moment, we see Maria enter") == "In a moment, Maria enter."
    assert autofix("  you see that  it  rains") == "It rains."
    assert autofix("Door opens and we see a dog.") == "Door opens and we see a dog."
    assert autofix("We see.") == "We see."
    assert autofix("already fine.") == "Already fine."
    assert autofix("  ") == ""


def test_autofix_keeps_quoted_text_verbatim():
    t = 'A sign reads: "We see   you".'
    assert autofix(t) == t
    assert autofix('Door. A slide says "a  b". Done') == 'Door. A slide says "a  b". Done.'


def test_autofix_terminal_punctuation():
    assert autofix("Exit:") == "Exit:"
    assert autofix("Wait…") == "Wait…"
    assert autofix("Really?") == "Really?"
    assert autofix("A door") == "A door."


def test_roman_numeral_i_not_person():
    for t in ["World War I ends.", "Part I begins.", "Road I-95 glows.", "Type I appears."]:
        assert "R7-person" not in rules(t), t
    assert "R7-person" in rules("I enter.")
    assert "R7-person" in rules("I’m here.")


def test_r15_case_sensitive_and_quoted_intro():
    assert "R15-name" not in rules("A red rose blooms.", named={"Rose"})
    assert "R15-name" not in rules("It will rain.", named={"Will"})
    assert "R15-name" in rules("Rose blooms.", named={"Rose"})
    assert "R15-name" not in rules('A card reads: "Rose". Rose smiles.', named={"Rose"})


def test_r2_per_sentence_and_quotes():
    assert "R2-ahead-format" not in rules("A door shuts.  In a moment, Maria enters.")
    assert "R2-ahead-format" in rules("In a moment, a door shuts. Then in a moment it opens.")
    assert "R2-ahead-format" not in rules('A sign reads: "in a moment".')


def test_unmatched_quote_does_not_exempt():
    assert "R7-person" in rules('He says " and we enter.')


def test_curly_apostrophe_tokenised():
    assert "R14-gender" in rules("He’s near.")


def test_r18_close_up_noun_only():
    assert "R18-label" in rules("A close-up of a hand.")
    assert "R18-label" in rules("The close up of a hand.")
    assert "R18-label" not in rules("Maria will close up the shop.")


def test_named_risk_negatives():
    assert not rules("A title appears.") - {"R7-tense"}
    assert not rules("Rain falls there.")
    assert "R7-person" not in rules("A sign reads: Hello we")


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


def test_a_quote_ending_a_sentence_keeps_the_next_look_ahead_at_its_start():
    """Masking the quote dropped its period, so the look-ahead after it looked
    like the middle of a sentence."""
    assert "R2-ahead-format" not in rules('He holds a card: "Hi." In a moment, a door opens.')
    assert "R2-ahead-format" not in rules('A sign reads "Exit!" In a moment, it flickers.')
    assert "R2-ahead-format" not in rules("A caption reads: Day one. In a moment, rain falls.")
    assert "R2-ahead-format" in rules('A card says "Hi" and in a moment a door opens.')


def test_on_screen_text_read_out_is_not_an_unintroduced_name():
    """A line that reads the screen names nobody: an ALL-CAPS run ("STEP 2: BAKE") or the
    text watch read off the screen. The same name used as a name still needs introducing."""
    assert "R15-name" not in rules("A card reads STEP 2: BAKE.", named={"BAKE"})
    banner = ["Welcome, Proog"]
    t = "A banner reads Welcome, Proog. A man waves."
    assert "R15-name" not in rules(t, named={"Proog"}, verbatim=banner)
    t = "A banner reads Welcome, Proog. Proog waves."
    found = [f for f in lint(t, named={"Proog"}, verbatim=banner) if f.rule == "R15-name"]
    assert [f.span for f in found] == [(31, 36)]  # the second Proog, a name
    assert "R15-name" in rules("HAL watches the crew.", named={"HAL"})  # a lone capital name


def test_words_read_off_the_screen_are_not_the_describers_own():
    """On-screen text read out in capitals, or the text watch read, is not first person or
    past tense: "I" in "AND I JUST WANT TO BE AWESOME IN SPACE" is the screen's."""
    t = "Pink text appears over the buildings: ...AND I JUST WANT TO BE AWESOME IN SPACE."
    assert "R7-person" not in rules(t)
    assert "R7-person" not in rules("A note reads I was here.", verbatim=["I was here"])
    assert "R7-tense" not in rules("A note reads I was here.", verbatim=["I was here"])
    assert "R7-person" in rules("I see a door.")  # the describer's own "I" still counts


def test_a_run_of_capitals_stops_at_a_sentence_end():
    """"OK. I" is two sentences: the screen's "OK" does not make the describer's "I" the
    screen's too."""
    assert "R7-person" in rules("The screen shows OK. I wave.")
    assert "R15-name" in rules("A card reads STOP. BAKE waves.", named={"BAKE"})
    assert "R7-person" not in rules("A banner reads: AND I JUST WANT TO WIN.")
