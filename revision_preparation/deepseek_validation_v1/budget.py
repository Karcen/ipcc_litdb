"""Thread-safe CNY reservations using the published peak-rate upper bound."""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path
import datetime
from email.utils import parsedate_to_datetime
import json
import os
import tempfile
import threading

PEAK_CNY_PER_MILLION = {
    'deepseek-flash': {'hit': Decimal('0.04'), 'miss': Decimal('2'), 'output': Decimal('8')},
    'deepseek-v4-pro': {'hit': Decimal('0.30'), 'miss': Decimal('9'), 'output': Decimal('27')},
}
PRICING_URL = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
MILLION = Decimal(1000000)
OFFPEAK_FACTOR = Decimal('0.5')
RATE_BUFFER_SECONDS = 300
MAX_SERVICE_CLOCK_SKEW_SECONDS = 120
MAX_REQUEST_INTERVAL_SECONDS = 1800


def _aware_utc(value):
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Rate evidence requires timezone-aware timestamps')
    return value.astimezone(datetime.timezone.utc)


def settlement_rate(service_date=None, requested_utc=None, received_utc=None):
    """Discount only a verified, buffered interval wholly outside weekday peaks.

    Reservations always retain the peak rate. Holiday discounts are ignored.
    Ambiguous timing receives no discount and never changes previous entries.
    """
    evidence = {'pricing_url': PRICING_URL, 'pricing_observed_date': '2026-10-25',
                'service_date': service_date if isinstance(service_date, str) else None,
                'buffer_seconds_each_side': RATE_BUFFER_SECONDS,
                'max_clock_skew_seconds': MAX_SERVICE_CLOCK_SKEW_SECONDS,
                'max_request_interval_seconds': MAX_REQUEST_INTERVAL_SECONDS,
                'weekday_peak_windows_utc': ['01:00-04:00', '06:00-10:00'],
                'holiday_discounts_applied': False, 'classification': 'peak_fallback'}
    if service_date is None or requested_utc is None or received_utc is None:
        evidence['reason'] = 'missing_time_evidence'
        return Decimal(1), evidence
    try:
        if not isinstance(service_date, str):
            raise ValueError('Provider Date must be an HTTP date string')
        provider = _aware_utc(parsedate_to_datetime(service_date))
        requested, received = _aware_utc(requested_utc), _aware_utc(received_utc)
    except (TypeError, ValueError, OverflowError):
        evidence['reason'] = 'unparseable_or_naive_time_evidence'
        return Decimal(1), evidence
    evidence.update(provider_utc=provider.isoformat(), requested_utc=requested.isoformat(),
                    received_utc=received.isoformat())
    duration = (received - requested).total_seconds()
    skew = abs((provider - received).total_seconds())
    evidence.update(request_interval_seconds=duration, provider_to_received_seconds=skew)
    if not 0 <= duration <= MAX_REQUEST_INTERVAL_SECONDS:
        evidence['reason'] = 'invalid_or_excessive_request_interval'
        return Decimal(1), evidence
    if skew > MAX_SERVICE_CLOCK_SKEW_SECONDS:
        evidence['reason'] = 'provider_date_clock_skew_exceeds_limit'
        return Decimal(1), evidence
    try:
        start = requested - datetime.timedelta(seconds=RATE_BUFFER_SECONDS)
        end = received + datetime.timedelta(seconds=RATE_BUFFER_SECONDS)
    except OverflowError:
        evidence['reason'] = 'buffered_window_out_of_range'
        return Decimal(1), evidence
    evidence.update(buffered_start_utc=start.isoformat(), buffered_end_utc=end.isoformat())
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        if day.weekday() < 5:
            for first, last in ((1, 4), (6, 10)):
                peak_start = day + datetime.timedelta(hours=first)
                peak_end = day + datetime.timedelta(hours=last)
                # Treat exact boundary contact conservatively as peak overlap.
                if start <= peak_end and end >= peak_start:
                    evidence['reason'] = 'buffered_window_intersects_weekday_peak'
                    return Decimal(1), evidence
        if day.date() == end.date():
            break
        day += datetime.timedelta(days=1)
    evidence.update(classification='verified_offpeak', reason='verified_buffered_offpeak_window')
    return OFFPEAK_FACTOR, evidence

class BudgetStop(RuntimeError):
    pass

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def usage_upper(model, usage):
    rates = PEAK_CNY_PER_MILLION[model]
    total = usage['prompt_tokens']
    output = usage['completion_tokens']
    if any(type(value) is not int or value < 0 for value in (total, output)):
        raise ValueError('Invalid token usage')
    if 'prompt_cache_hit_tokens' in usage and 'prompt_cache_miss_tokens' in usage:
        hit, miss = usage['prompt_cache_hit_tokens'], usage['prompt_cache_miss_tokens']
        if any(type(value) is not int or value < 0 for value in (hit, miss)) or hit + miss != total:
            raise ValueError('Cache-token accounting does not match prompt tokens')
    else:
        hit, miss = 0, total
    return (Decimal(hit) * rates['hit'] + Decimal(miss) * rates['miss'] + Decimal(output) * rates['output']) / MILLION

class Ledger:
    def __init__(self, path, limit_cny):
        self.path = Path(path)
        self.lock = threading.RLock()
        limit = Decimal(str(limit_cny))
        if not limit.is_finite() or not Decimal(0) < limit <= Decimal(100):
            raise ValueError('Budget must be finite, positive, and no greater than authorized CNY 100')
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if Decimal(self.data['authorized_limit_cny']) != limit:
                raise ValueError('Existing budget limit differs; do not silently change authorization')
        else:
            self.data = {'authorized_limit_cny': str(limit), 'currency': 'CNY', 'entries': {},
                         'pricing_url': PRICING_URL, 'pricing_observed_date': '2026-10-25',
                         'accounting': 'Peak-rate upper estimate from actual usage; not a provider invoice.',
                         'created_utc': now()}
            self.save()

    def save(self):
        write_json(self.path, self.data)

    def totals(self):
        with self.lock:
            charged = sum((Decimal(x['charged_upper_cny']) for x in self.data['entries'].values()
                           if x.get('charged_upper_cny') is not None), Decimal(0))
            reserved = sum((Decimal(x['reservation_upper_cny']) for x in self.data['entries'].values()
                            if x['state'] == 'reserved'), Decimal(0))
            return {'charged_upper_cny': str(charged), 'reserved_upper_cny': str(reserved),
                    'remaining_upper_cny': str(Decimal(self.data['authorized_limit_cny']) - charged - reserved)}

    def reserve(self, identifier, model, input_upper, max_output):
        with self.lock:
            if any(type(value) is not int or value < 0 for value in (input_upper, max_output)):
                raise ValueError('Reservation token bounds must be nonnegative integers')
            if identifier in self.data['entries']:
                raise BudgetStop('Attempt already reserved or charged; refuse an ambiguous resend')
            if any(item.get('reservation_exceeded') for item in self.data['entries'].values()):
                raise BudgetStop('An earlier usage amount exceeded its reservation; explicit review is required')
            rates = PEAK_CNY_PER_MILLION[model]
            amount = (Decimal(input_upper) * rates['miss'] + Decimal(max_output) * rates['output']) / MILLION
            if amount > Decimal(self.totals()['remaining_upper_cny']):
                raise BudgetStop('Next request cannot fit within the authorized budget')
            self.data['entries'][identifier] = {'model': model, 'state': 'reserved',
                'reservation_upper_cny': str(amount), 'charged_upper_cny': None, 'utc': now()}
            self.save()
            return str(amount)

    def settle(self, identifier, usage, *, service_date=None, requested_utc=None, received_utc=None):
        with self.lock:
            item = self.data['entries'][identifier]
            if item['state'] != 'reserved':
                raise ValueError('Attempt already settled')
            peak_amount = usage_upper(item['model'], usage)
            factor, evidence = settlement_rate(service_date, requested_utc, received_utc)
            amount = peak_amount * factor
            item.update(state='settled', usage=usage, peak_usage_upper_cny=str(peak_amount),
                        settlement_factor=str(factor), rate_evidence=evidence,
                        charged_upper_cny=str(amount), finished_utc=now())
            exceeded = peak_amount > Decimal(item['reservation_upper_cny'])
            item['reservation_exceeded'] = exceeded
            self.save()
            if exceeded:
                raise BudgetStop('Provider usage exceeded the reserved upper bound; stop further calls')
            return str(amount)

    def uncertain(self, identifier, error_class):
        with self.lock:
            item = self.data['entries'][identifier]
            if item['state'] == 'reserved':
                item.update(state='unknown_charge', charged_upper_cny=item['reservation_upper_cny'],
                            error_class=error_class, finished_utc=now())
                self.save()
