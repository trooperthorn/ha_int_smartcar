"""How often to ask Smartcar for data, and when an event is worth a call.

Three facts drive this module, all from Smartcar's own documentation:

- The allowance is **500 calls per vehicle per month** on the free tier, about
  16 a day for a single car, shared with commands.
- The Vehicles API "is not designed for continuous polling", and without a
  webhook subscription the data behind it "is typically updated once every 24
  hours". Polling an unsubscribed vehicle hourly does not produce hourly data.
  It produces the same daily data sixteen times and then a billing error.
- A vehicle subscribed to a webhook is updated as often as the OEM allows and
  pushed, at no cost to the allowance.

So the cheapest useful information is not a poll at all. It is a webhook, and
failing that it is something Home Assistant already knows for free: where the
people are. A phone leaving home says the car probably moved, and asking the
car once at that moment is worth more than sixteen scheduled asks that all land
while it sits on the drive.

The profiles below run from "never poll" to "fixed interval", with the
event-driven ones in between. Every one of them is subject to the budget in
`budget.py`; this module decides what is worth asking for, not what is
affordable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
import logging
from typing import Any

from homeassistant.const import STATE_HOME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class PollProfile(StrEnum):
    """How the user wants this vehicle polled."""

    WEBHOOK_ONLY = "webhook_only"
    ON_DEMAND = "on_demand"
    DAILY = "daily"
    TWICE_DAILY = "twice_daily"
    INTERVAL = "interval"


# the scheduled cadence of each profile. None means nothing is scheduled and
# the only polls are the ones an event or an action asks for.
PROFILE_INTERVALS: dict[PollProfile, timedelta | None] = {
    PollProfile.WEBHOOK_ONLY: None,
    PollProfile.ON_DEMAND: None,
    PollProfile.DAILY: timedelta(hours=24),
    PollProfile.TWICE_DAILY: timedelta(hours=12),
    PollProfile.INTERVAL: None,  # the interval comes from the options
}

# a floor on the configurable interval. one hour is already 720 calls a month,
# above the free tier allowance for a single vehicle, so anything shorter is a
# guaranteed billing error rather than a choice.
MIN_INTERVAL = timedelta(hours=1)

DEFAULT_INTERVAL = timedelta(hours=6)
DEFAULT_PROFILE = PollProfile.TWICE_DAILY

# how many event-driven polls a day a profile may add on top of its schedule.
# without a cap, a phone that flaps between home and not_home would drain a
# month of allowance in an afternoon.
DEFAULT_DAILY_EVENT_CAP = 4

# below this state of charge, a charging vehicle is worth watching more
# closely, because that is when "will it be ready" is an actual question.
DEFAULT_LOW_BATTERY = 30

PRESENCE_AWAY_STATES = {"not_home", "away"}


@dataclass(frozen=True, kw_only=True)
class PollSettings:
    """The user's answers, resolved into what this module needs."""

    profile: PollProfile = DEFAULT_PROFILE
    interval: timedelta = DEFAULT_INTERVAL
    presence_entities: tuple[str, ...] = ()
    poll_on_leave_home: bool = True
    poll_on_arrive_home: bool = False
    low_battery: int = DEFAULT_LOW_BATTERY
    daily_event_cap: int = DEFAULT_DAILY_EVENT_CAP

    @property
    def scheduled_interval(self) -> timedelta | None:
        """The coordinator's update interval, or None for no schedule.

        Returns:
            The interval to give DataUpdateCoordinator.
        """
        if self.profile is PollProfile.INTERVAL:
            return max(self.interval, MIN_INTERVAL)

        return PROFILE_INTERVALS[self.profile]

    @property
    def watches_presence(self) -> bool:
        """Whether any presence entity can trigger a poll.

        Returns:
            True when presence is configured and at least one edge is wanted.
        """
        return bool(self.presence_entities) and (
            self.poll_on_leave_home or self.poll_on_arrive_home
        )

    def monthly_estimate(self) -> int:
        """Roughly how many calls a month this profile schedules.

        Event-driven polls are counted at their cap, so this is a ceiling
        rather than a guess. It exists to be shown to the user next to the
        allowance, where "60 of 500" means more than an interval does.

        Returns:
            The estimated calls per 30 day month.
        """
        scheduled = 0

        if (interval := self.scheduled_interval) is not None:
            scheduled = int(timedelta(days=30) / interval)

        events = self.daily_event_cap * 30 if self.watches_presence else 0

        return scheduled + events


def resolve_settings(options: dict[str, Any]) -> PollSettings:
    """Build settings from stored options, tolerating missing keys.

    Returns:
        The resolved settings.
    """
    from .const import (  # noqa: PLC0415
        CONF_DAILY_EVENT_CAP,
        CONF_LOW_BATTERY,
        CONF_POLL_INTERVAL_HOURS,
        CONF_POLL_ON_ARRIVE_HOME,
        CONF_POLL_ON_LEAVE_HOME,
        CONF_POLL_PROFILE,
        CONF_PRESENCE_ENTITIES,
    )

    raw_profile = options.get(CONF_POLL_PROFILE, DEFAULT_PROFILE)

    try:
        profile = PollProfile(raw_profile)
    except ValueError:
        _LOGGER.warning(
            "Unknown poll profile %r, falling back to %s", raw_profile, DEFAULT_PROFILE
        )
        profile = DEFAULT_PROFILE

    hours = options.get(CONF_POLL_INTERVAL_HOURS)
    interval = timedelta(hours=hours) if hours else DEFAULT_INTERVAL

    return PollSettings(
        profile=profile,
        interval=interval,
        presence_entities=tuple(options.get(CONF_PRESENCE_ENTITIES, ()) or ()),
        poll_on_leave_home=options.get(CONF_POLL_ON_LEAVE_HOME, True),
        poll_on_arrive_home=options.get(CONF_POLL_ON_ARRIVE_HOME, False),
        low_battery=options.get(CONF_LOW_BATTERY, DEFAULT_LOW_BATTERY),
        daily_event_cap=options.get(CONF_DAILY_EVENT_CAP, DEFAULT_DAILY_EVENT_CAP),
    )


def is_away(state: str | None) -> bool:
    """Whether a presence state means "not at home".

    Anything unknown is deliberately not "away". A device_tracker that drops to
    unavailable while a phone reboots must not read as a departure and spend a
    call.

    Returns:
        True only for a positive away state.
    """
    if state is None or state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
        return False

    return state in PRESENCE_AWAY_STATES or (
        state != STATE_HOME and not state.startswith("zone.")
    )


def presence_transition_wants_poll(
    settings: PollSettings, old_state: str | None, new_state: str | None
) -> bool:
    """Whether a presence change is worth one call.

    Only the edges are interesting. A phone that stays away all day says
    nothing new; the moment it left says the car probably moved.

    Returns:
        True when this transition should trigger a poll.
    """
    if old_state == new_state:
        return False

    was_away = is_away(old_state)
    now_away = is_away(new_state)

    if was_away == now_away:
        return False

    # a transition out of an unknown state is not a departure, it is the
    # tracker waking up.
    if old_state is None or old_state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
        return False

    if now_away:
        return settings.poll_on_leave_home

    return settings.poll_on_arrive_home


def anyone_away(hass: HomeAssistant, entities: Iterable[str]) -> bool:
    """Whether any watched person or tracker is currently away.

    Returns:
        True when at least one entity positively reports away.
    """
    return any(
        is_away(state.state)
        for entity_id in entities
        if (state := hass.states.get(entity_id)) is not None
    )


def charge_wants_attention(
    settings: PollSettings, battery_level: float | None, *, is_charging: bool | None
) -> bool:
    """Whether the charge state justifies a poll outside the schedule.

    A vehicle actively charging below the threshold is the one case where the
    answer changes usefully within the hour. A full, idle vehicle is the case
    where it does not change at all, and where the schedule alone is plenty.

    Returns:
        True when the charge state is worth a call.
    """
    if not is_charging or battery_level is None:
        return False

    return battery_level < settings.low_battery
