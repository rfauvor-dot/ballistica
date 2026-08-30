"""Canonical text for the four in-app audio walkthrough sections -- the
single source of truth for TTS generation (scripts/generate_walkthrough_
audio.py), used verbatim, paragraph for paragraph, from the finalized
script Rick provided (Ballistica_Audio_Walkthrough_Script.docx,
2026-08-28). Narration paragraphs only -- the script's own "Runtime
target: N to M minutes" line under each heading is a production note for
Rick, not narration, and is deliberately excluded from what gets spoken.

Playback itself is static, pre-generated MP3 (ballistica/web/audio/,
served via the /audio mount in api.py) -- not live TTS per request. This
content never changes per user and doesn't need regenerating on every
play, unlike a live ballistic solution.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WalkthroughSection:
    key: str  # matches the generated filename stem, e.g. "walkthrough-1-getting-started"
    title: str
    paragraphs: tuple[str, ...]

    @property
    def narration_text(self) -> str:
        return "\n\n".join(self.paragraphs)


WALKTHROUGH_SECTIONS: tuple[WalkthroughSection, ...] = (
    WalkthroughSection(
        key="walkthrough-1-getting-started",
        title="Getting Started",
        paragraphs=(
            "Welcome to Ballistica. I'm going to walk you through everything you need to know to "
            "get set up and comfortable using the app. This section covers the basics, your "
            "account, how the app is organized, and how to talk to me. If you already know this "
            "part, feel free to skip ahead using the menu.",
            "Everything in Ballistica is voice-first. You can talk to me naturally, the way you'd "
            "talk to a shooting partner. You don't need to memorize exact phrases, just tell me "
            "what you want, like you would at the range.",
            "Your account keeps your rifles, your loads, and your shooting data private to you. "
            "Nothing you enter is shared with other shooters directly. If you ever choose to "
            "contribute data to help improve Ballistica's reference database for everyone, that "
            "data is stripped of anything that identifies you the moment it's submitted, there's "
            "no way to trace it back to your account, even for us.",
            "Once you're signed in, everything is organized around three things: your rifles, "
            "your loads, and your sessions. A rifle is your firearm profile. A load is a specific "
            "ammunition setup you use in that rifle. A session is a single trip to the range where "
            "you're actually shooting and logging data.",
            "There are three other sections waiting for you in the menu whenever you need them: "
            "setting up your rifle and equipment, checking a load and its velocity, and long-range "
            "shooting and spotting. You can come back to any of them, anytime, as many times as "
            "you like.",
            "That's the basics. Let's get you shooting.",
        ),
    ),
    WalkthroughSection(
        key="walkthrough-2-rifle-setup",
        title="Rifle and Equipment Setup",
        paragraphs=(
            "This section covers setting up a new rifle in Ballistica, along with the equipment "
            "details that go with it.",
            "To start, just tell me you want to add a new rifle. I'll ask you for the details "
            "naturally, one at a time if I need to, or you can rattle them all off at once if "
            "that's easier, either way works.",
            "Here's what I'll need: the rifle's name, whatever you want to call it. The caliber. "
            "The barrel length. The barrel twist rate. And your scope details, things like turret "
            "click value and reticle type, if you want ballistic solutions dialed into your "
            "specific glass.",
            "You can set up as many rifles as you own. Each one gets its own profile, and each one "
            "can have its own loads tied to it, so switching between rifles is as simple as saying "
            "which one you want to use.",
            "If you ever need to check or change a rifle's details later, just ask me to pull up "
            "that rifle by name, or ask what's on file for it. You can update any field at any "
            "time, nothing is locked in once you save it.",
            "Once your rifle is set up, you're ready to add loads to it, which is covered in the "
            "next section, checking a load and its velocity.",
        ),
    ),
    WalkthroughSection(
        key="walkthrough-3-load-and-velocity",
        title="Checking a Load and Velocity",
        paragraphs=(
            "This section covers setting up a load, and how Ballistica helps you verify and "
            "calibrate its actual velocity out of your rifle.",
            "A load is tied to a specific rifle. To set one up, tell me you want to add a load, "
            "and I'll walk you through the details, powder, charge weight, bullet weight and type, "
            "primer, case, and seating depth, whatever you have. Just like rifle setup, you can "
            "give it to me all at once or piece by piece.",
            "When a load is first created, Ballistica uses published book data as its starting "
            "reference for velocity, if a match is available. That's a starting point only, it's "
            "not a substitute for verifying your actual velocity out of your actual rifle.",
            "That's where calibration comes in. At the range, once you're set up and firing that "
            "load through a chronograph, tell me you're starting a calibration string. Then, as "
            "each shot goes downrange, just call out the reading, shot one, twenty seven fifty, "
            "shot two, twenty seven sixty, and so on. I'll keep a running average as you go, and "
            "I'll flag anything that looks like an outlier rather than quietly folding it into "
            "your average.",
            "You can ask me for the current average at any point mid-string. When you're done, "
            "tell me the string is finished, and I'll lock in that trued average as the calibrated "
            "velocity for that load, replacing the book estimate for every future calculation "
            "using it.",
            "One more thing worth remembering, every load and every velocity figure in Ballistica, "
            "whether it's book data or your own calibrated number, is a reference tool. Always "
            "verify against a published manufacturer manual before loading or firing anything, and "
            "always work up a load gradually while watching for pressure signs. That's your "
            "responsibility, not something the app can do for you.",
            "Once a load is calibrated, you're ready to actually use it for real solutions, which "
            "brings us to long-range shooting and spotting.",
        ),
    ),
    WalkthroughSection(
        key="walkthrough-4-long-range-and-spotting",
        title="Long Range Shooting and Spotting",
        paragraphs=(
            "This section covers how to actually use Ballistica live, at distance, to get a firing "
            "solution and make corrections.",
            "Once you've got a rifle and a calibrated or book-spec load selected, just tell me the "
            "distance to your target, and ask for a solution. Something like, distance four "
            "hundred yards, get solution, works, but you don't have to be rigid about it, natural "
            "phrasing works too.",
            "I'll give you back your elevation and windage corrections. If you miss what I said, "
            "or you just want it again, ask me to repeat the solution, or repeat just the windage, "
            "or repeat just the elevation, and I'll give you that piece again without repeating "
            "everything else.",
            "Conditions matter for accuracy, so you can update wind, temperature, altitude, or "
            "humidity at any point, just by telling me the new numbers, and your next solution "
            "will reflect them. There's also a faster way to get most of that: tap use my "
            "location, and Ballistica pulls temperature, humidity, altitude, and pressure "
            "automatically from the nearest weather station to you. Wind still needs your own "
            "call either way, since GPS has no way of knowing which direction you're actually "
            "facing.",
            "If you need to switch loads or rifles mid-session, just tell me which one you want, "
            "and I'll switch context immediately, your dialed corrections and history stay tied to "
            "the right rifle and load automatically.",
            "While you're actually shooting, I keep my responses short and precise on purpose, "
            "confirming your numbers, then getting out of your way, rather than talking over your "
            "rhythm at the line.",
            "That covers the core of live shooting and spotting with Ballistica. If you ever want "
            "a refresher on rifle setup, load and velocity work, or the basics, you can always "
            "come back to any of these sections from the menu.",
        ),
    ),
)
