"""Shared brand and product-name vocabulary for listing reconciliation.

Seeded from recurring warehouse and marketplace titles; aliases are search
hints, not proof of product identity. Extend this list as new brands arrive.
Brands the BOL spells twice ("Nambe Nambe Braid") are also learned from the
rack and BOL titles on every rebuild, so a new lot needs no edit here.
"""
import hashlib
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache

BRANDS = {
    'Ralph Lauren': ['ralph lauren'], 'The Cellar': ['the cellar'],
    'Michael Aram': ['michael aram'], 'Villeroy & Boch': ['villeroy boch', 'villeroy and boch'],
    'Saro Lifestyle': ['saro lifestyle', 'saro'], 'Lenox': ['lenox'],
    'MacKenzie-Childs': ['mackenzie childs', 'mackenziechilds'], 'Elrene': ['elrene'],
    'Kate Spade': ['kate spade'], 'Certified International': ['certified international'],
    'Noritake': ['noritake'], 'Lawrence Frames': ['lawrence frames'],
    'Laura Ashley': ['laura ashley'], 'Classic Touch': ['classic touch'],
    'French Home': ['french home'], 'Martha Stewart': ['martha stewart'],
    'Tabletops Gallery': ['tabletops gallery'], 'Godinger': ['godinger'],
    'Hotel Collection': ['hotel collection'], 'Lorren Home': ['lorren home'],
    'Nearly Natural': ['nearly natural'], 'Newbridge': ['newbridge'],
    'Design Imports': ['design imports', 'dii'], 'Arch Studio': ['arch studio'],
    'Hudson Park': ['hudson park'], 'Euro Ceramica': ['euro ceramica'],
    'Portmeirion': ['portmeirion'], 'Fitz and Floyd': ['fitz and floyd', 'fitz floyd'],
    'Waterford': ['waterford', 'marquis by waterford'],
    'Cambridge Silversmiths': ['cambridge silversmiths'], 'JoyJolt': ['joyjolt'],
    'Mele & Co': ['mele co', 'mele and co'], 'Charter Club': ['charter club'],
    'Glitzhome': ['glitzhome'], 'Henckels': ['henckels'], 'Zwilling': ['zwilling'],
    'Mikasa': ['mikasa'], 'Abode Homewares': ['abode homewares'],
    'Tabletops Unlimited': ['tabletops unlimited'], 'Thirstystone': ['thirstystone'],
    'Threadmade': ['threadmade'], 'Georg Jensen': ['georg jensen'],
    'Gibson': ['gibson'], 'Hampton Forge': ['hampton forge'],
    'Yinka Ilori': ['yinka ilori', 'yinka lori', 'yinka ilora'],
    'Oake': ['oake'], 'Royal Albert': ['royal albert'],
    'Monique Lhuillier': ['monique lhuillier'], 'Qualia': ['qualia'],
    'Spode': ['spode'], 'Swarovski': ['swarovski'], 'Anchor Hocking': ['anchor hocking'],
    'Dura Living': ['dura living'], 'Elite Gourmet': ['elite gourmet'],
    'Reed & Barton': ['reed barton', 'reed and barton'],
    'Royal Doulton': ['royal doulton'], 'Everyday White': ['everyday white'],
    'Arthur Court': ['arthur court'], 'La Rochere': ['la rochere'],
}
COLORS = {'white','black','blue','red','green','yellow','pink','purple','orange','brown',
          'gray','grey','ivory','cream','beige','gold','silver','clear','multicolor','multi',
          'navy','charcoal','sage','rust','taupe','cobalt','platinum','champagne','copper','bronze',
          'pewter','chrome','teal','turquoise','aqua','coral','blush','burgundy','lavender','mint',
          'olive','khaki','tan','amber','slate','graphite','indigo','emerald','lilac','plum','mauve',
          'gunmetal','nickel','brass','onyx','natural','multicolored','assorted','offwhite'}
_COLOR_ALIASES = {'grey': 'gray', 'multi': 'multicolor', 'multicolored': 'multicolor', 'assorted': 'multicolor',
                  'offwhite': 'ivory'}
TYPES = {
    'mug': {'mug','mugs'}, 'cup': {'cup','cups','teacup','teacups'},
    'dinnerware': {'dinnerware'}, 'napkin': {'napkin','napkins'},
    'placemat': {'placemat','placemats'}, 'towel': {'towel','towels'},
    'vase': {'vase','vases'}, 'bowl': {'bowl','bowls'}, 'plate': {'plate','plates'},
    'frame': {'frame','frames'}, 'pitcher': {'pitcher','pitchers','carafe','carafes','jug','jugs'},
    'tray': {'tray','trays','platter','platters'}, 'paddle': {'paddle','paddles'},
    'ball': {'ball','balls'}, 'flatware': {'flatware','cutlery','fork','forks','spoon','spoons'},
    'glass': {'glass','glasses','goblet','goblets','tumbler','tumblers','flute','flutes','stemware',
              'highball','highballs','glassware','drinkware'},
    'tablecloth': {'tablecloth','tablecloths'}, 'runner': {'runner','runners'},
    'pillow': {'pillow','pillows'}, 'blanket': {'blanket','blankets','throw','throws'},
    'candleholder': {'candleholder','candleholders','candlestick','candlesticks','hurricane','lantern','lanterns'},
    'teapot': {'teapot','teapots','kettle','kettles'},
    'utensil': {'ladle','ladles','spatula','spatulas','utensil','utensils','tongs','whisk'},
}
RELATED_TYPES = [{'cup','mug'}, {'dinnerware','plate','bowl'}, {'tray','plate'}]
GENERIC_TYPE = 'set'
_SET_WORDS = {'set','sets','setting','settings'}
_FILLER = {'no','size','color','one','fits','all','and','the','of','by','for','with','new','place',
           'piece','pieces','pc','pcs','oz','x','in','inch'} | _SET_WORDS
# Sizes pick a variant of a product line, never the line itself.
SIZES = {'twin','full','queen','king','standard','euro','european','small','medium','large'}
# A row naming no brand or type that shares product-name words has more likely lost the
# wording (style codes, cut-off BOL text) than named another item. Types get less, so a
# typeless sibling from the same collection stays below the row whose type matches.
ABSENT_BRAND_SOFT = .7
ABSENT_TYPE_SOFT = .6
MIN_SHARED_NAMES = 2


def normalized(text):
    text = str(text or '').casefold()
    if not text.isascii():  # only non-ASCII text carries accents; skip the per-character pass otherwise
        text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def _index_brands(learned):
    index = defaultdict(list)
    for brand, aliases in list(BRANDS.items()) + list(learned.items()):
        for alias in aliases:
            words = tuple(normalized(alias).split())
            if words:
                index[words[0]].append((words, brand))
    return dict(index)


def vocabulary_words():
    """Kind, colour and filler words: whatever else a title says names a brand or product."""
    return set().union(*TYPES.values()) | COLORS | _FILLER


def _plain_words(brand_index):
    return vocabulary_words() | {w for entries in brand_index.values() for alias, _ in entries for w in alias}


_learned = {}
_brand_index = _index_brands(_learned)
_plain = _plain_words(_brand_index)
_learned_from = None  # digest of the titles the vocabulary was last learned from
# Rebuilds run on several request threads: the cache is keyed by this generation,
# bumped only after the new vocabulary is in place, so a lookup under the new
# generation can never be served brands from the old one.
_generation = 0
_learn_lock = threading.Lock()


def brand_names():
    return {**_learned, **BRANDS}


def name_words(words):
    """Shared words that name the product rather than its brand, type or colour."""
    return {w for w in words if w not in _plain and not w.isdigit()}


def learn_brands(titles):
    """Brands the BOL writes twice at the head of a title ("Nambe Nambe Braid").

    A word group counts only when it mostly opens the titles that contain it:
    a pattern word can repeat once but usually sits mid-title. Returns True
    when the vocabulary changed.
    """
    global _learned, _brand_index, _plain, _generation, _learned_from
    titles = [str(t) for t in titles if t]
    # Every warehouse load (a rebuild, each "more matches" tap) relearns; the same titles teach the same brands.
    digest = hashlib.sha1('\0'.join(titles).encode()).digest()
    if digest == _learned_from:
        return False
    texts = [normalized(t).split() for t in titles]
    doubled = Counter()
    for words in texts:
        for size in (4, 3, 2, 1):
            if len(words) >= 2 * size and words[:size] == words[size:2 * size]:
                doubled[tuple(words[:size])] += 1
                break
    vocabulary = vocabulary_words()
    groups = sorted((g for g in doubled
                     if not all(w in vocabulary or w.isdigit() for w in g) and len(' '.join(g)) >= 3),
                    key=lambda g: (len(g), g))
    by_first = defaultdict(list)
    for group in groups:
        by_first[group[0]].append(group)
    head, anywhere = Counter(), Counter()
    for words in texts:
        seen = set()
        for i, word in enumerate(words):
            for group in by_first.get(word, ()):
                if group not in seen and tuple(words[i:i + len(group)]) == group:
                    seen.add(group)
                    anywhere[group] += 1
                    head[group] += i == 0
    covered = {tuple(normalized(a).split()) for aliases in BRANDS.values() for a in aliases}
    learned = {}
    for group in groups:
        if head[group] < 2 or 2 * head[group] < anywhere[group]:
            continue
        if any(len(k) <= len(group) and any(group[i:i + len(k)] == k for i in range(len(group) - len(k) + 1))
               for k in covered):
            continue  # "Kate Spade New York" is already "Kate Spade"
        covered.add(group)
        learned[' '.join(w.capitalize() for w in group)] = [' '.join(group)]
    with _learn_lock:
        _learned_from = digest
        if learned == _learned:
            return False
        brand_index = _index_brands(learned)
        _learned, _brand_index, _plain = learned, brand_index, _plain_words(brand_index)
        _generation += 1
    return True


def _truncated_type(words):
    """BOL descriptions are cut at a fixed width ("Cereal Bow", "Salad Pl")."""
    for word in reversed(words):
        if word in COLORS or word in _FILLER or re.fullmatch(r'\d+(?:x\d+)?[a-z]{0,2}', word):
            continue
        if len(word) >= 3 and any(color.startswith(word) for color in COLORS):
            continue  # a cut-off colour ("Bla" is black, not blanket)
        if len(word) < 3 or any(word in tokens for tokens in TYPES.values()):
            return None
        kinds = {kind for kind, tokens in TYPES.items() if any(t.startswith(word) for t in tokens)}
        return next(iter(kinds)) if len(kinds) == 1 else None
    return None


def _aliases(words):
    """(alias words, brand) for every brand alias, static or learned, the words spell out."""
    brand_index = _brand_index
    for i, word in enumerate(words):
        for alias, brand in brand_index.get(word, ()):
            if tuple(words[i:i + len(alias)]) == alias:
                yield alias, brand


def brand_words(title):
    """Words of the brand aliases a title names; they say which maker, not which product."""
    return frozenset(word for alias, _ in _aliases(normalized(title).split()) for word in alias)


def attributes(title, spelled=()):
    """Brands, item types, colours and piece count of a title.

    spelled pairs a rack word the BOL cut short or abbreviated ("Bow", "PLT") with the
    listing word it spells, so that word names the listing word's item type.
    """
    return _attributes(title, _generation, spelled)


@lru_cache(maxsize=16384)
def _attributes(title, generation, spelled):
    text = normalized(title)
    words = text.split()
    wordset = set(words)
    brands = {brand for _, brand in _aliases(words)}
    # A word that already names a brand, type, colour or filler ("Place" of "Place Setting") is whole, not cut.
    spelled_words = {word for short, word in spelled if short in wordset and short not in _plain}
    types = {kind for kind, tokens in TYPES.items() if (wordset | spelled_words) & tokens}
    if not types:
        # No listing word spells it: a stub closing the description still names the one type it starts.
        kind = _truncated_type(words)
        types = {kind} if kind else set()
    if not types and wordset & _SET_WORDS:
        types = {GENERIC_TYPE}
    colors = {_COLOR_ALIASES.get(word, word) for word in wordset & COLORS}
    count = re.search(r'\b(?:set|pack|service) (?:of )?(\d+)\b|\b(\d+) (?:piece|pieces|pc|pcs|count|ct)\b', text)
    return {'brands': sorted(brands), 'types': sorted(types), 'colors': sorted(colors),
            'pieces': int(next(g for g in count.groups() if g)) if count else None}


def _related(wanted, actual):
    return any(set(wanted) & family and set(actual) & family for family in RELATED_TYPES)


def weighted_similarity(query, candidate, text_score, text_weight=.12, conflict_factor=.65, shared_names=0):
    """Brand > type > color > pieces; absent attributes remain uncertain.

    shared_names counts product-name words both titles use (not brand, type or
    colour words); with MIN_SHARED_NAMES of them a missing brand or type only
    lowers the match.
    """
    weighted, total = text_weight * text_score, text_weight
    reasons = []
    # "12-Pc. Dessert Set" may well be the bowls; a set conflicts with no type.
    generic = GENERIC_TYPE in query['types'] + candidate['types']
    named = shared_names >= MIN_SHARED_NAMES
    for field, weight, label in [('brands',.4,'brand'),('types',.25,'item type'),('colors',.15,'color'),('pieces',.08,'piece count')]:
        wanted, actual = query[field], candidate[field]
        if not wanted:
            continue
        total += weight
        if not actual:
            similarity = {'brands': ABSENT_BRAND_SOFT, 'types': ABSENT_TYPE_SOFT}.get(field, .35) if named else .35
        elif field == 'pieces':
            similarity = float(wanted == actual)
        else:
            overlap = set(wanted) & set(actual)
            similarity = len(overlap)/len(set(wanted))
            if field == 'types' and not overlap:
                similarity = .65 if _related(wanted, actual) else .5 if generic else 0
        weighted += weight * similarity
        if similarity == 1:
            reasons.append('same '+label)
    score = weighted/total
    if query['types'] and candidate['types'] and not set(query['types']) & set(candidate['types']):
        # A mismatch lowers priority rather than acting as an absolute exclusion.
        if not generic and not _related(query['types'], candidate['types']):
            score *= conflict_factor
    if query['pieces'] and candidate['pieces'] and query['pieces'] != candidate['pieces']:
        score = min(score,.89)
    if text_score < .8:
        score = min(score,.7 + .19 * text_score/.8)
    return round(score,3), reasons
