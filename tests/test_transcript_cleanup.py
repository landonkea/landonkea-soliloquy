from soliloquy.transcript_cleanup import clean_transcript


def test_empty_string_stays_empty():
    assert clean_transcript("") == ""


def test_collapses_whitespace():
    assert clean_transcript("hello   there\n\nfriend") == "Hello there friend."


def test_capitalizes_the_start_of_each_sentence():
    result = clean_transcript("today was good. tomorrow will be better")
    assert result == "Today was good. Tomorrow will be better."


def test_adds_terminal_punctuation_when_missing():
    assert clean_transcript("just a thought with no ending") == "Just a thought with no ending."


def test_preserves_existing_terminal_punctuation():
    assert clean_transcript("is this working") == "Is this working."
    assert clean_transcript("this is exciting!") == "This is exciting!"
    assert clean_transcript("wait what is this?") == "Wait what is this?"


def test_breaks_into_paragraphs_every_few_sentences():
    text = " ".join(f"Sentence {i}." for i in range(1, 9))  # 8 sentences
    result = clean_transcript(text)

    paragraphs = result.split("\n\n")
    assert len(paragraphs) == 2
    assert paragraphs[0] == "Sentence 1. Sentence 2. Sentence 3. Sentence 4."
    assert paragraphs[1] == "Sentence 5. Sentence 6. Sentence 7. Sentence 8."


def test_short_transcript_stays_a_single_paragraph():
    result = clean_transcript("one sentence only")
    assert "\n\n" not in result
