"""Several points of view on one feature.

A topic carries one headline, one stat and one hook per channel, so each
time it comes round it renders the identical card and makes the identical
argument. The Claude rewrite varies the WORDS; an angle varies the POINT —
pain, proof, audience, objection — and carries its own headline, body,
stat and hooks.

The leverage is in where it plugs in. cards.py, adspot.py, narration.py
and captions.py all read `topic["headline"]`, `topic["stat"]`, and so on,
so merging an angle into a copy of the topic dict varies the card image
and the voiceover as well as the text, and none of those modules change.

A topic with no `angles` key resolves to exactly one angle — its own
fields — so the calendar keeps working untouched and adopting this is
per-topic rather than all-at-once.
"""

# What an angle may override. `id` and `feature` are identity and stay put:
# id drives the media filename, the live-site capture anchor and the repeat
# cooldown, and a `verification` block is a claim traced to source, which an
# angle inherits rather than replaces.
ANGLE_FIELDS = ("headline", "body", "stat", "hook_x", "hook_ig")


def angles_for(topic):
    """Every angle for a topic, its existing copy first.

    The base fields are angle zero rather than a special case: that copy
    is already written, already verified, and already clean through the
    compliance gate, so a topic adopts angles by ADDING rather than by
    being rewritten.
    """
    base = {f: topic.get(f) for f in ANGLE_FIELDS}
    return [base] + list(topic.get("angles") or [])


def apply_angle(topic, angle):
    """A copy of `topic` with `angle`'s fields merged over it.

    A copy, not a mutation: the calendar dict is shared across the run and
    written back to disk, so editing it in place would persist one day's
    angle as the topic's permanent copy.
    """
    merged = dict(topic)
    merged.update({f: angle[f] for f in ANGLE_FIELDS if f in angle})
    merged.pop("angles", None)
    return merged


def select(available, post_count):
    """The angle for a topic's next post, or None if it has none usable.

    Indexed by the topic's OWN post count. select_format's docstring is
    the cautionary tale: indexing one rotation by a counter that also
    drives another silently re-locks whenever the periods share a factor,
    which is how every topic once got pinned to a single format. A
    per-topic count advances only when that topic ships, so it shares no
    period with the topic cursor or the format cycle.

    Returning None rather than raising on an empty list: empty means every
    angle failed compliance, and the caller is better placed to decide
    whether that drops the topic or the post.
    """
    if not available:
        return None
    return available[post_count % len(available)]
