"""Lister Stats: what was listed through the Sweet Shelves Lister side panel, per day and per person.

Reads what the panel already records - listing_links (a listing recorded on a store), lister_queue_state
(the X / skip on a store list), lister_choices (category and catalog-match picks on eBay's steps) and the
'Lister ...' rows of ai_usage.db - so there is nothing new to write. Shared verbatim with debby app.py
through lister_routes.register.
"""
import datetime
import sqlite3
from pathlib import Path

from flask import jsonify, render_template, request

RANGES = (7, 30, 90, 365)
AI_KINDS = {'Lister title': 'title', 'Lister description': 'description', 'Lister AI photoshop': 'photo'}


def _parse(stamp):
    text = str(stamp or '').strip().replace(' ', 'T')
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text[:19])
    except ValueError:
        return None


def _utc_to_local(stamp):
    """ai_usage.db stores UTC; the lister tables store the server's local time."""
    moment = _parse(stamp)
    if moment is None:
        return None
    return moment.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)


def _actor(value):
    value = str(value or '').strip()
    return '' if value.lower() in ('', 'lister', 'extension') else value


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows(cur, query, args=()):
    cur.execute(query, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


class ListerStats:
    def __init__(self, lister, ai_db_path=None):
        self.lister = lister
        self.ai_db_path = ai_db_path

    def _ai_path(self):
        if self.ai_db_path:
            return Path(self.ai_db_path)
        try:
            import ai_usage
            return Path(ai_usage.DB_PATH)
        except Exception:
            return None

    def _collect(self):
        links, skips, choices, waiting = [], [], [], 0
        with self.lister.db('listagent.db') as conn:
            cur = conn.cursor()
            self.lister.init_tables(cur)
            # Only listings the panel took from start to end count as the Lister's output; rows for a
            # listing the store already carried ("Use that listing", kind = 'existing') are not ours.
            links = _rows(cur, '''SELECT id, upc, platform, listing_id, sku, asin, title, price, quantity, url, created_by, created_at
                                  FROM listing_links WHERE COALESCE(NULLIF(TRIM(kind), ''), 'listed') <> 'existing'
                                  ORDER BY created_at, id''')
            skips = _rows(cur, "SELECT upc, platform, actor, created_at FROM lister_queue_state WHERE state = 'skipped'")
            choices = _rows(cur, 'SELECT upc, base_upc, platform, step, chosen, suggested, actor, created_at FROM lister_choices')
            if self.lister._has_table(cur, 'listing_queue'):
                cur.execute("SELECT COUNT(*) FROM listing_queue WHERE status = 'queued'")
                waiting = cur.fetchone()[0] or 0
        ai = []
        path = self._ai_path()
        if path and path.is_file():
            try:
                conn = sqlite3.connect(str(path), timeout=5)
                try:
                    ai = _rows(conn.cursor(), "SELECT created_at, feature, cost_usd FROM ai_usage WHERE feature LIKE 'Lister %'")
                finally:
                    conn.close()
            except sqlite3.Error:
                ai = []
        return links, skips, choices, waiting, ai

    def _sales(self, links):
        """Orders that came back for each linked listing (same matching as the ledger)."""
        if not links:
            return {}
        try:
            entries = self.lister.ledger(limit=max(len(links), 1) + 50)
        except Exception:
            return {}
        return {e['id']: e.get('sales') or [] for e in entries}

    def compute(self, *, days=30, actor='', today=None):
        days = days if days in RANGES else 30
        today = today or datetime.date.today()
        start = today - datetime.timedelta(days=days - 1)
        week_start = today - datetime.timedelta(days=6)
        actor = _actor(actor)
        links, skips, choices, waiting, ai = self._collect()

        def keep(who):
            return not actor or _actor(who) == actor

        people = sorted(({_actor(r.get('created_by')) for r in links} | {_actor(r.get('actor')) for r in skips}
                         | {_actor(r.get('actor')) for r in choices}) - {''})
        sales = self._sales(links)

        series = {}
        for offset in range(days):
            day = start + datetime.timedelta(days=offset)
            series[day] = {'date': day.isoformat(), 'ebay': 0, 'amazon': 0, 'listed': 0, 'value': 0.0,
                           'skipped': 0, 'worked': set(), 'ai': 0}
        totals = {name: {'listed': 0, 'ebay': 0, 'amazon': 0, 'value': 0.0, 'skipped': 0, 'worked': set()}
                  for name in ('today', 'week', 'range', 'all')}
        users = {}
        listings = []
        today_times = []
        sold = {'listings': 0, 'orders': 0, 'revenue': 0.0}

        def user(name):
            return users.setdefault(name or '', {'actor': name or '', 'listed': 0, 'ebay': 0, 'amazon': 0, 'value': 0.0,
                                                 'skipped': 0, 'worked': set(), 'today': 0, 'last_at': ''})

        def buckets(day):
            out = [totals['all']]
            if day is not None and start <= day <= today:
                out.append(totals['range'])
            if day is not None and week_start <= day <= today:
                out.append(totals['week'])
            if day == today:
                out.append(totals['today'])
            return out

        for link in links:
            if not keep(link.get('created_by')):
                continue
            moment = _parse(link.get('created_at'))
            day = moment.date() if moment else None
            platform = link.get('platform') if link.get('platform') in ('ebay', 'amazon') else 'ebay'
            price = _num(link.get('price'))
            qty = link.get('quantity') if isinstance(link.get('quantity'), int) and link.get('quantity') > 0 else 1
            value = round(price * qty, 2) if price is not None else 0.0
            orders = sales.get(link['id'], [])
            for bucket in buckets(day):
                bucket['listed'] += 1
                bucket[platform] += 1
                bucket['value'] += value
                bucket['worked'].add(link.get('upc'))
            in_range = day is not None and start <= day <= today
            if in_range:
                entry = series[day]
                entry['listed'] += 1
                entry[platform] += 1
                entry['value'] += value
                entry['worked'].add(link.get('upc'))
                who = user(_actor(link.get('created_by')))
                who['listed'] += 1
                who[platform] += 1
                who['value'] += value
                who['worked'].add(link.get('upc'))
                who['today'] += 1 if day == today else 0
                who['last_at'] = max(who['last_at'], link.get('created_at') or '')
                if orders:
                    sold['listings'] += 1
                    sold['orders'] += len(orders)
                    sold['revenue'] += sum((_num(o.get('price')) or 0) * (o.get('quantity') or 1) for o in orders)
                listings.append({
                    'id': link['id'], 'date': day.isoformat(), 'at': link.get('created_at'), 'platform': platform,
                    'upc': link.get('upc'), 'title': link.get('title') or '', 'price': price, 'quantity': qty,
                    'key': link.get('listing_id') or link.get('sku') or link.get('asin') or '', 'url': link.get('url') or '',
                    'actor': _actor(link.get('created_by')), 'sold': len(orders),
                })
            if day == today and moment:
                today_times.append(moment)

        for skip in skips:
            if not keep(skip.get('actor')):
                continue
            moment = _parse(skip.get('created_at'))
            day = moment.date() if moment else None
            for bucket in buckets(day):
                bucket['skipped'] += 1
                bucket['worked'].add(skip.get('upc'))
            if day is not None and start <= day <= today:
                series[day]['skipped'] += 1
                series[day]['worked'].add(skip.get('upc'))
                who = user(_actor(skip.get('actor')))
                who['skipped'] += 1
                who['worked'].add(skip.get('upc'))

        suggestions = {'asked': 0, 'agreed': 0}
        for choice in choices:
            if not keep(choice.get('actor')):
                continue
            moment = _parse(choice.get('created_at'))
            day = moment.date() if moment else None
            for bucket in buckets(day):
                bucket['worked'].add(choice.get('upc'))
            if day is not None and start <= day <= today:
                series[day]['worked'].add(choice.get('upc'))
                user(_actor(choice.get('actor')))['worked'].add(choice.get('upc'))
                if str(choice.get('suggested') or '').strip():
                    suggestions['asked'] += 1
                    if str(choice.get('suggested')).strip().lower() == str(choice.get('chosen') or '').strip().lower():
                        suggestions['agreed'] += 1

        ai_totals = {'title': 0, 'description': 0, 'photo': 0, 'cost': 0.0}
        if not actor:  # ai_usage has no person on it
            for row in ai:
                moment = _utc_to_local(row.get('created_at'))
                kind = AI_KINDS.get(row.get('feature'))
                if moment is None or kind is None or not (start <= moment.date() <= today):
                    continue
                series[moment.date()]['ai'] += 1
                ai_totals[kind] += 1
                ai_totals['cost'] += _num(row.get('cost_usd')) or 0

        today_times.sort()
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(today_times, today_times[1:])]
        # Breaks longer than an hour are not listing pace.
        gaps = [g for g in gaps if g <= 60]

        def public(bucket):
            out = dict(bucket)
            out['worked'] = len({w for w in bucket['worked'] if w})
            out['value'] = round(bucket['value'], 2)
            return out

        best = max(series.values(), key=lambda d: d['listed'], default=None)
        active_days = [d for d in series.values() if d['listed']]
        return {
            'today': today.isoformat(),
            'days': days,
            'start': start.isoformat(),
            'actor': actor,
            'people': people,
            'totals': {name: public(bucket) for name, bucket in totals.items()},
            'pace': {
                'first_at': today_times[0].isoformat() if today_times else None,
                'last_at': today_times[-1].isoformat() if today_times else None,
                'avg_minutes': round(sum(gaps) / len(gaps), 1) if gaps else None,
            },
            'best_day': {'date': best['date'], 'listed': best['listed']} if best and best['listed'] else None,
            'avg_per_active_day': round(sum(d['listed'] for d in active_days) / len(active_days), 1) if active_days else 0,
            'series': [public(series[day]) for day in sorted(series)],
            'users': sorted((public(u) for u in users.values()), key=lambda u: (-u['listed'], -u['worked'], u['actor'])),
            'listings': sorted(listings, key=lambda l: (l['at'] or '', l['id']), reverse=True),
            'sold': {**sold, 'revenue': round(sold['revenue'], 2)},
            'ai': {**ai_totals, 'cost': round(ai_totals['cost'], 4)},
            'suggestions': suggestions,
            'queue_waiting': waiting,
        }


def register(app, lister, ai_db_path=None):
    stats = ListerStats(lister, ai_db_path)

    def api_lister_stats():
        try:
            try:
                days = int(request.args.get('days') or 30)
            except ValueError:
                days = 30
            return jsonify({'success': True, **stats.compute(days=days, actor=request.args.get('actor') or '')})
        except Exception as e:
            return jsonify({'success': False, 'error': lister.safe_error(e, 'lister:stats')}), 500

    def lister_stats_page():
        return render_template('lister_stats.html')

    app.add_url_rule('/api/lister/stats', 'api_lister_stats', api_lister_stats)
    app.add_url_rule('/lister-stats', 'lister_stats_page', lister_stats_page)
    return stats
