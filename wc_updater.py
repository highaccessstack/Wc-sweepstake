#!/usr/bin/env python3
"""
wc_updater.py — World Cup 2026 Sweepstake Dashboard Updater

Fetches live results from football-data.org and patches all 3 dashboard HTML files.

Sign up free at https://www.football-data.org/ — no daily limit, covers WC 2026.

Usage:
  python wc_updater.py              # fetch + patch dashboards
  python wc_updater.py --setup      # one-time: inject banter UI into HTML files
  python wc_updater.py --dry-run    # fetch + print results without patching HTML
  python wc_updater.py --force-demo # patch with fake demo data (no API call)

API key priority:
  1. Env var: FOOTBALL_DATA_KEY  (or legacy: API_FOOTBALL_KEY)
  2. File:    api_key.txt (single line, in same directory as this script)
"""

import argparse
import json
import logging
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).parent
HTML_FILES    = [
    SCRIPT_DIR / 'WorldCupDashboard-A.html',
    SCRIPT_DIR / 'WorldCupDashboard-B.html',
    SCRIPT_DIR / 'WorldCupDashboard-C.html',
]
LOG_FILE      = SCRIPT_DIR / 'wc_updater.log'
RESULTS_JSON  = SCRIPT_DIR / 'wc_results.json'
BANTER_TXT    = SCRIPT_DIR / 'wc_banter.txt'
API_KEY_FILE  = SCRIPT_DIR / 'api_key.txt'

# ── API (football-data.org — free tier covers WC) ────────────────────────────
BASE_URL = 'https://api.football-data.org/v4'
# api-football free tier only covers up to season 2024; football-data.org is free for WC

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

# ── Stage system (must match POINTS_MAP in dashboards) ────────────────────────
STAGES = [
    'Group stage exit',
    'Round of 32',
    'Round of 16',
    'Quarterfinals',
    'Semifinals',
    'Final (runner-up)',
    'Champion',
]
STAGE_POINTS = {
    'Group stage exit':   0,
    'Round of 32':        2,
    'Round of 16':        4,
    'Quarterfinals':      6,
    'Semifinals':        10,
    'Final (runner-up)': 15,
    'Champion':          25,
}

# football-data.org stage → stage label assigned to the LOSER
ROUND_TO_STAGE = {
    'ROUND_OF_32':    'Round of 32',
    'ROUND_OF_16':    'Round of 16',
    'QUARTER_FINALS': 'Quarterfinals',
    'SEMI_FINALS':    'Semifinals',
    # 3rd place loser = 4th place, same Semifinals points
    'THIRD_PLACE':    'Semifinals',
    # FINAL is handled separately: winner=Champion, loser=Final (runner-up)
}

# ── Team name normalisation ───────────────────────────────────────────────────
# football-data.org may use different spellings; map to our dashboard names
API_NAME_MAP = {
    'Turkey':                        'T\u00FCrkiye',
    'Turkiye':                       'T\u00FCrkiye',
    "Côte d'Ivoire":                 'C\u00F4te d\u2019Ivoire',
    "Cote d'Ivoire":                 'C\u00F4te d\u2019Ivoire',
    'Ivory Coast':                   'C\u00F4te d\u2019Ivoire',
    "Côte D'Ivoire":                 'C\u00F4te d\u2019Ivoire',
    'Curacao':                       'Cura\u00E7ao',
    'Curaçao':                       'Cura\u00E7ao',
    'Czech Republic':                'Czechia',
    'Bosnia-Herzegovina':            'Bosnia and Herzegovina',
    'Bosnia & Herzegovina':          'Bosnia and Herzegovina',
    'Congo DR':                      'DR Congo',
    'Democratic Republic of Congo':  'DR Congo',
    'Korea Republic':                'South Korea',
    'Korea South':                   'South Korea',
    'Republic of Korea':             'South Korea',
    'United States':                 'USA',
    'USA':                           'USA',
    'New Zealand':                   'New Zealand',
}

ALL_TEAMS = {
    'England', 'Switzerland', 'Panama', 'Iraq',
    'Netherlands', 'Australia', 'Qatar', 'DR Congo',
    'Brazil', 'Senegal', 'Paraguay', 'Ghana',
    'Argentina', 'Canada', 'Scotland', 'T\u00FCrkiye',
    'Germany', 'Uruguay', 'Egypt', 'Cura\u00E7ao',
    'Morocco', 'Iran', 'Uzbekistan', 'Jordan',
    'Portugal', 'Ecuador', 'C\u00F4te d\u2019Ivoire', 'Sweden',
    'Spain', 'Austria', 'Norway', 'Cape Verde',
    'France', 'Japan', 'Algeria', 'Czechia',
    'Colombia', 'USA', 'Tunisia', 'New Zealand',
    'Croatia', 'South Korea', 'South Africa', 'Bosnia and Herzegovina',
    'Belgium', 'Mexico', 'Saudi Arabia', 'Haiti',
}

PARTICIPANTS = [
    {'name': 'Pritish', 'teams': ['England', 'Switzerland', 'Panama', 'Iraq']},
    {'name': 'Steve',   'teams': ['Netherlands', 'Australia', 'Qatar', 'DR Congo']},
    {'name': 'Damir',   'teams': ['Brazil', 'Senegal', 'Paraguay', 'Ghana']},
    {'name': 'Hugh',    'teams': ['Argentina', 'Canada', 'Scotland', 'T\u00FCrkiye']},
    {'name': 'Adam',    'teams': ['Germany', 'Uruguay', 'Egypt', 'Cura\u00E7ao']},
    {'name': 'James',   'teams': ['Morocco', 'Iran', 'Uzbekistan', 'Jordan']},
    {'name': 'Pardeep', 'teams': ['Portugal', 'Ecuador', 'C\u00F4te d\u2019Ivoire', 'Sweden']},
    {'name': 'Ajit',    'teams': ['Spain', 'Austria', 'Norway', 'Cape Verde']},
    {'name': 'Nuno',    'teams': ['France', 'Japan', 'Algeria', 'Czechia']},
    {'name': 'Quentin', 'teams': ['Colombia', 'USA', 'Tunisia', 'New Zealand']},
    {'name': 'Nomso',   'teams': ['Croatia', 'South Korea', 'South Africa', 'Bosnia and Herzegovina']},
    {'name': 'Mark',    'teams': ['Belgium', 'Mexico', 'Saudi Arabia', 'Haiti']},
]

TEAM_OWNER = {t: p['name'] for p in PARTICIPANTS for t in p['teams']}

# ── Banter strings ────────────────────────────────────────────────────────────
# Keyed by stage. {team} and {owner} are substituted at runtime.
BANTER_BY_STAGE = {
    'Group stage exit': [
        "{team} gone in the groups. {owner}\u2019s already googling \u2018how to fake enjoying a tournament you\u2019re out of\u2019.",
        "Pack your bags, {team}. {owner} has transitioned seamlessly into pure spectator mode.",
        "{team} finish dead last. {owner} claims they had them as a \u2018wildcard pick\u2019 all along.",
        "{team} out in the groups. {owner} is statistically the dead weight of this sweepstake.",
        "Group stage exit for {team}. {owner} is now rooting for literally anyone else\u2019s teams.",
        "Three games, zero advancement. {owner}\u2019s \u2018strategy\u2019 of picking {team} is not paying dividends.",
    ],
    'Round of 32': [
        "{team} fall at the Round of 32. {owner} takes home 2 consolation points and absolutely nothing else.",
        "R32 exit for {team}. {owner} can at least say they made the knockouts. That\u2019s something.",
        "{team} out first knockout round. {owner} is bravely calling it \u2018a successful campaign\u2019.",
        "Two points for {owner} as {team} bow out. Better than nothing. Barely.",
    ],
    'Round of 16': [
        "{team} bow out at the Round of 16. {owner} puts on their best stoic face.",
        "Last 16 for {team} \u2014 4 points for {owner}. Could be worse, could be much better.",
        "{team} gave it a go before the R16 exit. {owner} quietly accepts their fate.",
    ],
    'Quarterfinals': [
        "{team} out at the Quarters! {owner} insists they were robbed by VAR.",
        "Quarter-final exit for {team}. {owner} had one hand on the trophy. Now they have nothing.",
        "{team} so close to the semis. {owner} will be dining out on this for months.",
    ],
    'Semifinals': [
        "{team} reach the semis \u2014 10 points for {owner}! Absolute scenes.",
        "Semi-final run from {team}! {owner} is starting to look dangerously confident.",
        "{team} in the last four. {owner} has genuinely started believing. Dangerous.",
    ],
    'Final (runner-up)': [
        "Silver medal! {team} were THIS close. {owner} banks 15 points and a lifetime of \u2018what ifs\u2019.",
        "{team} in the Final! {owner} takes 15 points but the real prize was the memories. Probably.",
        "Runners-up! {team} fell at the final hurdle. {owner} is coping. Sort of.",
    ],
    'Champion': [
        "\U0001f3c6 CHAMPIONS! {team} WIN THE WORLD CUP! {owner} wins the sweepstake and is contractually obligated to buy a round.",
        "{team} are WORLD CHAMPIONS! {owner} has achieved immortality. 25 points. The pot is theirs.",
        "IT\u2019S {team}! World Cup winners 2026! {owner}, take a bow. Absolute hero. Drinks on you.",
    ],
}

GENERAL_BANTER = [
    "World Cup 2026 update: the sweepstake drama continues. No refunds.",
    "Another update from the tournament. Someone\u2019s about to get very happy and someone\u2019s about to be insufferable about it.",
    "Live results in. The leaderboard has been updated. Manage your expectations accordingly.",
    "Fresh scores. Fresh heartbreak. Fresh hope. It\u2019s the World Cup.",
    "The beautiful game has spoken. Whether you like what it said is another matter.",
    "Results updated. If your teams are doing well: don\u2019t be smug. If they\u2019re not: condolences.",
]

# ── Rivalry banter: Aus vs Poms, Aus vs Kiwis ────────────────────────────────
STAGE_RANK = {s: i for i, s in enumerate(STAGES)}

RIVALRY_BANTER = {
    'aus_beats_eng': [
        "Australia went further than England! Steve is composing his victory email as we speak. The Ashes are healed.",
        "The Poms out before the Aussies \u2014 fair dinkum! Steve\u2019s walking taller than ever. Pritish, how\u2019s that for a sledge?",
        "Strewth! Australia outlasted England. Steve is sending Pritish a cricket bat emoji right now.",
        "Australia did the Poms over. Steve\u2019s having a ripper time. Pritish is \u2018very philosophical about it\u2019.",
        "England eliminated before Australia. Steve from down under would like Pritish to know he\u2019s \u2018not bothered at all\u2019. He\u2019s extremely bothered.",
        "The Aussies outlasted the Poms. Mate, you love to see it. Steve does. Pritish does not.",
    ],
    'eng_beats_aus': [
        "England went further than Australia. Pritish is quietly insufferable. Steve needs several beers.",
        "The Poms outlasted the Aussies. Steve pretends not to care. He cares enormously.",
        "Australia out before England. The Ashes wound is real for Steve. She\u2019ll be right, mate. (It won\u2019t be right.)",
        "England surviving while Australia didn\u2019t \u2014 Pritish is doing laps of the office. Steve is \u2018fine\u2019.",
        "The Poms are still in it and the Aussies aren\u2019t. Steve has gone very quiet. Very unlike him.",
    ],
    'aus_eng_same': [
        "Australia and England both eliminated at the same stage. Steve and Pritish are united in shared misery. Beautiful.",
        "The Poms and the Aussies go out together. Steve and Pritish can console each other over a beer. Or argue about cricket. Same thing.",
        "Both Australia and England gone at the same round. No bragging rights for either. Steve and Pritish both insufferable for different reasons.",
    ],
    'aus_beats_nz': [
        "Australia outlasted New Zealand! Steve is already texting Quentin across the Tasman ditch.",
        "The Kiwis on the plane home before the Aussies. Trans-Tasman pride: Steve 1, Quentin 0.",
        "New Zealand out before Australia. Steve is being annoyingly smug about the whole thing. Crikey.",
        "Quentin\u2019s New Zealand knocked out before Steve\u2019s Australia. The sheep are not celebrating tonight.",
        "Australia still alive, New Zealand not. Steve\u2019s activated full Kiwi-sledge mode. It\u2019s something to witness.",
        "Aussies go further than the Kiwis. Steve: \u2018No hard feelings, Quentin.\u2019 (There are feelings.)",
    ],
    'nz_beats_aus': [
        "New Zealand outlasted Australia! Even the Kiwis went further, Steve. How does that feel, mate?",
        "Quentin\u2019s New Zealand survived longer than Steve\u2019s Australia. The Tasman rivalry: Kiwis win this round.",
        "Steve\u2019s Aussies out before NZ. The sheep are celebrating across the ditch. Quentin is absolutely delighted.",
        "Australia knocked out before New Zealand. Steve is blaming the draw, the ref, the pitch, and everything except his own teams.",
        "NZ going further than Australia \u2014 Steve\u2019ll be hearing about this from Quentin for the rest of the tournament. And beyond.",
    ],
    'aus_nz_same': [
        "Australia and New Zealand both out at the same stage. Southern Hemisphere solidarity in defeat. Steve and Quentin nod at each other solemnly.",
        "The Aussies and Kiwis go home together. Steve and Quentin can argue about rugby on the way out.",
        "Both Australia and NZ eliminated at the same round. The Tasman derby ends in a draw. Nobody wins. Nobody is happy.",
    ],
}

def build_rivalry_banter(results):
    """Generate cross-team rivalry lines for Aus/Eng and Aus/NZ derbies."""
    def rank(team):   return STAGE_RANK.get(results.get(team, {}).get('stage', 'Group stage exit'), 0)
    def played(team): r = results.get(team, {}); return r.get('gf', 0) + r.get('ga', 0) > 0

    aus_r = rank('Australia')
    eng_r = rank('England')
    nz_r  = rank('New Zealand')
    lines = []

    if played('Australia') and played('England'):
        if aus_r > eng_r:
            lines.append(random.choice(RIVALRY_BANTER['aus_beats_eng']))
        elif eng_r > aus_r:
            lines.append(random.choice(RIVALRY_BANTER['eng_beats_aus']))
        elif aus_r == eng_r and aus_r > 0:
            lines.append(random.choice(RIVALRY_BANTER['aus_eng_same']))

    if played('Australia') and played('New Zealand'):
        if aus_r > nz_r:
            lines.append(random.choice(RIVALRY_BANTER['aus_beats_nz']))
        elif nz_r > aus_r:
            lines.append(random.choice(RIVALRY_BANTER['nz_beats_aus']))
        elif aus_r == nz_r and aus_r > 0:
            lines.append(random.choice(RIVALRY_BANTER['aus_nz_same']))

    return lines

# ── API helpers ───────────────────────────────────────────────────────────────
def _load_api_key():
    # Support both old and new env var names
    key = (os.environ.get('API_FOOTBALL_KEY', '') or
           os.environ.get('FOOTBALL_DATA_KEY', '')).strip()
    if key:
        return key
    if API_KEY_FILE.exists():
        key = API_KEY_FILE.read_text(encoding='utf-8').strip()
        if key:
            return key
    return None

def api_get(endpoint, params=None, api_key=None):
    url = f'{BASE_URL}/{endpoint}'
    headers = {'X-Auth-Token': api_key}
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=30)
    except requests.exceptions.SSLError:
        import urllib3; urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.warning('SSL verification failed (likely corporate proxy) — retrying without SSL verify')
        resp = requests.get(url, headers=headers, params=params or {}, timeout=30, verify=False)
    resp.raise_for_status()
    data = resp.json()
    matches = data.get('matches', [])
    log.info('GET %s -> %d matches', endpoint, len(matches))
    return matches

def normalise(api_name):
    return API_NAME_MAP.get(api_name, api_name)

# ── Core: derive RESULTS_DATA from raw fixtures ───────────────────────────────
def compute_results(fixtures):
    """
    Walk all completed fixtures to determine each team's furthest stage reached.
    Uses football-data.org match format (stage: GROUP_STAGE, ROUND_OF_32, etc.)
    """
    gf = {t: 0 for t in ALL_TEAMS}
    ga = {t: 0 for t in ALL_TEAMS}

    ko_participants = {}   # stage_key → set of teams
    ko_losers = {}         # stage_key → set of teams
    final_winner = None
    final_loser  = None

    KO_ORDER = ['ROUND_OF_32', 'ROUND_OF_16', 'QUARTER_FINALS', 'SEMI_FINALS', 'THIRD_PLACE', 'FINAL']
    PREV_STAGE = {
        'ROUND_OF_32':    'Group stage exit',
        'ROUND_OF_16':    'Round of 32',
        'QUARTER_FINALS': 'Round of 16',
        'SEMI_FINALS':    'Quarterfinals',
        'THIRD_PLACE':    'Semifinals',
    }

    for f in fixtures:
        stage  = f.get('stage', '')
        status = f.get('status', '')
        home   = normalise(f['homeTeam']['name'])
        away   = normalise(f['awayTeam']['name'])
        hg     = f['score']['fullTime'].get('home') or 0
        ag     = f['score']['fullTime'].get('away') or 0

        is_finished = status == 'FINISHED'

        if stage == 'GROUP_STAGE':
            if is_finished:
                if home in ALL_TEAMS:
                    gf[home] += hg; ga[home] += ag
                if away in ALL_TEAMS:
                    gf[away] += ag; ga[away] += hg
        else:
            ko_participants.setdefault(stage, set())
            if home in ALL_TEAMS: ko_participants[stage].add(home)
            if away in ALL_TEAMS: ko_participants[stage].add(away)

            if is_finished:
                if home in ALL_TEAMS:
                    gf[home] += hg; ga[home] += ag
                if away in ALL_TEAMS:
                    gf[away] += ag; ga[away] += hg

                winner_side = f['score'].get('winner')  # 'HOME_TEAM', 'AWAY_TEAM', 'DRAW'
                if winner_side == 'HOME_TEAM':
                    winner, loser = home, away
                elif winner_side == 'AWAY_TEAM':
                    winner, loser = away, home
                else:
                    winner, loser = None, None

                if stage == 'FINAL':
                    if winner and winner in ALL_TEAMS: final_winner = winner
                    if loser  and loser  in ALL_TEAMS: final_loser  = loser
                else:
                    ko_losers.setdefault(stage, set())
                    if loser and loser in ALL_TEAMS:
                        ko_losers[stage].add(loser)

    results = {}
    for team in ALL_TEAMS:
        stage = 'Group stage exit'

        highest_ko = None
        for rnd in KO_ORDER:
            if team in ko_participants.get(rnd, set()):
                highest_ko = rnd

        if team == final_winner:
            stage = 'Champion'
        elif team == final_loser:
            stage = 'Final (runner-up)'
        elif highest_ko and highest_ko != 'FINAL':
            if team in ko_losers.get(highest_ko, set()):
                stage = ROUND_TO_STAGE.get(highest_ko, 'Group stage exit')
            else:
                # Still alive — show the stage they survived to reach this round
                stage = PREV_STAGE.get(highest_ko, 'Group stage exit')

        results[team] = {'stage': stage, 'gf': gf[team], 'ga': ga[team]}

    return results

# ── Banter generation ─────────────────────────────────────────────────────────
def build_banter_feed(results):
    """Return list of banter strings for injection into the dashboard."""
    feed = []

    # Add general opener
    feed.append(random.choice(GENERAL_BANTER))

    # Group-exit banter only fires once knockouts are confirmed started
    # (so mid-group-stage teams never get prematurely roasted)
    knockout_started = any(r['stage'] != 'Group stage exit' for r in results.values())

    for team, r in sorted(results.items()):
        stage = r['stage']
        owner = TEAM_OWNER.get(team, 'Someone')
        templates = BANTER_BY_STAGE.get(stage, [])
        if not templates:
            continue

        if stage == 'Group stage exit':
            # Skip: tournament hasn't reached knockouts yet, or team has no results at all
            if not knockout_started or (r['gf'] == 0 and r['ga'] == 0):
                continue
        else:
            # Skip knockout-stage teams with zero stats (pre-tournament or not yet played)
            if r['gf'] == 0 and r['ga'] == 0 and stage not in ('Champion', 'Final (runner-up)'):
                continue

        banter = random.choice(templates).format(team=team, owner=owner)
        feed.append(banter)

    # Aus/Pom/Kiwi rivalry lines
    feed.extend(build_rivalry_banter(results))

    # Leaderboard snapshot banter
    scored = [(TEAM_OWNER.get(t,'?'), sum(STAGE_POINTS.get(r['stage'],0) for r in [rv] if r)
               ) for t, rv in results.items()]
    # Build per-participant totals
    totals = {}
    for p in PARTICIPANTS:
        pts = sum(STAGE_POINTS.get(results.get(t, {}).get('stage', 'Group stage exit'), 0)
                  for t in p['teams'])
        totals[p['name']] = pts

    sorted_leaders = sorted(totals.items(), key=lambda x: -x[1])
    if sorted_leaders:
        leader_name, leader_pts = sorted_leaders[0]
        if leader_pts > 0:
            feed.append(
                f"Current leader: {leader_name} with {leader_pts} points. "
                f"Everyone else is playing for second place."
            )
        if len(sorted_leaders) > 1:
            last_name, last_pts = sorted_leaders[-1]
            feed.append(
                f"Bottom of the table: {last_name} on {last_pts} points. "
                f"The floor is lava and {last_name} is standing in it."
            )

    random.shuffle(feed[1:])  # keep the opener first, shuffle the rest
    return feed

# ── HTML patching ─────────────────────────────────────────────────────────────
def _js_str(s):
    """Escape a string for inclusion in a single-quoted JS string literal."""
    return s.replace('\\', '\\\\').replace("'", "\\'")

def patch_results_data(content, results):
    """Replace const RESULTS_DATA = {...}; in the HTML."""
    lines = ['const RESULTS_DATA = {']
    for team in sorted(results.keys()):
        r = results[team]
        lines.append(f"  '{_js_str(team)}':{{stage:'{r['stage']}',gf:{r['gf']},ga:{r['ga']}}},")
    lines.append('};')
    new_block = '\n'.join(lines)

    pattern = r'const RESULTS_DATA\s*=\s*\{[\s\S]*?\};'
    new_content, count = re.subn(pattern, new_block, content, count=1)
    if count == 0:
        log.warning('RESULTS_DATA block not found')
        return content, False
    return new_content, True

def patch_banter_feed(content, feed):
    """Replace const BANTER_FEED = [...]; in the HTML."""
    items = ',\n  '.join(f"'{_js_str(s)}'" for s in feed)
    new_block = f'const BANTER_FEED = [\n  {items}\n];'

    pattern = r'const BANTER_FEED\s*=\s*\[[\s\S]*?\];'
    new_content, count = re.subn(pattern, new_block, content, count=1)
    if count == 0:
        log.warning('BANTER_FEED block not found — run with --setup first')
        return content, False
    return new_content, True

def patch_last_updated(content, ts):
    """Replace const LAST_UPDATED = '...'; in the HTML."""
    new_line = f"const LAST_UPDATED = '{ts}';"
    pattern  = r"const LAST_UPDATED\s*=\s*'[^']*';"
    new_content, count = re.subn(pattern, new_line, content, count=1)
    if count == 0:
        log.warning('LAST_UPDATED not found — run with --setup first')
        return content, False
    return new_content, True

def patch_html_file(filepath, results, feed, ts):
    path    = Path(filepath)
    content = path.read_text(encoding='utf-8')
    changed = False

    content, ok = patch_results_data(content, results); changed = changed or ok
    content, ok = patch_banter_feed(content, feed);     changed = changed or ok
    content, ok = patch_last_updated(content, ts);      changed = changed or ok

    if changed:
        path.write_text(content, encoding='utf-8')
        log.info('Patched  %s', path.name)
    else:
        log.info('No change %s', path.name)
    return changed

# ── One-time setup: inject banter UI into HTML files ─────────────────────────
SETUP_JS_VARS = """\
const BANTER_FEED = ['Welcome to the World Cup 2026 sweepstake! Results coming soon...'];
const LAST_UPDATED = '';
"""

# Replacement footer that shows banter + timestamp inside the existing template literal
_FOOTER_OLD = '<footer class="footer">Results update via RESULTS_DATA &middot; All times HKT (UTC+8)</footer>'
_FOOTER_NEW = (
    '<footer class="footer">'
    '<div id="banter-msg" style="font-style:italic;opacity:.85;margin-bottom:4px;min-height:1.2em;">'
    '${BANTER_FEED[0]||\'\'}</div>'
    '<small style="opacity:.55;font-size:.78em;">'
    '&#8635; ${LAST_UPDATED||\'-\'} &middot; All times HKT (UTC+8)'
    '</small></footer>'
)

# Banter rotation — injected once, appended after the existing setInterval block
_BANTER_TICKER_JS = """\

/* banter ticker: rotate every 13 seconds without triggering renderAll */
(function(){
  let _bi=0;
  setInterval(function(){
    const el=document.getElementById('banter-msg');
    if(el&&BANTER_FEED.length){
      _bi=(_bi+1)%BANTER_FEED.length;
      el.textContent=BANTER_FEED[_bi];
    }
  },13000);
})();
"""

def setup_html_file(filepath):
    """One-time: inject BANTER_FEED, LAST_UPDATED, footer banter, and ticker JS."""
    path    = Path(filepath)
    content = path.read_text(encoding='utf-8')

    if 'BANTER_FEED' in content:
        log.info('Already set up: %s', path.name)
        return False

    # 1. Inject JS vars immediately before `const RESULTS_DATA`
    content = content.replace(
        'const RESULTS_DATA =',
        SETUP_JS_VARS + 'const RESULTS_DATA =',
        1
    )

    # 2. Replace footer inside the template literal
    content = content.replace(_FOOTER_OLD, _FOOTER_NEW, 1)

    # 3. Append banter ticker before the closing </script> tag (last one)
    content = content.rstrip()
    # Insert before the final </script>
    last_script = content.rfind('</script>')
    if last_script != -1:
        content = content[:last_script] + _BANTER_TICKER_JS + content[last_script:]

    path.write_text(content, encoding='utf-8')
    log.info('Set up    %s', path.name)
    return True

# ── Demo / dry-run helpers ────────────────────────────────────────────────────
def _demo_results():
    """Fake mid-tournament results for testing."""
    demo = {t: {'stage': 'Group stage exit', 'gf': 0, 'ga': 0} for t in ALL_TEAMS}
    # Some group exits
    for t in ['Panama', 'Iraq', 'Qatar', 'Haiti', 'Curaçao', 'Jordan',
              'New Zealand', 'Cape Verde', 'Uzbekistan', 'Scotland',
              'South Africa', 'Czech Republic']:
        if t in demo: demo[t] = {'stage': 'Group stage exit', 'gf': 1, 'ga': 3}
    # R32
    for t in ['Australia', 'DR Congo', 'Paraguay', 'Egypt', 'Sweden',
              'Algeria', 'Tunisia', 'Croatia', 'Saudi Arabia', 'Bosnia and Herzegovina']:
        if t in demo: demo[t] = {'stage': 'Round of 32', 'gf': 2, 'ga': 3}
    # R16
    for t in ['Switzerland', 'Ghana', 'Mexico', 'Ecuador', 'South Korea',
              'Senegal', 'Czechia', 'Iran']:
        if t in demo: demo[t] = {'stage': 'Round of 16', 'gf': 4, 'ga': 5}
    # QF
    for t in ['Canada', 'Uruguay', 'Japan', 'Colombia']:
        if t in demo: demo[t] = {'stage': 'Quarterfinals', 'gf': 6, 'ga': 7}
    # SF
    for t in ['Germany', 'Belgium', 'Morocco', 'Spain']:
        if t in demo: demo[t] = {'stage': 'Semifinals', 'gf': 8, 'ga': 9}
    # Final
    demo['Brazil']    = {'stage': 'Final (runner-up)', 'gf': 12, 'ga': 5}
    demo['Argentina'] = {'stage': 'Champion', 'gf': 14, 'ga': 4}
    return demo

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='WC 2026 Sweepstake Dashboard Updater')
    parser.add_argument('--setup',      action='store_true', help='One-time: inject banter UI into HTML files')
    parser.add_argument('--dry-run',    action='store_true', help='Fetch + print results, do not patch HTML')
    parser.add_argument('--force-demo', action='store_true', help='Use fake demo data (no API call)')
    args = parser.parse_args()

    log.info('=== WC Updater starting (setup=%s dry-run=%s demo=%s) ===',
             args.setup, args.dry_run, args.force_demo)

    # ── One-time setup mode ──────────────────────────────────────────────────
    if args.setup:
        for f in HTML_FILES:
            if f.exists():
                setup_html_file(f)
            else:
                log.warning('Not found: %s', f)
        log.info('Setup complete. Run without --setup to fetch results.')
        return

    # ── Resolve API key ──────────────────────────────────────────────────────
    api_key = None
    if not args.force_demo:
        api_key = _load_api_key()
        if not api_key:
            log.error(
                'No API key found.\n'
                '  Option A: set env var  FOOTBALL_DATA_KEY=your_key\n'
                '  Option B: create file  api_key.txt  with your key on one line\n'
                '  Option C: run with     --force-demo  (no API, fake data)\n'
                '  Sign up free at https://www.football-data.org/'
            )
            sys.exit(1)

    # ── Fetch or use demo data ───────────────────────────────────────────────
    if args.force_demo:
        log.info('Using demo data (--force-demo)')
        results = _demo_results()
    else:
        try:
            fixtures = api_get('competitions/WC/matches', api_key=api_key)
            log.info('Fetched %d fixtures', len(fixtures))
        except Exception as e:
            log.error('API fetch failed: %s', e)
            sys.exit(1)
        results = compute_results(fixtures)
        log.info('Computed results for %d teams', len(results))

    # ── Save debug JSON ──────────────────────────────────────────────────────
    RESULTS_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')

    # ── Dry-run: just print ──────────────────────────────────────────────────
    if args.dry_run:
        print('\n=== RESULTS_DATA (dry-run) ===')
        for team, r in sorted(results.items()):
            print(f"  {team:30s} {r['stage']:20s}  GF:{r['gf']}  GA:{r['ga']}")
        return

    # ── Build banter feed ────────────────────────────────────────────────────
    feed = build_banter_feed(results)
    ts   = datetime.now(timezone.utc).strftime('%d %b %H:%M UTC')

    # ── Patch HTML files ─────────────────────────────────────────────────────
    patched = 0
    for f in HTML_FILES:
        if f.exists():
            if patch_html_file(f, results, feed, ts):
                patched += 1
        else:
            log.warning('Not found: %s', f)

    log.info('Patched %d/%d dashboard files', patched, len(HTML_FILES))

    # ── Save banter log ──────────────────────────────────────────────────────
    banter_lines = [f'=== WC 2026 Sweepstake — {ts} ===\n']
    totals = {}
    for p in PARTICIPANTS:
        pts = sum(STAGE_POINTS.get(results.get(t, {}).get('stage', 'Group stage exit'), 0)
                  for t in p['teams'])
        totals[p['name']] = pts

    for name, pts in sorted(totals.items(), key=lambda x: -x[1]):
        teams_str = ', '.join(
            f"{t} ({results.get(t,{}).get('stage','?')})" for t in
            next(p['teams'] for p in PARTICIPANTS if p['name'] == name)
        )
        banter_lines.append(f"  {pts:3d}pts  {name:10s}  {teams_str}")

    banter_lines.append('\nBanter feed:')
    banter_lines.extend(f'  {b}' for b in feed)
    BANTER_TXT.write_text('\n'.join(banter_lines), encoding='utf-8')
    log.info('Banter log -> %s', BANTER_TXT.name)
    log.info('=== WC Updater done ===')


if __name__ == '__main__':
    main()
