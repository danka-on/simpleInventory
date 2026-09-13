"""Shared brand and product-name vocabulary for listing reconciliation.

Seeded from recurring warehouse and marketplace titles; aliases are search
hints, not proof of product identity. Extend this list as new brands arrive.
"""
import re
import unicodedata
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
          'gray','grey','ivory','cream','beige','gold','silver','clear','multicolor','multi'}
TYPES = {
    'mug': {'mug','mugs'}, 'cup': {'cup','cups','teacup','teacups'},
    'dinnerware': {'dinnerware'}, 'napkin': {'napkin','napkins'},
    'placemat': {'placemat','placemats'}, 'towel': {'towel','towels'},
    'vase': {'vase','vases'}, 'bowl': {'bowl','bowls'}, 'plate': {'plate','plates'},
    'frame': {'frame','frames'}, 'pitcher': {'pitcher','pitchers','carafe','carafes','jug','jugs'},
    'tray': {'tray','trays','platter','platters'}, 'paddle': {'paddle','paddles'},
    'ball': {'ball','balls'}, 'flatware': {'flatware','cutlery','fork','forks','spoon','spoons'},
    'glass': {'glass','glasses','goblet','goblets','tumbler','tumblers'},
    'tablecloth': {'tablecloth','tablecloths'}, 'runner': {'runner','runners'},
    'pillow': {'pillow','pillows'}, 'blanket': {'blanket','blankets','throw','throws'},
}

def normalized(text):
    text = unicodedata.normalize('NFKD', str(text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', text))


@lru_cache(maxsize=8192)
def attributes(title):
    text = normalized(title)
    words = set(text.split())
    brands = {brand for brand, aliases in BRANDS.items()
              if any(' '+alias+' ' in ' '+text+' ' for alias in aliases)}
    types = {kind for kind, tokens in TYPES.items() if words & tokens}
    if not types and words & {'set','sets'}:
        types = {'set'}
    colors = {('gray' if c=='grey' else 'multicolor' if c=='multi' else c) for c in words & COLORS}
    count = re.search(r'\b(?:set|pack|service) (?:of )?(\d+)\b|\b(\d+) (?:piece|pieces|pc|pcs|count|ct)\b', text)
    return {'brands': sorted(brands), 'types': sorted(types), 'colors': sorted(colors),
            'pieces': int(next(g for g in count.groups() if g)) if count else None}


def weighted_similarity(query, candidate, text_score):
    """Brand > type > color > pieces; absent attributes remain uncertain."""
    weighted, total = .12 * text_score, .12
    reasons = []
    for field, weight, label in [('brands',.4,'brand'),('types',.25,'item type'),('colors',.15,'color'),('pieces',.08,'piece count')]:
        wanted, actual = query[field], candidate[field]
        if not wanted:
            continue
        total += weight
        if not actual:
            similarity = .35
        elif field == 'pieces':
            similarity = float(wanted == actual)
        else:
            overlap = set(wanted) & set(actual)
            similarity = len(overlap)/len(set(wanted))
            if field=='types' and not overlap:
                related = [{'cup','mug'}, {'dinnerware','plate','bowl'}, {'tray','plate'}]
                similarity = .65 if any(set(wanted)&family and set(actual)&family for family in related) else 0
        weighted += weight * similarity
        if similarity == 1:
            reasons.append('same '+label)
    score = weighted/total
    if query['types'] and candidate['types'] and not set(query['types']) & set(candidate['types']):
        # A mismatch lowers priority rather than acting as an absolute exclusion.
        related = [{'cup','mug'}, {'dinnerware','plate','bowl'}, {'tray','plate'}]
        if not any(set(query['types'])&family and set(candidate['types'])&family for family in related):
            score *= .65
    if query['pieces'] and candidate['pieces'] and query['pieces'] != candidate['pieces']:
        score = min(score,.89)
    if text_score < .8:
        score = min(score,.7 + .19 * text_score/.8)
    return round(score,3), reasons
