"""Name matching for the warehouse finder; identifiers are handled separately."""
import re
import unicodedata
from functools import lru_cache


def name_tokens(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'(?<=\d)\s*[x×]\s*(?=\d)', ' ', text)
    return list(dict.fromkeys(re.findall(r'\d+(?:\.\d+)?|[^\W\d_]+', text)))


def name_scorer(query, any_words=False):
    words = name_tokens(query)

    @lru_cache(maxsize=4096)
    def score(title):
        title_words = name_tokens(title)
        if not words:
            return 0
        matches = []
        for word in words:
            # Sizes must match whole numbers: 8 must not match 18 or 8.5.
            matches.append(any(
                word == candidate or (not word[0].isdigit() and len(word) >= 3
                                      and word in candidate)
                for candidate in title_words
            ))
        if not (any(matches) if any_words else all(matches)):
            return 0
        exact = sum(word in title_words for word in words)
        return sum(matches) * 100 + exact * 10 + (20 if words == title_words else 0)

    return score


def is_barcode_query(query):
    """Only a whole identifier can search barcodes, never a short size token."""
    return bool(re.fullmatch(r'\d{7,14}(?:-[\w-]+)?', query))
