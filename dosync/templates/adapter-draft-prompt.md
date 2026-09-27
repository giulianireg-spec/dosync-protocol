You are drafting a DoSync declarative adapter: a YAML file that tells a hub what
a physical device can do, and how to ask it to do those things.

THE DEVICE YOU ARE DESCRIBING
This is everything the hub observed about it. It is not a specification of the device and
may be incomplete.
%%DEVICE_EVIDENCE%%

WHAT TO PRODUCE
A single YAML document and nothing else — no prose before or after, no markdown
fences. It will be saved at:
    %%OUTPUT_PATH%%

WHAT DOSYNC DOES WITH IT
The hub resolves *goals* into device actions. It never reads your action NAMES;
it reads each action's `type`, which says what the action MEANS. `pause_job`
means nothing to the resolver — `type: pause` does. The actions you declare
decide which goals a device takes part in: a goal that needs `stop` reaches every
device declaring one. Tags only rank the capable devices against each other, and
where the device is placed restricts it — so declare what the device can really
do, and add a tag only where it truly describes the device.

TAGS — use these where one applies, and do not invent alternatives
%%TAG_VOCABULARY%%
A tag no other deployment would write belongs to that deployment, not to the
device. Vendor names are never tags: `wiz` tells another hub nothing.

THE FORMAT — real, shipped examples. Follow their structure.
%%EXAMPLES%%

RULES
1. Only declare an action if you have concrete grounds to believe the endpoint
   exists on THIS device. Plausible is not grounds. A printer usually has a
   heated bed; this one may not expose it.
2. Where you do not know how to do something, write a YAML comment saying so —
   `# I could not determine how to cancel a job on this device` — instead of
   guessing. An incomplete honest file is worth more than a complete false one,
   because the person reading it can finish the first and cannot detect the
   second.
3. Mark anything you are not confident about with `# UNVERIFIED` on its own line
   above the action.
4. Prefer read-only actions and status endpoints first. Those can be checked
   against the device before anyone relies on them; the rest cannot.
5. Set `emergency_capable: true` only if this device genuinely matters in an
   emergency, and say why in a comment. It lets emergency actions reach the
   device in every emergency of the deployment, unless one is restricted to a
   place the device is not in.
6. Never include credentials. Where one is needed use a placeholder such as
   REPLACE_WITH_YOUR_API_KEY and note in a comment what it is and where the
   operator finds it.
7. Assume nothing about the setting. This device may be in a house, a factory, a
   hospital or a vehicle. Do not write comments, names or tags that presume one.
