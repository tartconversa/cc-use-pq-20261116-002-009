import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import DomainError, require


IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
KINDS = {'tea', 'tourism', 'convenience', 'industry', 'other'}


def identifier(value: str, label: str = 'id') -> str:
    require(isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)),
            'invalid_identifier', f'{label} must be 1-64 ASCII identifier characters')
    return value


def nonempty(value: str, label: str, maximum: int = 160) -> str:
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum,
            'invalid_text', f'{label} must be nonempty and at most {maximum} characters')
    return value.strip()


def instant(value: str, label: str = 'timestamp') -> datetime:
    require(isinstance(value, str), 'invalid_timestamp', f'{label} must be ISO 8601')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise DomainError('invalid_timestamp', f'{label} must be ISO 8601') from exc
    require(result.tzinfo is not None and result.utcoffset() is not None,
            'invalid_timestamp', f'{label} requires an explicit offset')
    return result.astimezone(timezone.utc)


def zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise DomainError('invalid_timezone', 'Unknown IANA time zone') from exc


def order(start: str, end: str) -> None:
    require(instant(start, 'starts_at') < instant(end, 'ends_at'),
            'invalid_interval', 'Start must precede end')


def decimal_score(value: object, maximum: Decimal) -> Decimal:
    require(not isinstance(value, bool), 'invalid_score', 'Scores must be finite decimal numbers')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DomainError('invalid_score', 'Scores must be finite decimal numbers') from exc
    require(number.is_finite() and 0 <= number <= maximum,
            'invalid_score', f'Score must be between zero and {maximum}')
    require(number.as_tuple().exponent >= -2, 'invalid_score', 'Scores use at most two decimals')
    return number


def rubric(value: dict) -> dict[str, str]:
    require(isinstance(value, dict) and 2 <= len(value) <= 8,
            'invalid_rubric', 'Rubric requires 2-8 criteria')
    result = {}
    for key, maximum in value.items():
        identifier(key, 'criterion')
        try:
            number = Decimal(str(maximum))
        except InvalidOperation as exc:
            raise DomainError('invalid_rubric', 'Criterion maximum must be numeric') from exc
        require(number.is_finite() and 0 < number <= 100 and number.as_tuple().exponent >= -2,
                'invalid_rubric', 'Criterion maximum must be positive, finite and at most 100')
        result[key] = str(number)
    require(sum(map(Decimal, result.values())) == Decimal('100'),
            'invalid_rubric', 'Criterion maxima must sum to 100')
    return result


def payload_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def positive(value: object, label: str, maximum: int = 100000) -> int:
    require(type(value) is int and 1 <= value <= maximum,
            'invalid_count', f'{label} must be an integer from 1 to {maximum}')
    return value


def normalized_attributes(value: dict) -> dict:
    require(isinstance(value, dict), 'invalid_attributes', 'Attributes must be an object')
    require(set(value) <= {'channel', 'topic', 'district'},
            'invalid_attributes', 'Unknown attribute')
    return {key: nonempty(item, key, 80) for key, item in value.items()}
