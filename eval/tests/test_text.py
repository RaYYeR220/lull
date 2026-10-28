from suite_a.text import (
    contains_phrase,
    content_words,
    fmt_value,
    norm,
    numbers_in,
    overlap,
    states_number,
)


def test_norm_ignores_case_punctuation_and_spacing():
    assert norm("  The  Krebs-Cycle, Explained! ") == "the krebs cycle explained"
    assert norm("1,200 units at 4.5%") == "1200 units at 4.5"


def test_contains_phrase_needs_whole_tokens():
    assert contains_phrase('A slide reads: "Rate of Reaction".', "rate of reaction")
    assert not contains_phrase("A slide reads: Rates of Reactions", "rate of reaction")
    assert not contains_phrase("anything", "")


def test_numbers_in_digits_and_words():
    assert numbers_in("It rose from 1,200 to 4.5 and then 45%") == {1200.0, 4.5, 45.0}
    assert numbers_in("forty-five people and two hundred cars") >= {45.0, 200.0}
    assert numbers_in("three point five metres") >= {3.5}
    assert 2.0 not in numbers_in("CO2 and x3 are not numbers", words=False)
    assert numbers_in("twelve thousand", words=False) == set()
    assert 12000.0 in numbers_in("twelve thousand")


def test_states_number_tolerates_formatting():
    assert states_number("The bar for 2020 shows 12,000.", 12000)
    assert states_number("about thirty six", 36)
    assert not states_number("about 360", 36)


def test_content_words_drop_stop_words_and_plurals():
    assert content_words("The cells need constant energies") == {
        "cell",
        "need",
        "constant",
        "energy",
    }


def test_overlap_is_share_of_the_part():
    assert overlap("cells need energy", "every cell needs a lot of energy") == 1.0
    assert overlap("cells need energy", "nothing related") == 0.0
    assert overlap("the of", "anything") == 0.0


def test_fmt_value_matches_the_chart_label():
    assert fmt_value(36.0) == "36"
    assert fmt_value(4.5) == "4.5"
