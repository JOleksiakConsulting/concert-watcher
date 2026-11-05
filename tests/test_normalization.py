from app.matching.normalization import strict_normalize


def test_strict_normalize_casefold():
    assert strict_normalize("Chelsea Wolfe") == strict_normalize("CHELSEA WOLFE")


def test_strict_normalize_preserves_special_chars():
    assert strict_normalize("!!!") == "!!!"
    assert strict_normalize("P!nk") == "p!nk"
    assert strict_normalize("MØ") == "mø"
