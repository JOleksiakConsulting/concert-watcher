from rapidfuzz import fuzz


def venues_compatible(
    existing_venue: str | None,
    existing_normalized: str | None,
    new_venue: str | None,
    new_normalized: str | None,
) -> bool:
    if not existing_venue or not new_venue:
        return True
    if existing_normalized and new_normalized and existing_normalized == new_normalized:
        return True
    if existing_normalized and new_normalized:
        if existing_normalized in new_normalized or new_normalized in existing_normalized:
            return True
        ratio = max(
            fuzz.ratio(existing_normalized, new_normalized),
            fuzz.token_set_ratio(existing_normalized, new_normalized),
        )
        return ratio >= 80
    return False
