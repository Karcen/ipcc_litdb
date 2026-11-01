# Portable replay copy: algorithms and frozen labels preserved; paths adapted.
import type_paths as P
"""A_type_classifier.py -- deterministic document-type classifier for IPCC reference
occurrence strings (Task A of the CJE revision).

WHY THIS EXISTS
---------------
The bibliographic database records whether a cited work matched Web of Science /
Crossref / OpenAlex, but its document-type fields are empty. To ask whether the
three IPCC working groups rely on different *kinds* of evidence, and whether the
measurement infrastructure is unevenly able to see those kinds, we must code
document type from the raw reference string. The raw string is available for
every one of the 160,029 occurrences, including those that never matched.

WHAT IT DOES
------------
Labels every occurrence into one of six categories:
    journal_article | book_or_chapter | institutional_report |
    working_paper | other | uncertain
using only surface cues in the normalised raw string (punctuation, lexical
markers, volume/page patterns, publisher and institutional names, URL domains).
Matched metadata is NEVER an input here; it is used only for validation in a
separate script.

PRECEDENCE
----------
Rules are applied in a fixed order; the first rule that fires wins. The order is
documented in analysis/A_type_classifier_protocol.md and encoded in the
`classify()` function below. `uncertain` is a real category: a string with no
usable cue, or whose only cue is too weak, is left uncertain rather than forced.

OUTPUT
------
analysis/data/occurrence_types.csv with one row per occurrence:
    occurrence_id, category, subcategory, rule_id, confidence, matched_rules,
    conflict, deterministic key diagnostics.

USAGE
-----
    python3 analysis/A_type_classifier.py            # writes occurrence_types.csv
    python3 analysis/A_type_classifier.py --check    # prints distribution/sanity
Deterministic, no sampling, no network, re-runnable.
"""
import os, re, sys, csv, json
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = str(P.DATA)

CATEGORIES = ["journal_article", "book_or_chapter", "institutional_report",
              "working_paper", "other", "uncertain"]

# ---------------------------------------------------------------------------
# 1. Normalisation
# ---------------------------------------------------------------------------
_WS = re.compile(r"[\u200b\u00ad\u200c\u200d\ufeff\u00a0\u2009\u2007]")
def normalize(ref: str) -> str:
    """Normalise the raw string for deterministic matching.

    - collapse zero-width characters, soft hyphens and exotic spaces to a plain space
    - unify unicode dashes/minus signs to '-'
    - unify curly quotes
    - collapse runs of whitespace
    Case is preserved (some cues rely on capitalisation of article codes)."""
    s = ref or ""
    s = s.replace("\u200b", "").replace("\u00ad", "").replace("\ufeff", "")
    s = _WS.sub(" ", s)
    s = (s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2015", "-")
           .replace("\u2212", "-").replace("\u2010", "-").replace("\u2011", "-"))
    s = (s.replace("\u2019", "'").replace("\u2018", "'")
           .replace("\u201c", '"').replace("\u201d", '"'))
    s = re.sub(r"\s+", " ", s).strip()
    # PDF line-break hyphenation ("Pro- ceedings", "Environ- mental"): a hyphen
    # followed by a space and a lower-case letter is joined. This is a surface
    # artefact, not a semantic change.
    s = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", s)
    return s

_URL = re.compile(r"(?:https?\s*:\s*/\s*/|www\.)[^\s,;)]+")
def strip_urls(s: str) -> str:
    """Remove URL material before lexical cue matching, so that domain names such
    as 'ssrn.com', 'nber.org' or 'worldbank.org' cannot fire document-type rules
    outside their publication context. URLs are retained in the full string for
    the institutional-domain rule."""
    return re.sub(r"\s+", " ", _URL.sub(" ", s)).strip()

# ---------------------------------------------------------------------------
# 2. Lexica (surface cues; every entry is a literal visible marker of the type)
# ---------------------------------------------------------------------------
# Book publishers. Deliberately explicit rather than inferred: a reader can add
# or remove an entry and rerun. Publisher imprints that are also journal-name
# words (e.g. bare "Wiley", "Elsevier") are excluded to avoid flagging journal
# articles; the journal is separately recognised by its volume/pages fingerprint.
BOOK_PUBLISHERS = [
 "springer-verlag", "springer verlag", "springer international", "springer nature", "edward elgar", "elgar",
 "springerbriefs", "palgrave macmillan", "palgrave", "macmillan",
 "routledge", "taylor & francis", "taylor and francis", "edward elgar",
 "cambridge university press", "oxford university press", "princeton university press",
 "harvard university press", "yale university press", "columbia university press",
 "university of chicago press", "chicago university press",
 "johns hopkins university press", "cornell university press",
 "university of california press", "stanford university press",
 "duke university press", "new york university press", "nyu press",
 "earthscan", "island press", "wiley-blackwell", "john wiley & sons", "john wiley",
 "crc press", "world scientific", "imperial college press", "kluwer",
 "martinus nijhoff", "brill", "peter lang", "rowman & littlefield", "rowman and littlefield",
 "sage publications", "pearson", "prentice hall", "mcgraw-hill", "w.w. norton",
 "basic books", "harvard business", "penguin books", "vintage", "vintage books", "random house",
 "doubleday", "faber & faber", "allen & unwin", "bloomsbury", "polity press",
 "verso", "zed books", "monthly review press", "edinburgh university press",
 "manchester university press", "university of toronto press", "mcgill-queen's",
 "anu press", "anu e press", "csiro publishing",
 "james & james", "bell & bain", "belknap",
 "russell sage foundation", "aldine", "transaction publishers", "ashgate", "gower",
 "academic press", "elsevier science", "elsevier", "springer", "butterworth", "heinemann", "longman", "intech", "intechopen",
 "basil blackwell", "birkhauser", "birkhäuser", "karger", "nova science", "unctad",
 "university press of", "university presses",
 "jossey-bass", "wiley periodicals", "cengage", "houghton mifflin", "harcourt",
 "macmillian", "charles scribner", "scribner", "free press", "beacon press",
 "perseus books", "public affairs", "grove press", "farrar, straus",
 "new left books", "the new press", "wiley & sons", "blackwell publishing",
 "blackwell science", "blackwell publishers", "sage", "focus", "unwin hyman",
 "croom helm", "routledge/earthscan", "earthscan publications", "cedar",
 "centre for european policy studies", "ceps", "chatham house",
]
# Generic publisher markers that are book-specific when in venue position
PUB_GENERIC_RE = re.compile(
    r"(?i)(?:(?:[A-Z][A-Za-z'\.& \-]{1,40}\s+)?university press"      # X University Press
    r"|\b[A-Z][A-Za-z'\.& \-]{2,40}(?:press|publishers?|publishing)\b)" # standalone X Press
)
# Institutions / intergovernmental and government bodies. Used only in
# document (venue/publisher/author) position, never for title text.
INSTITUTIONS = [
 "ipcc", "intergovernmental panel on climate change", "wmo",
 "world meteorological organization", "world meteorological organisation",
 "unep", "united nations environment programme", "united nations environment program",
 "unep-wcmc", "world conservation monitoring centre", "world bank", "world bank group",
 "imf", "international monetary fund", "oecd",
 "organisation for economic co-operation and development",
 "organization for economic cooperation and development",
 "iea", "international energy agency", "irena", "international renewable energy agency",
 "fao", "food and agriculture organization", "food and agriculture organisation",
 "who", "world health organization", "world health organisation",
 "unfccc", "united nations framework convention", "undp", "unido", "unesco",
 "un-habitat", "un habitat", "iaea", "wto", "world trade organization",
 "world trade organisation", "unctad", "global environment facility",
 "asian development bank", "african development bank", "inter-american development bank",
 "european commission", "european union", "european parliament",
 "european environment agency", "european investment bank", "european communities",
 "nasa", "noaa", "us epa", "u.s. epa", "environmental protection agency",
 "us department of energy", "u.s. department of energy", "department of energy",
 "usaid", "nrel", "national renewable energy laboratory", "oak ridge national laboratory",
 "national research council", "national academies", "national academy of sciences",
 "national academy of engineering", "institute of medicine", "royal society",
 "csiro", "iied", "international institute for environment and development",
 "odi", "overseas development institute", "institute of development studies",
 "unu-wider", "unu wider", "united nations university", "cgd",
 "center for global development", "world resources institute", "worldwatch",
 "wwf", "iucn", "ipbes", "cbd", "convention on biological diversity",
 "cabi", "cab international", "secretariat", "ministry of", "ministry for", "ministry of the", "ministerio",
 "department of", "department for", "department of the", "government of",
 "federal ministry", "us government", "u.s. government",
 "agency", "authority", "administration", "council", "committee", "commission",
 "board", "panel", "national institute", "research institute", "institute for",
 "institute", "laboratory", "centre for", "center for",
 "centre for", "center for", "foundation", "association", "federation",
 "alliance", "network", "bank", "fund", "consortium", "programme", "program",
]
STRICT_INSTITUTIONS = [
 "ipcc", "intergovernmental panel on climate change", "wmo",
 "world meteorological organization", "unep", "united nations environment programme",
 "unep-wcmc", "world bank", "world bank group", "imf", "international monetary fund",
 "oecd", "organisation for economic co-operation and development",
 "organization for economic cooperation and development", "iea", "international energy agency",
 "irena", "international renewable energy agency", "fao",
 "food and agriculture organization", "who", "world health organization",
 "unfccc", "undp", "unido", "unesco", "un-habitat", "un habitat", "iaea", "wto",
 "unctad", "global environment facility", "asian development bank",
 "african development bank", "inter-american development bank", "european commission",
 "european union", "european parliament", "european environment agency",
 "european investment bank", "european communities", "nasa", "noaa", "us epa",
 "environmental protection agency", "us department of energy", "department of energy",
 "usaid", "nrel", "national renewable energy laboratory", "oak ridge national laboratory",
 "national research council", "national academies", "national academy of sciences",
 "royal society", "csiro", "iied", "odi", "overseas development institute",
 "unu-wider", "united nations university", "cgd", "center for global development",
 "world resources institute", "worldwatch", "iucn", "ipbes", "cbd",
 "resources for the future", "sustainable energy for all", "unicef",
 "world food programme", "world wildlife fund", "conservation international",
 "international water association", "national renewable energy lab",
 "c40 cities", "climate action network", "e3g", "iita",
 "international institute of tropical agriculture",
]
# Institutional bodies recognised as AUTHORS at the head of a reference
ORG_AUTHOR_RE = re.compile(
    r"(?i)^(?:the\s+)?(?:"
    r"ipcc|wmo|unep|unfccc|undp|unido|unesco|un-habitat|iaea|wto|unctad|gef|oecd|iea|irena|"
    r"fao|who|imf|world bank(?: group)?|united nations(?: [a-z\-]+){0,4}|european (?:commission|union|parliament|environment agency|communities)|"
    r"us epa|u\.s\. epa|epa|doe|noaa|nasa|usaid|nrel|csiro|iied|odi|cgd|wri|wwf|iucn|ipbes|cbd|"
    r"national research council|national academies|national academy of [a-z]+|royal society|"
    r"government of(?: [a-z\-]+){1,5}|ministry of(?: [a-z\-]+){1,5}|ministry for(?: [a-z\-]+){1,5}|"
    r"department of(?: [a-z\-]+){1,5}|department for(?: [a-z\-]+){1,5}|"
    r"us department of(?: [a-z\-]+){1,5}|u\.s\. department of(?: [a-z\-]+){1,5}|"
    r"african development bank|asian development bank|inter-american development bank|"
    r"(?:[a-z\-]+ ){0,3}agency|(?:[a-z\-]+ ){0,2}authority|(?:[a-z\-]+ ){0,2}administration|"
    r"(?:[a-z\-]+ ){0,3}council|(?:[a-z\-]+ ){0,3}committee|(?:[a-z\-]+ ){0,3}commission|"
    r"(?:[a-z\-]+ ){0,2}secretariat|(?:[a-z\-]+ ){0,2}association|(?:[a-z\-]+ ){0,2}federation|"
    r"(?:[a-z\-]+ ){0,2}alliance|(?:[a-z\-]+ ){0,2}network|(?:[a-z\-]+ ){0,2}initiative|"
    r"(?:[a-z\-]+ ){0,3}foundation|(?:[a-z\-]+ ){0,2}institute|"
    r"(?:[a-z\-]+ ){0,2}organisation|(?:[a-z\-]+ ){0,2}organization|"
    r"statistics (?:canada|new zealand)|eurostat|"
    r"[a-z]{2,12} (?:nz|uk|usa|canada|australia)"
    r")\s*[,\(]"
)
ORG_ACRONYM_RE = re.compile(
    r"^\s*(?:[A-Z][A-Z0-9]{1,7}|[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\s*[,(\(]\s*(?:(?:19|20)\d{2})")
# an acronym / short name in author position is an institutional author when it is
# followed by a citation year AND contains a digit or is all-caps (IPCC, IEA, OECD,
# C40 Cities, E3G, ICAO, NASEM, UNEP, WMO, ...). Pure surname lists are title-case
# and multi-token, so they do not match the all-caps branch.
IGO_URL_RE = re.compile(
    r"(?i)(?:https?\s*:\s*/\s*/|www\.)[^\s,;)]*(?:"
    r"ipcc\.ch|wmo\.int|unep\.org|worldbank\.org|openknowledge\.worldbank\.org|imf\.org|oecd\.org|"
    r"iea\.org|irena\.org|fao\.org|who\.int|unfccc\.int|undp\.org|unido\.org|unesco\.org|"
    r"un\.org|unhabitat\.org|iaea\.org|wto\.org|unctad\.org|gef\.org|adb\.org|afdb\.org|iadb\.org|"
    r"europa\.eu|ec\.europa\.eu|eea\.europa\.eu|nasa\.gov|noaa\.gov|epa\.gov|energy\.gov|"
    r"usaid\.gov|nrel\.gov|ornl\.gov|csiro\.au|iied\.org|odi\.org|wri\.org|wwf|iucn\.org|"
    r"ipbes\.net|cbd\.int|unep-wcmc\.org|cdn\.gca\.org|"
    r"\.gov(?:\.[a-z]{2})?(?:/|$)|\.gov\.|\.int(?:/|$)|\.mil(?:/|$)|"
    r"\.ac\.uk|\.edu(?:/|$)"
    r")")
# Journal lead-in phrases: these open a journal title even when the title also
# contains an institution-like word ("Proceedings of the National Academy of
# Sciences", "Journal of the American Medical Association").
JOURNAL_VENUE_LIKE = re.compile(
    r"(?i)\b(?:journal of|proceedings of|review of|reviews of|transactions of|"
    r"annals of|bulletin of|advances in|archives of|international journal|"
    r"eos\b|nature\b|science\b|plos\b|pnas\b)")
# Journal-name lexical evidence (used only for the medium-strength journal rules)
JOURNAL_TOKENS = re.compile(
    r"(?i)\b(?:journal|j\.|rev\.|review|letters?|lett\.|science|sci\.|nature|"
    r"proceedings|proc\.|transactions|annals|ann\.|bulletin|bull\.|"
    r"climate|climatic|clim\.|change|dynamics|dyn\.|ecology|ecol\.|economics|econ\.|"
    r"policy|energy|environment|environmental|environ\.|geophysical|geophys\.|"
    r"atmosphere|atmospheric|atmos\.|ocean|oceans|water|hydrology|hydrol\.|"
    r"meteorolog|weather|biogeochem|biogeosciences|earth|planet|global|glob\.|"
    r"international|int\.|research|res\.|studies|quarterly|frontiers|front\.|"
    r"marine|mar\.|biology|biol\.|conservation|sustainability|resources|"
    r"management|manag\.|assessment|model|modelling|data|society|social|"
    r"geography|geogr\.|geology|geol\.|quaternary|holocene|paleo|palaeo|"
    r"urban|cities|agricultur|forest|ecology|health|medicine|public|"
    r"technolog|technol\.|innovation|finance|financial|development|dev\.|"
    r"world|land|use|law|legal|political|politics|anthropocene|one earth|"
    r"plos|pnas|eos|science advances|scientific reports|elements|elementa)\b"
)
# Working-paper / preprint markers
WP_RE = re.compile(
    r"(?i)(?:\bworking papers?\b|\bdiscussion papers?\b|"
    r"\bresearch paper no\.?\b|\bpolicy research working paper\b|\bstaff working paper\b|"
    r"\bworking paper series\b|\bwider working paper\b|\bicrlp working paper\b|"
    r"\bnational bureau of economic research\b|"
    r"\bfeem working paper\b|\bpier working paper\b|\bimf working paper\b|"
    r"\bpreprint\b|\barxiv\b|\bssrn\s+(?:electronic|electron\.)\s+j(?:ournal)?\b|"
    r"\bavailable at ssrn\b|\bmimeo\b|\bresearch papers?\b(?=\s*no\.?\s*\d)"
    r")"
)
# Report phrase markers (grey literature reports)
REPORT_RE = re.compile(
    r"(?i)(?:\breport\s*(?:no\.?|number|series|of the)\b|"
    r"\btechnical report\b|\btechnical paper\b|\bspecial report\b|"
    r"\bassessment report\b|\bannual report\b|\bsynthesis report\b|"
    r"\bwhite paper\b|\bpolicy brief\b|\bbriefing (?:paper|note)\b|"
    r"\bissue[s]? paper\b|\boccasional paper\b|\bguidance (?:document|note)\b|"
    r"\bprogress report\b|\bcountry report\b|\binterim report\b|"
    r"\bprepared (?:for|by)\b|\bsubmitted to\b|\bcommissioned by\b|"
    r"\btechnical note\b|\bbackground paper\b|\bconference room paper\b|"
    r"\badministrative report\b|\bcase study report\b|\bglobal report\b|"
    r"\bflagship report\b|\bdata report\b|\bworking note\b"
    r")"
)
DATASET_RE = re.compile(
    r"(?i)(?:\bmodel output\b|\bcmip6?\s+model output\b|\bemission inventory dataset\b|"
    r"\bprepared for cmip6?\b|\bdata set version\b|\bdatabase version\b)")
THESIS_RE = re.compile(
    r"(?i)(?:\b(?:ph\.?\s?d\.?|doctoral|doctorate|master'?s|m\.?sc\.?|msc|bachelor'?s|"
    r"undergraduate|honours|honors)\s+(?:thesis|dissertation)\b|"
    r"\bphd\b|\bdissertation\b|\bthesis\b|\bhabilitation\b)")
CONFERENCE_RE = re.compile(
    r"(?i)(?:\bproceedings of\b(?!\s+(?:the\s+)?(?:National Academy|Royal Society))|"
    r"\bconference proceedings\b|\bproceedings?\s+(?:of the\s+)?(?:[A-Z][\w\s\-]{0,40})(?:summer study|conference|symposium|workshop)\b|"
    r"\bproceeding\b|\bpaper presented\b|\bpresented at the\b|"
    r"\bannual meeting of the\b|\bworkshop proceedings\b|\bconference paper\b|"
    r"(?:\bIn\s*:|\bProceedings\b|\bProceedings of the\b)[^.]{0,80}\b(?:symposium|conference|workshop)\b)")
OTHER_RE = re.compile(
    r"(?i)(?:\bpress release\b|\bpresentation\b|\bwebinar\b|\bpersonal communication\b|"
    r"\bnews release\b|\bmedia release\b|\bfact sheet\b|\bleaflet\b|\bpodcast\b|"
    r"\bworkshop report\b|\bconference report\b)")

# Journal fingerprint patterns, strongest first. A match span is used to derive
# the venue segment (text immediately to the left of the volume number).
JOURNAL_FP = [
    # volume(issue) plus optional pages / article number
    ("J1_vol_issue", re.compile(
        r"\b\d{1,4}\s*\(\s*(?:[A-Za-z0-9\.\s\-]{1,14}?\d[\w\s\.\-]{0,10}|[\w\.\s]{1,14})\s*\)"
        r"(?:\s*[,:.]?\s*(?:pp?\.?\s*)?(?:[A-Za-z]{0,2}\d{1,8}(?:[-]\w{1,8})?|(?:\d{1,6}\s*[-]\s*\d{1,6})))"
    )),
    # volume, page range  (journal or book-chapter-like; venue decides)
    ("J2_vol_pages", re.compile(
        r"\b\d{1,4}\s*,\s*[A-Za-z]{0,2}\d{1,6}\s*-\s*[A-Za-z]{0,2}\d{1,6}\b"
    )),
    # volume, alphanumeric article code (D10208, L08706, GB2009, e0179498) or long article number
    ("J3_vol_article", re.compile(
        r"\b\d{1,4}\s*,\s*(?:[A-Z]{1,3}\d{3,8}[A-Z]?|\d{5,8})\b"
    )),
]
VOL_ISSUE_ONLY = re.compile(
    r"\b\d{1,4}\s*\(\s*(?:[A-Za-z]{0,8}\s*[\d\-]{1,12}|[\d\-]{1,12})\s*\)")
TAIL_YEAR = re.compile(r"(?<![0-9])((?:19|20)\d{2})[a-z]?\s*[\):]")


def _last_sentence_start(s: str) -> int:
    """Index after the last sentence/segment boundary within s (used to trim a
    venue segment back to the last '. ' / '; ' / '] ' / ': ' boundary)."""
    best = 0
    for m in re.finditer(r"(?:\.\s+|;\s+|\]\s+|\.\s*$)", s):
        best = max(best, m.end())
    return best


def venue_segment(r: str, start: int, width: int = 120) -> str:
    """Text immediately preceding a volume/fingerprint match: the best available
    proxy for the venue (journal name, book title or report series)."""
    seg = r[max(0, start - width):start]
    cut = _last_sentence_start(seg)
    if cut and cut < len(seg) - 1:
        seg = seg[cut:]
    return seg.strip()


def _has(pats, s):
    return any(p.search(s) for p in pats)

_PUB_RES = [re.compile(r"(?i)\b" + re.escape(p) + r"\b") for p in BOOK_PUBLISHERS]
_INST_RES = [re.compile(r"(?i)\b" + re.escape(p.rstrip()) + ("" if p.endswith(" ") else r"\b"))
             for p in INSTITUTIONS]
CITY_RE = re.compile(
    r"(?i)(?:^|[\.,]\s*)(?:[A-Z][A-Za-z'\.\-]+(?:\s+[A-Z][A-Za-z'\.\-]+){0,3})\s*,\s*"
    r"(?:[A-Z]{2,3}\b|U\.S\.A?\.?|USA|UK|U\.K\.?|DC|D\.C\.?|NY|New York|London|Cambridge|"
    r"Oxford|Paris|Washington|Geneva|Rome|Tokyo|Berlin|Brussels|Vienna|Milan|Madrid|"
    r"Delhi|Nairobi|Bonn|Ottawa|Canberra|Wellington|Stockholm|Copenhagen|Helsinki|Oslo|"
    r"Amsterdam|Beijing|Seoul|Jakarta|Manila|Bangkok|Mexico City|Sao Paulo|"
    r"Buenos Aires|Cape Town|Johannesburg|Luxembourg|Rotterdam|Cheltenham|"
    r"Northampton|Cham|Singapore|Abu Dhabi|Gland|Laxenburg|Luxemburg|Potsdam|"
    r"Wageningen|Leeds|Bristol|Exeter|Reading|Norwich|Edinburgh|Glasgow|Manchester|"
    r"Melbourne|Sydney|Toronto|Vancouver|Montreal|Boston|Chicago|Princeton|"
    r"Berkeley|Los Angeles|San Francisco|Seattle|Atlanta|Baltimore|Philadelphia|"
    r"Pittsburgh|Auckland|Hamilton|Nairobi|Addis Ababa|Manila|Hanoi|Bangkok)"
)
PP_RE = re.compile(r"(?i)\b(?:pp?\.\s*\d|\d{1,5}\s*pp?\b|\bpages?\s+\d)")
COUNTRY_RE = re.compile(
    r"(?i),\s*(?:The\s+)?(?:Netherlands|Germany|France|UK|U\.K\.|USA|U\.S\.A?\.?|"
    r"United States(?: of America)?|Canada|Australia|China|Japan|India|Brazil|Mexico|"
    r"South Africa|Spain|Italy|Switzerland|Sweden|Norway|Denmark|Finland|Belgium|Austria|"
    r"Poland|Czech Republic|New Zealand|Singapore|Korea|Taiwan|Israel|Turkey|Russia|"
    r"Indonesia|Philippines|Thailand|Vietnam|Malaysia|Kenya|Ethiopia|Ghana|Nigeria|Egypt|"
    r"Morocco|Chile|Argentina|Colombia|Peru|Costa Rica|Iran|Pakistan|Bangladesh|"
    r"Sri Lanka|Nepal|Tanzania|Uganda|Zimbabwe|Zambia|Mozambique|Senegal|Mali|Bolivia|"
    r"Ecuador|Cuba|Jamaica|Fiji|Papua New Guinea|Samoa|Maldives|Bhutan|Mongolia|Kazakhstan|"
    r"Ukraine|Romania|Bulgaria|Croatia|Greece|Portugal|Ireland|Iceland|Luxembourg|"
    r"Slovakia|Slovenia|Estonia|Latvia|Lithuania|Hungary|Serbia|Albania|Cyprus|Malta)\b")
ISBN_RE = re.compile(r"(?i)\bISBN\b")
DOI_RE = re.compile(r"(?i)\bdoi\b|doi\.org")
IN_RE = re.compile(r"(?i)\bIn\s*:")
EDS_RE = re.compile(r"(?i)\(\s*eds?\.?\s*\)|\beditors?\b|\beds?\.\s|\bed\.\s*\]|\[\s*eds?")
IPCC_AUTHOR_RE = re.compile(r"(?i)^\s*(?:the\s+)?(?:ipcc|intergovernmental panel on climate change)\s*[,\(]")
IPCC_STRUCT_RE = re.compile(
    r"(?i)(?:\bIPCC\b|\bIntergovernmental Panel on Climate Change\b).*"
    r"(?:\bSpecial Report\b|\bAssessment Report\b|\bTechnical Paper\b|"
    r"\bSynthesis Report\b|\bMethodology Report\b|\bGuidelines\b|\bIn Press\b|\bpp\.\s*\d)")
IPCC_IN_RE = re.compile(
    r"(?i)\bIn\s*:\s*(?:(?:IPCC\s+)?(?:Climate Change\s+(?:20\d{2}|and Land)|Global Warming of|"
    r"The Ocean and Cryosphere|Managing the Risks of Extreme|Land Use, Land-Use Change|"
    r"Global Energy Assessment))")
CONTRIBUTION_RE = re.compile(
    r"(?i)\bContribution of Working Group\b|\bReport of the Intergovernmental Panel\b")
VENUE_DOI_RE = re.compile(r"(?i)([A-Za-z][A-Za-z\.\s&'\-]{2,70}),?\s*(?:doi\b|https?://doi)")
SINGLE_NUM_TAIL = re.compile(r"(?i)\b(?:\d{1,4})\s*,\s*(?:pp?\.?\s*)?\d{1,6}\s*,?\s*$")

CUE_FAMILIES = [
    ("journal_fingerprint", "journal_article"),
    ("book_chapter", "book_or_chapter"),
    ("book_publisher", "book_or_chapter"),
    ("institutional_report", "institutional_report"),
    ("ipcc_report", "institutional_report"),
    ("working_paper", "working_paper"),
    ("thesis", "other"),
    ("conference", "other"),
    ("dataset", "other"),
    ("other_misc", "other"),
]

def competing_cues(r: str):
    """Independent audit of surface-cue families present in the (URL-stripped)
    string, regardless of which rule won. Used only to populate the audit columns
    `matched_rules`/`conflict`; it never changes a label. A string with cues from
    more than one family is a genuine coding conflict, resolved by the fixed
    precedence, and is flagged so a sceptical reader can inspect it."""
    present = []
    if any(p.search(r) for _, p in JOURNAL_FP):
        present.append("journal_fingerprint")
    if IN_RE.search(r) and EDS_RE.search(r):
        present.append("book_chapter")
    if _pub_in(r):
        present.append("book_publisher")
    if REPORT_RE.search(r) or _inst_in(r) or IGO_URL_RE.search(r):
        present.append("institutional_report")
    if IPCC_AUTHOR_RE.search(r) or IPCC_IN_RE.search(r) or CONTRIBUTION_RE.search(r) or IPCC_STRUCT_RE.search(r):
        present.append("ipcc_report")
    if WP_RE.search(r):
        present.append("working_paper")
    if THESIS_RE.search(r):
        present.append("thesis")
    if CONFERENCE_RE.search(r):
        present.append("conference")
    if DATASET_RE.search(r):
        present.append("dataset")
    if OTHER_RE.search(r):
        present.append("other_misc")
    return present

FAMILY_CATEGORY = dict(CUE_FAMILIES)

RULE_CATALOG = [
 ("R01_thesis", "other", "thesis", "Explicit thesis/dissertation/habilitation marker"),
 ("R02_conference", "other", "conference", "Explicit proceedings/presented-at-conference marker"),
 ("R03_working_paper", "working_paper", "working_paper", "Explicit working-paper/preprint/SSRN-series marker"),
 ("R04_fp_venue_working_paper", "working_paper", "working_paper", "Volume/pages fingerprint whose venue segment is a working-paper series"),
 ("R05_fp_venue_report", "institutional_report", "report", "Volume/pages fingerprint whose venue segment is a report series"),
 ("R06_fp_venue_institution", "institutional_report", "report", "Volume/pages fingerprint whose venue segment names an institution"),
 ("R07_fp_venue_chapter", "book_or_chapter", "book_chapter", "Volume/pages fingerprint whose venue segment is an edited book ('In:' / editors)"),
 ("R08_fp_venue_publisher", "book_or_chapter", "book", "Volume/pages fingerprint whose venue segment is a book publisher"),
 ("R09_journal_fingerprint", "journal_article", "journal_article", "Journal volume/issue/pages or volume/article-code fingerprint"),
 ("R09b_dataset", "other", "dataset", "Scientific data product / model-output dataset"),
 ("R10_report_phrase", "institutional_report", "report", "Explicit report-series phrase ('Report No.', 'Technical Report', 'Special Report', ...)"),
 ("R11_ipcc_structural", "institutional_report", "ipcc_report", "IPCC report structure (IPCC author; 'In: Climate Change 20XX'; Contribution of Working Group; Assessment Report of the IPCC)"),
 ("R12_org_author", "institutional_report", "report", "Reference headed by an institutional author followed by a year"),
 ("R13_book_chapter", "book_or_chapter", "book_chapter", "'In:' plus editors, i.e. an edited book chapter"),
 ("R14_book_publisher", "book_or_chapter", "book", "Book publisher (explicit lexicon or generic '... Press') with city/pages/ISBN"),
 ("R15_isbn", "book_or_chapter", "book", "ISBN without other cue"),
 ("R16_institution", "institutional_report", "report", "Institution in document position with city/pages, or institutional URL domain"),
 ("R17_journal_volume_issue", "journal_article", "journal_article", "Bare volume(issue) pattern with no competing cue"),
 ("R18_journal_venue_doi", "journal_article", "journal_article", "Journal-like venue immediately followed by a DOI and no competing cue"),
 ("R19_book_publisher_weak", "book_or_chapter", "book", "Book publisher named anywhere without further structure"),
 ("R20_institution_weak", "institutional_report", "report", "Institution named anywhere with pages/URL evidence"),
 ("R21_other_misc", "other", "other", "Press release / dataset / software / presentation / blog marker"),
 ("R22_journal_doi_weak", "journal_article", "journal_article", "DOI with a journal-like venue and no publisher/institution cue"),
 ("R99_uncertain", "uncertain", "uncertain", "No usable cue, or only a weak cue that does not support a single category"),
]

_STRICT_RES = [re.compile(r"(?i)\b" + re.escape(p.rstrip()) + r"\b") for p in STRICT_INSTITUTIONS]

def _inst_in(s):
    return any(p.search(s) for p in _INST_RES)

def _strict_inst_in(s):
    return any(p.search(s) for p in _STRICT_RES)

def _pub_lex_in(s):
    return any(p.search(s) for p in _PUB_RES)

def _pub_in(s):
    return _pub_lex_in(s) or bool(PUB_GENERIC_RE.search(s))

def _fp_match(rn):
    """Return (kind, start) of the first journal-fingerprint match after the
    citation year, or (None, None)."""
    ym = TAIL_YEAR.search(rn)
    ypos = ym.end() if ym else 0
    best = None
    for kind, pat in JOURNAL_FP:
        for m in pat.finditer(rn):
            if m.start() < ypos:
                continue
            if best is None or m.start() < best[1]:
                best = (kind, m.start())
            break
    return best if best else (None, None)

def classify(raw: str):
    """Return dict(category, subcategory, rule_id, confidence, matched_rules, conflict).

    Rules are evaluated in the fixed order R01..R99; first fire wins. All rules
    that fire are recorded in matched_rules for audit and for the conflict flag
    (two independent cues pointing to different categories)."""
    rn = normalize(raw)
    r = strip_urls(rn)
    fired = []   # (rule_id, category)
    hit = None

    def fire(rule_id, category, subcategory, conf):
        nonlocal hit
        fired.append((rule_id, category))
        if hit is None:
            hit = (rule_id, category, subcategory, conf)

    # R01 thesis
    if THESIS_RE.search(r):
        fire("R01_thesis", "other", "thesis", "high")
    # R02 conference
    if hit is None and CONFERENCE_RE.search(r):
        fire("R02_conference", "other", "conference", "high")
    # R03 working paper/preprint
    if hit is None and WP_RE.search(r):
        fire("R03_working_paper", "working_paper", "working_paper", "high")
    # R04-R09: volume/pages fingerprint, adjudicated by its venue segment
    kind, start = _fp_match(r)
    if hit is None and kind is not None:
        vs = venue_segment(r, start)
        jm = JOURNAL_FP and [p for k, p in JOURNAL_FP if k == kind][0].search(r, start)
        vlow = vs.lower()
        if WP_RE.search(vs) or WP_RE.search(r[max(0, start - 200):start]):
            fire("R04_fp_venue_working_paper", "working_paper", "working_paper", "high")
        elif REPORT_RE.search(vs):
            fire("R05_fp_venue_report", "institutional_report", "report", "high")
        elif JOURNAL_VENUE_LIKE.search(vs) or JOURNAL_TOKENS.search(vs):
            fire("R09_journal_fingerprint", "journal_article", "journal_article", "high")
        elif _strict_inst_in(vs):
            fire("R06_fp_venue_institution", "institutional_report", "report", "high")
        elif IN_RE.search(vs) or EDS_RE.search(vs):
            fire("R07_fp_venue_chapter", "book_or_chapter", "book_chapter", "high")
        elif _pub_in(vs):
            fire("R08_fp_venue_publisher", "book_or_chapter", "book", "high")
        else:
            fire("R09_journal_fingerprint", "journal_article", "journal_article", "high")
    # R09b dataset / model output (scientific data products, not reports)
    if hit is None and DATASET_RE.search(r):
        fire("R09b_dataset", "other", "dataset", "medium")
    # R10 explicit report phrase
    if hit is None and REPORT_RE.search(r):
        fire("R10_report_phrase", "institutional_report", "report", "medium")
    # R11 IPCC structural
    if hit is None and (IPCC_AUTHOR_RE.search(r) or IPCC_IN_RE.search(r)
                        or CONTRIBUTION_RE.search(r)
                        or (IPCC_STRUCT_RE.search(r) and not DOI_RE.search(r))):
        fire("R11_ipcc_structural", "institutional_report", "ipcc_report", "high")
    # R12 institutional author at head
    if hit is None and ORG_AUTHOR_RE.search(r):
        fire("R12_org_author", "institutional_report", "report", "high")
    if hit is None and ORG_ACRONYM_RE.search(r) and not _pub_in(r[:80]):
        fire("R12b_org_acronym", "institutional_report", "report", "medium")
    # R13 edited-book chapter
    if hit is None and IN_RE.search(r) and EDS_RE.search(r):
        fire("R13_book_chapter", "book_or_chapter", "book_chapter", "high")
    # R15b named institution in document position with city/pages
    if hit is None and _strict_inst_in(r) and (CITY_RE.search(r) or COUNTRY_RE.search(r) or PP_RE.search(r) or IGO_URL_RE.search(rn)):
        fire("R15b_strict_institution", "institutional_report", "report", "high")
    # R14 publisher + structure
    if hit is None and _pub_in(r) and (CITY_RE.search(r) or COUNTRY_RE.search(r) or PP_RE.search(r) or ISBN_RE.search(r)):
        # a lexicon publisher always indicates a book; a generic '... Press' is
        # overridden when an institution is also named (e.g. 'OECD Publishing')
        if _pub_lex_in(r) or not _inst_in(r):
            fire("R14_book_publisher", "book_or_chapter", "book", "high")
    # R15 ISBN
    if hit is None and ISBN_RE.search(r):
        fire("R15_isbn", "book_or_chapter", "book", "medium")
    # R16 institution + structure / institutional URL
    if hit is None and (_inst_in(r) or IGO_URL_RE.search(rn)):
        structured = bool(CITY_RE.search(r) or COUNTRY_RE.search(r) or PP_RE.search(r) or IGO_URL_RE.search(rn))
        if structured:
            fire("R16_institution", "institutional_report", "report", "medium")
    # R17 bare volume(issue)
    if hit is None and VOL_ISSUE_ONLY.search(r):
        fire("R17_journal_volume_issue", "journal_article", "journal_article", "medium")
    # R18 journal-like venue + DOI
    if hit is None:
        m = VENUE_DOI_RE.search(r)
        if m:
            vs = m.group(1)
            if not _pub_in(vs) and not _inst_in(vs) and not REPORT_RE.search(vs):
                fire("R18_journal_venue_doi", "journal_article", "journal_article", "medium")
    # R19 publisher weak
    if hit is None and _pub_in(r):
        fire("R19_book_publisher_weak", "book_or_chapter", "book", "low")
    # R16c: grey-literature structure without a named type: page count + city +
    # URL and no publisher signal is a published institutional report
    if hit is None and PP_RE.search(r) and (CITY_RE.search(r) or COUNTRY_RE.search(r)) and re.search(r"https?\s*:\s*/\s*/", rn) and not _pub_in(r):
        fire("R16c_grey_structure", "institutional_report", "report", "low")
    # R20 institution weak
    if hit is None and (_inst_in(r) or IGO_URL_RE.search(rn)):
        fire("R20_institution_weak", "institutional_report", "report", "low")
    # R22 doi weak
    if hit is None and DOI_RE.search(r):
        fired.append(("R22_journal_doi_weak", "journal_article"))
        # weak DOI-only: do not claim journal unless a journal-like token is present
        if JOURNAL_TOKENS.search(r):
            fire("R22_journal_doi_weak", "journal_article", "journal_article", "low")
    # R21 other
    if hit is None and OTHER_RE.search(r):
        fire("R21_other_misc", "other", "other", "medium")
    # R99 uncertain
    if hit is None:
        fire("R99_uncertain", "uncertain", "uncertain", "uncertain")

    rule_id, category, subcat, conf = hit
    present = competing_cues(r)
    implied = {FAMILY_CATEGORY[f] for f in present}
    implied.add(category)
    conflict = int(len(implied) > 1)
    return dict(category=category, subcategory=subcat, rule_id=rule_id,
                confidence=conf,
                matched_rules="|".join(present) if present else "",
                conflict=conflict)

def main():
    src = os.path.join(DATA, "occurrences_all.csv")
    dst = os.path.join(DATA, "occurrence_types.csv")
    if "--check" in sys.argv:
        rows = list(csv.DictReader(open(src, encoding="utf-8")))
        dist = Counter(); byrep = Counter()
        for row in rows:
            out = classify(row["raw_reference"])
            dist[out["category"]] += 1
            if row["report_id"] in ("AR5", "AR6"):
                byrep[(row["report_id"], out["category"])] += 1
        tot = sum(dist.values())
        print(f"occurrences classified: {tot:,}")
        for c in CATEGORIES:
            print(f"  {c:22s} {dist[c]:7,} {100*dist[c]/tot:6.2f}%")
        print("\nAR5/AR6:")
        for rep in ("AR5", "AR6"):
            tot_r = sum(v for (rp, c), v in byrep.items() if rp == rep)
            print(f" {rep} n={tot_r:,}")
            for c in CATEGORIES:
                n = byrep[(rep, c)]
                print(f"   {c:22s} {n:7,} {100*n/tot_r:6.2f}%")
        return
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    fields = ["occurrence_id", "report_id", "wg_label", "paper_id", "match_status",
              "category", "subcategory", "rule_id", "confidence", "matched_rules", "conflict"]
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        dist = Counter()
        for row in rows:
            out = classify(row["raw_reference"])
            dist[out["category"]] += 1
            w.writerow(dict(occurrence_id=row["occurrence_id"], report_id=row["report_id"],
                            wg_label=row["wg_label"], paper_id=row["paper_id"],
                            match_status=row["match_status"], **out))
    tot = sum(dist.values())
    print(f"wrote {tot:,} rows -> {dst}")
    for c in CATEGORIES:
        print(f"  {c:22s} {dist[c]:7,} {100*dist[c]/tot:6.2f}%")

if __name__ == "__main__":
    main()
